"""Servicio de Ordenes/Facturacion + ORQUESTADOR de la SAGA de reservas.

Solo accesible desde la red interna (via el API Gateway) con X-Internal-Token.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import timedelta
from functools import partial

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Response
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .actions import OrderActions
from . import wallet as wallets
from .models import Base, Invoice, Order, SagaStep, now
from .prefect_mirror import MirroredRepo, PrefectMirror
from .repo import SqlSagaRepo
from .runner import SagaRunner
from .saga import PENDING, SagaEngine
from .schemas import DemoBalanceIn, ItemOut, OrderIn, OrderOut, StepOut, WalletMovementOut, WalletOut

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("orders")

DATABASE_URL = os.environ["DATABASE_URL"]
INTERNAL_TOKEN = os.environ["INTERNAL_TOKEN"]
ALLOW_SIMULATION = os.getenv("ALLOW_FAILURE_SIMULATION", "false").lower() == "true"
# Sin progreso durante este tiempo => SAGA interrumpida. Debe superar holgadamente el peor caso de UN paso
# (3 intentos x timeout HTTP de 5 s + backoff ~= 25 s): si no, se compensarian SAGAs vivas de otras replicas.
STUCK_AFTER_S = int(os.getenv("SAGA_STUCK_AFTER_SECONDS", "60"))
RECOVERY_INTERVAL_S = int(os.getenv("SAGA_RECOVERY_INTERVAL_SECONDS", "30"))
# Cuanto espera POST /orders el final de la SAGA antes de responder PENDING (debe ser << timeout de gateway/nginx)
SYNC_WAIT_S = float(os.getenv("SAGA_SYNC_WAIT_SECONDS", "10"))
SHUTDOWN_GRACE_S = float(os.getenv("SAGA_SHUTDOWN_GRACE_SECONDS", "8"))
# Observabilidad: cada SAGA se refleja en Prefect como un flow run con un paso por tarea (vacío = desactivado)
PREFECT_API_URL = os.getenv("PREFECT_API_URL", "")
SERVICE_URLS = {
    "FLIGHT": os.getenv("FLIGHTS_URL", "http://flights:8000"),
    "HOTEL": os.getenv("HOTELS_URL", "http://hotels:8000"),
    "CAR": os.getenv("CARS_URL", "http://cars:8000"),
}

db_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=10)
Session = async_sessionmaker(db_engine, expire_on_commit=False)

mirror = PrefectMirror(None)          # se reemplaza en el lifespan si hay PREFECT_API_URL
runner = SagaRunner()               # SAGAs en segundo plano + conjunto `inflight` de este proceso
saga: SagaEngine
repo: SqlSagaRepo


async def internal_auth(x_internal_token: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_internal_token.encode(), INTERNAL_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="invalid internal token")


async def recover_once(min_age_s: int) -> None:
    """Recuperacion de SAGAs interrumpidas (crash del orquestador o compensaciones fallidas):
    recompensa hacia atras lo que quedo pendiente. Es seguro porque todo es idempotente.
    Seguro con VARIAS replicas: una SAGA viva actualiza updated_at en cada paso (peor caso ~25 s por paso),
    asi que solo se toca lo que lleva `min_age_s` sin avanzar, y cada orden la reclama una sola replica."""
    cutoff = now() - timedelta(seconds=min_age_s)
    for order_id in await repo.find_stuck(cutoff):
        if order_id in runner.inflight:
            continue
        if not await repo.claim_stuck(order_id, cutoff):       # otra replica la reclamo, o avanzo/termino
            continue
        runner.inflight.add(order_id)
        try:
            log.warning("recuperando SAGA atascada order=%s", order_id)
            await saga.compensate(order_id, reason="recuperacion automatica de SAGA interrumpida")
        except Exception:
            log.exception("fallo la recuperacion de order=%s", order_id)
        finally:
            runner.inflight.discard(order_id)


async def _recovery_loop() -> None:
    while True:
        await asyncio.sleep(RECOVERY_INTERVAL_S)
        try:
            await recover_once(STUCK_AFTER_S)
        except Exception:
            log.exception("bucle de recuperacion")


@asynccontextmanager
async def lifespan(_: FastAPI):
    global saga, repo
    for attempt in range(1, 31):
        try:
            async with db_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            break
        except Exception as exc:
            log.warning("base no lista (%s), reintento %d/30", type(exc).__name__, attempt)
            await asyncio.sleep(2)
    else:
        raise RuntimeError("no se pudo conectar a orders_db")

    global mirror
    http = httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0))
    prefect_http = httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0))
    mirror = PrefectMirror(PREFECT_API_URL, prefect_http)
    mirror.start()
    log.info("espejo de la SAGA en Prefect: %s", PREFECT_API_URL or "desactivado")
    repo = SqlSagaRepo(Session)
    actions = OrderActions(Session, http, SERVICE_URLS, INTERNAL_TOKEN, ALLOW_SIMULATION)
    saga = SagaEngine(MirroredRepo(repo, mirror), actions)     # la base guarda; Prefect lo refleja después

    # Al arrancar NO se asume que todo lo no final es huerfano: con varias replicas, otra puede estar
    # ejecutandolo. Se aplica el mismo umbral que el bucle (sin progreso durante STUCK_AFTER_S).
    await recover_once(min_age_s=STUCK_AFTER_S)
    task = asyncio.create_task(_recovery_loop())
    yield
    task.cancel()
    await runner.shutdown(SHUTDOWN_GRACE_S)         # deja terminar las SAGAs en curso antes de cerrar HTTP/BD
    await mirror.close()                            # vacía (con margen) los eventos pendientes hacia Prefect
    await prefect_http.aclose()
    await http.aclose()
    await db_engine.dispose()


app = FastAPI(title="WanderSync orders", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


async def _outputs(s, orders: list[Order], include_steps: bool = True) -> list[OrderOut]:
    """Arma la respuesta de N ordenes con un numero CONSTANTE de consultas (sin N+1): facturas en una consulta y,
    solo si el cliente pidio los pasos de la SAGA, todos los pasos en otra."""
    ids = [o.id for o in orders]
    if not ids:
        return []
    invoices = {i.order_id: i for i in (await s.execute(select(Invoice).where(Invoice.order_id.in_(ids)))).scalars()}
    steps: dict[str, list[SagaStep]] = defaultdict(list)
    if include_steps:
        rows = await s.execute(select(SagaStep).where(SagaStep.order_id.in_(ids)).order_by(SagaStep.id))
        for r in rows.scalars():
            steps[r.order_id].append(r)
    out = []
    for order in orders:
        invoice = invoices.get(order.id)
        items = [ItemOut(kind=k, title=order.items[k]["title"], detail=order.items[k].get("detail", ""),
                         price_cents=order.items[k]["price_cents"]) for k in ("FLIGHT", "HOTEL", "CAR")]
        out.append(OrderOut(
            id=order.id, user_id=order.user_id, status=order.status, total_cents=order.total_cents,
            currency=order.currency, failure_reason=order.failure_reason,
            invoice_number=invoice.number if invoice and invoice.status == "ISSUED" else None,
            created_at=order.created_at, items=items,
            steps=[StepOut(seq=i + 1, name=r.name, phase=r.phase, status=r.status, attempts=r.attempts,
                           error=r.error, started_at=r.started_at, finished_at=r.finished_at)
                   for i, r in enumerate(steps[order.id])],
        ))
    return out


async def _to_out(order_id: str, user_id: str | None = None, include_steps: bool = True) -> OrderOut:
    async with Session() as s:
        order = await s.get(Order, order_id)
        if order is None or (user_id is not None and order.user_id != user_id):   # anti-IDOR
            raise HTTPException(status_code=404, detail="order_not_found")
        return (await _outputs(s, [order], include_steps))[0]


@app.get("/health")
async def health() -> dict:
    async with Session() as s:
        await s.execute(text("select 1"))
    return {"status": "ok", "service": "orders", "failure_simulation": ALLOW_SIMULATION}


@app.post("/orders", response_model=OrderOut, status_code=201, dependencies=[Depends(internal_auth)])
async def create_order(body: OrderIn, response: Response):
    """Crea la orden y ejecuta la SAGA. Si termina dentro de SYNC_WAIT_S responde el estado FINAL (201);
    si no, responde 202 con la orden PENDING y la SAGA sigue en segundo plano (el cliente consulta el estado)."""
    async with Session() as s:
        dup = (await s.execute(select(Order).where(
            Order.user_id == body.user_id, Order.idempotency_key == body.idempotency_key))).scalar_one_or_none()
        if dup:
            response.status_code = 200
            return await _to_out(dup.id)
        total_cents = body.flight.price_cents + body.hotel.price_cents + body.car.price_cents
        # Validación previa: sin saldo suficiente no se crea la orden. (El PAGO vuelve a validar con la fila
        # bloqueada, por si dos reservas compiten por el mismo saldo.)
        wallet = await wallets.open_wallet(s, body.user_id)
        await s.commit()                                # la apertura de la billetera, si fue nueva, queda guardada
        if wallet.balance_cents < total_cents:
            raise HTTPException(status_code=409, detail={
                "code": "insufficient_funds", "balance_cents": wallet.balance_cents, "required_cents": total_cents,
                "message": str(wallets.InsufficientFunds(wallet.balance_cents, total_cents))})
        order = Order(
            user_id=body.user_id, contact_name=body.contact_name, idempotency_key=body.idempotency_key,
            status=PENDING, currency=body.flight.currency.upper(),
            total_cents=total_cents,
            items={"FLIGHT": body.flight.model_dump(), "HOTEL": body.hotel.model_dump(),
                   "CAR": body.car.model_dump()},
            simulate_failure=body.simulate_failure if ALLOW_SIMULATION else None,
        )
        s.add(order)
        try:
            await s.commit()
        except IntegrityError:          # doble envio simultaneo con la misma clave de idempotencia
            await s.rollback()
            dup = (await s.execute(select(Order).where(
                Order.user_id == body.user_id, Order.idempotency_key == body.idempotency_key))).scalar_one()
            response.status_code = 200
            return await _to_out(dup.id)

    # Modo presentacion: la SAGA avanza con una pausa por paso y se responde YA (PENDING) para que el cliente
    # muestre cada paso desde el primero. No se persiste: si el proceso cae, la recuperacion va sin pausas.
    step_delay_s = body.step_delay_ms / 1000 if ALLOW_SIMULATION else 0
    wait_s = 0 if step_delay_s else SYNC_WAIT_S
    mirror.order_started(order.id, {
        "total": f"{order.total_cents / 100:.2f} {order.currency}",
        "vuelo": order.items["FLIGHT"]["title"], "hotel": order.items["HOTEL"]["title"], "auto": order.items["CAR"]["title"],
        "fallo_simulado": order.simulate_failure, "paso_a_paso": bool(step_delay_s),
    })
    if not await runner.start_and_wait(order.id, partial(saga.run, step_delay_s=step_delay_s), wait_s):
        if step_delay_s:
            log.info("order=%s SAGA en modo presentacion (%.1f s por paso): se responde PENDING (202)",
                     order.id, step_delay_s)
        else:
            log.warning("order=%s SAGA sigue tras %.0f s: se responde PENDING (202)", order.id, SYNC_WAIT_S)
        response.status_code = 202
    return await _to_out(order.id)


@app.get("/orders/{order_id}", response_model=OrderOut, dependencies=[Depends(internal_auth)])
async def get_order(order_id: str, user_id: str = Query(min_length=1, max_length=64), include_steps: bool = True):
    return await _to_out(order_id, user_id, include_steps)


@app.get("/orders", response_model=list[OrderOut], dependencies=[Depends(internal_auth)])
async def list_orders(user_id: str = Query(min_length=1, max_length=64), limit: int = Query(20, ge=1, le=50),
                      include_steps: bool = True):
    async with Session() as s:
        orders = (await s.execute(select(Order).where(Order.user_id == user_id)
                                  .order_by(Order.created_at.desc()).limit(limit))).scalars().all()
        return await _outputs(s, list(orders), include_steps)


# ------------------------------------------------------------------ billetera
async def _wallet_out(s, wallet, limit: int = 20) -> WalletOut:
    rows = await wallets.movements(s, wallet.user_id, limit)
    return WalletOut(user_id=wallet.user_id, balance_cents=wallet.balance_cents, currency=wallet.currency,
                     movements=[WalletMovementOut(kind=m.kind, amount_cents=m.amount_cents,
                                                  balance_after_cents=m.balance_after_cents, order_id=m.order_id,
                                                  created_at=m.created_at) for m in rows])


@app.get("/wallets/{user_id}", response_model=WalletOut, dependencies=[Depends(internal_auth)])
async def get_wallet(user_id: str = Path(min_length=1, max_length=64), limit: int = Query(20, ge=1, le=100)):
    """Saldo y últimos movimientos. Abre la billetera (US$ 5.000) si el usuario aún no tenía."""
    async with Session() as s:
        wallet = await wallets.open_wallet(s, user_id)
        await s.commit()
        return await _wallet_out(s, wallet, limit)


@app.post("/wallets/{user_id}", response_model=WalletOut, dependencies=[Depends(internal_auth)])
async def open_wallet(user_id: str = Path(min_length=1, max_length=64)):
    """Apertura explícita al registrarse (idempotente)."""
    return await get_wallet(user_id, 20)


@app.post("/wallets/{user_id}/demo-balance", response_model=WalletOut, dependencies=[Depends(internal_auth)])
async def demo_set_balance(body: DemoBalanceIn, user_id: str = Path(min_length=1, max_length=64)):
    """Solo en modo demostración: fija el saldo para mostrar la validación de saldo insuficiente."""
    if not ALLOW_SIMULATION:
        raise HTTPException(status_code=403, detail="demo_mode_disabled")
    async with Session() as s:
        wallet = await wallets.set_balance(s, user_id, body.balance_cents)
        await s.commit()
        return await _wallet_out(s, wallet)
