"""Renderiza las paginas HTML del sitio simulado (sin depender de FastAPI: facil de testear)."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import generator as g

_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=select_autoescape(["html", "j2"]),
)


def _page_url(path: str, params: dict, page: int) -> str:
    return f"{path}?{urlencode({**params, 'page': page})}"


def render_index(sample_date: str) -> str:
    return _env.get_template("index.html.j2").render(sample_date=sample_date)


def render_flights(origin: str, destination: str, day: date, page: int) -> str:
    items = g.generate_flights(origin, destination, day)
    page_items, total_pages = g.paginate(items, page)
    params = {"origin": origin, "destination": destination, "date": day.isoformat()}
    return _env.get_template("flights.html.j2").render(
        origin=origin, destination=destination, day=day.isoformat(),
        origin_city=g.AIRPORTS[origin][0], destination_city=g.AIRPORTS[destination][0],
        total=len(items), items=page_items, page=page, total_pages=total_pages,
        prev_url=_page_url("/flights", params, page - 1), next_url=_page_url("/flights", params, page + 1),
    )


def render_hotels(city: str, checkin: date, nights: int, page: int) -> str:
    items = g.generate_hotels(city, checkin, nights)
    page_items, total_pages = g.paginate(items, page)
    params = {"city": city, "checkin": checkin.isoformat(), "nights": nights}
    return _env.get_template("hotels.html.j2").render(
        city=city, city_name=g.AIRPORTS[city][0], checkin=checkin.isoformat(), nights=nights,
        total=len(items), items=page_items, page=page, total_pages=total_pages,
        prev_url=_page_url("/hotels", params, page - 1), next_url=_page_url("/hotels", params, page + 1),
    )


def render_cars(city: str, pickup: date, days: int, page: int) -> str:
    items = g.generate_cars(city, pickup, days)
    page_items, total_pages = g.paginate(items, page)
    params = {"city": city, "pickup": pickup.isoformat(), "days": days}
    return _env.get_template("cars.html.j2").render(
        city=city, city_name=g.AIRPORTS[city][0], pickup=pickup.isoformat(), days=days,
        total=len(items), items=page_items, page=page, total_pages=total_pages,
        prev_url=_page_url("/cars", params, page - 1), next_url=_page_url("/cars", params, page + 1),
    )
