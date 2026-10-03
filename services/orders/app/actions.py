"""Acciones concretas de cada paso: locales (pago, factura) y remotas (Vuelos/Hoteles/Autos)."""
from __future__ import annotations

import asyncio
import logging
from datetime import date

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import Invoice, Order, Payment, now
from .saga import StepError

log = logging.getLogger("orders.actions")

REMOTE_STEPS = {"FLIGHT": "flights", "HOTEL": "hotels", "CAR": "cars"}


class OrderActions:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], http: httpx.AsyncClient,
                 service_urls: dict[str, str], internal_token: str, allow_simulation: bool,
                 max_attempts: int = 3, backoff_s: float = 0.3):
        self.sf, self.http = session_factory, http
        self.urls, self.token = service_urls, internal_token
        self.allow_sim = allow_simulation
        self.max_attempts, self.backoff_s = max_attempts, backoff_s

    # ------------------------------------------------------------------ API del motor
    async def execute(self, step: str, order_id: str) -> int:
        order = await self._order(order_id)
        if step == "PAYMENT":
            await self._charge(order)
            return 1
        if step in REMOTE_STEPS:
            return await self._reserve(step, order)
        if step == "INVOICE":
            await self._issue_invoice(order)
            return 1
        raise ValueError(f"paso desconocido {step}")

    async def compensate(self, step: str, order_id: str) -> int:
        if step == "PAYMENT":
            await self._refund(order_id)
            return 1
        if step in REMOTE_STEPS:
            return await self._cancel_remote(step, order_id)
        if step == "INVOICE":
            await self._void_invoice(order_id)
            return 1
        raise ValueError(f"paso desconocido {step}")

    # ------------------------------------------------------------------ helpers
    async def _order(self, order_id: str) -> Order:
        async with self.sf() as s:
            order = await s.get(Order, order_id)
        if order is None:
            raise StepError(f"orden {order_id} no existe")
        return order

    def _should_fail(self, order: Order, step: str) -> bool:
        return self.allow_sim and order.simulate_failure == step

    async def _call(self, url: str, json: dict | None, headers: dict) -> tuple[httpx.Response, int]:
        """HTTP con reintentos SOLO ante fallos transitorios (red, 5xx, 429). Un 4xx es definitivo."""
        last = "sin respuesta"
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = await self.http.post(url, json=json, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = f"error de red ({type(exc).__name__})"
            else:
                if resp.status_code < 500 and resp.status_code != 429:
                    return resp, attempt
                last = f"HTTP {resp.status_code}"
            if attempt < self.max_attempts:
                await asyncio.sleep(self.backoff_s * 2 ** (attempt - 1))
        raise StepError(f"{last} tras {self.max_attempts} intentos", attempts=self.max_attempts)

    # ------------------------------------------------------------------ pasos remotos
    async def _reserve(self, step: str, order: Order) -> int:
        item = order.items[step]
        headers = {"X-Internal-Token": self.token}
        if self._should_fail(order, step):
            headers["X-Simulate-Failure"] = "true"
        body = {
            "order_id": order.id, "offer_id": item["id"], "customer_name": order.contact_name,
            "amount_cents": item["price_cents"], "currency": item["currency"],
            "details": {"title": item.get("title"), "detail": item.get("detail")},
        }
        resp, attempts = await self._call(f"{self.urls[step]}/reservations", body, headers)
        if resp.status_code not in (200, 201):
            raise StepError(f"{REMOTE_STEPS[step]} respondio HTTP {resp.status_code}: {resp.text[:160]}", attempts)
        return attempts

    async def _cancel_remote(self, step: str, order_id: str) -> int:
        resp, attempts = await self._call(f"{self.urls[step]}/reservations/{order_id}/cancel", None,
                                          {"X-Internal-Token": self.token})
        if resp.status_code != 200:
            raise StepError(f"cancelacion en {REMOTE_STEPS[step]}: HTTP {resp.status_code}", attempts)
        return attempts

    # ------------------------------------------------------------------ pasos locales
    async def _charge(self, order: Order) -> None:
        if self._should_fail(order, "PAYMENT"):
            raise StepError("Pago rechazado (fallo simulado)")
        async with self.sf() as s:
            existing = (await s.execute(select(Payment).where(Payment.order_id == order.id))).scalar_one_or_none()
            if existing is not None:
                if existing.status == "CAPTURED":
                    return                      # idempotente
                raise StepError("el pago de esta orden ya fue reembolsado")
            s.add(Payment(order_id=order.id, amount_cents=order.total_cents, currency=order.currency,
                          status="CAPTURED"))
            try:
                await s.commit()
            except IntegrityError:
                await s.rollback()

    async def _refund(self, order_id: str) -> None:
        async with self.sf() as s:
            pay = (await s.execute(select(Payment).where(Payment.order_id == order_id))).scalar_one_or_none()
            if pay is not None and pay.status == "CAPTURED":
                pay.status, pay.updated_at = "REFUNDED", now()
                await s.commit()                # si no hubo pago, no hay nada que reembolsar

    async def _issue_invoice(self, order: Order) -> None:
        async with self.sf() as s:
            if (await s.execute(select(Invoice).where(Invoice.order_id == order.id))).scalar_one_or_none():
                return
            number = f"WS-{date.today():%Y%m%d}-{order.id[:8].upper()}"
            s.add(Invoice(order_id=order.id, number=number, total_cents=order.total_cents,
                          currency=order.currency, status="ISSUED"))
            try:
                await s.commit()
            except IntegrityError:
                await s.rollback()

    async def _void_invoice(self, order_id: str) -> None:
        async with self.sf() as s:
            inv = (await s.execute(select(Invoice).where(Invoice.order_id == order_id))).scalar_one_or_none()
            if inv is not None and inv.status == "ISSUED":
                inv.status = "VOID"
                await s.commit()
