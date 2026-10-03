"""Regresion: el contexto GraphQL debe ser aceptado por Strawberry (si no, TODO /graphql responde 500).
Requiere las dependencias del gateway (strawberry, fastapi); si no estan instaladas, se omite.
Ejecutar desde services/gateway:  python -m unittest discover -s tests -v"""
import importlib.util
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HAS_DEPS = all(importlib.util.find_spec(m) for m in ("strawberry", "fastapi", "redis", "httpx"))
if not HAS_DEPS and os.getenv("REQUIRE_TEST_DEPS"):
    raise RuntimeError("REQUIRE_TEST_DEPS=1 pero faltan dependencias de test (ver requirements-test.txt)")


@unittest.skipUnless(HAS_DEPS, "requiere las dependencias del gateway (pip install -r requirements.txt)")
class GraphQLContext(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("INTERNAL_TOKEN", "test-token")
        os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")   # inalcanzable: el limitador global lo tolera
        from fastapi.testclient import TestClient

        import app.context as ctx
        from app.main import app

        async def anonymous():
            return "anon-sid"

        async def resolve(sid):
            if sid == "good":
                return {"valid": True, "user": {"id": "u1", "email": "a@b.co", "full_name": "Ana"}}
            return {"valid": False, "user": None}

        ctx.auth_api.anonymous, ctx.auth_api.resolve = anonymous, resolve
        cls.client = TestClient(app, raise_server_exceptions=False)

    def setUp(self):
        self.client.cookies.clear()

    def test_query_without_backends_succeeds(self):
        r = self.client.post("/graphql", json={"query": "{ __typename }"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"data": {"__typename": "Query"}})

    def test_anonymous_session_cookie_reaches_response(self):
        r = self.client.post("/graphql", json={"query": "{ me { id } }"})
        self.assertEqual(r.json(), {"data": {"me": None}})
        cookie = r.headers.get("set-cookie", "")
        self.assertIn("ws_sid=anon-sid", cookie)
        self.assertIn("HttpOnly", cookie)

    def test_valid_session_resolves_user(self):
        self.client.cookies.set("ws_sid", "good")
        r = self.client.post("/graphql", json={"query": "{ me { email } }"})
        self.assertEqual(r.json(), {"data": {"me": {"email": "a@b.co"}}})


@unittest.skipUnless(HAS_DEPS, "requiere las dependencias del gateway (pip install -r requirements.txt)")
class QueryCostLimits(unittest.TestCase):
    """Una sola peticion no debe poder multiplicar las llamadas a Supabase con alias (fan-out)."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("INTERNAL_TOKEN", "test-token")
        os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")
        from fastapi.testclient import TestClient

        import app.schema as schema
        from app.main import app

        cls.calls = 0

        async def execute(query, variables, collections):
            cls.calls += 1
            return {"flights": {"edges": []}, "hotels": {"edges": []}, "cars": {"edges": []}}

        cls.catalog = schema.catalog
        schema.catalog.execute = execute
        cls.client = TestClient(app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        cls.catalog.__dict__.pop("execute", None)          # vuelve al metodo real: no contaminar otros tests

    def packages_query(self, aliases: int) -> dict:
        self.__class__.calls = 0
        fields = " ".join(f'a{i}: packages(origin: "BOG", destination: "CTG", departureDate: "2026-10-12") {{ id }}'
                          for i in range(aliases))
        return self.client.post("/graphql", json={"query": "{ " + fields + " }"}).json()

    def test_up_to_five_aliases_allowed(self):
        body = self.packages_query(5)
        self.assertNotIn("errors", body)
        self.assertEqual(self.calls, 5)

    def test_too_many_aliases_rejected_before_any_backend_call(self):
        body = self.packages_query(20)
        self.assertIn("aliases", body["errors"][0]["message"])
        self.assertEqual(self.calls, 0)

    def test_oversized_document_rejected(self):
        query = "{ " + " ".join(["__typename"] * 1200) + " }"
        body = self.client.post("/graphql", json={"query": query}).json()
        self.assertIn("1000 tokens", body["errors"][0]["message"])


if __name__ == "__main__":
    unittest.main()
