"""Tests del servicio auth contra la app FastAPI real, sin Docker: Postgres -> SQLite, Redis -> fakeredis.
Ejecutar desde services/auth:  python -m unittest discover -s tests -v
Requiere: pip install -r requirements.txt -r ../../requirements-test.txt (si faltan, se omite;
con REQUIRE_TEST_DEPS=1 falla en vez de omitirse)."""
import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HAS_DEPS = all(importlib.util.find_spec(m) for m in
               ("fastapi", "sqlalchemy", "aiosqlite", "fakeredis", "argon2", "email_validator"))
if not HAS_DEPS and os.getenv("REQUIRE_TEST_DEPS"):
    raise RuntimeError("REQUIRE_TEST_DEPS=1 pero faltan dependencias de test (ver requirements-test.txt)")

TOKEN = "test-internal-token"
PASSWORD = "Viaje-Seguro-2026"


@unittest.skipUnless(HAS_DEPS, "requiere requirements.txt + requirements-test.txt")
class AuthServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "auth.db")
        os.environ.update(DATABASE_URL=f"sqlite+aiosqlite:///{cls.db_path}", REDIS_URL="redis://unused:6379/0",
                          INTERNAL_TOKEN=TOKEN)
        import fakeredis
        from fastapi.testclient import TestClient

        from app import main
        main.rds = fakeredis.FakeAsyncRedis(decode_responses=True)
        cls.main = main
        cls.client = TestClient(main.app)
        cls.client.__enter__()                      # ejecuta el lifespan (create_all, DUMMY_HASH)

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.tmp.cleanup()

    # ------------------------------------------------------------------ helpers
    def post(self, path, body=None, token=TOKEN):
        headers = {"X-Internal-Token": token} if token is not None else {}
        return self.client.post(path, json=body, headers=headers)

    def new_email(self, local="viajero"):
        return f"{local}-{uuid.uuid4().hex[:8]}@example.com"

    def register(self, email, password=PASSWORD, name="Ana Viajera"):
        return self.post("/register", {"email": email, "password": password, "full_name": name})

    def login(self, email, password=PASSWORD, current=None):
        return self.post("/login", {"email": email, "password": password, "current_session_id": current})

    def resolve(self, sid):
        return self.post("/sessions/resolve", {"session_id": sid}).json()

    # ------------------------------------------------------------------ autenticacion servicio-a-servicio
    def test_internal_token_required(self):
        self.assertEqual(self.post("/sessions/anonymous", token=None).status_code, 401)
        self.assertEqual(self.post("/sessions/anonymous", token="wrong").status_code, 401)

    # ------------------------------------------------------------------ registro y hashing
    def test_register_stores_argon2id_hash_never_plaintext(self):
        email = self.new_email()
        r = self.register(email)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["email"], email)
        with sqlite3.connect(self.db_path) as db:
            (stored,) = db.execute("select password_hash from users where email = ?", (email,)).fetchone()
        self.assertTrue(stored.startswith("$argon2id$"))
        self.assertNotIn(PASSWORD, stored)

    def test_register_duplicate_email_is_409_case_insensitive(self):
        email = self.new_email()
        self.assertEqual(self.register(email).status_code, 201)
        self.assertEqual(self.register(email.upper()).status_code, 409)

    def test_weak_passwords_rejected(self):
        email = f"carolina@{uuid.uuid4().hex[:8]}.example.com"   # parte local "carolina" (regla: no incluirla)
        for weak in ("soloLetrasSinNumeros", "123456789012", "aabbaabbaa11", "carolina-2026-xyz"):
            with self.subTest(password=weak):
                r = self.register(email, password=weak)
                self.assertEqual(r.status_code, 422)
                self.assertEqual(r.json()["detail"], "weak_password")
        self.assertEqual(self.register(email, password="corta1").status_code, 422)   # < 12 caracteres

    # ------------------------------------------------------------------ login y anti-enumeracion
    def test_wrong_password_and_unknown_user_are_indistinguishable(self):
        email = self.new_email()
        self.register(email)
        wrong = self.login(email, "Otra-Clave-2026")
        unknown = self.login(self.new_email(), PASSWORD)
        self.assertEqual((wrong.status_code, wrong.json()), (unknown.status_code, unknown.json()))
        self.assertEqual(wrong.status_code, 401)

    def test_account_lockout_after_max_failures(self):
        email = self.new_email()
        self.register(email)
        for _ in range(self.main.LOGIN_MAX_FAILURES):
            self.assertEqual(self.login(email, "Otra-Clave-2026").status_code, 401)
        locked = self.login(email, PASSWORD)                   # ni siquiera la correcta pasa
        self.assertEqual(locked.status_code, 429)
        self.assertGreater(int(locked.headers["Retry-After"]), 0)

    def test_successful_login_resets_failure_counter(self):
        email = self.new_email()
        self.register(email)
        for _ in range(self.main.LOGIN_MAX_FAILURES - 1):
            self.login(email, "Otra-Clave-2026")
        self.assertEqual(self.login(email).status_code, 200)
        for _ in range(self.main.LOGIN_MAX_FAILURES - 1):     # el contador volvio a cero
            self.login(email, "Otra-Clave-2026")
        self.assertEqual(self.login(email).status_code, 200)

    # ------------------------------------------------------------------ sesiones
    def test_session_fixation_login_rotates_and_kills_planted_session(self):
        email = self.new_email()
        self.register(email)
        planted = self.post("/sessions/anonymous").json()["session_id"]     # id "plantado" por un atacante
        self.assertEqual(self.resolve(planted), {"valid": True, "user": None})

        r = self.login(email, current=planted)
        self.assertEqual(r.status_code, 200)
        fresh = r.json()["session_id"]
        self.assertNotEqual(fresh, planted)
        self.assertFalse(self.resolve(planted)["valid"])                    # el id plantado murio
        self.assertEqual(self.resolve(fresh)["user"]["email"], email)        # el nuevo esta autenticado

    def test_logout_destroys_session(self):
        email = self.new_email()
        self.register(email)
        sid = self.login(email).json()["session_id"]
        self.assertTrue(self.resolve(sid)["valid"])
        self.post("/logout", {"session_id": sid})
        self.assertFalse(self.resolve(sid)["valid"])

    def test_absolute_session_lifetime(self):
        email = self.new_email()
        self.register(email)
        sid = self.login(email).json()["session_id"]
        original = self.main.SESSION_ABSOLUTE_TTL
        self.main.SESSION_ABSOLUTE_TTL = -1                    # cualquier sesion ya supero su vida maxima
        try:
            self.assertFalse(self.resolve(sid)["valid"])
        finally:
            self.main.SESSION_ABSOLUTE_TTL = original
        self.assertFalse(self.resolve(sid)["valid"])           # y fue borrada, no solo rechazada

    def test_unknown_session_is_invalid(self):
        self.assertEqual(self.resolve("no-existe"), {"valid": False, "user": None})


if __name__ == "__main__":
    unittest.main()
