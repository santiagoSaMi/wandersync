"""Punto de entrada del contenedor 'ingestion': ejecuta una ingesta al arrancar y deja el
flow programado (cron) en Prefect. Desde la UI (http://localhost:4200) puedes lanzarlo a mano."""
from __future__ import annotations

import logging
import socket
import sys
import time
import urllib.request
from urllib.parse import urlparse

from .config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ingestion.serve")


def wait_http(url: str, timeout: int = 180) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3):
                return
        except Exception:
            time.sleep(2)
    raise SystemExit(f"Timeout esperando {url}")


def wait_tcp(host: str, port: int, timeout: int = 180) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=3):
                return
        except OSError:
            time.sleep(2)
    raise SystemExit(f"Timeout esperando {host}:{port}")


def main() -> int:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_key:
        log.error("Faltan SUPABASE_URL / SUPABASE_SERVICE_KEY. Revisa tu .env")
        return 1
    log.info("Esperando Prefect (%s) y Dask (%s)...", settings.prefect_api_url, settings.dask_address)
    wait_http(settings.prefect_api_url.rstrip("/") + "/health")
    dask = urlparse(settings.dask_address)
    wait_tcp(dask.hostname, dask.port)

    from .flows import ingest_catalog  # import tardio: requiere Prefect listo

    if settings.run_on_startup:
        log.info("Primera ingesta al arrancar...")
        try:
            ingest_catalog()
        except Exception:
            log.exception("La primera ingesta fallo; el flow queda programado igualmente (cron: %s)", settings.cron)

    log.info("Flow programado con cron '%s'. Tambien puedes lanzarlo desde la UI de Prefect.", settings.cron)
    ingest_catalog.serve(name="catalog-ingestion", cron=settings.cron, tags=["wandersync"], pause_on_shutdown=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
