"""Traza de las consultas que el gateway envia a Supabase, devuelta en `extensions.supabase` de la respuesta.

Sirve para DEMOSTRAR la ausencia de over-fetching: al quitar un campo en GraphiQL, su columna desaparece de la
consulta a Supabase en la misma respuesta. Solo se activa con SUPABASE_QUERY_TRACE (por defecto, igual que
GRAPHIQL): en produccion no se exponen detalles internos.
"""
from __future__ import annotations

import re
from contextvars import ContextVar

from strawberry.extensions import SchemaExtension

from .config import settings

_entries: ContextVar[list[dict] | None] = ContextVar("supabase_trace", default=None)
_SPACES = re.compile(r"\s+")


def record(collections: dict[str, list[str]], query: str | None = None, **extra) -> None:
    """Registra una peticion a Supabase (o un acierto de cache) si la operacion actual esta trazando."""
    entries = _entries.get()
    if entries is None:
        return
    entry = {"request": len(entries) + 1, "collections": collections, **extra}
    if query is not None:
        entry["query"] = _SPACES.sub(" ", query).strip()
    entries.append(entry)


class SupabaseTrace(SchemaExtension):
    """Una instancia por operacion GraphQL (Strawberry la crea a partir de la clase)."""

    def on_operation(self):
        self._token = _entries.set([] if settings.supabase_query_trace else None)
        yield
        self._collected = _entries.get()
        _entries.reset(self._token)

    def get_results(self) -> dict:
        collected = getattr(self, "_collected", None) or _entries.get()
        return {"supabase": collected} if collected else {}
