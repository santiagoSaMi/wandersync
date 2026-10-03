"""Contexto por peticion GraphQL: sesion (cookie), IP del cliente y rate limiting."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request, Response
from strawberry.fastapi import BaseContext

from .clients import gql_error
from .config import RATE_POLICIES, settings
from .deps import auth_api, limiter, trusted_proxies
from .netutil import client_ip


async def request_ip(request: Request) -> str:
    """IP del cliente para rate limiting: X-Real-IP solo si el peer es un proxy de confianza (el nginx)."""
    peer = request.client.host if request.client else None
    return client_ip(peer, request.headers, await trusted_proxies.networks())


@dataclass
class SessionState:
    session_id: str
    user: dict | None


class Context(BaseContext):
    # Strawberry solo acepta un dict o una subclase de BaseContext (si no: InvalidCustomContext -> HTTP 500)
    def __init__(self, request: Request, response: Response):
        super().__init__()
        self.request, self.response = request, response
        self._session: SessionState | None = None

    async def session(self) -> SessionState:
        """Resuelve la sesion de la cookie; si no hay (o expiro) crea una ANONIMA y la fija."""
        if self._session is not None:
            return self._session
        sid = self.request.cookies.get(settings.cookie_name)
        if sid:
            resolved = await auth_api.resolve(sid)
            if resolved.get("valid"):
                self._session = SessionState(sid, resolved.get("user"))
                return self._session
        sid = await auth_api.anonymous()
        self.set_session_cookie(sid)
        self._session = SessionState(sid, None)
        return self._session

    def set_session_cookie(self, session_id: str) -> None:
        # HttpOnly: JS no puede leerla (mitiga robo por XSS). SameSite=Lax: mitiga CSRF.
        self.response.set_cookie(key=settings.cookie_name, value=session_id, httponly=True,
                                 secure=settings.cookie_secure, samesite="lax", path="/")

    def clear_session_cookie(self) -> None:
        self.response.delete_cookie(key=settings.cookie_name, path="/")

    async def rate_limit(self, policy: str, identifier: str | None = None) -> None:
        limit, window = RATE_POLICIES[policy]
        allowed, retry = await limiter.hit(policy, identifier or await request_ip(self.request), limit, window)
        if not allowed:
            raise gql_error(f"Demasiadas solicitudes. Intenta de nuevo en {retry} segundos.",
                            "RATE_LIMITED", retryAfter=retry)
