from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class OfferSnapshot(BaseModel):
    """Copia (snapshot) de la oferta elegida, con el precio validado por el gateway."""
    id: str = Field(min_length=1, max_length=64)
    price_cents: int = Field(gt=0, le=1_000_000_00)
    currency: str = Field(min_length=3, max_length=3)
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=300)


class OrderIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    contact_name: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=64)
    flight: OfferSnapshot
    hotel: OfferSnapshot
    car: OfferSnapshot
    simulate_failure: Literal["PAYMENT", "FLIGHT", "HOTEL", "CAR"] | None = None
    # Modo presentacion: pausa por paso para ver la SAGA avanzar. Solo con ALLOW_FAILURE_SIMULATION.
    step_delay_ms: int = Field(default=0, ge=0, le=5000)

    @model_validator(mode="after")
    def same_currency(self):
        if len({self.flight.currency.upper(), self.hotel.currency.upper(), self.car.currency.upper()}) != 1:
            raise ValueError("todas las ofertas deben tener la misma moneda")
        return self


class ItemOut(BaseModel):
    kind: str
    title: str
    detail: str
    price_cents: int


class StepOut(BaseModel):
    seq: int
    name: str
    phase: str
    status: str
    attempts: int
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class OrderOut(BaseModel):
    id: str
    user_id: str
    status: str
    total_cents: int
    currency: str
    failure_reason: str | None
    invoice_number: str | None
    created_at: datetime
    items: list[ItemOut]
    steps: list[StepOut]


class WalletMovementOut(BaseModel):
    kind: str                       # OPENING | DEBIT | REFUND | ADJUSTMENT
    amount_cents: int               # con signo
    balance_after_cents: int
    order_id: str | None
    created_at: datetime


class WalletOut(BaseModel):
    user_id: str
    balance_cents: int
    currency: str
    movements: list[WalletMovementOut]


class DemoBalanceIn(BaseModel):
    balance_cents: int = Field(ge=0, le=10_000_000_00)
