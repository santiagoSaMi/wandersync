"""Tests de la billetera en el gateway: consulta `wallet`, error INSUFFICIENT_FUNDS al reservar sin saldo,
mutación de demostración y apertura de la billetera al registrarse.
Ejecutar desde services/gateway:  python -m unittest discover -s tests -v
Requiere las dependencias del gateway (si faltan, se omite; con REQUIRE_TEST_DEPS=1 falla en vez de omitirse)."""
import asyncio
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
WALLET = {"user_id": "user-1", "balance_cents": 465800, "currency": "USD", "movements": [
    {"kind": "DEBIT", "amount_cents": -34200, "balance_after_cents": 465800, "order_id": "o-1",
     "created_at": "2026-10-03T12:00:00Z"},
    {"kind": "OPENING", "amount_cents": 500000, "balance_after_cents": 500000, "order_id": None,
     "created_at": "2026-10-03T11:00:00Z"}]}


@unittest.skipUnless(HAS_DEPS, "requiere las dependencias del gateway (pip install -r requirements.txt)")
class WalletGatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("INTERNAL_TOKEN", "test-token")
        os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")
        from fastapi.testclient import TestClient

        import app.context as ctx
        import app.schema as schema
        from app.main import app

        cls.ctx, cls.schema = ctx, schema
        targets = [(ctx.auth_api, "resolve"), (ctx.auth_api, "anonymous"), (ctx.auth_api, "register"),
                   (ctx.auth_api, "login"), (ctx.limiter, "hit"), (schema.orders_api, "wallet"),
                   (schema.orders_api, "open_wallet"), (schema.orders_api, "demo_set_balance")]
        cls._saved = [(obj, name, obj.__dict__.get(name)) for obj, name in targets]

        async def resolve(sid):
            return {"valid": True, "user": USER} if sid == "good" else {"valid": False, "user": None}

        async def anonymous():
            return "anon"

        async def hit(*args):
            return True, 0

        async def register(email, password, full_name):
            return {**USER, "email": email}

        async def login(email, password, current):
            return {"session_id": "good", "user": USER}

        async def wallet(user_id):
            cls.calls.append(("wallet", user_id))
            return WALLET

        async def open_wallet(user_id):
            cls.calls.append(("open", user_id))
            if cls.open_fails:
                raise RuntimeError("orders caído")
            return WALLET

        async def demo_set_balance(user_id, cents):
            cls.calls.append(("demo", user_id, cents))
            return {**WALLET, "balance_cents": cents}

        ctx.auth_api.resolve, ctx.auth_api.anonymous, ctx.limiter.hit = resolve, anonymous, hit
        ctx.auth_api.register, ctx.auth_api.login = register, login
        schema.orders_api.wallet, schema.orders_api.open_wallet = wallet, open_wallet
        schema.orders_api.demo_set_balance = demo_set_balance
        cls.client = TestClient(app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        for obj, name, original in cls._saved:
            if original is None:
                obj.__dict__.pop(name, None)
            else:
                setattr(obj, name, original)

    def setUp(self):
        type(self).calls, type(self).open_fails = [], False
        self.client.cookies.clear()
        self.client.cookies.set("ws_sid", "good")
        self.schema.settings.allow_failure_simulation = True

    def gql(self, query, variables=None):
        return self.client.post("/graphql", json={"query": query, "variables": variables or {}}).json()

    def test_wallet_query_returns_balance_and_movements(self):
        body = self.gql("{ wallet { id balanceCents currency movements { kind amountCents balanceAfterCents orderId } } }")
        w = body["data"]["wallet"]
        self.assertEqual((w["id"], w["balanceCents"], w["currency"]), ("user-1", 465800, "USD"))
        self.assertEqual(w["movements"][0], {"kind": "DEBIT", "amountCents": -34200, "balanceAfterCents": 465800,
                                             "orderId": "o-1"})
        self.assertEqual(self.calls, [("wallet", "user-1")])               # el usuario sale de la sesión

    def test_wallet_requires_login(self):
        self.client.cookies.clear()
        body = self.gql("{ wallet { balanceCents } }")
        self.assertEqual(body["errors"][0]["extensions"]["code"], "UNAUTHENTICATED")
        self.assertEqual(self.calls, [])

    def test_register_opens_the_wallet(self):
        body = self.gql("mutation { register(input: {email: \"n@x.co\", password: \"Clave-Segura-2026\", fullName: \"N\"}) { id } }")
        self.assertEqual(body["data"]["register"]["id"], "user-1")
        self.assertIn(("open", "user-1"), self.calls)

    def test_register_survives_wallet_failure(self):
        type(self).open_fails = True                                       # se abrirá al usarla (perezosa)
        body = self.gql("mutation { register(input: {email: \"n@x.co\", password: \"Clave-Segura-2026\", fullName: \"N\"}) { id } }")
        self.assertNotIn("errors", body)

    def test_demo_balance_only_in_demo_mode(self):
        body = self.gql("mutation { demoSetWalletBalance(balanceCents: 10000) { balanceCents } }")
        self.assertEqual(body["data"]["demoSetWalletBalance"]["balanceCents"], 10000)
        self.schema.settings.allow_failure_simulation = False
        body = self.gql("mutation { demoSetWalletBalance(balanceCents: 10000) { balanceCents } }")
        self.assertEqual(body["errors"][0]["extensions"]["code"], "DEMO_DISABLED")
        self.assertEqual([c for c in self.calls if c[0] == "demo"], [("demo", "user-1", 10000)])


@unittest.skipUnless(HAS_DEPS, "requiere las dependencias del gateway (pip install -r requirements.txt)")
class InsufficientFundsMappingTests(unittest.TestCase):
    """El 409 de orders se convierte en un error GraphQL claro, con saldo y precio."""

    def test_409_becomes_insufficient_funds(self):
        import httpx
        from graphql import GraphQLError

        from app.clients import OrdersApi

        async def scenario():
            api = OrdersApi("http://orders", "tok")
            await api.http.aclose()
            api.http = httpx.AsyncClient(base_url="http://orders", transport=httpx.MockTransport(lambda r: httpx.Response(
                409, json={"detail": {"code": "insufficient_funds", "balance_cents": 10000, "required_cents": 34200}})))
            try:
                await api.create({})
            finally:
                await api.close()
        with self.assertRaises(GraphQLError) as ctx:
            asyncio.run(scenario())
        err = ctx.exception
        self.assertEqual(err.extensions["code"], "INSUFFICIENT_FUNDS")
        self.assertEqual((err.extensions["balanceCents"], err.extensions["requiredCents"]), (10000, 34200))
        self.assertIn("US$ 100.00", err.message)
        self.assertIn("US$ 342.00", err.message)


if __name__ == "__main__":
    unittest.main()
