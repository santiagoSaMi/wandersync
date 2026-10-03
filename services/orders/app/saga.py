"""Motor de la SAGA (ORQUESTACION). Logica pura: no conoce HTTP ni SQL, solo dos interfaces.

Pasos (hacia adelante) y su compensacion (hacia atras, en orden inverso):

    PAYMENT  ── refund_payment        (cobro simulado, servicio local de Ordenes/Facturacion)
    FLIGHT   ── cancel_flight         (servicio Vuelos)
    HOTEL    ── cancel_hotel          (servicio Hoteles)
    CAR      ── cancel_car            (servicio Autos)
    INVOICE  ── void_invoice          (factura, local)

Reglas del orquestador (todas verificadas en tests/test_saga.py):
  1. WRITE-AHEAD: cada paso se registra como STARTED *antes* de ejecutarse. Si el proceso
     muere a mitad, se sabe exactamente que pudo haber quedado hecho.
  2. Ante un fallo se compensan TODOS los pasos que llegaron a iniciarse (incluido el que
     fallo, porque su resultado puede ser incierto: timeout, respuesta perdida...), en orden
     inverso. Las compensaciones son IDEMPOTENTES, asi que compensar de mas es seguro.
  3. Si una compensacion falla, las demas continuan y la orden queda COMPENSATION_FAILED;
     `compensate()` puede reintentarse (recuperacion) y solo repite lo pendiente.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("orders.saga")

STEPS: tuple[str, ...] = ("PAYMENT", "FLIGHT", "HOTEL", "CAR", "INVOICE")

EXECUTE, COMPENSATE = "EXECUTE", "COMPENSATE"
STARTED, COMPLETED, FAILED = "STARTED", "COMPLETED", "FAILED"

# Estados de la orden
PENDING, CONFIRMED, COMPENSATING, CANCELLED, COMPENSATION_FAILED = (
    "PENDING", "CONFIRMED", "COMPENSATING", "CANCELLED", "COMPENSATION_FAILED")
NON_FINAL_STATES = (PENDING, COMPENSATING, COMPENSATION_FAILED)


class StepError(Exception):
    """Fallo controlado de un paso (tras agotar reintentos internos)."""

    def __init__(self, message: str, attempts: int = 1):
        super().__init__(message)
        self.attempts = attempts


@dataclass
class StepRecord:
    name: str
    phase: str
    status: str
    attempts: int = 0
    error: str | None = None


class SagaRepo(Protocol):
    async def set_status(self, order_id: str, status: str, failure_reason: str | None = None) -> None: ...
    async def upsert_step(self, order_id: str, name: str, phase: str, status: str,
                          attempts: int = 0, error: str | None = None) -> None: ...
    async def list_steps(self, order_id: str) -> list[StepRecord]: ...


class SagaActions(Protocol):
    async def execute(self, step: str, order_id: str) -> int: ...      # devuelve nº de intentos
    async def compensate(self, step: str, order_id: str) -> int: ...


class SagaEngine:
    def __init__(self, repo: SagaRepo, actions: SagaActions, steps: tuple[str, ...] = STEPS,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.repo, self.actions, self.steps = repo, actions, steps
        self._sleep = sleep

    async def run(self, order_id: str, step_delay_s: float = 0) -> str:
        """Ejecuta la SAGA. Devuelve el estado final de la orden.
        `step_delay_s` (modo presentacion): pausa tras marcar cada paso STARTED, para que se vea "en curso"."""
        for step in self.steps:
            await self.repo.upsert_step(order_id, step, EXECUTE, STARTED)          # write-ahead
            if step_delay_s:
                await self._sleep(step_delay_s)
            try:
                attempts = await self.actions.execute(step, order_id)
            except StepError as exc:
                await self.repo.upsert_step(order_id, step, EXECUTE, FAILED, attempts=exc.attempts, error=str(exc))
                log.warning("order=%s paso %s FALLO: %s -> compensando", order_id, step, exc)
                return await self.compensate(order_id, reason=f"{step}: {exc}", step_delay_s=step_delay_s)
            except Exception as exc:  # error inesperado: tambien se compensa
                log.exception("order=%s paso %s error inesperado", order_id, step)
                await self.repo.upsert_step(order_id, step, EXECUTE, FAILED, attempts=1, error=f"unexpected: {exc}")
                return await self.compensate(order_id, reason=f"{step}: error inesperado", step_delay_s=step_delay_s)
            await self.repo.upsert_step(order_id, step, EXECUTE, COMPLETED, attempts=attempts)
            log.info("order=%s paso %s OK", order_id, step)
        await self.repo.set_status(order_id, CONFIRMED)
        return CONFIRMED

    async def compensate(self, order_id: str, reason: str | None = None, step_delay_s: float = 0) -> str:
        """Compensa hacia atras lo que este pendiente. Idempotente y reanudable."""
        await self.repo.set_status(order_id, COMPENSATING, reason)
        records = await self.repo.list_steps(order_id)
        started = [r.name for r in records if r.phase == EXECUTE]                  # orden de creacion
        done = {r.name for r in records if r.phase == COMPENSATE and r.status == COMPLETED}
        pending = [name for name in reversed(started) if name not in done]

        all_ok = True
        for name in pending:
            await self.repo.upsert_step(order_id, name, COMPENSATE, STARTED)
            if step_delay_s:
                await self._sleep(step_delay_s)
            try:
                attempts = await self.actions.compensate(name, order_id)
            except Exception as exc:  # StepError u otro: seguimos con las demas compensaciones
                all_ok = False
                attempts = getattr(exc, "attempts", 1)
                await self.repo.upsert_step(order_id, name, COMPENSATE, FAILED, attempts=attempts, error=str(exc))
                log.error("order=%s compensacion de %s FALLO: %s", order_id, name, exc)
                continue
            await self.repo.upsert_step(order_id, name, COMPENSATE, COMPLETED, attempts=attempts)
            log.info("order=%s compensacion de %s OK", order_id, name)

        final = CANCELLED if all_ok else COMPENSATION_FAILED
        await self.repo.set_status(order_id, final)
        return final
