"""Tests del motor SAGA con repositorio y participantes falsos (sin BD ni red).
Ejecutar desde services/orders:  python -m unittest discover -s tests -v"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.saga import (CANCELLED, COMPENSATE, COMPENSATION_FAILED, COMPENSATING, COMPLETED, CONFIRMED,  # noqa: E402
                      EXECUTE, FAILED, STARTED, SagaEngine, StepError, StepRecord)


class FakeRepo:
    def __init__(self):
        self.steps: dict[str, list[StepRecord]] = {}
        self.status: dict[str, str] = {}
        self.reason: dict[str, str] = {}
        self.status_history: list[str] = []

    async def set_status(self, order_id, status, failure_reason=None):
        self.status[order_id] = status
        self.status_history.append(status)
        if failure_reason:
            self.reason[order_id] = failure_reason

    async def upsert_step(self, order_id, name, phase, status, attempts=0, error=None):
        rows = self.steps.setdefault(order_id, [])
        for r in rows:
            if r.name == name and r.phase == phase:
                r.status, r.attempts, r.error = status, attempts, error
                return
        rows.append(StepRecord(name, phase, status, attempts, error))

    async def list_steps(self, order_id):
        return [StepRecord(r.name, r.phase, r.status, r.attempts, r.error) for r in self.steps.get(order_id, [])]


class FakeActions:
    def __init__(self, fail_execute=(), fail_compensate=()):
        self.fail_execute, self.fail_compensate = set(fail_execute), set(fail_compensate)
        self.executed, self.compensated = [], []

    async def execute(self, step, order_id):
        self.executed.append(step)
        if step in self.fail_execute:
            raise StepError(f"{step} no disponible", attempts=3)
        return 1

    async def compensate(self, step, order_id):
        self.compensated.append(step)
        if step in self.fail_compensate:
            raise StepError("servicio caido", attempts=3)
        return 1


def run(coro):
    return asyncio.run(coro)


class SagaEngineTests(unittest.TestCase):
    def test_happy_path(self):
        repo, actions = FakeRepo(), FakeActions()
        final = run(SagaEngine(repo, actions).run("o1"))
        self.assertEqual(final, CONFIRMED)
        self.assertEqual(actions.executed, ["PAYMENT", "FLIGHT", "HOTEL", "CAR", "INVOICE"])
        self.assertEqual(actions.compensated, [])
        self.assertTrue(all(r.status == COMPLETED for r in repo.steps["o1"]))

    def test_car_failure_compensates_everything_in_reverse_order(self):
        repo, actions = FakeRepo(), FakeActions(fail_execute={"CAR"})
        final = run(SagaEngine(repo, actions).run("o1"))
        self.assertEqual(final, CANCELLED)
        self.assertEqual(actions.executed, ["PAYMENT", "FLIGHT", "HOTEL", "CAR"])   # INVOICE nunca se intento
        # se compensa tambien CAR (resultado incierto) y luego HOTEL, FLIGHT, PAYMENT
        self.assertEqual(actions.compensated, ["CAR", "HOTEL", "FLIGHT", "PAYMENT"])
        by = {(r.name, r.phase): r.status for r in repo.steps["o1"]}
        self.assertEqual(by[("CAR", EXECUTE)], FAILED)
        self.assertEqual(by[("HOTEL", EXECUTE)], COMPLETED)
        self.assertTrue(all(by[(n, COMPENSATE)] == COMPLETED for n in ("CAR", "HOTEL", "FLIGHT", "PAYMENT")))
        self.assertIn("CAR", repo.reason["o1"])
        self.assertEqual(repo.status_history, [COMPENSATING, CANCELLED])

    def test_first_step_failure_does_not_touch_later_services(self):
        repo, actions = FakeRepo(), FakeActions(fail_execute={"PAYMENT"})
        final = run(SagaEngine(repo, actions).run("o1"))
        self.assertEqual(final, CANCELLED)
        self.assertEqual(actions.executed, ["PAYMENT"])
        self.assertEqual(actions.compensated, ["PAYMENT"])

    def test_hotel_failure_only_compensates_started_steps(self):
        repo, actions = FakeRepo(), FakeActions(fail_execute={"HOTEL"})
        run(SagaEngine(repo, actions).run("o1"))
        self.assertEqual(actions.compensated, ["HOTEL", "FLIGHT", "PAYMENT"])
        self.assertNotIn("CAR", actions.executed)

    def test_failed_compensation_is_reported_and_resumable(self):
        repo = FakeRepo()
        broken = FakeActions(fail_execute={"CAR"}, fail_compensate={"FLIGHT"})
        final = run(SagaEngine(repo, broken).run("o1"))
        self.assertEqual(final, COMPENSATION_FAILED)
        # las demas compensaciones SI se ejecutaron
        self.assertEqual(broken.compensated, ["CAR", "HOTEL", "FLIGHT", "PAYMENT"])

        # recuperacion: el servicio ya funciona -> solo se repite lo pendiente (FLIGHT)
        healed = FakeActions()
        final = run(SagaEngine(repo, healed).compensate("o1"))
        self.assertEqual(final, CANCELLED)
        self.assertEqual(healed.compensated, ["FLIGHT"])

    def test_crash_recovery_compensates_started_steps(self):
        """Simula un crash: PAYMENT completado y FLIGHT quedo en STARTED (resultado desconocido)."""
        repo = FakeRepo()
        run(repo.upsert_step("o1", "PAYMENT", EXECUTE, COMPLETED, attempts=1))
        run(repo.upsert_step("o1", "FLIGHT", EXECUTE, STARTED))
        actions = FakeActions()
        final = run(SagaEngine(repo, actions).compensate("o1", reason="recovered"))
        self.assertEqual(final, CANCELLED)
        self.assertEqual(actions.compensated, ["FLIGHT", "PAYMENT"])

    def test_compensate_is_idempotent(self):
        repo, actions = FakeRepo(), FakeActions(fail_execute={"CAR"})
        engine = SagaEngine(repo, actions)
        run(engine.run("o1"))
        again = FakeActions()
        self.assertEqual(run(SagaEngine(repo, again).compensate("o1")), CANCELLED)
        self.assertEqual(again.compensated, [])   # nada pendiente

    def test_presentation_delay_pauses_right_after_each_step_starts(self):
        """Modo presentacion: cada paso (y cada compensacion) queda STARTED -> pausa -> se ejecuta,
        para que la UI lo muestre 'en curso'."""
        events = []

        class RecordingRepo(FakeRepo):
            async def upsert_step(self, order_id, name, phase, status, attempts=0, error=None):
                events.append((name, phase, status))
                await super().upsert_step(order_id, name, phase, status, attempts, error)

        async def sleep(seconds):
            events.append(("sleep", seconds))

        actions = FakeActions(fail_execute={"HOTEL"})
        final = run(SagaEngine(RecordingRepo(), actions, sleep=sleep).run("o1", step_delay_s=2.5))
        self.assertEqual(final, CANCELLED)
        started = [i for i, e in enumerate(events) if e[-1] == STARTED]
        self.assertEqual([events[i][:2] for i in started],
                         [("PAYMENT", EXECUTE), ("FLIGHT", EXECUTE), ("HOTEL", EXECUTE),
                          ("HOTEL", COMPENSATE), ("FLIGHT", COMPENSATE), ("PAYMENT", COMPENSATE)])
        self.assertTrue(all(events[i + 1] == ("sleep", 2.5) for i in started))
        self.assertEqual(sum(e[0] == "sleep" for e in events), len(started))

    def test_no_pause_without_delay(self):
        slept = []

        async def sleep(seconds):
            slept.append(seconds)
        run(SagaEngine(FakeRepo(), FakeActions(fail_execute={"CAR"}), sleep=sleep).run("o1"))
        self.assertEqual(slept, [])

    def test_unexpected_exception_also_compensates(self):
        class Boom(FakeActions):
            async def execute(self, step, order_id):
                if step == "HOTEL":
                    raise RuntimeError("bug")
                return await super().execute(step, order_id)
        repo, actions = FakeRepo(), Boom()
        self.assertEqual(run(SagaEngine(repo, actions).run("o1")), CANCELLED)
        self.assertEqual(actions.compensated, ["HOTEL", "FLIGHT", "PAYMENT"])


if __name__ == "__main__":
    unittest.main()
