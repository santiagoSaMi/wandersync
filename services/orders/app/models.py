"""Modelos SQLAlchemy de la base orders_db (ordenes, bitacora de la SAGA, pagos y facturas)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key", name="uq_orders_user_idempotency"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    contact_name: Mapped[str] = mapped_column(String(120))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), index=True)     # ver saga.py
    total_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    # JSONB en Postgres; JSON generico en otros motores (los tests usan SQLite)
    items: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))  # {"FLIGHT": {...}, ...}
    simulate_failure: Mapped[str | None] = mapped_column(String(16), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SagaStep(Base):
    """Bitacora de la SAGA: una fila por (orden, paso, fase)."""
    __tablename__ = "saga_steps"
    __table_args__ = (UniqueConstraint("order_id", "name", "phase", name="uq_saga_step"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)   # orden cronologico
    order_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(16))          # PAYMENT | FLIGHT | HOTEL | CAR | INVOICE
    phase: Mapped[str] = mapped_column(String(12))         # EXECUTE | COMPENSATE
    status: Mapped[str] = mapped_column(String(12))        # STARTED | COMPLETED | FAILED
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Payment(Base):
    """Cobro simulado. WanderSync NO recibe ni guarda datos de tarjeta (fuera de alcance PCI-DSS)."""
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(12))        # CAPTURED | REFUNDED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Wallet(Base):
    """Billetera del usuario: el PAGO de la SAGA la debita y la compensación la reembolsa."""
    __tablename__ = "wallets"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    balance_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WalletMovement(Base):
    """Libro mayor (solo se agrega): apertura, débitos, reembolsos y ajustes, con el saldo resultante.
    UNIQUE(order_id, kind): una orden se debita y se reembolsa como máximo UNA vez (idempotencia)."""
    __tablename__ = "wallet_movements"
    __table_args__ = (UniqueConstraint("order_id", "kind", name="uq_wallet_movement_order_kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(12))              # OPENING | DEBIT | REFUND | ADJUSTMENT
    amount_cents: Mapped[int] = mapped_column(Integer)         # con signo: -débito, +reembolso
    balance_after_cents: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    number: Mapped[str] = mapped_column(String(40), unique=True)
    total_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(12))        # ISSUED | VOID
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
