"""Tests de la API de orders (POST/GET /orders) de punta a punta con la app real: lifespan, SAGA, runner,
idempotencia y anti-IDOR. Sin Docker: SQLite + participantes falsos en memoria (httpx.MockTransport).
Ejecutar desde services/orders:  python -m unittest discover -s tests -v
Requiere: pip install -r requirements.txt -r ../../requirements-test.txt (si faltan, se omite;
con REQUIRE_TEST_DEPS=1 falla en vez de omitirse)."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HAS_DEPS = all(importlib.util.find_spec(m) for m in ("fastapi", "sqlalchemy", "aiosqlite", "httpx"))
if not HAS_DEPS and os.getenv("REQUIRE_TEST_DEPS"):
    raise RuntimeError("REQUIRE_TEST_DEPS=1 pero faltan dependencias de test (ver requirements-test.txt)")

TOKEN = "test-internal-token"
HEADERS = {"X-Internal-Token": TOKEN}


class FakeParticipants:
    """Vuelos/Hoteles/Autos en memoria con el mismo contrato que los reales (idempotencia + lapidas)."""

    def __init__(self):
        self.reservations: dict[tuple[str, str], str] = {}     # (servicio, order_id) -> estado
        self.creates = 0
        self.failing_cancels: set[str] = set()                  # servicios cuya compensacion responde 503

    def __call__(self, request):
        import httpx
        service, path = request.url.host, request.url.path
        if path == "/reservations":
            body = json.loads(request.content)
            if request.headers.get("X-Simulate-Failure") == "true":
                return httpx.Response(503, json={"detail": "simulated_failure"})
            key = (service, body["order_id"])
            if self.reservations.get(key) == "CANCELLED":
                return httpx.Response(409, json={"detail": "reservation_cancelled"})
            created = key not in self.reservations
            self.reservations[key] = "CONFIRMED"
            self.creates += created
            return httpx.Response(201 if created else 200, json={"status": "CONFIRMED"})
        order_id = path.split("/")[2]                           # /reservations/{order_id}/cancel
        if service in self.failing_cancels:
            return httpx.Response(503, json={"detail": "down"})
        self.reservations[(service, order_id)] = "CANCELLED"
        return httpx.Response(200, json={"status": "CANCELLED"})


@unittest.skipUnless(HAS_DEPS, "requiere requirements.txt + requirements-test.txt")
class OrdersApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ.update(DATABASE_URL=f"sqlite+aiosqlite:///{os.path.join(cls.tmp.name, 'orders.db')}",
                          INTERNAL_TOKEN=TOKEN, ALLOW_FAILURE_SIMULATION="true")
        import httpx
        from fastapi.testclient import TestClient

        from app import main
        cls.main = main
        cls.client = TestClient(main.app)
        cls.client.__enter__()                                 # lifespan: create_all, recuperacion, runner
        cls.participants = FakeParticipants()
        actions = main.saga.actions
        actions.http = httpx.AsyncClient(transport=httpx.MockTransport(cls.participants))
        actions.backoff_s = 0

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.tmp.cleanup()

    def setUp(self):
        self.main.ALLOW_SIMULATION = True

    def create(self, user="u1", key=None, simulate=None, currency="USD", token=TOKEN, step_delay_ms=0):
        offer = lambda i, cents, cur=currency: {"id": f"off-{i}", "price_cents": cents, "currency": cur,  # noqa: E731
                                               "title": f"Oferta {i}", "detail": "d"}
        body = {"user_id": user, "contact_name": "Ana", "idempotency_key": key or uuid.uuid4().hex,
                "flight": offer("f", 20000), "hotel": offer("h", 30000), "car": offer("c", 10000, "USD"),
                "simulate_failure": simulate, "step_delay_ms": step_delay_ms}
        return self.client.post("/orders", json=body, headers={"X-Internal-Token": token} if token else {})

    def test_happy_path_confirms_and_reserves_everything(self):
        r = self.create()
        self.assertEqual(r.status_code, 201)
        order = r.json()
        self.assertEqual(order["status"], "CONFIRMED")
        self.assertEqual(order["total_cents"], 60000)
        self.assertTrue(order["invoice_number"].startswith("WS-"))
        self.assertEqual([(s["name"], s["phase"], s["status"]) for s in order["steps"]],
                         [(n, "EXECUTE", "COMPLETED") for n in ("PAYMENT", "FLIGHT", "HOTEL", "CAR", "INVOICE")])
        for service in ("flights", "hotels", "cars"):
            self.assertEqual(self.participants.reservations[(service, order["id"])], "CONFIRMED")

    def test_same_idempotency_key_returns_same_order_without_duplicates(self):
        key = uuid.uuid4().hex
        first = self.create(key=key)
        creates = self.participants.creates
        again = self.create(key=key)                             # doble clic / reintento del cliente
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertEqual(again.json()["id"], first.json()["id"])
        self.assertEqual(self.participants.creates, creates)    # no se reservo nada otra vez

    def test_idempotency_key_is_scoped_per_user(self):
        key = uuid.uuid4().hex
        self.assertNotEqual(self.create(user="u1", key=key).json()["id"],
                            self.create(user="u2", key=key).json()["id"])

    def test_failure_compensates_in_reverse_order(self):
        order = self.create(simulate="CAR").json()
        self.assertEqual(order["status"], "CANCELLED")
        self.assertIn("CAR", order["failure_reason"])
        self.assertIsNone(order["invoice_number"])
        undo = [s["name"] for s in order["steps"] if s["phase"] == "COMPENSATE"]
        self.assertEqual(undo, ["CAR", "HOTEL", "FLIGHT", "PAYMENT"])
        for service in ("flights", "hotels", "cars"):
            self.assertEqual(self.participants.reservations[(service, order["id"])], "CANCELLED")

    def test_simulation_ignored_when_disabled(self):
        self.main.ALLOW_SIMULATION = False
        self.assertEqual(self.create(simulate="CAR").json()["status"], "CONFIRMED")

    def test_orders_are_isolated_per_user(self):
        """Anti-IDOR: con el id de una orden ajena se responde 404, igual que si no existiera."""
        order_id = self.create(user="owner").json()["id"]
        own = self.client.get(f"/orders/{order_id}", params={"user_id": "owner"}, headers=HEADERS)
        other = self.client.get(f"/orders/{order_id}", params={"user_id": "intruder"}, headers=HEADERS)
        missing = self.client.get(f"/orders/{uuid.uuid4()}", params={"user_id": "owner"}, headers=HEADERS)
        self.assertEqual(own.status_code, 200)
        self.assertEqual(other.status_code, 404)
        self.assertEqual(other.json(), missing.json())
        listed = self.client.get("/orders", params={"user_id": "intruder"}, headers=HEADERS).json()
        self.assertNotIn(order_id, [o["id"] for o in listed])

    def wait_final(self, order_id, timeout=5.0):
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            order = self.client.get(f"/orders/{order_id}", params={"user_id": "u1"}, headers=HEADERS).json()
            if order["status"] in ("CONFIRMED", "CANCELLED", "COMPENSATION_FAILED"):
                return order
            time.sleep(0.05)
        self.fail(f"la orden {order_id} no llego a un estado final")

    def test_presentation_mode_returns_immediately_and_progresses(self):
        r = self.create(step_delay_ms=60)
        self.assertEqual(r.status_code, 202)                       # sin esperar: el cliente muestra cada paso
        self.assertEqual(r.json()["status"], "PENDING")
        order = self.wait_final(r.json()["id"])
        self.assertEqual(order["status"], "CONFIRMED")
        self.assertEqual(len(order["steps"]), 5)

    def test_presentation_mode_ignored_outside_demo_mode(self):
        self.main.ALLOW_SIMULATION = False
        r = self.create(step_delay_ms=3000)
        self.assertEqual((r.status_code, r.json()["status"]), (201, "CONFIRMED"))   # sin pausas ni 202

    def test_presentation_delay_is_capped(self):
        self.assertEqual(self.create(step_delay_ms=60_000).status_code, 422)

    def test_recovery_leaves_other_replicas_live_sagas_alone(self):
        """Otra replica ejecuta una SAGA (PAYMENT hecho, FLIGHT en curso). Al arrancar ESTA replica, la
        recuperacion no debe compensarla; solo si deja de avanzar (la otra replica murio)."""
        from datetime import timedelta

        from sqlalchemy import update

        from app.models import Order, SagaStep, now

        main = self.main

        async def other_replica_saga(age_s):
            async with main.Session() as s:
                order = Order(user_id="u1", contact_name="Ana", idempotency_key=uuid.uuid4().hex, status="PENDING",
                              total_cents=1, currency="USD", items={})
                s.add(order)
                await s.flush()
                s.add_all([SagaStep(order_id=order.id, name="PAYMENT", phase="EXECUTE", status="COMPLETED"),
                           SagaStep(order_id=order.id, name="FLIGHT", phase="EXECUTE", status="STARTED")])
                await s.commit()
                await s.execute(update(Order).where(Order.id == order.id)
                                .values(updated_at=now() - timedelta(seconds=age_s)))
                await s.commit()
                return order.id

        async def status(order_id):
            async with main.Session() as s:
                return (await s.get(Order, order_id)).status

        portal = self.client.portal
        live = portal.call(other_replica_saga, 5)                  # avanzo hace 5 s: viva en otra replica
        dead = portal.call(other_replica_saga, 120)                # 2 min sin avanzar: su replica murio
        portal.call(main.recover_once, main.STUCK_AFTER_S)         # lo mismo que hace el arranque
        self.assertEqual(portal.call(status, live), "PENDING")     # intacta
        self.assertNotIn(("flights", live), self.participants.reservations)
        self.assertEqual(portal.call(status, dead), "CANCELLED")   # compensada hacia atras
        self.assertEqual(self.participants.reservations[("flights", dead)], "CANCELLED")   # lapida

    def test_saga_steps_only_when_requested(self):
        order_id = self.create(user="steps-user").json()["id"]
        q = {"user_id": "steps-user"}
        with_steps = self.client.get(f"/orders/{order_id}", params=q, headers=HEADERS).json()
        without = self.client.get(f"/orders/{order_id}", params={**q, "include_steps": "false"}, headers=HEADERS).json()
        listed = self.client.get("/orders", params={**q, "include_steps": "false"}, headers=HEADERS).json()
        self.assertEqual(len(with_steps["steps"]), 5)
        self.assertEqual((without["steps"], listed[0]["steps"]), ([], []))
        self.assertEqual(without["invoice_number"], with_steps["invoice_number"])   # lo demas, intacto

    def test_listing_orders_uses_constant_number_of_queries(self):
        """Sin N+1: listar 1 o 6 ordenes cuesta las mismas consultas SQL."""
        from sqlalchemy import event

        for _ in range(6):
            self.create(user="many")
        self.create(user="one")
        statements = []

        def count(*args):
            statements.append(1)
        event.listen(self.main.db_engine.sync_engine, "before_cursor_execute", count)
        try:
            costs = {}
            for user in ("one", "many"):
                statements.clear()
                r = self.client.get("/orders", params={"user_id": user}, headers=HEADERS)
                costs[user] = (len(r.json()), len(statements))
        finally:
            event.remove(self.main.db_engine.sync_engine, "before_cursor_execute", count)
        self.assertEqual(costs["one"][0], 1)
        self.assertEqual(costs["many"][0], 6)
        self.assertEqual(costs["one"][1], costs["many"][1])      # mismas consultas para 1 que para 6 ordenes
        self.assertLessEqual(costs["many"][1], 3)                # ordenes + facturas + pasos

    def test_recovery_keeps_the_original_failure_reason(self):
        """B5, de punta a punta: falla el auto, su compensacion tambien (servicio caido) -> COMPENSATION_FAILED;
        el servicio vuelve y la recuperacion termina en CANCELLED SIN pisar la causa real del fallo."""
        from datetime import timedelta

        from sqlalchemy import update

        from app.models import Order, now

        self.participants.failing_cancels.add("cars")
        try:
            order = self.create(simulate="CAR").json()
        finally:
            self.participants.failing_cancels.clear()
        self.assertEqual(order["status"], "COMPENSATION_FAILED")
        original = order["failure_reason"]
        self.assertTrue(original.startswith("CAR:"))

        async def make_stale():                                 # sin avanzar desde hace 2 min
            async with self.main.Session() as s:
                await s.execute(update(Order).where(Order.id == order["id"])
                                .values(updated_at=now() - timedelta(seconds=120)))
                await s.commit()
        self.client.portal.call(make_stale)
        self.client.portal.call(self.main.recover_once, self.main.STUCK_AFTER_S)

        after = self.client.get(f"/orders/{order['id']}", params={"user_id": "u1"}, headers=HEADERS).json()
        self.assertEqual(after["status"], "CANCELLED")
        self.assertEqual(after["failure_reason"], original)
        self.assertEqual(self.participants.reservations[("cars", order["id"])], "CANCELLED")

    def test_internal_token_and_validation(self):
        self.assertEqual(self.create(token=None).status_code, 401)
        self.assertEqual(self.create(token="wrong").status_code, 401)
        self.assertEqual(self.create(currency="EUR").status_code, 422)   # monedas mezcladas (vuelo/hotel vs auto)


if __name__ == "__main__":
    unittest.main()
