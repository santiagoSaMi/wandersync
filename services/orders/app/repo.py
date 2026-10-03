"""Repositorio SQL que implementa SagaRepo (cada operacion = una transaccion corta y commit)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import Order, SagaStep, now
from .saga import COMPLETED, FAILED, NON_FINAL_STATES, StepRecord


class SqlSagaRepo:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.sf = session_factory

    async def set_status(self, order_id: str, status: str, failure_reason: str | None = None) -> None:
        """El motivo de fallo es el PRIMERO que se registra: una recuperacion posterior (p. ej. reintentar una
        compensacion fallida) no pisa la causa real ("CAR: HTTP 503 ..."); solo la pone si aun no habia ninguna."""
        values: dict = {"status": status, "updated_at": now()}
        if failure_reason:
            values["failure_reason"] = func.coalesce(Order.failure_reason, failure_reason[:500])
        async with self.sf() as s:
            await s.execute(update(Order).where(Order.id == order_id).values(**values))
            await s.commit()

    async def upsert_step(self, order_id: str, name: str, phase: str, status: str,
                          attempts: int = 0, error: str | None = None) -> None:
        async with self.sf() as s:
            row = (await s.execute(select(SagaStep).where(
                SagaStep.order_id == order_id, SagaStep.name == name, SagaStep.phase == phase,
            ))).scalar_one_or_none()
            stamp = now()
            if row is None:
                s.add(SagaStep(order_id=order_id, name=name, phase=phase, status=status,
                               attempts=attempts, error=error, started_at=stamp,
                               finished_at=stamp if status in (COMPLETED, FAILED) else None))
            else:
                row.status, row.attempts, row.error = status, attempts, (error[:500] if error else None)
                if status in (COMPLETED, FAILED):
                    row.finished_at = stamp
                else:                       # STARTED: se reinicia (p. ej. al reintentar una compensacion)
                    row.started_at, row.finished_at = stamp, None
            await s.execute(update(Order).where(Order.id == order_id).values(updated_at=stamp))
            await s.commit()

    async def list_steps(self, order_id: str) -> list[StepRecord]:
        async with self.sf() as s:
            rows = (await s.execute(
                select(SagaStep).where(SagaStep.order_id == order_id).order_by(SagaStep.id)
            )).scalars().all()
        return [StepRecord(r.name, r.phase, r.status, r.attempts, r.error) for r in rows]

    async def claim_stuck(self, order_id: str, older_than: datetime) -> bool:
        """Reclama ATOMICAMENTE una orden atascada para recuperarla. Solo una replica gana: el UPDATE
        condicional refresca updated_at, y en Postgres una segunda replica que actualice la misma fila
        en paralelo re-evalua el WHERE tras el commit de la primera y ya no coincide (rowcount 0).
        Tambien devuelve False si la orden avanzo (otra replica la esta ejecutando) o ya es final."""
        async with self.sf() as s:
            result = await s.execute(
                update(Order)
                .where(Order.id == order_id, Order.status.in_(NON_FINAL_STATES), Order.updated_at < older_than)
                .values(updated_at=now())
            )
            await s.commit()
        return result.rowcount == 1

    async def find_stuck(self, older_than: datetime) -> list[str]:
        async with self.sf() as s:
            return list((await s.execute(
                select(Order.id).where(Order.status.in_(NON_FINAL_STATES), Order.updated_at < older_than)
            )).scalars().all())
