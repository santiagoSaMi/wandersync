"""Ayudas para leer el arbol de campos que el cliente PIDIO en la consulta GraphQL.

Sirven para no traer de Supabase columnas que nadie solicito (sin over-fetching en la capa de datos).
Trabajan por nombre de clase para poder testearse sin instalar Strawberry.
"""
from __future__ import annotations

import re

_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")


def snake(name: str) -> str:
    return _CAMEL.sub("_", name).lower()


def flatten_fields(selections):
    """Aplana fragmentos (spreads / inline) y devuelve solo campos reales."""
    for sel in selections or []:
        if type(sel).__name__ == "SelectedField":
            yield sel
        else:
            yield from flatten_fields(getattr(sel, "selections", None))


def child_selections(field, name: str):
    for f in flatten_fields(getattr(field, "selections", None)):
        if f.name == name:
            return f.selections
    return []


def requested_columns(selections, allowed: frozenset[str], required: frozenset[str] = frozenset()) -> list[str]:
    """Columnas a pedir = las solicitadas (en lista blanca) + las imprescindibles para calcular."""
    cols = set(required)
    for f in flatten_fields(selections):
        col = snake(f.name)
        if col in allowed:
            cols.add(col)
    return sorted(cols)
