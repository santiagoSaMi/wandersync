"""App de orders compartida por los tests de API (una sola instancia por proceso).

app.main lee DATABASE_URL al importarse: si cada clase de test levantara "su" app con su propia base temporal,
la segunda apuntaría a la base ya borrada de la primera. Por eso se levanta UNA vez y se limpia al salir.
"""
import atexit
import json
import os
import tempfile

TOKEN = "test-internal-token"
_shared = {}


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


def shared_app():
    """(main, client, participants): la app de orders con lifespan activo, SQLite temporal y participantes falsos."""
    if not _shared:
        import httpx
        from fastapi.testclient import TestClient

        tmp = tempfile.TemporaryDirectory()
        os.environ.update(DATABASE_URL=f"sqlite+aiosqlite:///{os.path.join(tmp.name, 'orders.db')}",
                          INTERNAL_TOKEN=TOKEN, ALLOW_FAILURE_SIMULATION="true")
        from app import main
        client = TestClient(main.app)
        client.__enter__()                                     # lifespan: create_all, recuperación, runner
        participants = FakeParticipants()
        main.saga.actions.http = httpx.AsyncClient(transport=httpx.MockTransport(participants))
        main.saga.actions.backoff_s = 0

        def cleanup():
            client.__exit__(None, None, None)
            tmp.cleanup()
        atexit.register(cleanup)
        _shared.update(main=main, client=client, participants=participants)
    return _shared["main"], _shared["client"], _shared["participants"]
