"""Tests del espejo de la SAGA en Prefect, con una API de Prefect falsa en memoria (httpx.MockTransport).
Comprueba: un flow run por reserva, una task run por paso con flechas (despues_de / revierte), estados finales
y, sobre todo, que un Prefect caído o inestable NUNCA cambia el resultado de la SAGA.
Ejecutar desde services/orders:  python -m unittest discover -s tests -v
Requiere httpx (si falta, se omite; con REQUIRE_TEST_DEPS=1 falla en vez de omitirse)."""
import asyncio
import importlib.util
import itertools
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HAS_DEPS = importlib.util.find_spec("httpx") is not None
if not HAS_DEPS and os.getenv("REQUIRE_TEST_DEPS"):
    raise RuntimeError("REQUIRE_TEST_DEPS=1 pero faltan dependencias de test (ver requirements-test.txt)")

from app.saga import CANCELLED, CONFIRMED, SagaEngine  # noqa: E402
from test_saga import FakeActions, FakeRepo  # noqa: E402


class FakePrefect:
    """API REST mínima de Prefect: guarda flows, flow runs, task runs, estados y logs."""

    def __init__(self, flaky=0, down=False):
        self.flaky, self.down = flaky, down        # flaky: cuántas respuestas 503 dar antes de funcionar
        self.flow_runs, self.task_runs, self.logs, self.calls = {}, {}, [], 0
        self.ids = (f"id-{i}" for i in itertools.count())

    def __call__(self, request):
        import httpx
        self.calls += 1
        if self.down:
            raise httpx.ConnectError("prefect caido", request=request)
        if self.flaky:
            self.flaky -= 1
            return httpx.Response(503, json={"exception_message": "Service Unavailable"})
        path, body = request.url.path.removeprefix("/api"), json.loads(request.content or b"null")
        if path == "/flows/":
            return httpx.Response(201, json={"id": "flow-saga", "name": body["name"]})
        if path == "/flow_runs/":
            fid = next(self.ids)
            self.flow_runs[fid] = {**body, "states": [body["state"]]}
            return httpx.Response(201, json={"id": fid})
        if path == "/task_runs/":
            tid = next(self.ids)
            self.task_runs[tid] = {**body, "states": [body["state"]]}
            return httpx.Response(201, json={"id": tid})
        if path.endswith("/set_state"):
            kind, rid = path.split("/")[1], path.split("/")[2]
            (self.flow_runs if kind == "flow_runs" else self.task_runs)[rid]["states"].append(body["state"])
            return httpx.Response(201, json={"status": "ACCEPT"})
        if path == "/logs/":
            self.logs += body
            return httpx.Response(201)
        return httpx.Response(404)

    # vistas cómodas para los asserts
    def tasks(self):
        return list(self.task_runs.values())

    def name_of(self, tid):
        return self.task_runs[tid]["name"]

    def edges(self, key):
        return [(self.name_of(i["id"]), t["name"]) for t in self.tasks() for i in t["task_inputs"].get(key, [])]


@unittest.skipUnless(HAS_DEPS, "requiere httpx")
class PrefectMirrorTests(unittest.TestCase):
    def run_saga(self, prefect, fail_execute=(), fail_compensate=(), order_id="0rder-1234-abcd"):
        import httpx

        from app.prefect_mirror import MirroredRepo, PrefectMirror

        async def scenario():
            http = httpx.AsyncClient(transport=httpx.MockTransport(prefect))
            mirror = PrefectMirror("http://prefect/api", http, retry_delays=(0, 0, 0))
            mirror.start()
            mirror.order_started(order_id, {"total": "600.00 USD"})
            engine = SagaEngine(MirroredRepo(FakeRepo(), mirror),
                                FakeActions(fail_execute=fail_execute, fail_compensate=fail_compensate))
            final = await engine.run(order_id)
            await mirror.drain()
            await mirror.close()
            await http.aclose()
            return final
        return asyncio.run(scenario())

    def test_happy_path_is_one_run_with_a_chain_of_arrows(self):
        prefect = FakePrefect()
        self.assertEqual(self.run_saga(prefect), CONFIRMED)
        (run,) = prefect.flow_runs.values()
        self.assertEqual(run["name"], "SAGA reserva 0rder-12")
        self.assertEqual(run["parameters"]["total"], "600.00 USD")
        self.assertEqual([t["name"] for t in prefect.tasks()],
                         ["1 · Pago", "2 · Reserva de vuelo", "3 · Reserva de hotel", "4 · Reserva de auto", "5 · Factura"])
        self.assertEqual(prefect.edges("despues_de"), [("1 · Pago", "2 · Reserva de vuelo"),
                                                      ("2 · Reserva de vuelo", "3 · Reserva de hotel"),
                                                      ("3 · Reserva de hotel", "4 · Reserva de auto"),
                                                      ("4 · Reserva de auto", "5 · Factura")])
        self.assertTrue(all(t["states"][-1]["type"] == "COMPLETED" for t in prefect.tasks()))
        self.assertEqual(run["states"][-1], {"type": "COMPLETED", "name": "Confirmada", "message": None})

    def test_failure_shows_compensations_in_reverse_with_arrows_to_what_they_undo(self):
        prefect = FakePrefect()
        self.assertEqual(self.run_saga(prefect, fail_execute={"CAR"}), CANCELLED)
        names = [t["name"] for t in prefect.tasks()]
        self.assertEqual(names, ["1 · Pago", "2 · Reserva de vuelo", "3 · Reserva de hotel", "4 · Reserva de auto",
                                 "↩ Cancelar auto", "↩ Cancelar hotel", "↩ Cancelar vuelo", "↩ Reembolso del pago"])
        failed = [t for t in prefect.tasks() if t["states"][-1]["type"] == "FAILED"]
        self.assertEqual([t["name"] for t in failed], ["4 · Reserva de auto"])
        self.assertIn("CAR no disponible", failed[0]["states"][-1]["message"])
        self.assertIn(("4 · Reserva de auto", "↩ Cancelar auto"), prefect.edges("despues_de"))
        self.assertEqual(prefect.edges("revierte"), [("3 · Reserva de hotel", "↩ Cancelar hotel"),
                                                     ("2 · Reserva de vuelo", "↩ Cancelar vuelo"),
                                                     ("1 · Pago", "↩ Reembolso del pago")])
        (run,) = prefect.flow_runs.values()
        self.assertEqual([s["name"] for s in run["states"]], ["Ejecutando", "Compensando", "Revertida"])
        self.assertTrue(any("FALLÓ" in entry["message"] for entry in prefect.logs))

    def test_transient_503s_are_retried(self):
        prefect = FakePrefect(flaky=3)
        self.assertEqual(self.run_saga(prefect), CONFIRMED)
        self.assertEqual(len(prefect.tasks()), 5)

    def test_prefect_down_never_affects_the_saga(self):
        prefect = FakePrefect(down=True)
        self.assertEqual(self.run_saga(prefect), CONFIRMED)
        self.assertEqual(self.run_saga(prefect, fail_execute={"HOTEL"}), CANCELLED)
        self.assertGreater(prefect.calls, 0)                    # lo intentó, falló y la SAGA siguió igual
        self.assertEqual(prefect.task_runs, {})

    def test_disabled_without_url_makes_no_calls(self):
        from app.prefect_mirror import PrefectMirror

        async def scenario():
            mirror = PrefectMirror(None)
            mirror.start()
            mirror.order_started("o1", {})
            mirror.step("o1", "PAYMENT", "EXECUTE", "STARTED")
            return mirror.enabled, mirror.queue.qsize()
        self.assertEqual(asyncio.run(scenario()), (False, 0))

    def test_recovery_of_an_unknown_order_creates_a_recovery_run(self):
        """Tras reiniciar orders, el espejo no conoce la orden: la recuperación aparece como su propio flow run."""
        import httpx

        from app.prefect_mirror import MirroredRepo, PrefectMirror

        async def scenario():
            prefect = FakePrefect()
            http = httpx.AsyncClient(transport=httpx.MockTransport(prefect))
            mirror = PrefectMirror("http://prefect/api", http, retry_delays=(0,))
            mirror.start()
            repo = FakeRepo()
            await repo.upsert_step("o9", "PAYMENT", "EXECUTE", "COMPLETED", 1)    # quedó así antes del reinicio
            await repo.upsert_step("o9", "FLIGHT", "EXECUTE", "STARTED")
            final = await SagaEngine(MirroredRepo(repo, mirror), FakeActions()).compensate("o9", reason="recuperación")
            await mirror.drain()
            await mirror.close()
            await http.aclose()
            return final, prefect
        final, prefect = asyncio.run(scenario())
        self.assertEqual(final, CANCELLED)
        (run,) = prefect.flow_runs.values()
        self.assertTrue(run["name"].startswith("SAGA recuperación"))
        self.assertEqual([t["name"] for t in prefect.tasks()], ["↩ Cancelar vuelo", "↩ Reembolso del pago"])


if __name__ == "__main__":
    unittest.main()
