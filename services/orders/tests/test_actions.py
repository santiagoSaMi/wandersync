"""Tests de OrderActions: reintentos HTTP hacia los participantes y pasos locales (pago, factura).
Sin red ni Docker: httpx.MockTransport + SQLite. Ejecutar desde services/orders:
    python -m unittest discover -s tests -v
Requiere: pip install -r requirements.txt -r ../../requirements-test.txt (si faltan, se omite;
con REQUIRE_TEST_DEPS=1 falla en vez de omitirse)."""
import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HAS_DEPS = all(importlib.util.find_spec(m) for m in ("sqlalchemy", "aiosqlite", "httpx"))
if not HAS_DEPS and os.getenv("REQUIRE_TEST_DEPS"):
    raise RuntimeError("REQUIRE_TEST_DEPS=1 pero faltan dependencias de test (ver requirements-test.txt)")

URLS = {"FLIGHT": "http://flights", "HOTEL": "http://hotels", "CAR": "http://cars"}
ITEM = {"id": "offer-1", "price_cents": 10000, "currency": "USD", "title": "t", "detail": "d"}


class Scripted:
    """Participante falso: devuelve, en orden, los codigos de `script` ("net" = error de red)."""

    def __init__(self, *script):
        self.script, self.requests = list(script), []

    def __call__(self, request):
        import httpx
        self.requests.append(request)
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if step == "net":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(step, json={"status": "CONFIRMED" if step < 300 else "ERR"})


@unittest.skipUnless(HAS_DEPS, "requiere requirements.txt + requirements-test.txt")
class OrderActionsTests(unittest.TestCase):
    def run_with(self, scenario, handler=None, allow_sim=True, simulate_failure=None):
        """Crea una base SQLite con una orden, construye OrderActions y ejecuta `scenario(actions, order_id, sf)`."""
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
                async with sf() as s:
                    order = Order(user_id="u1", contact_name="Ana", idempotency_key="key-12345678", status="PENDING",
                                  total_cents=30000, currency="USD", simulate_failure=simulate_failure,
                                  items={"FLIGHT": ITEM, "HOTEL": ITEM, "CAR": ITEM})
                    s.add(order)
                    await s.commit()
                http = httpx.AsyncClient(transport=httpx.MockTransport(handler or Scripted(201)))
                actions = OrderActions(sf, http, URLS, "tok", allow_simulation=allow_sim, backoff_s=0)
                try:
                    return await scenario(actions, order.id, sf)
                finally:
                    await http.aclose()
                    await engine.dispose()
        return asyncio.run(main())

    # ------------------------------------------------------------------ reintentos: solo fallos transitorios
    def test_retries_5xx_until_success(self):
        participant = Scripted(503, 503, 201)
        attempts = self.run_with(lambda a, oid, sf: a.execute("FLIGHT", oid), participant)
        self.assertEqual(attempts, 3)
        self.assertEqual(len(participant.requests), 3)

    def test_retries_429(self):
        participant = Scripted(429, 201)
        self.assertEqual(self.run_with(lambda a, oid, sf: a.execute("HOTEL", oid), participant), 2)

    def test_client_errors_are_final_without_retry(self):
        from app.saga import StepError
        for code in (400, 409):
            with self.subTest(code=code):
                participant = Scripted(code)
                with self.assertRaises(StepError) as ctx:
                    self.run_with(lambda a, oid, sf: a.execute("CAR", oid), participant)
                self.assertEqual(ctx.exception.attempts, 1)
                self.assertEqual(len(participant.requests), 1)
                self.assertIn(str(code), str(ctx.exception))

    def test_network_errors_exhaust_retries(self):
        from app.saga import StepError
        participant = Scripted("net")
        with self.assertRaises(StepError) as ctx:
            self.run_with(lambda a, oid, sf: a.execute("FLIGHT", oid), participant)
        self.assertEqual(ctx.exception.attempts, 3)
        self.assertEqual(len(participant.requests), 3)
        self.assertIn("red", str(ctx.exception))

    def test_compensation_retries_and_requires_200(self):
        from app.saga import StepError
        participant = Scripted(503, 200)
        self.assertEqual(self.run_with(lambda a, oid, sf: a.compensate("HOTEL", oid), participant), 2)
        self.assertTrue(str(participant.requests[0].url).endswith("/cancel"))
        with self.assertRaises(StepError):
            self.run_with(lambda a, oid, sf: a.compensate("HOTEL", oid), Scripted(404))

    # ------------------------------------------------------------------ peticion al participante
    def test_reservation_request_carries_snapshot_and_token(self):
        import json
        participant = Scripted(201)
        oid = self.run_with(lambda a, oid, sf: _return(a.execute("FLIGHT", oid), oid), participant)
        req = participant.requests[0]
        body = json.loads(req.content)
        self.assertEqual(str(req.url), "http://flights/reservations")
        self.assertEqual(req.headers["X-Internal-Token"], "tok")
        self.assertEqual((body["order_id"], body["offer_id"], body["amount_cents"]), (oid, "offer-1", 10000))

    def test_failure_simulation_header_only_for_target_step_and_when_enabled(self):
        async def both(a, oid, sf):
            await a.execute("FLIGHT", oid)
            await a.execute("CAR", oid)
        for allow, expected in ((True, [None, "true"]), (False, [None, None])):
            with self.subTest(allow=allow):
                participant = Scripted(201)
                self.run_with(both, participant, allow_sim=allow, simulate_failure="CAR")
                self.assertEqual([r.headers.get("X-Simulate-Failure") for r in participant.requests], expected)

    # ------------------------------------------------------------------ pasos locales idempotentes
    def test_payment_charge_is_idempotent_and_refund_is_final(self):
        from sqlalchemy import select

        from app.models import Payment
        from app.saga import StepError

        async def scenario(a, oid, sf):
            await a.execute("PAYMENT", oid)
            await a.execute("PAYMENT", oid)                 # reintento: no cobra dos veces
            async with sf() as s:
                rows = (await s.execute(select(Payment))).scalars().all()
            charged = [(p.status, p.amount_cents) for p in rows]
            await a.compensate("PAYMENT", oid)
            await a.compensate("PAYMENT", oid)              # reembolso idempotente
            async with sf() as s:
                refunded = [p.status for p in (await s.execute(select(Payment))).scalars().all()]
            try:
                await a.execute("PAYMENT", oid)             # no se re-cobra una orden reembolsada
                recharge = "allowed"
            except StepError:
                recharge = "rejected"
            return charged, refunded, recharge
        self.assertEqual(self.run_with(scenario), ([("CAPTURED", 30000)], ["REFUNDED"], "rejected"))

    def test_simulated_payment_failure(self):
        from app.saga import StepError
        with self.assertRaises(StepError):
            self.run_with(lambda a, oid, sf: a.execute("PAYMENT", oid), simulate_failure="PAYMENT")

    def test_invoice_issue_is_idempotent_and_void(self):
        from sqlalchemy import select

        from app.models import Invoice

        async def scenario(a, oid, sf):
            await a.execute("INVOICE", oid)
            await a.execute("INVOICE", oid)
            await a.compensate("INVOICE", oid)
            async with sf() as s:
                return [(i.status, i.number.startswith("WS-")) for i in (await s.execute(select(Invoice))).scalars()]
        self.assertEqual(self.run_with(scenario), [("VOID", True)])


async def _return(coro, value):
    await coro
    return value


if __name__ == "__main__":
    unittest.main()
