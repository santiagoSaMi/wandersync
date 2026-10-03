"""Extract (descarga de paginas) y Transform (HTML -> filas limpias) de una consulta contra la fuente.
Son funciones separadas porque en el flow son TAREAS separadas, con politicas de reintento distintas."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from . import parsing
from .config import CITY_NAMES, Settings

VERTICAL_PATH = {"flights": "/flights", "hotels": "/hotels", "cars": "/cars"}


class SourceHTTPError(Exception):
    """La fuente respondio con un error HTTP (5xx/429/...). Se usa en lugar de httpx.HTTPStatusError porque
    esa excepcion NO sobrevive al pickle con que Dask devuelve el error del worker al flow: su __init__ exige
    request/response como argumentos de palabra clave, y el flow veria un TypeError en vez del error real."""


def _request_params(query: dict, page: int) -> dict:
    v = query["vertical"]
    if v == "flights":
        return {"origin": query["origin"], "destination": query["destination"], "date": query["date"], "page": page}
    if v == "hotels":
        return {"city": query["city"], "checkin": query["checkin"], "nights": query["nights"], "page": page}
    if v == "cars":
        return {"city": query["city"], "pickup": query["pickup"], "days": query["days"], "page": page}
    raise ValueError(f"vertical desconocida: {v}")


def _parse(query: dict, html: str, settings: Settings, scraped_at: str) -> parsing.ParsedPage:
    common = dict(city_names=CITY_NAMES, source=settings.source_name, scraped_at=scraped_at)
    v = query["vertical"]
    if v == "flights":
        return parsing.parse_flights(html, origin=query["origin"], destination=query["destination"],
                                     day=query["date"], **common)
    if v == "hotels":
        return parsing.parse_hotels(html, city=query["city"], checkin=query["checkin"],
                                    nights=query["nights"], **common)
    return parsing.parse_cars(html, city=query["city"], pickup=query["pickup"], days=query["days"], **common)


def fetch_pages(query: dict, settings: Settings, client: httpx.Client | None = None) -> list[str]:
    """EXTRACT: descarga TODAS las paginas de la consulta (HTML crudo). Lanza excepcion ante fallos de red/HTTP o
    si una pagina no tiene la estructura esperada, para que Prefect reintente la extraccion completa."""
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(settings.http_timeout_s, connect=3.0),
                                    headers={"User-Agent": "WanderSync-Ingestion/1.0"})
    url = settings.mock_source_url + VERTICAL_PATH[query["vertical"]]
    pages: list[str] = []
    try:
        page_no, total_pages = 1, 1
        while page_no <= total_pages:
            resp = client.get(url, params=_request_params(query, page_no))
            if resp.is_error:         # 5xx / 429 => excepcion => reintento de Prefect
                raise SourceHTTPError(f"HTTP {resp.status_code} {resp.reason_phrase} en {resp.url}")
            total_pages, _ = parsing.page_info(resp.text)          # ParseError => reintento
            pages.append(resp.text)
            page_no += 1
    finally:
        if own:
            client.close()
    return pages


def parse_pages(query: dict, pages: list[str], settings: Settings, scraped_at: str) -> tuple[list[dict], int]:
    """TRANSFORM: HTML crudo -> filas limpias. Determinista (no se reintenta). Devuelve (filas, descartadas)."""
    rows: list[dict] = []
    dropped = 0
    for html in pages:
        parsed = _parse(query, html, settings, scraped_at)
        rows.extend(parsed.rows)
        dropped += parsed.dropped
    return rows, dropped


def scrape_query(query: dict, settings: Settings, client: httpx.Client | None = None) -> tuple[list[dict], int]:
    """Extract + Transform de una consulta. Devuelve (filas limpias, filas descartadas)."""
    scraped_at = datetime.now(timezone.utc).isoformat()
    return parse_pages(query, fetch_pages(query, settings, client), settings, scraped_at)
