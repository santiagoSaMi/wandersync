"""API Gateway GraphQL de WanderSync (unico punto de entrada para el frontend)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from strawberry.fastapi import GraphQLRouter

from .config import RATE_POLICIES, settings
from .context import Context, request_ip
from .deps import auth_api, catalog, limiter, orders_api
from .schema import schema

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("gateway")


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await catalog.close()
    await auth_api.close()
    await orders_api.close()
    await limiter.close()


app = FastAPI(title="WanderSync gateway", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Content-Type", "Apollo-Require-Preflight"],
)


@app.middleware("http")
async def global_guard(request: Request, call_next):
    """Tope global por IP a /graphql (defensa en profundidad; el limite por accion vive en los resolvers)
    y cabeceras de seguridad basicas."""
    if request.method == "POST" and request.url.path.startswith("/graphql"):
        ip = await request_ip(request)
        limit, window = RATE_POLICIES["graphql"]
        try:
            allowed, retry = await limiter.hit("graphql", ip, limit, window)
        except Exception:                      # Redis caido: no tumbar el API por el limitador global
            log.exception("rate limiter global no disponible")
            allowed, retry = True, 0
        if not allowed:
            return JSONResponse({"errors": [{"message": "Demasiadas solicitudes.",
                                             "extensions": {"code": "RATE_LIMITED", "retryAfter": retry}}]},
                                status_code=429, headers={"Retry-After": str(retry)})
    response: Response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Cache-Control", "no-store")
    return response


async def get_context(request: Request, response: Response) -> Context:
    return Context(request, response)


graphql_router = GraphQLRouter(
    schema, context_getter=get_context,
    graphql_ide="graphiql" if settings.graphiql else None,
    allow_queries_via_get=False,           # los queries por GET facilitan CSRF/cache poisoning: deshabilitados
)
app.include_router(graphql_router, prefix="/graphql")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "gateway"}
