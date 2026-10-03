"""Escritura en Supabase Cloud via PostgREST (HTTPS) con la clave secreta / service_role.

Se usa la API REST (no una conexion Postgres directa) porque funciona por IPv4/HTTPS desde
cualquier red y solo requiere URL + clave. El upsert es idempotente: on_conflict=id.
"""
from __future__ import annotations

import logging
from datetime import date

import httpx

from .config import Settings, auth_headers
from .parsing import dedupe_by_id

log = logging.getLogger("ingestion.supabase")

TABLES = {"flights": "flight_offers", "hotels": "hotel_offers", "cars": "car_offers"}
PAST_COLUMN = {"flight_offers": "departure_date", "hotel_offers": "checkin", "car_offers": "pickup_date"}


class SupabaseWriter:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        if not settings.supabase_url or not settings.supabase_service_key:
            raise RuntimeError("Faltan SUPABASE_URL / SUPABASE_SERVICE_KEY en el entorno (.env)")
        self.base = f"{settings.supabase_url}/rest/v1"
        self.headers = auth_headers(settings.supabase_service_key)
        self.chunk = settings.page_size_upsert
        self.client = client or httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0))

    def upsert(self, table: str, rows: list[dict]) -> int:
        rows = dedupe_by_id(rows)
        for start in range(0, len(rows), self.chunk):
            batch = rows[start:start + self.chunk]
            resp = self.client.post(
                f"{self.base}/{table}", params={"on_conflict": "id"}, json=batch,
                headers={**self.headers, "Content-Type": "application/json",
                         "Prefer": "resolution=merge-duplicates,return=minimal"},
            )
            if resp.status_code >= 300:
                raise RuntimeError(f"Supabase upsert {table} fallo: HTTP {resp.status_code} {resp.text[:300]}")
        return len(rows)

    def purge_past(self, today: date | None = None) -> None:
        """Elimina ofertas de fechas ya pasadas (el catalogo solo debe tener futuro)."""
        today = (today or date.today()).isoformat()
        for table, column in PAST_COLUMN.items():
            resp = self.client.delete(
                f"{self.base}/{table}", params={column: f"lt.{today}"},
                headers={**self.headers, "Prefer": "return=minimal"},
            )
            if resp.status_code >= 300:
                log.warning("purge %s fallo: HTTP %s %s", table, resp.status_code, resp.text[:200])

    def record_run(self, run: dict) -> None:
        resp = self.client.post(
            f"{self.base}/ingestion_runs", params={"on_conflict": "id"}, json=[run],
            headers={**self.headers, "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates,return=minimal"},
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"Supabase record_run fallo: HTTP {resp.status_code} {resp.text[:300]}")
