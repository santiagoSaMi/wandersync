"""Espejo de cada SAGA en Prefect (OBSERVABILIDAD): cada reserva aparece en la UI de Prefect como un flow run
"SAGA reserva <orden>" cuyas task runs son los pasos, con flechas entre ellos, y que crece en vivo.

    1 · Pago ─► 2 · Reserva de vuelo ─► 3 · Reserva de hotel ─► 4 · Reserva de auto ✖ ─► ↩ Cancelar auto ─► ...
                         ╰──────────────── cada reversión apunta también al paso que deshace ─────────╯

Prefect OBSERVA la SAGA; no la ejecuta. El orquestador sigue siendo SagaEngine (con su bitácora, idempotencia y
recuperación) y la base de datos es la fuente de verdad: cada evento se refleja DESPUÉS de guardarse en saga_steps.
Garantía clave: el espejo NUNCA afecta a una reserva. Encolar es no bloqueante, un único worker en segundo plano
habla con la API REST de Prefect (reintentando 408/429/502/503 como hace el cliente oficial) y si Prefect no está
disponible solo se registra una advertencia.
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from datetime import datetime, timezone

import httpx

from .saga import COMPENSATE, COMPLETED, EXECUTE, FAILED, STARTED

log = logging.getLogger("orders.prefect")

FLOW_NAME = "saga-reserva"
TITLES = {
    (EXECUTE, "PAYMENT"): "1 · Pago", (EXECUTE, "FLIGHT"): "2 · Reserva de vuelo",
    (EXECUTE, "HOTEL"): "3 · Reserva de hotel", (EXECUTE, "CAR"): "4 · Reserva de auto",
    (EXECUTE, "INVOICE"): "5 · Factura",
    (COMPENSATE, "PAYMENT"): "↩ Reembolso del pago", (COMPENSATE, "FLIGHT"): "↩ Cancelar vuelo",
    (COMPENSATE, "HOTEL"): "↩ Cancelar hotel", (COMPENSATE, "CAR"): "↩ Cancelar auto",
    (COMPENSATE, "INVOICE"): "↩ Anular factura",
}
# Estado final de la orden -> estado del flow run en Prefect (tipo, nombre visible)
FINAL_STATES = {
    "CONFIRMED": ("COMPLETED", "Confirmada"),
    "CANCELLED": ("CANCELLED", "Revertida"),
    "COMPENSATION_FAILED": ("FAILED", "Compensación pendiente"),
}
RETRY_STATUS = {408, 429, 502, 503}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PrefectUnavailable(Exception):
    pass


class PrefectMirror:
    def __init__(self, api_url: str | None, http: httpx.AsyncClient | None = None,
                 retry_delays: tuple[float, ...] = (0.2, 0.5, 1.0, 2.0), max_queue: int = 2000, max_orders: int = 500):
        self.api_url = (api_url or "").rstrip("/")
        self.enabled = bool(self.api_url)
        self.http = http
        self.retry_delays = retry_delays
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
        self.max_orders = max_orders
        self._runs: OrderedDict[str, dict] = OrderedDict()     # order_id -> {flow_run_id, tasks, last}
        self._flow_id: str | None = None
        self._worker: asyncio.Task | None = None
        self._warned = False

    # ------------------------------------------------------------------ API pública (no bloqueante, nunca lanza)
    def order_started(self, order_id: str, parameters: dict) -> None:
        self._emit(("start", order_id, parameters))

    def step(self, order_id: str, name: str, phase: str, status: str, attempts: int = 0, error: str | None = None) -> None:
        self._emit(("step", order_id, name, phase, status, attempts, error))

    def status(self, order_id: str, status: str, reason: str | None = None) -> None:
        self._emit(("status", order_id, status, reason))

    def start(self) -> None:
        if self.enabled and self._worker is None:
            self._worker = asyncio.create_task(self._run())

    async def close(self, grace_s: float = 3.0) -> None:
        """Al apagar: da un margen para vaciar la cola y luego detiene el worker."""
        if self._worker is None:
            return
        try:
            await asyncio.wait_for(self.queue.join(), grace_s)
        except TimeoutError:
            log.warning("prefect: se apaga con %d eventos sin reflejar", self.queue.qsize())
        self._worker.cancel()

    async def drain(self) -> None:
        """Espera a que se hayan procesado los eventos encolados (para tests)."""
        await self.queue.join()

    # ------------------------------------------------------------------ worker
    def _emit(self, event: tuple) -> None:
        if not self.enabled:
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            log.warning("prefect: cola llena, se descarta un evento de la SAGA (la reserva no se ve afectada)")

    async def _run(self) -> None:
        while True:
            event = await self.queue.get()
            try:
                await self._handle(event)
                self._warned = False
            except Exception as exc:  # nada de lo que pase aqui puede afectar a la SAGA
                if not self._warned:
                    log.warning("prefect: no se pudo reflejar la SAGA (%s: %s); se reintentara con los siguientes eventos",
                                type(exc).__name__, exc)
                    self._warned = True
            finally:
                self.queue.task_done()

    async def _api(self, method: str, path: str, body=None) -> dict | list | None:
        for delay in (*self.retry_delays, None):
            try:
                resp = await self.http.request(method, f"{self.api_url}{path}", json=body)
            except httpx.TransportError as exc:
                failure = f"{type(exc).__name__}"
            else:
                if resp.status_code < 300:
                    return resp.json() if resp.content else None
                if resp.status_code not in RETRY_STATUS:
                    raise PrefectUnavailable(f"HTTP {resp.status_code} en {path}: {resp.text[:200]}")
                failure = f"HTTP {resp.status_code}"
            if delay is None:
                raise PrefectUnavailable(f"{failure} en {path} tras {len(self.retry_delays) + 1} intentos")
            await asyncio.sleep(delay)
        return None

    async def _flow(self) -> str:
        if self._flow_id is None:
            self._flow_id = (await self._api("POST", "/flows/", {"name": FLOW_NAME, "tags": ["saga"]}))["id"]
        return self._flow_id

    async def _ensure_run(self, order_id: str, parameters: dict | None = None, recovery: bool = False) -> dict:
        run = self._runs.get(order_id)
        if run is not None:
            return run
        label = "SAGA recuperación" if recovery else "SAGA reserva"
        flow_run = await self._api("POST", "/flow_runs/", {
            "flow_id": await self._flow(), "name": f"{label} {order_id[:8]}",
            "parameters": {"order_id": order_id, **(parameters or {})},
            "tags": ["saga", "recuperacion" if recovery else "reserva"],
            "state": {"type": "RUNNING", "name": "Ejecutando"},
        })
        run = {"flow_run_id": flow_run["id"], "tasks": {}, "last": None}
        self._runs[order_id] = run
        while len(self._runs) > self.max_orders:                 # memoria acotada
            self._runs.popitem(last=False)
        return run

    async def _log(self, run: dict, message: str, task_run_id: str | None = None, level: int = 20) -> None:
        await self._api("POST", "/logs/", [{"name": "orders.saga", "level": level, "message": message,
                                            "timestamp": _now(), "flow_run_id": run["flow_run_id"],
                                            "task_run_id": task_run_id}])

    async def _handle(self, event: tuple) -> None:
        kind, order_id = event[0], event[1]
        if kind == "start":
            run = await self._ensure_run(order_id, event[2])
            await self._log(run, f"SAGA iniciada para la orden {order_id}")
        elif kind == "step":
            await self._handle_step(order_id, *event[2:])
        elif kind == "status":
            await self._handle_status(order_id, *event[2:])

    async def _handle_step(self, order_id, name, phase, status, attempts, error) -> None:
        run = await self._ensure_run(order_id, recovery=order_id not in self._runs)
        key = (phase, name)
        title = TITLES.get(key, f"{phase} {name}")
        if status == STARTED:
            task_id = run["tasks"].get(key)
            if task_id is not None:                          # reintento (p. ej. recuperación de una compensación)
                await self._api("POST", f"/task_runs/{task_id}/set_state",
                                {"state": {"type": "RUNNING", "name": "Reintentando"}, "force": True})
                return
            inputs = {}
            if run["last"]:
                inputs["despues_de"] = [{"input_type": "task_run", "id": run["last"]}]
            undone = run["tasks"].get((EXECUTE, name)) if phase == COMPENSATE else None
            if undone and undone != run["last"]:
                inputs["revierte"] = [{"input_type": "task_run", "id": undone}]
            task = await self._api("POST", "/task_runs/", {
                "flow_run_id": run["flow_run_id"], "task_key": f"saga.{phase.lower()}.{name.lower()}",
                "dynamic_key": "0", "name": title, "task_inputs": inputs,
                "tags": ["saga", "compensacion" if phase == COMPENSATE else "ejecucion"],
                "state": {"type": "RUNNING", "name": "Revirtiendo" if phase == COMPENSATE else "Ejecutando"},
            })
            run["tasks"][key] = run["last"] = task["id"]
            return
        task_id = run["tasks"].get(key)
        if task_id is None:
            return
        tries = f"{attempts} intento{'s' if attempts != 1 else ''}"
        if status == COMPLETED:
            state = {"type": "COMPLETED", "name": "Revertido" if phase == COMPENSATE else "Completado"}
            await self._api("POST", f"/task_runs/{task_id}/set_state", {"state": state, "force": True})
            await self._log(run, f"{title}: OK ({tries})", task_id)
        elif status == FAILED:
            state = {"type": "FAILED", "name": "Falló", "message": error}
            await self._api("POST", f"/task_runs/{task_id}/set_state", {"state": state, "force": True})
            await self._log(run, f"{title}: FALLÓ ({tries}): {error}", task_id, level=40)

    async def _handle_status(self, order_id, status, reason) -> None:
        if status not in FINAL_STATES and status != "COMPENSATING":
            return
        run = await self._ensure_run(order_id, recovery=order_id not in self._runs)
        if status == "COMPENSATING":
            await self._api("POST", f"/flow_runs/{run['flow_run_id']}/set_state",
                            {"state": {"type": "RUNNING", "name": "Compensando"}, "force": True})
            await self._log(run, f"Un paso falló ({reason or 'recuperación'}): se revierte lo hecho, en orden inverso",
                            level=30)
            return
        state_type, state_name = FINAL_STATES[status]
        await self._api("POST", f"/flow_runs/{run['flow_run_id']}/set_state",
                        {"state": {"type": state_type, "name": state_name, "message": reason}, "force": True})
        await self._log(run, f"SAGA terminada: {state_name}")


class MirroredRepo:
    """Envuelve el repositorio de la SAGA: guarda en la base (fuente de verdad) y DESPUÉS lo refleja en Prefect."""

    def __init__(self, repo, mirror: PrefectMirror):
        self.repo, self.mirror = repo, mirror

    async def set_status(self, order_id, status, failure_reason=None):
        await self.repo.set_status(order_id, status, failure_reason)
        self.mirror.status(order_id, status, failure_reason)

    async def upsert_step(self, order_id, name, phase, status, attempts=0, error=None):
        await self.repo.upsert_step(order_id, name, phase, status, attempts, error)
        self.mirror.step(order_id, name, phase, status, attempts, error)

    def __getattr__(self, attr):            # list_steps, find_stuck, claim_stuck... sin cambios
        return getattr(self.repo, attr)
