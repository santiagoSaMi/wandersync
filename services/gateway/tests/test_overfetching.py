"""Sin over-fetching de punta a punta, y DEMOSTRABLE:
  - hacia Supabase solo viajan las columnas seleccionadas (y la traza en `extensions.supabase` lo muestra);
  - hacia orders, la bitacora de la SAGA solo se pide si el cliente selecciono `steps`.
Ejecutar desde services/gateway:  python -m unittest discover -s tests -v
Requiere las dependencias del gateway (si faltan, se omite; con REQUIRE_TEST_DEPS=1 falla en vez de omitirse)."""
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HAS_DEPS = all(importlib.util.find_spec(m) for m in ("strawberry", "fastapi", "redis", "httpx"))
if not HAS_DEPS and os.getenv("REQUIRE_TEST_DEPS"):
    raise RuntimeError("REQUIRE_TEST_DEPS=1 pero faltan dependencias de test (ver requirements-test.txt)")

PACKAGES = '{ packages(origin: "BOG", destination: "CTG", departureDate: "2026-10-17", limit: 1) { %s } }'
NODE = {"id": "x", "price_cents": 1, "total_price_cents": 1, "currency": "USD", "nights": 3}


@unittest.skipUnless(HAS_DEPS, "requiere las dependencias del gateway (pip install -r requirements.txt)")
class OverFetchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("INTERNAL_TOKEN", "test-token")
        os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")
        import httpx
        from fastapi.testclient import TestClient

        import app.context as ctx
        import app.schema as schema
        from app.main import app

        cls.schema, cls.sent = schema, []
        catalog, settings = schema.catalog, schema.settings
        cls._saved_attrs = [(settings, n, getattr(settings, n)) for n in
                            ("supabase_url", "supabase_anon_key", "supabase_query_trace")]
        cls._saved_attrs += [(catalog, n, getattr(catalog, n)) for n in ("endpoint", "http", "_status_cache")]
        cls._saved_dict = [(o, n, o.__dict__.get(n)) for o, n in
                           ((ctx.auth_api, "resolve"), (schema.orders_api, "list"), (schema.orders_api, "get"))]

        def supabase(request):                                 # Supabase falso: registra lo que realmente se envio
            cls.sent.append(json.loads(request.content)["query"])
            empty = {"edges": [{"node": NODE}]}
            return httpx.Response(200, json={"data": {"flights": empty, "hotels": empty, "cars": empty, "r": empty,
                                                      "runs": {"edges": []}, "flight": empty, "hotel": empty,
                                                      "car": empty}})

        settings.supabase_url, settings.supabase_anon_key = "http://supabase.test", "key"
        catalog.endpoint = "http://supabase.test/graphql/v1"
        catalog.http = httpx.AsyncClient(transport=httpx.MockTransport(supabase))

        async def resolve(sid):
            return {"valid": True, "user": {"id": "u1", "email": "a@b.co", "full_name": "Ana"}}

        async def list_orders(user_id, include_steps=True):
            cls.include_steps.append(include_steps)
            return []

        async def get_order(order_id, user_id, include_steps=True):
            cls.include_steps.append(include_steps)
            return None

        ctx.auth_api.resolve, schema.orders_api.list, schema.orders_api.get = resolve, list_orders, get_order
        cls.client = TestClient(app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        for obj, name, value in cls._saved_attrs:
            setattr(obj, name, value)
        for obj, name, original in cls._saved_dict:
            if original is None:
                obj.__dict__.pop(name, None)
            else:
                setattr(obj, name, original)

    def setUp(self):
        type(self).sent, type(self).include_steps = [], []
        self.schema.settings.supabase_query_trace = True
        self.client.cookies.set("ws_sid", "good")

    def gql(self, query):
        return self.client.post("/graphql", json={"query": query}).json()

    # ------------------------------------------------------------------ Supabase: solo columnas seleccionadas
    def test_columns_follow_the_selection_and_trace_shows_them(self):
        small = self.gql(PACKAGES % "totalPriceCents flight { airline }")
        large = self.gql(PACKAGES % "totalPriceCents flight { airline flightNumber } hotel { name stars }")
        (t_small,), (t_large,) = small["extensions"]["supabase"], large["extensions"]["supabase"]
        self.assertEqual(t_small["collections"]["flight_offers"], ["airline", "currency", "id", "price_cents"])
        self.assertEqual(t_small["collections"]["hotel_offers"], ["id", "nights", "total_price_cents"])
        self.assertEqual(set(t_large["collections"]["flight_offers"]) - set(t_small["collections"]["flight_offers"]),
                         {"flight_number"})
        self.assertEqual(set(t_large["collections"]["hotel_offers"]) - set(t_small["collections"]["hotel_offers"]),
                         {"name", "stars"})

    def test_trace_matches_what_was_really_sent(self):
        body = self.gql(PACKAGES % "flight { airline cabin }")
        (entry,) = body["extensions"]["supabase"]
        self.assertEqual(len(self.sent), 1)                     # vuelos + hoteles + autos en UNA peticion
        self.assertEqual(entry["query"], " ".join(self.sent[0].split()))
        self.assertIn("node { airline cabin currency id price_cents }", entry["query"])
        self.assertNotIn("duration_minutes", self.sent[0])      # campo no pedido => columna no pedida

    def test_trace_disabled_adds_nothing(self):
        self.schema.settings.supabase_query_trace = False
        body = self.gql(PACKAGES % "id")
        self.assertNotIn("extensions", body)
        self.assertEqual(len(self.sent), 1)

    def test_cached_catalog_status_is_reported_as_cache(self):
        self.schema.catalog._status_cache = None
        first = self.gql("{ catalogStatus { ready } }")["extensions"]["supabase"]
        second = self.gql("{ catalogStatus { ready } }")["extensions"]["supabase"]
        self.assertNotIn("cached", first[0])
        self.assertTrue(second[0]["cached"])
        self.assertEqual(len(self.sent), 1)                     # la segunda no toco Supabase

    # ------------------------------------------------------------------ orders: bitacora SAGA solo si se pide
    def test_saga_steps_requested_only_when_selected(self):
        self.gql("{ myOrders { id status } }")
        self.gql("{ myOrders { id steps { name status } } }")
        self.gql("fragment F on Order { id steps { name } } { myOrders { ...F } }")   # como el frontend
        self.gql('{ order(id: "a1b2c3d4-0000-0000-0000-000000000000") { id totalCents } }')
        self.assertEqual(self.include_steps, [False, True, True, False])


if __name__ == "__main__":
    unittest.main()
