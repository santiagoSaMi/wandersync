"""Tests de SqlSagaRepo.claim_stuck: la recuperacion de una SAGA atascada la reclama UNA sola replica.
Sin Docker: SQLite. Ejecutar desde services/orders:  python -m unittest discover -s tests -v
Requiere: pip install -r requirements.txt -r ../../requirements-test.txt (si faltan, se omite;
con REQUIRE_TEST_DEPS=1 falla en vez de omitirse)."""
import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HAS_DEPS = all(importlib.util.find_spec(m) for m in ("sqlalchemy", "aiosqlite"))
if not HAS_DEPS and os.getenv("REQUIRE_TEST_DEPS"):
    raise RuntimeError("REQUIRE_TEST_DEPS=1 pero faltan dependencias de test (ver requirements-test.txt)")


@unittest.skipUnless(HAS_DEPS, "requiere requirements.txt + requirements-test.txt")
class ClaimStuckTests(unittest.TestCase):
    def run_with(self, scenario, status="PENDING", age_s=120):
        """Base SQLite con una orden en `status` cuyo ultimo progreso fue hace `age_s` segundos."""
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.models import Base, Order, now
        from app.repo import SqlSagaRepo

        async def main():
            with tempfile.TemporaryDirectory() as tmp:
                engine = create_async_engine(f"sqlite+aiosqlite:///{os.path.join(tmp, 'orders.db')}")
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                sf = async_sessionmaker(engine, expire_on_commit=False)
                async with sf() as s:
                    last_progress = now() - timedelta(seconds=age_s)
                    order = Order(user_id="u1", contact_name="Ana", idempotency_key="key-12345678", status=status,
                                  total_cents=1, currency="USD", items={}, created_at=last_progress,
                                  updated_at=last_progress)
                    s.add(order)
                    await s.commit()
                try:
                    return await scenario(SqlSagaRepo(sf), order.id, now() - timedelta(seconds=60))
                finally:
                    await engine.dispose()
        return asyncio.run(main())

    def test_stale_order_is_claimed_once(self):
        async def scenario(repo, oid, cutoff):
            return await repo.claim_stuck(oid, cutoff), await repo.claim_stuck(oid, cutoff)
        self.assertEqual(self.run_with(scenario), (True, False))   # el reclamo refresco updated_at

    def test_concurrent_replicas_only_one_wins(self):
        async def scenario(repo, oid, cutoff):
            return sorted(await asyncio.gather(*(repo.claim_stuck(oid, cutoff) for _ in range(5))))
        self.assertEqual(self.run_with(scenario), [False, False, False, False, True])

    def test_order_with_recent_progress_is_not_claimed(self):
        """Una SAGA viva en otra replica avanza (updated_at reciente): no se toca."""
        async def scenario(repo, oid, cutoff):
            return await repo.claim_stuck(oid, cutoff)
        self.assertFalse(self.run_with(scenario, age_s=10))

    def test_final_orders_are_never_claimed(self):
        async def scenario(repo, oid, cutoff):
            return await repo.claim_stuck(oid, cutoff)
        for status in ("CONFIRMED", "CANCELLED"):
            with self.subTest(status=status):
                self.assertFalse(self.run_with(scenario, status=status))

    def test_first_failure_reason_is_kept(self):
        """B5: la recuperacion no pisa la causa real del fallo; solo la pone si no habia ninguna."""
        from app.models import Order

        async def scenario(repo, oid, cutoff):
            await repo.set_status(oid, "COMPENSATION_FAILED", "CAR: HTTP 503 tras 3 intentos")
            await repo.set_status(oid, "COMPENSATING", "recuperacion automatica de SAGA interrumpida")
            await repo.set_status(oid, "CANCELLED")
            async with repo.sf() as s:
                order = await s.get(Order, oid)
                return order.status, order.failure_reason
        self.assertEqual(self.run_with(scenario), ("CANCELLED", "CAR: HTTP 503 tras 3 intentos"))

    def test_recovery_reason_is_set_when_there_was_none(self):
        """Una SAGA cortada por una caida del orquestador no tenia motivo: la recuperacion lo pone."""
        from app.models import Order

        async def scenario(repo, oid, cutoff):
            await repo.set_status(oid, "COMPENSATING", "recuperacion automatica de SAGA interrumpida")
            async with repo.sf() as s:
                return (await s.get(Order, oid)).failure_reason
        self.assertEqual(self.run_with(scenario), "recuperacion automatica de SAGA interrumpida")

    def test_failed_compensations_remain_recoverable(self):
        async def scenario(repo, oid, cutoff):
            return await repo.claim_stuck(oid, cutoff)
        self.assertTrue(self.run_with(scenario, status="COMPENSATION_FAILED"))


if __name__ == "__main__":
    unittest.main()
