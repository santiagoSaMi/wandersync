"""Clientes hacia el catalogo (Supabase GraphQL nativo) y hacia los servicios internos."""
from __future__ import annotations

import json
import logging
import time

import httpx
from graphql import GraphQLError

from . import trace
from .config import settings

log = logging.getLogger("gateway.clients")


def gql_error(message: str, code: str, **extra) -> GraphQLError:
    return GraphQLError(message, extensions={"code": code, **extra})


def supabase_headers(key: str) -> dict[str, str]:
    """sb_publishable_/sb_secret_ NO son JWT: solo 'apikey'. Las legacy (JWT 'eyJ...') tambien Bearer."""
    headers = {"apikey": key, "Content-Type": "application/json"}
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


# =============================================================================== Supabase
class CatalogClient:
    """Lee el catalogo a traves del endpoint GraphQL NATIVO de Supabase (pg_graphql) con la clave
    publica (rol anon): solo SELECT, protegido por RLS. Las columnas que se piden vienen de una
    lista blanca (ver selection.py), nunca de texto libre del cliente."""

    def __init__(self, url: str, anon_key: str):
        self.endpoint = f"{url}/graphql/v1"
        self.headers = supabase_headers(anon_key)
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0))
        self._status_cache: tuple[float, dict] | None = None

    async def close(self) -> None:
        await self.http.aclose()

    async def execute(self, query: str, variables: dict, collections: dict[str, list[str]]) -> dict:
        """`collections` = {tabla: columnas pedidas}: se registra en la traza (extensions.supabase)."""
        if not settings.supabase_url or not settings.supabase_anon_key:
            raise gql_error("El catálogo no está configurado.", "CATALOG_NOT_CONFIGURED")
        last: Exception | None = None
        for _ in range(2):                                  # 1 reintento ante fallos de red
            try:
                resp = await self.http.post(self.endpoint, json={"query": query, "variables": variables},
                                            headers=self.headers)
                break
            except httpx.TransportError as exc:
                last = exc
        else:
            log.error("catalogo inaccesible: %s", last)
            raise gql_error("El catálogo no está disponible en este momento.", "CATALOG_UNAVAILABLE")
        if resp.status_code != 200:
            log.error("supabase graphql HTTP %s: %s", resp.status_code, resp.text[:300])
            raise gql_error("El catálogo no está disponible en este momento.", "CATALOG_UNAVAILABLE")
        body = resp.json()
        if body.get("errors"):
            log.error("supabase graphql errors: %s", json.dumps(body["errors"])[:500])
            raise gql_error("No se pudo consultar el catálogo.", "CATALOG_ERROR")
        trace.record(collections, query=query, variables=variables)
        return body["data"]

    @staticmethod
    def _nodes(data: dict, alias: str) -> list[dict]:
        return [e["node"] for e in ((data.get(alias) or {}).get("edges") or [])]

    async def package_inputs(self, origin: str, destination: str, day: str,
                             fcols: list[str], hcols: list[str], ccols: list[str], first: int) -> dict:
        """Una sola peticion GraphQL a Supabase con 3 colecciones (alias) para armar paquetes."""
        query = f"""
        query Inputs($o: String!, $d: String!, $day: Date!, $n: Int!) {{
          flights: flight_offersCollection(
            filter: {{origin: {{eq: $o}}, destination: {{eq: $d}}, departure_date: {{eq: $day}}}},
            orderBy: [{{price_cents: AscNullsLast}}], first: $n) {{ edges {{ node {{ {' '.join(fcols)} }} }} }}
          hotels: hotel_offersCollection(
            filter: {{city_code: {{eq: $d}}, checkin: {{eq: $day}}}},
            orderBy: [{{total_price_cents: AscNullsLast}}], first: $n) {{ edges {{ node {{ {' '.join(hcols)} }} }} }}
          cars: car_offersCollection(
            filter: {{city_code: {{eq: $d}}, pickup_date: {{eq: $day}}}},
            orderBy: [{{total_price_cents: AscNullsLast}}], first: $n) {{ edges {{ node {{ {' '.join(ccols)} }} }} }}
        }}"""
        data = await self.execute(query, {"o": origin, "d": destination, "day": day, "n": first},
                                  {"flight_offers": fcols, "hotel_offers": hcols, "car_offers": ccols})
        return {k: self._nodes(data, k) for k in ("flights", "hotels", "cars")}

    async def flights(self, origin: str, destination: str, day: str, cols: list[str], first: int) -> list[dict]:
        query = f"""query($o: String!, $d: String!, $day: Date!, $n: Int!) {{
          r: flight_offersCollection(
            filter: {{origin: {{eq: $o}}, destination: {{eq: $d}}, departure_date: {{eq: $day}}}},
            orderBy: [{{price_cents: AscNullsLast}}], first: $n) {{ edges {{ node {{ {' '.join(cols)} }} }} }} }}"""
        variables = {"o": origin, "d": destination, "day": day, "n": first}
        return self._nodes(await self.execute(query, variables, {"flight_offers": cols}), "r")

    async def hotels(self, city: str, day: str, cols: list[str], first: int) -> list[dict]:
        query = f"""query($c: String!, $day: Date!, $n: Int!) {{
          r: hotel_offersCollection(
            filter: {{city_code: {{eq: $c}}, checkin: {{eq: $day}}}},
            orderBy: [{{total_price_cents: AscNullsLast}}], first: $n) {{ edges {{ node {{ {' '.join(cols)} }} }} }} }}"""
        return self._nodes(await self.execute(query, {"c": city, "day": day, "n": first}, {"hotel_offers": cols}), "r")

    async def cars(self, city: str, day: str, cols: list[str], first: int) -> list[dict]:
        query = f"""query($c: String!, $day: Date!, $n: Int!) {{
          r: car_offersCollection(
            filter: {{city_code: {{eq: $c}}, pickup_date: {{eq: $day}}}},
            orderBy: [{{total_price_cents: AscNullsLast}}], first: $n) {{ edges {{ node {{ {' '.join(cols)} }} }} }} }}"""
        return self._nodes(await self.execute(query, {"c": city, "day": day, "n": first}, {"car_offers": cols}), "r")

    # Columnas que el checkout relee de la fuente de verdad (la consulta de offers_by_ids se arma con ellas)
    OFFER_COLUMNS = {
        "flight_offers": ["id", "origin", "destination", "airline", "flight_number", "departure_date",
                          "departure_time", "arrival_time", "stops", "price_cents", "currency"],
        "hotel_offers": ["id", "city_code", "name", "stars", "checkin", "nights", "total_price_cents", "currency"],
        "car_offers": ["id", "city_code", "company", "model", "category", "transmission", "pickup_date", "days",
                       "total_price_cents", "currency"],
    }

    async def offers_by_ids(self, flight_id: str, hotel_id: str, car_id: str) -> dict:
        """Recupera las 3 ofertas ELEGIDAS desde la fuente de verdad (nunca se confia en precios del cliente)."""
        cols = {table: " ".join(columns) for table, columns in self.OFFER_COLUMNS.items()}
        query = f"""query($f: String!, $h: String!, $c: String!) {{
          flight: flight_offersCollection(filter: {{id: {{eq: $f}}}}, first: 1) {{ edges {{ node {{ {cols['flight_offers']} }} }} }}
          hotel: hotel_offersCollection(filter: {{id: {{eq: $h}}}}, first: 1) {{ edges {{ node {{ {cols['hotel_offers']} }} }} }}
          car: car_offersCollection(filter: {{id: {{eq: $c}}}}, first: 1) {{ edges {{ node {{ {cols['car_offers']} }} }} }}
        }}"""
        data = await self.execute(query, {"f": flight_id, "h": hotel_id, "c": car_id}, self.OFFER_COLUMNS)
        pick = lambda alias: (self._nodes(data, alias) or [None])[0]  # noqa: E731
        return {"flight": pick("flight"), "hotel": pick("hotel"), "car": pick("car")}

    async def status(self) -> dict:
        """Estado del catalogo (conteos + ultima ingesta). Cache de 30 s."""
        if self._status_cache and time.time() - self._status_cache[0] < 30:
            trace.record({"ingestion_runs": [], "flight_offers": [], "hotel_offers": [], "car_offers": []},
                         cached=True, note="servido desde la cache de 30 s: no se consulto Supabase")
            return self._status_cache[1]
        query = """query {
          runs: ingestion_runsCollection(filter: {status: {in: ["SUCCESS", "PARTIAL"]}},
                orderBy: [{finished_at: DescNullsLast}], first: 1) { edges { node {
                id finished_at status failed_queries meta } } }
          flights: flight_offersCollection(first: 1) { totalCount }
          hotels: hotel_offersCollection(first: 1) { totalCount }
          cars: car_offersCollection(first: 1) { totalCount }
        }"""
        data = await self.execute(query, {}, {
            "ingestion_runs": ["id", "finished_at", "status", "failed_queries", "meta"],
            "flight_offers": ["totalCount"], "hotel_offers": ["totalCount"], "car_offers": ["totalCount"]})
        run = (self._nodes(data, "runs") or [None])[0]
        meta = (run or {}).get("meta") or {}
        if isinstance(meta, str):          # pg_graphql puede serializar el JSON como texto
            meta = json.loads(meta)
        result = {
            "flights": (data.get("flights") or {}).get("totalCount", 0),
            "hotels": (data.get("hotels") or {}).get("totalCount", 0),
            "cars": (data.get("cars") or {}).get("totalCount", 0),
            "run": run, "meta": meta,
        }
        self._status_cache = (time.time(), result)
        return result


# =============================================================================== servicios internos
class InternalApi:
    def __init__(self, base_url: str, token: str):
        self.http = httpx.AsyncClient(base_url=base_url, headers={"X-Internal-Token": token},
                                      timeout=httpx.Timeout(30.0, connect=3.0))

    async def close(self) -> None:
        await self.http.aclose()

    async def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            return await self.http.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            log.error("servicio interno inaccesible %s %s: %s", method, path, exc)
            raise gql_error("Servicio temporalmente no disponible.", "SERVICE_UNAVAILABLE")


class AuthApi(InternalApi):
    async def anonymous(self) -> str:
        r = await self._send("POST", "/sessions/anonymous")
        r.raise_for_status()
        return r.json()["session_id"]

    async def resolve(self, session_id: str) -> dict:
        r = await self._send("POST", "/sessions/resolve", json={"session_id": session_id})
        r.raise_for_status()
        return r.json()

    async def register(self, email: str, password: str, full_name: str) -> dict:
        r = await self._send("POST", "/register", json={"email": email, "password": password, "full_name": full_name})
        if r.status_code == 201:
            return r.json()
        if r.status_code == 409:
            raise gql_error("Ya existe una cuenta con ese correo.", "EMAIL_TAKEN")
        if r.status_code == 422:
            detail = r.json().get("detail")
            if detail == "weak_password":
                raise gql_error("La contraseña es débil: usa 12 o más caracteres, mezcla letras y números "
                                "y no incluyas tu correo.", "WEAK_PASSWORD")
            raise gql_error("Revisa los datos ingresados (correo válido y contraseña de 12 a 128 caracteres).",
                            "VALIDATION_ERROR")
        log.error("auth.register HTTP %s %s", r.status_code, r.text[:200])
        raise gql_error("No se pudo crear la cuenta.", "SERVICE_ERROR")

    async def login(self, email: str, password: str, current_session_id: str | None) -> dict:
        r = await self._send("POST", "/login", json={"email": email, "password": password,
                                                     "current_session_id": current_session_id})
        if r.status_code == 200:
            return r.json()
        if r.status_code == 401:
            raise gql_error("Correo o contraseña incorrectos.", "INVALID_CREDENTIALS")
        if r.status_code == 429:
            raise gql_error("Demasiados intentos. Espera unos minutos antes de volver a intentar.",
                            "RATE_LIMITED", retryAfter=int(r.headers.get("Retry-After", "60")))
        if r.status_code == 422:
            raise gql_error("Revisa los datos ingresados.", "VALIDATION_ERROR")
        log.error("auth.login HTTP %s %s", r.status_code, r.text[:200])
        raise gql_error("No se pudo iniciar sesión.", "SERVICE_ERROR")

    async def logout(self, session_id: str) -> None:
        await self._send("POST", "/logout", json={"session_id": session_id})


class OrdersApi(InternalApi):
    async def create(self, payload: dict) -> dict:
        r = await self._send("POST", "/orders", json=payload)
        if r.status_code in (200, 201, 202):        # 202: la SAGA sigue; la orden vuelve PENDING y el cliente consulta
            return r.json()
        if r.status_code == 409 and isinstance((detail := r.json().get("detail")), dict) \
                and detail.get("code") == "insufficient_funds":
            raise gql_error(
                f"Saldo insuficiente: tienes {_usd(detail['balance_cents'])} en tu billetera y el paquete cuesta "
                f"{_usd(detail['required_cents'])}.", "INSUFFICIENT_FUNDS",
                balanceCents=detail["balance_cents"], requiredCents=detail["required_cents"])
        log.error("orders.create HTTP %s %s", r.status_code, r.text[:300])
        raise gql_error("No se pudo procesar la reserva.", "SERVICE_ERROR")

    # include_steps: el gateway solo pide la bitacora de la SAGA si el cliente selecciono `steps` (sin over-fetching)
    async def get(self, order_id: str, user_id: str, include_steps: bool = True) -> dict | None:
        r = await self._send("GET", f"/orders/{order_id}",
                             params={"user_id": user_id, "include_steps": str(include_steps).lower()})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def list(self, user_id: str, include_steps: bool = True) -> list[dict]:
        r = await self._send("GET", "/orders", params={"user_id": user_id, "include_steps": str(include_steps).lower()})
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------ billetera
    async def wallet(self, user_id: str) -> dict:
        r = await self._send("GET", f"/wallets/{user_id}")
        r.raise_for_status()
        return r.json()

    async def open_wallet(self, user_id: str) -> dict:
        r = await self._send("POST", f"/wallets/{user_id}")
        r.raise_for_status()
        return r.json()

    async def demo_set_balance(self, user_id: str, balance_cents: int) -> dict:
        r = await self._send("POST", f"/wallets/{user_id}/demo-balance", json={"balance_cents": balance_cents})
        if r.status_code == 403:
            raise gql_error("El modo demostración está desactivado.", "DEMO_DISABLED")
        r.raise_for_status()
        return r.json()


def _usd(cents: int) -> str:
    return f"US$ {cents / 100:,.2f}"
