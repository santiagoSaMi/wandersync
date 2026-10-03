"""Tests de la mutacion createBooking (checkout) contra el esquema GraphQL real del gateway.
Los servicios externos (auth, catalogo Supabase, orders, Redis) se sustituyen en sus fronteras.
Ejecutar desde services/gateway:  python -m unittest discover -s tests -v
Requiere las dependencias del gateway (si faltan, se omite; con REQUIRE_TEST_DEPS=1 falla en vez de omitirse)."""
import importlib.util
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HAS_DEPS = all(importlib.util.find_spec(m) for m in ("strawberry", "fastapi", "redis", "httpx"))
if not HAS_DEPS and os.getenv("REQUIRE_TEST_DEPS"):
    raise RuntimeError("REQUIRE_TEST_DEPS=1 pero faltan dependencias de test (ver requirements-test.txt)")

USER = {"id": "user-1", "email": "ana@example.com", "full_name": "Ana Viajera"}
MUTATION = """mutation($i: BookingInput!) { createBooking(input: $i) { id status totalCents } }"""


def offers(**changes):
    o = {
        "flight": {"id": "fl-1", "origin": "BOG", "destination": "CTG", "airline": "Avianca", "flight_number": "AV 1",
                   "departure_date": "2026-10-12", "departure_time": "06:00", "arrival_time": "07:30", "stops": 0,
                   "price_cents": 20000, "currency": "USD"},
        "hotel": {"id": "ho-1", "city_code": "CTG", "name": "Hotel Bahia", "stars": 4, "checkin": "2026-10-12",
                  "nights": 3, "total_price_cents": 30000, "currency": "USD"},
        "car": {"id": "ca-1", "city_code": "CTG", "company": "Hertz", "model": "Kwid", "category": "Economico",
                "transmission": "MANUAL", "pickup_date": "2026-10-12", "days": 3, "total_price_cents": 10000,
                "currency": "USD"},
    }
    for path, value in changes.items():                      # p. ej. hotel__city_code="MDE", hotel=None
        kind, _, field = path.partition("__")
        if field:
            o[kind][field] = value
        else:
            o[kind] = value
    return o


@unittest.skipUnless(HAS_DEPS, "requiere las dependencias del gateway (pip install -r requirements.txt)")
class CreateBookingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("INTERNAL_TOKEN", "test-token")
        os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")
        from fastapi.testclient import TestClient

        import app.context as ctx
        import app.schema as schema
        from app.main import app

        cls.ctx, cls.schema = ctx, schema
        cls._saved = [(ctx.auth_api, "resolve"), (ctx.auth_api, "anonymous"), (ctx.limiter, "hit"),
                      (schema.catalog, "offers_by_ids"), (schema.orders_api, "create")]
        cls._saved = [(obj, name, obj.__dict__.get(name)) for obj, name in cls._saved]

        async def resolve(sid):
            return {"valid": True, "user": USER} if sid == "good" else {"valid": False, "user": None}

        async def anonymous():
            return "anon"

        async def hit(bucket, identifier, limit, window):
            key = (bucket, identifier)
            cls.hits[key] = cls.hits.get(key, 0) + 1
            return cls.hits[key] <= limit, 60

        async def offers_by_ids(f, h, c):
            cls.offer_ids = (f, h, c)
            return cls.catalog_offers

        async def create(payload):
            cls.payloads.append(payload)
            return {"id": "order-1", "status": "CONFIRMED", "currency": "USD", "created_at": "2026-10-03T00:00:00Z",
                    "total_cents": sum(payload[k]["price_cents"] for k in ("flight", "hotel", "car")),
                    "failure_reason": None, "invoice_number": "WS-1", "items": [], "steps": []}

        ctx.auth_api.resolve, ctx.auth_api.anonymous, ctx.limiter.hit = resolve, anonymous, hit
        schema.catalog.offers_by_ids, schema.orders_api.create = offers_by_ids, create
        cls.client = TestClient(app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        for obj, name, original in cls._saved:                 # no contaminar otros modulos de test
            if original is None:
                obj.__dict__.pop(name, None)
            else:
                setattr(obj, name, original)

    def setUp(self):
        type(self).hits, type(self).payloads, type(self).catalog_offers = {}, [], offers()
        self.client.cookies.clear()
        self.client.cookies.set("ws_sid", "good")
        self.schema.settings.allow_failure_simulation = False

    def book(self, **overrides):
        variables = {"i": {"flightOfferId": "fl-1", "hotelOfferId": "ho-1", "carOfferId": "ca-1",
                           "idempotencyKey": "key-12345678", **overrides}}
        return self.client.post("/graphql", json={"query": MUTATION, "variables": variables}).json()

    def code(self, body):
        return body["errors"][0]["extensions"]["code"] if body.get("errors") else None

    # ------------------------------------------------------------------ camino feliz
    def test_prices_come_from_catalog_and_user_from_session(self):
        body = self.book()
        self.assertEqual(body["data"]["createBooking"], {"id": "order-1", "status": "CONFIRMED", "totalCents": 60000})
        (payload,) = self.payloads
        self.assertEqual(self.offer_ids, ("fl-1", "ho-1", "ca-1"))
        self.assertEqual((payload["user_id"], payload["contact_name"]), ("user-1", "Ana Viajera"))
        self.assertEqual([payload[k]["price_cents"] for k in ("flight", "hotel", "car")], [20000, 30000, 10000])
        self.assertEqual(payload["idempotency_key"], "key-12345678")

    def test_client_cannot_send_prices(self):
        body = self.book(priceCents=1)                         # el esquema ni siquiera tiene ese campo
        self.assertTrue(body.get("errors"))
        self.assertEqual(self.payloads, [])

    # ------------------------------------------------------------------ autenticacion y validacion
    def test_requires_authentication(self):
        self.client.cookies.clear()
        self.assertEqual(self.code(self.book()), "UNAUTHENTICATED")
        self.assertEqual(self.payloads, [])

    def test_invalid_arguments(self):
        for overrides in ({"idempotencyKey": "corta"}, {"idempotencyKey": "x" * 65},
                          {"flightOfferId": "fl-1; drop"}, {"carOfferId": ""}):
            with self.subTest(**overrides):
                self.assertEqual(self.code(self.book(**overrides)), "INVALID_ARGUMENT")
        self.assertEqual(self.payloads, [])

    def test_missing_offer(self):
        type(self).catalog_offers = offers(hotel=None)
        self.assertEqual(self.code(self.book()), "OFFER_NOT_FOUND")

    def test_offers_must_form_a_coherent_package(self):
        for change in ({"hotel__city_code": "MDE"}, {"car__pickup_date": "2026-10-19"},
                       {"car__currency": "EUR", "car__total_price_cents": 10000}):
            with self.subTest(**change):
                type(self).catalog_offers = offers(**change)
                self.assertEqual(self.code(self.book()), "INVALID_PACKAGE")
        self.assertEqual(self.payloads, [])

    # ------------------------------------------------------------------ modo demo
    def test_failure_simulation_dropped_unless_enabled(self):
        self.book(simulateFailure="CAR")
        self.schema.settings.allow_failure_simulation = True
        self.book(simulateFailure="CAR", idempotencyKey="key-87654321")
        self.assertEqual([p["simulate_failure"] for p in self.payloads], [None, "CAR"])

    def test_slow_motion_only_in_demo_mode(self):
        self.book(slowMotion=True)                             # modo demo apagado: se ignora
        self.book(idempotencyKey="key-11111111")               # sin pedirlo
        self.schema.settings.allow_failure_simulation = True
        self.book(slowMotion=True, idempotencyKey="key-22222222")
        delay = self.schema.settings.demo_step_delay_ms
        self.assertGreater(delay, 0)
        self.assertEqual([p["step_delay_ms"] for p in self.payloads], [0, 0, delay])

    # ------------------------------------------------------------------ rate limiting de checkout
    def test_checkout_rate_limited_per_user(self):
        results = [self.code(self.book(idempotencyKey=f"key-{i:08d}")) for i in range(6)]
        self.assertEqual(results, [None] * 5 + ["RATE_LIMITED"])
        self.assertEqual(len(self.payloads), 5)
        self.assertIn(("checkout", "user-1"), self.hits)        # por usuario, no por IP


if __name__ == "__main__":
    unittest.main()
