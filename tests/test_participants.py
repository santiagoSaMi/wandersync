"""Contrato de los participantes de la SAGA (flights, hotels, cars), contra sus apps FastAPI reales.
El MISMO suite corre contra los tres servicios: tambien detecta que las copias se desincronicen.
Sin Docker: cada servicio usa su propia base SQLite temporal.

Ejecutar desde la raiz:  python -m unittest discover -s tests -v
Requiere: pip install -r services/flights/requirements.txt -r requirements-test.txt (si faltan, se omite;
con REQUIRE_TEST_DEPS=1 falla en vez de omitirse)."""
import importlib.util
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HAS_DEPS = all(importlib.util.find_spec(m) for m in ("fastapi", "sqlalchemy", "aiosqlite", "httpx"))
if not HAS_DEPS and os.getenv("REQUIRE_TEST_DEPS"):
    raise RuntimeError("REQUIRE_TEST_DEPS=1 pero faltan dependencias de test (ver requirements-test.txt)")

TOKEN = "test-internal-token"


def load_service(name: str, db_path: str):
    """Importa services/<name>/app/main.py como modulo independiente (los tres se llaman `app`)."""
    os.environ.update(DATABASE_URL=f"sqlite+aiosqlite:///{db_path}", INTERNAL_TOKEN=TOKEN,
                      ALLOW_FAILURE_SIMULATION="true", FAIL_RATE="0")
    module_name = f"participant_{name}"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "services" / name / "app" / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module           # SQLAlchemy resuelve las anotaciones Mapped[...] por el modulo
    spec.loader.exec_module(module)
    return module


class ParticipantContract:
    SERVICE = ""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        cls.tmp = tempfile.TemporaryDirectory()
        cls.svc = load_service(cls.SERVICE, os.path.join(cls.tmp.name, f"{cls.SERVICE}.db"))
        cls.client = TestClient(cls.svc.app)
        cls.client.__enter__()                  # lifespan: create_all

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.tmp.cleanup()

    def setUp(self):
        self.svc.ALLOW_SIMULATION = True

    def reserve(self, order_id, headers=None, token=TOKEN, **overrides):
        body = {"order_id": order_id, "offer_id": "offer-1", "customer_name": "Ana", "amount_cents": 12345,
                "currency": "usd", "details": {"title": "x"}, **overrides}
        hdrs = {"X-Internal-Token": token, **(headers or {})} if token is not None else dict(headers or {})
        return self.client.post("/reservations", json=body, headers=hdrs)

    def cancel(self, order_id):
        return self.client.post(f"/reservations/{order_id}/cancel", headers={"X-Internal-Token": TOKEN})

    def get(self, order_id):
        return self.client.get(f"/reservations/{order_id}", headers={"X-Internal-Token": TOKEN})

    @staticmethod
    def oid():
        return str(uuid.uuid4())

    # ------------------------------------------------------------------ creacion idempotente
    def test_create_then_repeat_is_idempotent(self):
        oid = self.oid()
        first = self.reserve(oid)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()["status"], "CONFIRMED")
        self.assertEqual(first.json()["currency"], "USD")
        again = self.reserve(oid, amount_cents=999)             # reintento del orquestador
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["id"], first.json()["id"])
        self.assertEqual(again.json()["amount_cents"], 12345)   # no se duplico ni se piso
        self.assertEqual(self.get(oid).json()["id"], first.json()["id"])

    # ------------------------------------------------------------------ compensacion
    def test_cancel_is_idempotent(self):
        oid = self.oid()
        self.reserve(oid)
        for _ in range(2):
            r = self.cancel(oid)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["status"], "CANCELLED")

    def test_cancelled_reservation_cannot_be_revived(self):
        oid = self.oid()
        self.reserve(oid)
        self.cancel(oid)
        self.assertEqual(self.reserve(oid).status_code, 409)

    def test_tombstone_rejects_late_reservation(self):
        """La reserva se 'perdio' en la red y se compenso antes de llegar: al llegar tarde, 409 (no huerfana)."""
        oid = self.oid()
        tomb = self.cancel(oid)
        self.assertEqual(tomb.status_code, 200)
        self.assertEqual(tomb.json()["status"], "CANCELLED")
        self.assertEqual(self.reserve(oid).status_code, 409)
        self.assertEqual(self.get(oid).json()["status"], "CANCELLED")

    # ------------------------------------------------------------------ fallos simulados (demo)
    def test_simulated_failure_when_enabled(self):
        oid = self.oid()
        self.assertEqual(self.reserve(oid, headers={"X-Simulate-Failure": "true"}).status_code, 503)
        self.assertEqual(self.get(oid).status_code, 404)        # no quedo nada creado

    def test_simulation_header_ignored_when_disabled(self):
        self.svc.ALLOW_SIMULATION = False
        self.assertEqual(self.reserve(self.oid(), headers={"X-Simulate-Failure": "true"}).status_code, 201)

    # ------------------------------------------------------------------ seguridad y validacion
    def test_internal_token_required(self):
        self.assertEqual(self.reserve(self.oid(), token=None).status_code, 401)
        self.assertEqual(self.reserve(self.oid(), token="wrong").status_code, 401)
        r = self.client.post(f"/reservations/{self.oid()}/cancel")
        self.assertEqual(r.status_code, 401)

    def test_invalid_payload_rejected(self):
        self.assertEqual(self.reserve(self.oid(), amount_cents=-1).status_code, 422)
        self.assertEqual(self.reserve(self.oid(), currency="DOLLARS").status_code, 422)

    def test_unknown_reservation_is_404(self):
        self.assertEqual(self.get(self.oid()).status_code, 404)


def _suite(service: str):
    return unittest.skipUnless(HAS_DEPS, "requiere requirements.txt + requirements-test.txt")(
        type(f"{service.capitalize()}ContractTests", (ParticipantContract, unittest.TestCase), {"SERVICE": service}))


FlightsContractTests = _suite("flights")
HotelsContractTests = _suite("hotels")
CarsContractTests = _suite("cars")

if __name__ == "__main__":
    unittest.main()
