"""Ejecucion de SAGAs en segundo plano con espera sincrona ACOTADA. Logica pura (solo asyncio): testeable.

La peticion POST /orders espera el final de la SAGA como maximo `wait_s` segundos:
  - caso normal (incluidos los fallos simulados de la demo): termina antes y se responde el estado FINAL;
  - participante colgado/lento: se responde PENDING y la SAGA SIGUE en segundo plano. El cliente consulta
    el estado despues. Asi la SAGA nunca queda atada a los timeouts de nginx/gateway (30 s).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger("orders.runner")


class SagaRunner:
    def __init__(self) -> None:
        self.inflight: set[str] = set()              # ordenes que ESTE proceso esta ejecutando ahora mismo
        self._tasks: set[asyncio.Task] = set()       # referencias fuertes: el GC no puede descartar tareas vivas

    def start(self, order_id: str, run: Callable[[str], Awaitable[str]]) -> asyncio.Task:
        self.inflight.add(order_id)                  # antes de crear la tarea: la recuperacion no la toca
        task = asyncio.create_task(self._guarded(order_id, run))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def start_and_wait(self, order_id: str, run: Callable[[str], Awaitable[str]], wait_s: float) -> bool:
        """True si la SAGA termino dentro de `wait_s`; False si sigue ejecutandose en segundo plano."""
        task = self.start(order_id, run)
        try:
            await asyncio.wait_for(asyncio.shield(task), wait_s)   # shield: el timeout NO cancela la SAGA
            return True
        except TimeoutError:
            return False

    async def shutdown(self, grace_s: float) -> None:
        """Al apagar, da un margen a las SAGAs en curso; lo que no termine lo compensa la recuperacion al arrancar."""
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=grace_s)

    async def _guarded(self, order_id: str, run: Callable[[str], Awaitable[str]]) -> str | None:
        try:
            final = await run(order_id)
            log.info("order=%s SAGA terminada: %s", order_id, final)
            return final
        except Exception:                            # p. ej. la base cayo a mitad: la orden queda no-final
            log.exception("order=%s SAGA abortada; la recuperacion automatica la compensara", order_id)
            return None
        finally:
            self.inflight.discard(order_id)
