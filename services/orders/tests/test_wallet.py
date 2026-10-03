"""Tests de la billetera (US$ 5.000 por usuario) y de cómo la mueve la SAGA: el PAGO debita, la compensación
reembolsa, nunca dos veces, y sin saldo suficiente no se cobra nada.
Sin Docker: SQLite. Ejecutar desde services/orders:  python -m unittest discover -s tests -v
Requiere: pip install -r requirements.txt -r ../../requirements-test.txt (si faltan, se omite;
con REQUIRE_TEST_DEPS=1 falla en vez de omitirse)."""
import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HAS_DEPS = all(importlib.util.find_spec(m) for m in ("sqlalchemy", "aiosqlite", "httpx", "fastapi"))
if not HAS_DEPS and os.getenv("REQUIRE_TEST_DEPS"):
    raise RuntimeError("REQUIRE_TEST_DEPS=1 pero faltan dependencias de test (ver requirements-test.txt)")

ITEM = {"id": "offer-1", "price_cents": 10000, "currency": "USD", "title": "t", "detail": "d"}


@unittest.skipUnless(HAS_DEPS, "requiere requirements.txt + requirements-test.txt")
class WalletStepTests(unittest.TestCase):
    """El PAGO y su compensación contra una base SQLite real (OrderActions sin red)."""

    def run_with(self, scenario, total_cents=30000):
        import httpx
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.actions import OrderActions
        from app.models import Base, Order

        async def main():
            with tempfile.TemporaryDirectory() as tmp:
                engine = create_async_engine(f"sqlite+aiosqlite:///{os.path.join(tmp, 'orders.db')}")
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                sf = async_sessionmaker(engine, expire_on_commit=False)

                async def new_order(user="ana", total=total_cents):
                    async with sf() as s:
                        order = Order(user_id=user, contact_name="Ana", idempotency_key=uuid.uuid4().hex,
                                      status="PENDING", total_cents=total, currency="USD",
                                      items={"FLIGHT": ITEM, "HOTEL": ITEM, "CAR": ITEM})
                        s.add(order)
                        await s.commit()
                        return order.id
                http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(201)))
                actions = OrderActions(sf, http, {}, "tok", allow_simulation=True, backoff_s=0)
                try:
                    return await scenario(actions, sf, new_order)
                finally:
                    await http.aclose()
                    await engine.dispose()
        return asyncio.run(main())

    @staticmethod
    async def ledger(sf, user="ana"):
        from app import wallet as wallets
        async with sf() as s:
            w = await wallets.open_wallet(s, user)
            await s.commit()
            moves = [(m.kind, m.amount_cents, m.balance_after_cents) for m in reversed(await wallets.movements(s, user))]
            return w.balance_cents, moves

    def test_payment_debits_the_wallet(self):
        async def scenario(actions, sf, new_order):
            await actions.execute("PAYMENT", await new_order())
            return await self.ledger(sf)
        balance, moves = self.run_with(scenario)
        self.assertEqual(balance, 470000)                                  # 5.000 - 300
        self.assertEqual(moves, [("OPENING", 500000, 500000), ("DEBIT", -30000, 470000)])

    def test_compensation_refunds_the_wallet(self):
        async def scenario(actions, sf, new_order):
            oid = await new_order()
            await actions.execute("PAYMENT", oid)
            await actions.compensate("PAYMENT", oid)
            return await self.ledger(sf)
        balance, moves = self.run_with(scenario)
        self.assertEqual(balance, 500000)
        self.assertEqual([m[0] for m in moves], ["OPENING", "DEBIT", "REFUND"])
        self.assertEqual(moves[-1], ("REFUND", 30000, 500000))

    def test_debit_and_refund_happen_at_most_once(self):
        """Reintentos y recuperaciones repiten pasos: el dinero no se mueve dos veces."""
        async def scenario(actions, sf, new_order):
            oid = await new_order()
            for _ in range(3):
                await actions.execute("PAYMENT", oid)
            for _ in range(3):
                await actions.compensate("PAYMENT", oid)
            return await self.ledger(sf)
        balance, moves = self.run_with(scenario)
        self.assertEqual(balance, 500000)
        self.assertEqual([m[0] for m in moves], ["OPENING", "DEBIT", "REFUND"])

    def test_insufficient_funds_fails_the_step_without_charging(self):
        from app.saga import StepError

        async def scenario(actions, sf, new_order):
            oid = await new_order(total=600000)                            # US$ 6.000 > 5.000
            try:
                await actions.execute("PAYMENT", oid)
                outcome = "charged"
            except StepError as exc:
                outcome = (str(exc), exc.attempts)
            await actions.compensate("PAYMENT", oid)                       # la SAGA compensa: no hay nada que devolver
            return outcome, await self.ledger(sf)
        (message, attempts), (balance, moves) = self.run_with(scenario)
        self.assertIn("Saldo insuficiente", message)
        self.assertIn("US$ 5,000.00", message)
        self.assertEqual(attempts, 1)                                      # definitivo: sin reintentos
        self.assertEqual(balance, 500000)
        self.assertEqual([m[0] for m in moves], ["OPENING"])

    def test_cannot_spend_the_same_money_twice(self):
        """Dos órdenes de US$ 3.000 con US$ 5.000: solo la primera se cobra."""
        from app.saga import StepError

        async def scenario(actions, sf, new_order):
            first, second = await new_order(total=300000), await new_order(total=300000)
            await actions.execute("PAYMENT", first)
            with self.assertRaises(StepError):
                await actions.execute("PAYMENT", second)
            return await self.ledger(sf)
        balance, moves = self.run_with(scenario)
        self.assertEqual(balance, 200000)
        self.assertEqual([m[0] for m in moves], ["OPENING", "DEBIT"])

    def test_wallets_are_per_user(self):
        async def scenario(actions, sf, new_order):
            await actions.execute("PAYMENT", await new_order(user="ana"))
            return (await self.ledger(sf, "ana"))[0], (await self.ledger(sf, "beto"))[0]
        self.assertEqual(self.run_with(scenario), (470000, 500000))


@unittest.skipUnless(HAS_DEPS, "requiere requirements.txt + requirements-test.txt")
class WalletApiTests(unittest.TestCase):
    """La API de orders con la SAGA completa: lo que verá la presentación."""

    @classmethod
    def setUpClass(cls):
        from orders_app import shared_app
        cls.main, cls.client, _ = shared_app()

    H = {"X-Internal-Token": "test-internal-token"}

    def setUp(self):
        self.main.ALLOW_SIMULATION = True
        self.user = f"user-{uuid.uuid4().hex[:8]}"

    def wallet(self):
        return self.client.get(f"/wallets/{self.user}", headers=self.H).json()

    def book(self, cents=(20000, 30000, 10000), simulate=None):
        offer = lambda i, c: {"id": f"off-{i}", "price_cents": c, "currency": "USD", "title": i, "detail": ""}  # noqa: E731
        return self.client.post("/orders", headers=self.H, json={
            "user_id": self.user, "contact_name": "Ana", "idempotency_key": uuid.uuid4().hex,
            "flight": offer("f", cents[0]), "hotel": offer("h", cents[1]), "car": offer("c", cents[2]),
            "simulate_failure": simulate})

    def test_new_user_gets_5000(self):
        w = self.wallet()
        self.assertEqual((w["balance_cents"], w["currency"]), (500000, "USD"))
        self.assertEqual([m["kind"] for m in w["movements"]], ["OPENING"])

    def test_confirmed_booking_spends_the_total(self):
        self.assertEqual(self.book().json()["status"], "CONFIRMED")
        w = self.wallet()
        self.assertEqual(w["balance_cents"], 440000)                       # 5.000 - 600
        self.assertEqual(w["movements"][0]["kind"], "DEBIT")               # más reciente primero

    def test_failed_booking_returns_the_money(self):
        """Lo que se demuestra: el PAGO debita y, al fallar el auto, la compensación lo devuelve."""
        order = self.book(simulate="CAR").json()
        self.assertEqual(order["status"], "CANCELLED")
        w = self.wallet()
        self.assertEqual(w["balance_cents"], 500000)
        self.assertEqual([(m["kind"], m["amount_cents"], m["order_id"]) for m in w["movements"][:2]],
                         [("REFUND", 60000, order["id"]), ("DEBIT", -60000, order["id"])])

    def test_insufficient_funds_rejected_before_creating_the_order(self):
        r = self.book(cents=(300000, 200000, 100000))                      # US$ 6.000
        self.assertEqual(r.status_code, 409)
        detail = r.json()["detail"]
        self.assertEqual((detail["code"], detail["balance_cents"], detail["required_cents"]),
                         ("insufficient_funds", 500000, 600000))
        orders = self.client.get("/orders", params={"user_id": self.user}, headers=self.H).json()
        self.assertEqual(orders, [])                                       # no se creó ninguna orden
        self.assertEqual(self.wallet()["balance_cents"], 500000)

    def test_open_wallet_is_idempotent(self):
        for _ in range(3):
            self.client.post(f"/wallets/{self.user}", headers=self.H)
        self.assertEqual([m["kind"] for m in self.wallet()["movements"]], ["OPENING"])

    def test_demo_balance_only_in_demo_mode(self):
        r = self.client.post(f"/wallets/{self.user}/demo-balance", headers=self.H, json={"balance_cents": 10000})
        self.assertEqual(r.json()["balance_cents"], 10000)
        self.assertEqual(r.json()["movements"][0]["kind"], "ADJUSTMENT")
        self.assertEqual(self.book().status_code, 409)                     # ahora no alcanza para US$ 600
        self.main.ALLOW_SIMULATION = False
        r = self.client.post(f"/wallets/{self.user}/demo-balance", headers=self.H, json={"balance_cents": 999})
        self.assertEqual(r.status_code, 403)

    def test_wallet_requires_internal_token(self):
        self.assertEqual(self.client.get(f"/wallets/{self.user}").status_code, 401)


if __name__ == "__main__":
    unittest.main()
