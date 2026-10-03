"""Servicio de autos - participante de la SAGA de reservas de WanderSync.

Contrato (identico en Vuelos/Hoteles/Autos, pero codigo y base de datos independientes):
  POST /reservations                  crea la reserva (idempotente por order_id)
  POST /reservations/{order_id}/cancel  COMPENSACION (idempotente; deja "lapida" si no existia)
  GET  /reservations/{order_id}       consulta

Solo accesible desde la red interna, con el token compartido X-Internal-Token.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, DateTime, Integer, String, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("cars")

SERVICE = "cars"
DATABASE_URL = os.environ["DATABASE_URL"]
INTERNAL_TOKEN = os.environ["INTERNAL_TOKEN"]
FAIL_RATE = float(os.getenv("FAIL_RATE", "0"))                       # fallos transitorios aleatorios
ALLOW_SIMULATION = os.getenv("ALLOW_FAILURE_SIMULATION", "false").lower() == "true"

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=5)
Session = async_sessionmaker(engine, expire_on_commit=False)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Reservation(Base):
    __tablename__ = "reservations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)   # clave de idempotencia
    status: Mapped[str] = mapped_column(String(16))                              # CONFIRMED | CANCELLED
    offer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ReservationIn(BaseModel):
    order_id: str = Field(min_length=1, max_length=64)
    offer_id: str = Field(min_length=1, max_length=64)
    customer_name: str = Field(min_length=1, max_length=120)
    amount_cents: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    details: dict[str, Any] = Field(default_factory=dict)


class ReservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    order_id: str
    status: str
    offer_id: str | None
    amount_cents: int
    currency: str
    created_at: datetime


async def internal_auth(x_internal_token: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_internal_token.encode(), INTERNAL_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="invalid internal token")


@asynccontextmanager
async def lifespan(_: FastAPI):
    for attempt in range(1, 31):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            break
        except Exception as exc:  # la base aun arranca
            log.warning("base no lista (%s), reintento %d/30", type(exc).__name__, attempt)
            await asyncio.sleep(2)
    else:
        raise RuntimeError("no se pudo conectar a la base de datos")
    yield
    await engine.dispose()


app = FastAPI(title=f"WanderSync {SERVICE}", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def _maybe_fail(simulate_header: str | None) -> None:
    if ALLOW_SIMULATION and (simulate_header or "").lower() == "true":
        log.warning("[%s] fallo SIMULADO solicitado por el orquestador", SERVICE)
        raise HTTPException(status_code=503, detail="simulated_failure")
    if FAIL_RATE and random.random() < FAIL_RATE:
        raise HTTPException(status_code=503, detail="transient_failure")


async def _get(session, order_id: str) -> Reservation | None:
    return (await session.execute(select(Reservation).where(Reservation.order_id == order_id))).scalar_one_or_none()


def _existing_or_conflict(row: Reservation) -> Reservation:
    if row.status == "CANCELLED":  # ya compensada (o "lapida"): una reserva tardia NO debe revivirla
        raise HTTPException(status_code=409, detail="reservation_cancelled")
    return row


@app.get("/health")
async def health() -> dict:
    async with Session() as s:
        await s.execute(text("select 1"))
    return {"status": "ok", "service": SERVICE}


@app.post("/reservations", response_model=ReservationOut, status_code=201, dependencies=[Depends(internal_auth)])
async def create_reservation(body: ReservationIn, response: Response,
                             x_simulate_failure: str | None = Header(default=None)):
    _maybe_fail(x_simulate_failure)
    async with Session() as s:
        existing = await _get(s, body.order_id)
        if existing:
            response.status_code = 200
            return _existing_or_conflict(existing)
        row = Reservation(order_id=body.order_id, status="CONFIRMED", offer_id=body.offer_id,
                          customer_name=body.customer_name, amount_cents=body.amount_cents,
                          currency=body.currency.upper(), details=body.details)
        s.add(row)
        try:
            await s.commit()
        except IntegrityError:            # carrera: otra peticion inserto primero
            await s.rollback()
            existing = await _get(s, body.order_id)
            response.status_code = 200
            return _existing_or_conflict(existing)
    log.info("[%s] reserva CONFIRMADA order=%s offer=%s", SERVICE, body.order_id, body.offer_id)
    return row


@app.post("/reservations/{order_id}/cancel", response_model=ReservationOut, dependencies=[Depends(internal_auth)])
async def cancel_reservation(order_id: str):
    _maybe_fail(None)
    async with Session() as s:
        row = await _get(s, order_id)
        if row is None:
            # La reserva pudo perderse en la red: dejamos una lapida CANCELLED para que,
            # si llega tarde, sea rechazada (409) en lugar de quedar huerfana.
            row = Reservation(order_id=order_id, status="CANCELLED")
            s.add(row)
            try:
                await s.commit()
            except IntegrityError:
                await s.rollback()
                row = await _get(s, order_id)
        if row.status != "CANCELLED":
            row.status = "CANCELLED"
            row.updated_at = _now()
            await s.commit()
    log.info("[%s] COMPENSACION aplicada: reserva order=%s CANCELADA", SERVICE, order_id)
    return row


@app.get("/reservations/{order_id}", response_model=ReservationOut, dependencies=[Depends(internal_auth)])
async def get_reservation(order_id: str):
    async with Session() as s:
        row = await _get(s, order_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not_found")
    return row
