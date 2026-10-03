"""Billetera de cada usuario (US$ 5.000 al abrirla) y su libro mayor de movimientos.

Todas las operaciones van dentro de la transacción del llamador. El débito es UN UPDATE condicional
(`... SET balance = balance - x WHERE balance >= x RETURNING balance`): comprobar y restar es atómico, así que
dos reservas simultáneas nunca pueden gastar el mismo saldo (0 filas afectadas => saldo insuficiente).
El PAGO de la SAGA llama a debit(); su compensación, a refund().
"""
from __future__ import annotations

import os

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Payment, Wallet, WalletMovement, now

INITIAL_BALANCE_CENTS = int(os.getenv("WALLET_INITIAL_BALANCE_CENTS", "500000"))   # US$ 5.000
CURRENCY = "USD"


class InsufficientFunds(Exception):
    def __init__(self, balance_cents: int, required_cents: int):
        super().__init__(f"Saldo insuficiente: disponible {usd(balance_cents)}, requerido {usd(required_cents)}")
        self.balance_cents, self.required_cents = balance_cents, required_cents


def usd(cents: int) -> str:
    return f"US$ {cents / 100:,.2f}"


async def open_wallet(s: AsyncSession, user_id: str, lock: bool = False) -> Wallet:
    """Devuelve la billetera del usuario; si no existe la abre con el saldo inicial (y su movimiento OPENING)."""
    query = select(Wallet).where(Wallet.user_id == user_id)
    wallet = (await s.execute(query.with_for_update() if lock else query)).scalar_one_or_none()
    if wallet is not None:
        return wallet
    wallet = Wallet(user_id=user_id, balance_cents=INITIAL_BALANCE_CENTS, currency=CURRENCY)
    s.add(wallet)
    s.add(WalletMovement(user_id=user_id, kind="OPENING", amount_cents=INITIAL_BALANCE_CENTS,
                         balance_after_cents=INITIAL_BALANCE_CENTS))
    try:
        await s.flush()
    except IntegrityError:                     # otra petición la abrió a la vez: se usa esa
        await s.rollback()
        wallet = (await s.execute(query.with_for_update() if lock else query)).scalar_one()
    return wallet


async def debit(s: AsyncSession, user_id: str, order_id: str, amount_cents: int, currency: str) -> int:
    """Cobra la orden de la billetera. Lanza InsufficientFunds si el saldo bloqueado no alcanza."""
    wallet = await open_wallet(s, user_id)
    if currency.upper() != wallet.currency:
        raise ValueError(f"la billetera es en {wallet.currency}, la orden en {currency}")
    new_balance = (await s.execute(
        update(Wallet).where(Wallet.user_id == user_id, Wallet.balance_cents >= amount_cents)
        .values(balance_cents=Wallet.balance_cents - amount_cents, updated_at=now())
        .returning(Wallet.balance_cents))).scalar_one_or_none()
    if new_balance is None:                          # el WHERE no se cumplió: no alcanza el saldo
        await s.refresh(wallet)
        raise InsufficientFunds(wallet.balance_cents, amount_cents)
    s.add(WalletMovement(user_id=user_id, order_id=order_id, kind="DEBIT", amount_cents=-amount_cents,
                         balance_after_cents=new_balance))
    return new_balance


async def refund(s: AsyncSession, user_id: str, order_id: str, amount_cents: int) -> int:
    """Devuelve a la billetera lo cobrado por la orden (compensación del PAGO)."""
    await open_wallet(s, user_id)
    new_balance = (await s.execute(
        update(Wallet).where(Wallet.user_id == user_id)
        .values(balance_cents=Wallet.balance_cents + amount_cents, updated_at=now())
        .returning(Wallet.balance_cents))).scalar_one()
    s.add(WalletMovement(user_id=user_id, order_id=order_id, kind="REFUND", amount_cents=amount_cents,
                         balance_after_cents=new_balance))
    return new_balance


async def set_balance(s: AsyncSession, user_id: str, balance_cents: int) -> Wallet:
    """Solo para la demostración: fija el saldo (deja un movimiento ADJUSTMENT en el libro mayor)."""
    wallet = await open_wallet(s, user_id)
    old_balance = (await s.execute(
        select(Wallet.balance_cents).where(Wallet.user_id == user_id).with_for_update())).scalar_one()
    await s.execute(update(Wallet).where(Wallet.user_id == user_id)
                    .values(balance_cents=balance_cents, updated_at=now()))
    s.add(WalletMovement(user_id=user_id, kind="ADJUSTMENT", amount_cents=balance_cents - old_balance,
                         balance_after_cents=balance_cents))
    await s.flush()
    await s.refresh(wallet)
    return wallet


async def movements(s: AsyncSession, user_id: str, limit: int = 20) -> list[WalletMovement]:
    rows = await s.execute(select(WalletMovement).where(WalletMovement.user_id == user_id)
                           .order_by(WalletMovement.id.desc()).limit(limit))
    return list(rows.scalars())


async def captured_payment(s: AsyncSession, order_id: str, lock: bool = False) -> Payment | None:
    query = select(Payment).where(Payment.order_id == order_id)
    return (await s.execute(query.with_for_update() if lock else query)).scalar_one_or_none()
