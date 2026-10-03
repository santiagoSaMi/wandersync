"""Configuracion de la ingesta (variables de entorno). Sin dependencias pesadas."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta

# Ciudades por codigo IATA (nombre para mostrar). El sitio simulado usa los mismos codigos.
CITY_NAMES = {
    "BOG": "Bogotá", "MDE": "Medellín", "CTG": "Cartagena", "CLO": "Cali", "SMR": "Santa Marta",
    "ADZ": "San Andrés", "BAQ": "Barranquilla", "MIA": "Miami", "MAD": "Madrid",
    "CUN": "Cancún", "PTY": "Ciudad de Panamá",
}

DEFAULT_ROUTES = (
    "BOG-MDE,BOG-CTG,BOG-SMR,BOG-ADZ,BOG-CLO,MDE-CTG,CLO-CTG,BOG-MIA,BOG-MAD,BOG-CUN,MDE-MIA,BOG-PTY"
)


def _csv(name: str, default: str) -> list[str]:
    return [p.strip() for p in os.getenv(name, default).split(",") if p.strip()]


@dataclass(frozen=True)
class Settings:
    mock_source_url: str = field(default_factory=lambda: os.getenv("MOCK_SOURCE_URL", "http://mock-source:8090"))
    source_name: str = "mocktravel"
    supabase_url: str = field(default_factory=lambda: os.getenv("SUPABASE_URL", "").rstrip("/"))
    supabase_service_key: str = field(default_factory=lambda: os.getenv("SUPABASE_SERVICE_KEY", ""))
    dask_address: str = field(default_factory=lambda: os.getenv("DASK_SCHEDULER_ADDRESS", "tcp://dask-scheduler:8786"))
    prefect_api_url: str = field(default_factory=lambda: os.getenv("PREFECT_API_URL", "http://prefect-server:4200/api"))
    cron: str = field(default_factory=lambda: os.getenv("INGEST_CRON", "*/30 * * * *"))
    run_on_startup: bool = field(default_factory=lambda: os.getenv("INGEST_RUN_ON_STARTUP", "true").lower() == "true")
    stay_nights: int = field(default_factory=lambda: int(os.getenv("INGEST_STAY_NIGHTS", "3")))
    date_offsets: tuple[int, ...] = field(
        default_factory=lambda: tuple(int(x) for x in _csv("INGEST_DATE_OFFSETS", "7,14,21,28"))
    )
    routes: tuple[tuple[str, str], ...] = field(
        default_factory=lambda: tuple(
            (r.split("-")[0].upper(), r.split("-")[1].upper()) for r in _csv("INGEST_ROUTES", DEFAULT_ROUTES)
        )
    )
    http_timeout_s: float = 10.0
    page_size_upsert: int = 500


def auth_headers(key: str) -> dict[str, str]:
    """Las claves nuevas de Supabase (sb_secret_... / sb_publishable_...) NO son JWT: solo van
    en 'apikey'. Las legacy (anon / service_role, JWT 'eyJ...') tambien requieren
    'Authorization: Bearer'."""
    headers = {"apikey": key}
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


def get_settings() -> Settings:
    return Settings()


def build_queries(settings: Settings, today: date | None = None, date_offsets: list[int] | None = None) -> list[dict]:
    """Una consulta = una unidad de trabajo para Dask (todas sus paginas)."""
    today = today or date.today()
    offsets = date_offsets or list(settings.date_offsets)
    days = [(today + timedelta(days=o)).isoformat() for o in offsets]
    cities = sorted({dest for _, dest in settings.routes})
    queries: list[dict] = []
    for day in days:
        for origin, destination in settings.routes:
            queries.append({"vertical": "flights", "origin": origin, "destination": destination, "date": day})
        for city in cities:
            queries.append({"vertical": "hotels", "city": city, "checkin": day, "nights": settings.stay_nights})
            queries.append({"vertical": "cars", "city": city, "pickup": day, "days": settings.stay_nights})
    return queries
