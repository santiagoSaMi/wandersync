"""Parseo y LIMPIEZA de paginas HTML (BeautifulSoup). Funciones puras: sin red, sin Prefect.

Aqui esta la etapa "Transform" del ETL: precios en 4 formatos, duraciones "2h 15m",
escalas en texto, puntuaciones con coma decimal, normalizacion de IDs estables, etc.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from bs4 import BeautifulSoup


class ParseError(ValueError):
    """La pagina no tiene la estructura esperada (cambio del sitio, respuesta corrupta...)."""


@dataclass
class ParsedPage:
    rows: list[dict] = field(default_factory=list)
    total_pages: int = 1
    total_results: int = 0
    dropped: int = 0  # filas descartadas por no pasar la validacion de calidad


# ----------------------------------------------------------------- limpieza de campos
def parse_price_to_cents(text: str) -> int:
    """'US$ 1,234.50' | 'USD 1,234.50' | '1.234,50 US$' | 'US$1,234' -> centavos (int)."""
    cleaned = re.sub(r"[^\d.,]", "", text or "")
    if not re.search(r"\d", cleaned):
        raise ParseError(f"precio sin digitos: {text!r}")
    last_dot, last_comma = cleaned.rfind("."), cleaned.rfind(",")
    decimal_sep = None
    if last_dot != -1 and last_comma != -1:
        decimal_sep = "." if last_dot > last_comma else ","
    elif last_dot != -1 or last_comma != -1:
        sep = "." if last_dot != -1 else ","
        digits_after = len(cleaned) - cleaned.rfind(sep) - 1
        if cleaned.count(sep) == 1 and digits_after in (1, 2):
            decimal_sep = sep
    if decimal_sep:
        int_part, frac = cleaned.rsplit(decimal_sep, 1)
    else:
        int_part, frac = cleaned, "0"
    int_part = re.sub(r"[.,]", "", int_part) or "0"
    return int(int_part) * 100 + int((frac + "00")[:2])


def detect_currency(text: str) -> str:
    upper = (text or "").upper()
    if "US$" in upper or "USD" in upper or "$" in upper:
        return "USD"
    raise ParseError(f"moneda no soportada: {text!r}")


def parse_duration_minutes(text: str) -> int:
    """'2h 15m' -> 135, '45m' -> 45, '12h' -> 720."""
    m = re.fullmatch(r"\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*", text or "")
    if not m or not (m.group(1) or m.group(2)):
        raise ParseError(f"duracion invalida: {text!r}")
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def parse_stops(text: str) -> int:
    t = (text or "").strip().lower()
    if t in ("directo", "sin escalas", "nonstop"):
        return 0
    m = re.match(r"(\d+)\s*escala", t)
    if not m:
        raise ParseError(f"escalas invalidas: {text!r}")
    return int(m.group(1))


def parse_rating(text: str) -> float | None:
    t = (text or "").strip().replace(",", ".")
    try:
        value = float(t)
    except ValueError:
        return None
    return value if 0 <= value <= 10 else None


def stable_id(prefix: str, *parts: object) -> str:
    """ID determinista a partir de la clave natural: mismo item => mismo id en cada ingesta."""
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha1(raw).hexdigest()[:16]}"


def _text(node, selector: str) -> str:
    el = node.select_one(selector)
    if el is None:
        raise ParseError(f"falta el elemento {selector!r}")
    return el.get_text(" ", strip=True)


def _pagination(soup: BeautifulSoup) -> tuple[int, int]:
    nav = soup.select_one("nav.pagination")
    main = soup.select_one("main.results-page")
    if nav is None or main is None:
        raise ParseError("la pagina no tiene la estructura de resultados esperada")
    return int(nav.get("data-total-pages", "1")), int(main.get("data-total-results", "0"))


def page_info(html: str) -> tuple[int, int]:
    """(total de paginas, total de resultados). Lanza ParseError si la pagina no tiene la estructura esperada
    (mantenimiento, bloqueo, cambio del sitio): en la etapa Extract eso provoca un reintento."""
    return _pagination(BeautifulSoup(html, "html.parser"))


def _cards(soup: BeautifulSoup, klass: str):
    return soup.select(f"li.result-card.{klass}")


# ----------------------------------------------------------------- vuelos
def parse_flights(html: str, *, origin: str, destination: str, day: str, city_names: dict[str, str],
                  source: str, scraped_at: str) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    total_pages, total_results = _pagination(soup)
    page = ParsedPage(total_pages=total_pages, total_results=total_results)
    for card in _cards(soup, "flight"):
        try:
            airline = _text(card, ".airline-name")
            flight_number = re.sub(r"\s+", " ", _text(card, ".flight-code"))
            dep_iso = card.select_one("time.dep")["datetime"]      # 2026-10-15T06:40
            arr_iso = card.select_one("time.arr")["datetime"]
            price_text = _text(card, ".fare .price")
            price_cents = parse_price_to_cents(price_text)
            if price_cents <= 0:
                raise ParseError("precio no positivo")
            page.rows.append({
                "id": stable_id("fl", source, origin, destination, day, flight_number),
                "source": source,
                "origin": origin, "destination": destination,
                "origin_city": city_names.get(origin, origin),
                "destination_city": city_names.get(destination, destination),
                "airline": airline, "flight_number": flight_number,
                "departure_date": day,
                "departure_time": dep_iso[11:16], "arrival_time": arr_iso[11:16],
                "duration_minutes": parse_duration_minutes(_text(card, ".duration")),
                "stops": parse_stops(_text(card, ".stops")),
                "cabin": _text(card, ".fare .cabin").upper().replace(" ", "_"),
                "price_cents": price_cents, "currency": detect_currency(price_text),
                "scraped_at": scraped_at,
            })
        except (ParseError, TypeError, KeyError):
            page.dropped += 1
    return page


# ----------------------------------------------------------------- hoteles
def parse_hotels(html: str, *, city: str, checkin: str, nights: int, city_names: dict[str, str],
                 source: str, scraped_at: str) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    total_pages, total_results = _pagination(soup)
    page = ParsedPage(total_pages=total_pages, total_results=total_results)
    checkout = (date.fromisoformat(checkin) + timedelta(days=nights)).isoformat()
    for card in _cards(soup, "hotel"):
        try:
            name = _text(card, ".hotel-name")
            stars_el = card.select_one(".stars")
            stars = len(stars_el.get_text(strip=True)) if stars_el else 0
            if not 1 <= stars <= 5:
                raise ParseError("estrellas invalidas")
            price_text = _text(card, ".rate .price")
            per_night = parse_price_to_cents(price_text)
            if per_night <= 0:
                raise ParseError("precio no positivo")
            amenities = ",".join(li.get_text(strip=True) for li in card.select(".amenities li"))
            page.rows.append({
                "id": stable_id("ho", source, city, checkin, name),
                "source": source,
                "city_code": city, "city_name": city_names.get(city, city),
                "name": name, "stars": stars,
                "rating": parse_rating(_text(card, ".score")),
                "amenities": amenities,
                "checkin": checkin, "checkout": checkout, "nights": nights,
                "price_per_night_cents": per_night, "total_price_cents": per_night * nights,
                "currency": detect_currency(price_text),
                "scraped_at": scraped_at,
            })
        except (ParseError, TypeError, KeyError):
            page.dropped += 1
    return page


# ----------------------------------------------------------------- autos
_TRANSMISSION = {"manual": "MANUAL", "automático": "AUTOMATIC", "automatico": "AUTOMATIC"}


def parse_cars(html: str, *, city: str, pickup: str, days: int, city_names: dict[str, str],
               source: str, scraped_at: str) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    total_pages, total_results = _pagination(soup)
    page = ParsedPage(total_pages=total_pages, total_results=total_results)
    dropoff = (date.fromisoformat(pickup) + timedelta(days=days)).isoformat()
    for card in _cards(soup, "car"):
        try:
            company = _text(card, ".company")
            model = re.sub(r"\s+o similar$", "", _text(card, ".model"), flags=re.I)
            transmission = _TRANSMISSION[_text(card, ".transmission").lower()]
            seats = int(re.search(r"\d+", _text(card, ".seats")).group())
            price_text = _text(card, ".rate .price")
            per_day = parse_price_to_cents(price_text)
            if per_day <= 0:
                raise ParseError("precio no positivo")
            page.rows.append({
                "id": stable_id("ca", source, city, pickup, company, model, transmission),
                "source": source,
                "city_code": city, "city_name": city_names.get(city, city),
                "company": company, "model": model, "category": _text(card, ".category"),
                "transmission": transmission, "seats": seats,
                "pickup_date": pickup, "dropoff_date": dropoff, "days": days,
                "price_per_day_cents": per_day, "total_price_cents": per_day * days,
                "currency": detect_currency(price_text),
                "scraped_at": scraped_at,
            })
        except (ParseError, TypeError, KeyError, AttributeError):
            page.dropped += 1
    return page


def dedupe_by_id(rows: list[dict]) -> list[dict]:
    """PostgREST falla si un mismo id aparece dos veces en un upsert por lotes."""
    seen: dict[str, dict] = {}
    for row in rows:
        seen[row["id"]] = row
    return list(seen.values())
