"""Esquema GraphQL del gateway (BFF). Agrega:
   - CATALOGO  -> Supabase (GraphQL nativo pg_graphql), solo lectura
   - IDENTIDAD -> servicio auth
   - RESERVAS  -> servicio orders (orquestador SAGA)
El frontend habla UNICAMENTE con este esquema."""
import logging
import re
from datetime import date, datetime
from typing import Optional

import strawberry
from graphql import GraphQLError
from strawberry.extensions import (DisableIntrospection, MaskErrors, MaxAliasesLimiter, MaxTokensLimiter,
                                   QueryDepthLimiter)
from strawberry.permission import BasePermission
from strawberry.types import Info

from .clients import gql_error
from .config import settings
from .context import Context
from .deps import auth_api, catalog, orders_api
from .packages import compose_packages
from .selection import child_selections, requested_columns
from .trace import SupabaseTrace
from .types import (BookingInput, CarOffer, CatalogStatus, FlightOffer, HotelOffer, LoginInput, Order, Wallet,
                    Package, RegisterInput, Route, User)

log = logging.getLogger("gateway.schema")

FLIGHT_COLS = frozenset({"id", "origin", "destination", "origin_city", "destination_city", "airline",
                         "flight_number", "departure_date", "departure_time", "arrival_time",
                         "duration_minutes", "stops", "cabin", "price_cents", "currency", "source", "scraped_at"})
HOTEL_COLS = frozenset({"id", "city_code", "city_name", "name", "stars", "rating", "amenities", "checkin",
                        "checkout", "nights", "price_per_night_cents", "total_price_cents", "currency",
                        "source", "scraped_at"})
CAR_COLS = frozenset({"id", "city_code", "city_name", "company", "model", "category", "transmission", "seats",
                      "pickup_date", "dropoff_date", "days", "price_per_day_cents", "total_price_cents",
                      "currency", "source", "scraped_at"})

_IATA = re.compile(r"^[A-Za-z]{3}$")
_IDEM = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_OFFER_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _iata(code: str, field: str) -> str:
    if not _IATA.match(code or ""):
        raise gql_error(f"'{field}' debe ser un código IATA de 3 letras.", "INVALID_ARGUMENT")
    return code.upper()


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


class IsAuthenticated(BasePermission):
    message = "Debes iniciar sesión para continuar."
    error_extensions = {"code": "UNAUTHENTICATED"}

    async def has_permission(self, source, info: Info, **kwargs) -> bool:
        return (await info.context.session()).user is not None


@strawberry.type
class Query:
    @strawberry.field(description="Estado del catálogo scrapeado en Supabase y opciones de búsqueda.")
    async def catalog_status(self) -> CatalogStatus:
        st = await catalog.status()
        meta, run = st["meta"], st["run"]
        names = meta.get("cities", {})
        routes = [Route(origin=o, destination=d, origin_city=names.get(o, o), destination_city=names.get(d, d))
                  for o, d in meta.get("routes", [])]
        return CatalogStatus(
            ready=run is not None and st["flights"] > 0,
            flight_offers=st["flights"], hotel_offers=st["hotels"], car_offers=st["cars"],
            last_run_at=_parse_dt(run["finished_at"]) if run else None,
            failed_queries=(run or {}).get("failed_queries", 0),
            routes=routes, dates=[date.fromisoformat(d) for d in meta.get("dates", [])],
            nights=int(meta.get("nights", 3)))

    @strawberry.field(description="Paquetes vuelo + hotel + auto ordenados por precio total.")
    async def packages(self, info: Info[Context, None], origin: str, destination: str,
                       departure_date: date, limit: int = 6) -> list[Package]:
        origin, destination = _iata(origin, "origin"), _iata(destination, "destination")
        if origin == destination:
            raise gql_error("El origen y el destino no pueden ser iguales.", "INVALID_ARGUMENT")
        limit = _clamp(limit, 1, 12)
        top = info.selected_fields[0]
        # Solo se piden a Supabase las columnas que el cliente selecciono (+ las necesarias para el total)
        fcols = requested_columns(child_selections(top, "flight"), FLIGHT_COLS, frozenset({"id", "price_cents", "currency"}))
        hcols = requested_columns(child_selections(top, "hotel"), HOTEL_COLS, frozenset({"id", "total_price_cents", "nights"}))
        ccols = requested_columns(child_selections(top, "car"), CAR_COLS, frozenset({"id", "total_price_cents"}))
        data = await catalog.package_inputs(origin, destination, departure_date.isoformat(), fcols, hcols, ccols, 6)
        combos = compose_packages(data["flights"], data["hotels"], data["cars"], limit)
        return [Package(id=p["id"], total_price_cents=p["total_price_cents"], currency=p["currency"],
                        nights=p["nights"], flight=FlightOffer.from_row(p["flight"]),
                        hotel=HotelOffer.from_row(p["hotel"]), car=CarOffer.from_row(p["car"]))
                for p in combos]

    @strawberry.field
    async def flight_offers(self, info: Info[Context, None], origin: str, destination: str,
                            departure_date: date, first: int = 10) -> list[FlightOffer]:
        cols = requested_columns(info.selected_fields[0].selections, FLIGHT_COLS, frozenset({"id"}))
        rows = await catalog.flights(_iata(origin, "origin"), _iata(destination, "destination"),
                                     departure_date.isoformat(), cols, _clamp(first, 1, 30))
        return [FlightOffer.from_row(r) for r in rows]

    @strawberry.field
    async def hotel_offers(self, info: Info[Context, None], city_code: str, checkin: date,
                           first: int = 10) -> list[HotelOffer]:
        cols = requested_columns(info.selected_fields[0].selections, HOTEL_COLS, frozenset({"id"}))
        rows = await catalog.hotels(_iata(city_code, "cityCode"), checkin.isoformat(), cols, _clamp(first, 1, 30))
        return [HotelOffer.from_row(r) for r in rows]

    @strawberry.field
    async def car_offers(self, info: Info[Context, None], city_code: str, pickup_date: date,
                         first: int = 10) -> list[CarOffer]:
        cols = requested_columns(info.selected_fields[0].selections, CAR_COLS, frozenset({"id"}))
        rows = await catalog.cars(_iata(city_code, "cityCode"), pickup_date.isoformat(), cols, _clamp(first, 1, 30))
        return [CarOffer.from_row(r) for r in rows]

    @strawberry.field(description="Usuario de la sesión actual (null si es anónimo).")
    async def me(self, info: Info[Context, None]) -> Optional[User]:
        user = (await info.context.session()).user
        return User(id=user["id"], email=user["email"], full_name=user["full_name"]) if user else None

    @strawberry.field(permission_classes=[IsAuthenticated],
                      description="Billetera del usuario (US$ 5.000 al registrarse) y sus últimos movimientos.")
    async def wallet(self, info: Info[Context, None]) -> Wallet:
        user = (await info.context.session()).user
        return Wallet.from_api(await orders_api.wallet(user["id"]))

    @strawberry.field(permission_classes=[IsAuthenticated])
    async def my_orders(self, info: Info[Context, None]) -> list[Order]:
        user = (await info.context.session()).user
        return [Order.from_api(o) for o in await orders_api.list(user["id"], include_steps=_wants_steps(info))]

    @strawberry.field(permission_classes=[IsAuthenticated])
    async def order(self, info: Info[Context, None], id: strawberry.ID) -> Optional[Order]:
        user = (await info.context.session()).user
        if not _OFFER_ID.match(str(id)) and not re.match(r"^[0-9a-fA-F-]{36}$", str(id)):
            return None
        raw = await orders_api.get(str(id), user["id"], include_steps=_wants_steps(info))   # anti-IDOR en orders
        return Order.from_api(raw) if raw else None


def _wants_steps(info: Info) -> bool:
    """¿El cliente selecciono `steps` (directamente o en un fragmento)? Si no, orders no carga la bitacora SAGA."""
    return bool(child_selections(info.selected_fields[0], "steps"))


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def _login(ctx: Context, email: str, password: str) -> User:
    old = ctx.request.cookies.get(settings.cookie_name)
    result = await auth_api.login(email.strip().lower(), password, old)   # auth destruye `old` y emite un id NUEVO
    ctx.set_session_cookie(result["session_id"])                          # session fixation: id rotado en cada login
    u = result["user"]
    return User(id=u["id"], email=u["email"], full_name=u["full_name"])


@strawberry.type
class Mutation:
    @strawberry.mutation
    async def register(self, info: Info[Context, None], input: RegisterInput) -> User:
        ctx = info.context
        await ctx.rate_limit("register")
        await auth_api.register(input.email.strip().lower(), input.password, input.full_name)
        user = await _login(ctx, input.email, input.password)
        try:                                   # billetera con US$ 5.000 desde el registro (si falla, se abre al usarla)
            await orders_api.open_wallet(str(user.id))
        except Exception:
            log.warning("no se pudo abrir la billetera de user=%s al registrarse; se abrirá al usarla", user.id)
        return user

    @strawberry.mutation
    async def login(self, info: Info[Context, None], input: LoginInput) -> User:
        ctx = info.context
        await ctx.rate_limit("login")
        return await _login(ctx, input.email, input.password)

    @strawberry.mutation
    async def logout(self, info: Info[Context, None]) -> bool:
        ctx = info.context
        sid = ctx.request.cookies.get(settings.cookie_name)
        if sid:
            await auth_api.logout(sid)
        ctx.clear_session_cookie()
        return True

    @strawberry.mutation(permission_classes=[IsAuthenticated],
                         description="Solo demostración: fija el saldo de tu billetera (p. ej. para mostrar saldo insuficiente).")
    async def demo_set_wallet_balance(self, info: Info[Context, None], balance_cents: int) -> Wallet:
        if not settings.allow_failure_simulation:
            raise gql_error("El modo demostración está desactivado.", "DEMO_DISABLED")
        if not 0 <= balance_cents <= 1_000_000_000:
            raise gql_error("Saldo fuera de rango.", "INVALID_ARGUMENT")
        user = (await info.context.session()).user
        return Wallet.from_api(await orders_api.demo_set_balance(user["id"], balance_cents))

    @strawberry.mutation(permission_classes=[IsAuthenticated],
                         description="Crea la reserva: ejecuta la SAGA (pago, vuelo, hotel, auto, factura).")
    async def create_booking(self, info: Info[Context, None], input: BookingInput) -> Order:
        ctx = info.context
        user = (await ctx.session()).user
        await ctx.rate_limit("checkout", identifier=user["id"])          # checkout / pago
        if not _IDEM.match(input.idempotency_key):
            raise gql_error("Clave de idempotencia inválida.", "INVALID_ARGUMENT")
        ids = [str(input.flight_offer_id), str(input.hotel_offer_id), str(input.car_offer_id)]
        if not all(_OFFER_ID.match(i) for i in ids):
            raise gql_error("Identificador de oferta inválido.", "INVALID_ARGUMENT")

        # Los PRECIOS se releen del catalogo: nunca se confia en valores enviados por el cliente.
        offers = await catalog.offers_by_ids(*ids)
        f, h, c = offers["flight"], offers["hotel"], offers["car"]
        if not (f and h and c):
            raise gql_error("Alguna de las ofertas ya no está disponible. Vuelve a buscar.", "OFFER_NOT_FOUND")
        consistent = (f["destination"] == h["city_code"] == c["city_code"]
                      and f["departure_date"] == h["checkin"] == c["pickup_date"])
        if not consistent or len({f["currency"], h["currency"], c["currency"]}) != 1:
            raise gql_error("Las ofertas elegidas no forman un paquete válido.", "INVALID_PACKAGE")

        # Viaje armado por el usuario: hora de recogida del auto, nunca antes de que aterrice el vuelo elegido
        # (si el vuelo llega al día siguiente, arrival < departure, no se compara con la hora).
        pickup = input.car_pickup_time
        if pickup is not None:
            if not _HHMM.match(pickup):
                raise gql_error("Hora de recogida inválida: usa el formato HH:MM.", "INVALID_ARGUMENT")
            lands_same_day = f["arrival_time"] >= f["departure_time"]
            if lands_same_day and pickup < f["arrival_time"]:
                raise gql_error(f"La recogida del auto ({pickup}) no puede ser antes de que aterrice tu vuelo "
                                f"({f['arrival_time']}).", "INVALID_PICKUP_TIME")

        stops = "directo" if f["stops"] == 0 else f"{f['stops']} escala{'s' if f['stops'] > 1 else ''}"
        payload = {
            "user_id": user["id"], "contact_name": user["full_name"], "idempotency_key": input.idempotency_key,
            "flight": {"id": f["id"], "price_cents": f["price_cents"], "currency": f["currency"],
                       "title": f"{f['airline']} {f['flight_number']}, {f['origin']} a {f['destination']}",
                       "detail": f"{f['departure_date']}, {f['departure_time']} a {f['arrival_time']}, {stops}"},
            "hotel": {"id": h["id"], "price_cents": h["total_price_cents"], "currency": h["currency"],
                      "title": h["name"], "detail": f"{h['stars']} estrellas, {h['nights']} noches desde {h['checkin']}"},
            "car": {"id": c["id"], "price_cents": c["total_price_cents"], "currency": c["currency"],
                    "title": f"{c['company']} {c['model']}",
                    "detail": f"{c['category']}, {'automático' if c['transmission'] == 'AUTOMATIC' else 'manual'}, {c['days']} días"
                              + (f", recogida {c['pickup_date']} {pickup}" if pickup else "")},
            "simulate_failure": (input.simulate_failure.value
                                 if input.simulate_failure and settings.allow_failure_simulation else None),
            "step_delay_ms": (settings.demo_step_delay_ms
                              if input.slow_motion and settings.allow_failure_simulation else 0),
        }
        return Order.from_api(await orders_api.create(payload))


def _should_mask(error: GraphQLError) -> bool:
    """Los errores que lanzamos nosotros (GraphQLError / permisos) llegan al cliente; las excepciones
    inesperadas se enmascaran para no filtrar detalles internos."""
    orig = error.original_error
    return orig is not None and not isinstance(orig, (PermissionError, GraphQLError))


_extensions = [MaxTokensLimiter(max_token_count=settings.max_query_tokens),     # tamano del documento (antes de parsear)
               MaxAliasesLimiter(max_alias_count=settings.max_query_aliases),   # fan-out hacia Supabase
               QueryDepthLimiter(max_depth=settings.max_query_depth),
               SupabaseTrace,                       # clase: una instancia por operacion (solo traza si esta activada)
               MaskErrors(should_mask_error=_should_mask, error_message="Error interno. Intenta de nuevo.")]
if not settings.graphiql:
    _extensions.append(DisableIntrospection())

schema = strawberry.Schema(query=Query, mutation=Mutation, extensions=_extensions)
