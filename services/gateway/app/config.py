from __future__ import annotations

import os

from .netutil import parse_cidrs


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() == "true"


class Settings:
    supabase_url: str = os.environ.get("SUPABASE_URL", "").rstrip("/")
    supabase_anon_key: str = os.environ.get("SUPABASE_ANON_KEY", "")
    internal_token: str = os.environ["INTERNAL_TOKEN"]
    auth_url: str = os.getenv("AUTH_URL", "http://auth:8000")
    orders_url: str = os.getenv("ORDERS_URL", "http://orders:8000")
    redis_url: str = os.environ["REDIS_URL"]
    cookie_name: str = "ws_sid"
    cookie_secure: bool = _bool("COOKIE_SECURE")
    graphiql: bool = _bool("GRAPHIQL")
    # Traza de consultas a Supabase en `extensions` (demostrar que no hay over-fetching). Por defecto = GRAPHIQL.
    supabase_query_trace: bool = _bool("SUPABASE_QUERY_TRACE", os.getenv("GRAPHIQL", "false"))
    allow_failure_simulation: bool = _bool("ALLOW_FAILURE_SIMULATION")
    # Modo presentacion (slowMotion en createBooking): pausa por paso de la SAGA. Solo en modo demo.
    demo_step_delay_ms: int = int(os.getenv("DEMO_STEP_DELAY_MS", "2500"))
    cors_origins: list[str] = [o.strip() for o in os.getenv(
        "CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",") if o.strip()]
    # Solo se cree X-Real-IP si la conexion viene de estos proxies. Por defecto: de NINGUNO (seguro ante
    # configuraciones incompletas). En el compose: TRUSTED_PROXY_HOSTS=frontend (el nginx).
    trusted_proxy_cidrs = parse_cidrs(os.getenv("TRUSTED_PROXY_CIDRS", ""))
    trusted_proxy_hosts: list[str] = [h.strip() for h in os.getenv("TRUSTED_PROXY_HOSTS", "").split(",") if h.strip()]
    max_query_depth: int = int(os.getenv("MAX_QUERY_DEPTH", "12"))
    # Sin limite de alias, UNA peticion puede repetir `packages` N veces => N llamadas a Supabase.
    # El frontend usa 0 alias y <= 80 tokens por consulta.
    max_query_aliases: int = int(os.getenv("MAX_QUERY_ALIASES", "5"))
    max_query_tokens: int = int(os.getenv("MAX_QUERY_TOKENS", "1000"))


settings = Settings()

# (peticiones, ventana en segundos). Ver docs/SEGURIDAD.md
RATE_POLICIES: dict[str, tuple[int, int]] = {
    "graphql": (240, 60),      # tope global por IP a /graphql
    "login": (10, 60),         # por IP  (ademas: bloqueo por cuenta en el servicio auth)
    "register": (10, 3600),    # por IP
    "checkout": (5, 60),       # por usuario: reservas / pago
}
