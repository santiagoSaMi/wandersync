"""Generador determinista de resultados de un sitio de viajes FALSO ("MockTravel").

Objetivo: comportarse como una fuente real de scraping (HTML, paginacion, formatos de
precio heterogeneos, precios que cambian con el tiempo) pero 100% controlable.

- Los datos de una misma consulta son estables (semilla = parametros), asi los IDs
  naturales no cambian entre ingestas y el upsert en Supabase es idempotente.
- Los PRECIOS varian por "cubeta" de tiempo (5 min) para simular tarifas en tiempo real.
"""
from __future__ import annotations

import hashlib
import itertools
import random
import time
from datetime import date, datetime, timedelta

PAGE_SIZE = 10
PRICE_BUCKET_SECONDS = 300

# IATA -> (ciudad, pais)
AIRPORTS: dict[str, tuple[str, str]] = {
    "BOG": ("Bogotá", "CO"),
    "MDE": ("Medellín", "CO"),
    "CTG": ("Cartagena", "CO"),
    "CLO": ("Cali", "CO"),
    "SMR": ("Santa Marta", "CO"),
    "ADZ": ("San Andrés", "CO"),
    "BAQ": ("Barranquilla", "CO"),
    "MIA": ("Miami", "US"),
    "MAD": ("Madrid", "ES"),
    "CUN": ("Cancún", "MX"),
    "PTY": ("Ciudad de Panamá", "PA"),
}

AIRLINES = [
    ("AV", "Avianca"), ("LA", "LATAM"), ("P5", "Wingo"), ("JA", "JetSMART"),
    ("CM", "Copa Airlines"), ("AA", "American Airlines"), ("IB", "Iberia"), ("AM", "Aeroméxico"),
]

_FLIGHT_CLASSES = {
    "domestic": {"price": (55, 190), "duration": (55, 100)},
    "regional": {"price": (170, 480), "duration": (150, 340)},
    "long": {"price": (560, 1150), "duration": (600, 780)},
}

_CITY_PRICE_FACTOR = {"MIA": 2.2, "MAD": 1.6, "CUN": 1.5, "PTY": 1.3}

_HOTEL_PREFIX = ["Hotel", "Casa", "Boutique Hotel", "Gran Hotel", "Posada"]
_HOTEL_NAMES = [
    "Bahía", "Getsemaní", "Andino", "Aurora", "Mirador", "Palma", "Colonial",
    "Océano", "Jardín", "Faro", "Alborada", "Sierra", "Marina", "Cumbre",
]
_AMENITIES = ["WiFi", "Desayuno", "Piscina", "Gimnasio", "Spa", "Parqueadero", "Aire acondicionado", "Vista al mar"]

_CAR_COMPANIES = ["Localiza", "Hertz", "Avis", "Budget", "Alamo", "Sixt", "Europcar"]
_CAR_MODELS = {
    "Económico": [("Chevrolet Spark", 4), ("Kia Picanto", 4), ("Renault Kwid", 4)],
    "Compacto": [("Chevrolet Onix", 5), ("Mazda 2", 5), ("Renault Logan", 5)],
    "SUV": [("Renault Duster", 5), ("Toyota Corolla Cross", 5), ("Kia Sportage", 5)],
    "Van": [("Hyundai H1", 9), ("Toyota Hiace", 12)],
}
_CAR_DAILY = {"Económico": (18, 32), "Compacto": (26, 44), "SUV": (48, 85), "Van": (70, 120)}


def _seed(*parts: object) -> int:
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:16], 16)


def _price_factor(*key: object, now: float | None = None) -> float:
    bucket = int((time.time() if now is None else now) // PRICE_BUCKET_SECONDS)
    return random.Random(_seed("price", bucket, *key)).uniform(0.93, 1.08)


def format_price(value: float, style: int) -> str:
    """Cuatro formatos distintos a proposito: el scraper debe limpiarlos."""
    us = f"{value:,.2f}"
    eu = us.replace(",", "X").replace(".", ",").replace("X", ".")
    if style == 0:
        return f"US$ {us}"
    if style == 1:
        return f"USD {us}"
    if style == 2:
        return f"{eu} US$"
    return f"US${value:,.0f}"


def _hhmm(minutes: int) -> str:
    minutes %= 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _flight_class(origin: str, destination: str) -> str:
    o, d = AIRPORTS[origin][1], AIRPORTS[destination][1]
    if o == "CO" and d == "CO":
        return "domestic"
    if "ES" in (o, d):
        return "long"
    return "regional"


def generate_flights(origin: str, destination: str, day: date, *, now: float | None = None) -> list[dict]:
    rng = random.Random(_seed("flights", origin, destination, day.isoformat()))
    klass = _flight_class(origin, destination)
    cfg = _FLIGHT_CLASSES[klass]
    total = rng.randint(18, 30)
    used: set[tuple[str, int]] = set()
    items: list[dict] = []
    for _ in range(total):
        code, airline = rng.choice(AIRLINES)
        number = rng.randint(100, 9899)
        while (code, number) in used:
            number = rng.randint(100, 9899)
        used.add((code, number))
        if klass == "domestic":
            stops = rng.choices([0, 1], [0.85, 0.15])[0]
        else:
            stops = rng.choices([0, 1, 2], [0.6, 0.3, 0.1])[0]
        duration = rng.randint(*cfg["duration"]) + stops * rng.randint(70, 170)
        dep_min = rng.randrange(5 * 60, 22 * 60, 5)
        arr_total = dep_min + duration
        day_offset = arr_total // 1440
        midnight = datetime.combine(day, datetime.min.time())
        dep_dt = midnight + timedelta(minutes=dep_min)
        arr_dt = midnight + timedelta(minutes=arr_total)
        base = rng.uniform(*cfg["price"]) * (1 - 0.08 * stops)
        cabin = rng.choices(["Economy", "Premium Economy", "Business"], [0.8, 0.12, 0.08])[0]
        cabin_factor = {"Economy": 1.0, "Premium Economy": 1.35, "Business": 2.4}[cabin]
        price = round(base * cabin_factor * _price_factor("flights", origin, destination, day, code, number, now=now), 2)
        h, m = divmod(duration, 60)
        items.append({
            "airline": airline, "code": code, "number": number,
            "dep_iso": dep_dt.strftime("%Y-%m-%dT%H:%M"), "dep_time": _hhmm(dep_min),
            "arr_iso": arr_dt.strftime("%Y-%m-%dT%H:%M"), "arr_time": _hhmm(arr_total),
            "day_offset": day_offset,
            "duration_text": f"{h}h {m:02d}m" if h else f"{m}m",
            "stops_text": "Directo" if stops == 0 else f"{stops} escala{'s' if stops > 1 else ''}",
            "cabin": cabin,
            "price_text": format_price(price, rng.randrange(4)),
            "offer_id": f"mt-{_seed('f', origin, destination, day, code, number):x}"[:20],
        })
    return items


def generate_hotels(city: str, checkin: date, nights: int, *, now: float | None = None) -> list[dict]:
    rng = random.Random(_seed("hotels", city, checkin.isoformat(), nights))
    total = rng.randint(16, 28)
    combos = list(itertools.product(_HOTEL_PREFIX, _HOTEL_NAMES))
    chosen = rng.sample(combos, k=min(total, len(combos)))
    city_name = AIRPORTS[city][0]
    factor_city = _CITY_PRICE_FACTOR.get(city, 1.0)
    items: list[dict] = []
    for prefix, name in chosen:
        stars = rng.choices([2, 3, 4, 5], [0.15, 0.35, 0.35, 0.15])[0]
        rating = round(min(9.8, max(5.5, rng.gauss(6.0 + stars * 0.6, 0.6))), 1)
        base = (25 + stars * 24 + rng.uniform(-8, 12)) * factor_city
        hotel_name = f"{prefix} {name} {city_name}"
        price = round(base * _price_factor("hotels", city, checkin, hotel_name, now=now), 2)
        amenities = rng.sample(_AMENITIES, k=rng.randint(2, 5))
        items.append({
            "name": hotel_name, "stars": stars,
            "rating_text": f"{rating:.1f}".replace(".", ","),  # coma decimal, como en muchos sitios
            "amenities": amenities,
            "price_text": format_price(price, rng.randrange(4)),
            "offer_id": f"mt-{_seed('h', city, checkin, hotel_name):x}"[:20],
        })
    return items


def generate_cars(city: str, pickup: date, days: int, *, now: float | None = None) -> list[dict]:
    rng = random.Random(_seed("cars", city, pickup.isoformat(), days))
    total = rng.randint(14, 26)
    combos = [
        (company, category, model, seats, trans)
        for company in _CAR_COMPANIES
        for category, models in _CAR_MODELS.items()
        for model, seats in models
        for trans in ("Manual", "Automático")
    ]
    chosen = rng.sample(combos, k=min(total, len(combos)))
    factor_city = _CITY_PRICE_FACTOR.get(city, 1.0)
    items: list[dict] = []
    for company, category, model, seats, trans in chosen:
        base = rng.uniform(*_CAR_DAILY[category]) * factor_city * (1.12 if trans == "Automático" else 1.0)
        price = round(base * _price_factor("cars", city, pickup, company, model, trans, now=now), 2)
        items.append({
            "company": company, "model": f"{model} o similar", "category": category,
            "transmission": trans, "seats_text": f"{seats} asientos",
            "price_text": format_price(price, rng.randrange(4)),
            "offer_id": f"mt-{_seed('c', city, pickup, company, model, trans):x}"[:20],
        })
    return items


def paginate(items: list[dict], page: int) -> tuple[list[dict], int]:
    total_pages = max(1, -(-len(items) // PAGE_SIZE))
    start = (page - 1) * PAGE_SIZE
    return items[start:start + PAGE_SIZE], total_pages
