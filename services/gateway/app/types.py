"""Tipos del esquema GraphQL publico (lo unico que ve el frontend)."""
from datetime import date, datetime
from enum import Enum
from typing import Optional

import strawberry

_EPOCH_DT = datetime(1970, 1, 1)
_EPOCH_D = date(1970, 1, 1)


def _d(value) -> date:
    return date.fromisoformat(str(value)[:10]) if value else _EPOCH_D


def _dt(value) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else _EPOCH_DT


# ------------------------------------------------------------------ catalogo
@strawberry.type
class FlightOffer:
    id: strawberry.ID
    origin: str
    destination: str
    origin_city: str
    destination_city: str
    airline: str
    flight_number: str
    departure_date: date
    departure_time: str
    arrival_time: str
    duration_minutes: int
    stops: int
    cabin: str
    price_cents: int
    currency: str
    source: str
    scraped_at: datetime

    @staticmethod
    def from_row(r: dict) -> "FlightOffer":
        # Solo se piden a Supabase las columnas seleccionadas; el resto toma un valor neutro
        # que jamas se serializa (GraphQL solo resuelve los campos solicitados).
        return FlightOffer(
            id=r["id"], origin=r.get("origin", ""), destination=r.get("destination", ""),
            origin_city=r.get("origin_city", ""), destination_city=r.get("destination_city", ""),
            airline=r.get("airline", ""), flight_number=r.get("flight_number", ""),
            departure_date=_d(r.get("departure_date")), departure_time=r.get("departure_time", ""),
            arrival_time=r.get("arrival_time", ""), duration_minutes=r.get("duration_minutes", 0),
            stops=r.get("stops", 0), cabin=r.get("cabin", ""), price_cents=r.get("price_cents", 0),
            currency=r.get("currency", "USD"), source=r.get("source", ""), scraped_at=_dt(r.get("scraped_at")))


@strawberry.type
class HotelOffer:
    id: strawberry.ID
    city_code: str
    city_name: str
    name: str
    stars: int
    rating: Optional[float]
    amenities: list[str]
    checkin: date
    checkout: date
    nights: int
    price_per_night_cents: int
    total_price_cents: int
    currency: str
    source: str
    scraped_at: datetime

    @staticmethod
    def from_row(r: dict) -> "HotelOffer":
        return HotelOffer(
            id=r["id"], city_code=r.get("city_code", ""), city_name=r.get("city_name", ""),
            name=r.get("name", ""), stars=r.get("stars", 0), rating=r.get("rating"),
            amenities=[a for a in (r.get("amenities") or "").split(",") if a],
            checkin=_d(r.get("checkin")), checkout=_d(r.get("checkout")), nights=r.get("nights", 0),
            price_per_night_cents=r.get("price_per_night_cents", 0),
            total_price_cents=r.get("total_price_cents", 0), currency=r.get("currency", "USD"),
            source=r.get("source", ""), scraped_at=_dt(r.get("scraped_at")))


@strawberry.type
class CarOffer:
    id: strawberry.ID
    city_code: str
    city_name: str
    company: str
    model: str
    category: str
    transmission: str
    seats: int
    pickup_date: date
    dropoff_date: date
    days: int
    price_per_day_cents: int
    total_price_cents: int
    currency: str
    source: str
    scraped_at: datetime

    @staticmethod
    def from_row(r: dict) -> "CarOffer":
        return CarOffer(
            id=r["id"], city_code=r.get("city_code", ""), city_name=r.get("city_name", ""),
            company=r.get("company", ""), model=r.get("model", ""), category=r.get("category", ""),
            transmission=r.get("transmission", ""), seats=r.get("seats", 0),
            pickup_date=_d(r.get("pickup_date")), dropoff_date=_d(r.get("dropoff_date")),
            days=r.get("days", 0), price_per_day_cents=r.get("price_per_day_cents", 0),
            total_price_cents=r.get("total_price_cents", 0), currency=r.get("currency", "USD"),
            source=r.get("source", ""), scraped_at=_dt(r.get("scraped_at")))


@strawberry.type
class Package:
    id: strawberry.ID
    total_price_cents: int
    currency: str
    nights: int
    flight: FlightOffer
    hotel: HotelOffer
    car: CarOffer


@strawberry.type
class Route:
    origin: str
    destination: str
    origin_city: str
    destination_city: str


@strawberry.type
class CatalogStatus:
    ready: bool
    flight_offers: int
    hotel_offers: int
    car_offers: int
    last_run_at: Optional[datetime]
    failed_queries: int
    routes: list[Route]
    dates: list[date]
    nights: int


# ------------------------------------------------------------------ identidad
@strawberry.type
class User:
    id: strawberry.ID
    email: str
    full_name: str


# ------------------------------------------------------------------ ordenes / SAGA
@strawberry.enum
class FailureTarget(Enum):
    """Solo para la demostracion: en que paso de la SAGA forzar un fallo."""
    PAYMENT = "PAYMENT"
    FLIGHT = "FLIGHT"
    HOTEL = "HOTEL"
    CAR = "CAR"


@strawberry.type
class OrderItem:
    kind: str
    title: str
    detail: str
    price_cents: int


@strawberry.type
class SagaStep:
    seq: int
    name: str
    phase: str
    status: str
    attempts: int
    error: Optional[str]
    started_at: datetime
    finished_at: Optional[datetime]


@strawberry.type
class Order:
    id: strawberry.ID
    status: str
    total_cents: int
    currency: str
    failure_reason: Optional[str]
    invoice_number: Optional[str]
    created_at: datetime
    items: list[OrderItem]
    steps: list[SagaStep]

    @staticmethod
    def from_api(o: dict) -> "Order":
        return Order(
            id=o["id"], status=o["status"], total_cents=o["total_cents"], currency=o["currency"],
            failure_reason=o.get("failure_reason"), invoice_number=o.get("invoice_number"),
            created_at=_dt(o["created_at"]),
            items=[OrderItem(kind=i["kind"], title=i["title"], detail=i["detail"], price_cents=i["price_cents"])
                   for i in o["items"]],
            steps=[SagaStep(seq=s["seq"], name=s["name"], phase=s["phase"], status=s["status"],
                            attempts=s["attempts"], error=s.get("error"), started_at=_dt(s["started_at"]),
                            finished_at=_dt(s["finished_at"]) if s.get("finished_at") else None)
                   for s in o["steps"]])


# ------------------------------------------------------------------ billetera
@strawberry.type
class WalletMovement:
    kind: str = strawberry.field(description="OPENING | DEBIT | REFUND | ADJUSTMENT")
    amount_cents: int = strawberry.field(description="Con signo: negativo = débito, positivo = abono")
    balance_after_cents: int
    order_id: Optional[str]
    created_at: datetime


@strawberry.type(description="Billetera del usuario: el PAGO de la SAGA la debita y la compensación la reembolsa.")
class Wallet:
    id: strawberry.ID
    balance_cents: int
    currency: str
    movements: list[WalletMovement]

    @staticmethod
    def from_api(w: dict) -> "Wallet":
        return Wallet(id=w["user_id"], balance_cents=w["balance_cents"], currency=w["currency"],
                      movements=[WalletMovement(kind=m["kind"], amount_cents=m["amount_cents"],
                                                balance_after_cents=m["balance_after_cents"], order_id=m.get("order_id"),
                                                created_at=_dt(m["created_at"])) for m in w["movements"]])


# ------------------------------------------------------------------ entradas
@strawberry.input
class RegisterInput:
    email: str
    password: str
    full_name: str


@strawberry.input
class LoginInput:
    email: str
    password: str


@strawberry.input
class BookingInput:
    flight_offer_id: strawberry.ID
    hotel_offer_id: strawberry.ID
    car_offer_id: strawberry.ID
    idempotency_key: str
    simulate_failure: Optional[FailureTarget] = None
    car_pickup_time: Optional[str] = strawberry.field(default=None, description=(
        "Hora de recogida del auto (HH:MM), p. ej. al aterrizar el vuelo elegido. Opcional (paquetes armados)."))
    slow_motion: bool = strawberry.field(default=False, description=(
        "Solo para la demostracion: la SAGA avanza con una pausa por paso para verla en vivo."))
