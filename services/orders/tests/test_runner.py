"""Tests de SagaRunner: SAGA en segundo plano con espera sincrona acotada (sin BD ni red).
Ejecutar desde services/orders:  python -m unittest discover -s tests -v"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.runner import SagaRunner  # noqa: E402
from app.saga import CANCELLED, CONFIRMED, SagaEngine  # noqa: E402
from test_saga import FakeActions, FakeRepo  # noqa: E402


class SlowActions(FakeActions):
    """Participante lento/colgado: `step` tarda `delay` segundos."""

    def __init__(self, step: str, delay: float, **kw):
        super().__init__(**kw)
        self.step, self.delay = step, delay

    async def execute(self, step, order_id):
        if step == self.step:
            await asyncio.sleep(self.delay)
        return await super().execute(step, order_id)


class SagaRunnerTests(unittest.TestCase):
    def test_fast_saga_returns_final_state_within_wait(self):
        async def scenario():
            repo, runner = FakeRepo(), SagaRunner()
            finished = await runner.start_and_wait("o1", SagaEngine(repo, FakeActions()).run, wait_s=1)
            return finished, repo.status["o1"], set(runner.inflight)
        finished, status, inflight = asyncio.run(scenario())
        self.assertTrue(finished)
        self.assertEqual(status, CONFIRMED)
        self.assertEqual(inflight, set())

    def test_simulated_failure_also_finishes_within_wait(self):
        async def scenario():
            repo, runner = FakeRepo(), SagaRunner()
            finished = await runner.start_and_wait("o1", SagaEngine(repo, FakeActions(fail_execute={"CAR"})).run, 1)
            return finished, repo.status["o1"]
        self.assertEqual(asyncio.run(scenario()), (True, CANCELLED))

    def test_slow_saga_returns_early_and_keeps_running(self):
        async def scenario():
            repo, runner = FakeRepo(), SagaRunner()
            engine = SagaEngine(repo, SlowActions("HOTEL", delay=0.3))
            finished = await runner.start_and_wait("o1", engine.run, wait_s=0.05)
            during = (repo.status.get("o1"), "o1" in runner.inflight)
            await runner.shutdown(grace_s=2)                 # espera a que la SAGA termine
            after = (repo.status.get("o1"), "o1" in runner.inflight)
            return finished, during, after
        finished, during, after = asyncio.run(scenario())
        self.assertFalse(finished)                           # la peticion responderia 202 + PENDING
        self.assertEqual(during, (None, True))               # aun sin estado final y protegida de la recuperacion
        self.assertEqual(after, (CONFIRMED, False))          # el timeout de espera NO cancelo la SAGA

    def test_crashing_saga_is_contained_and_released(self):
        async def boom(order_id):
            raise RuntimeError("base caida")

        async def scenario():
            runner = SagaRunner()
            finished = await runner.start_and_wait("o1", boom, wait_s=1)
            return finished, set(runner.inflight)
        finished, inflight = asyncio.run(scenario())
        self.assertTrue(finished)                            # no se propaga: la recuperacion se encargara
        self.assertEqual(inflight, set())


if __name__ == "__main__":
    unittest.main()
