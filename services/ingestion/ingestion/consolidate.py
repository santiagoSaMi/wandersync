"""Consolidacion DISTRIBUIDA con Dask (dask.bag): une lo que transformaron las tareas, valida, deduplica, separa por
tabla y calcula estadisticas de precio por ruta/ciudad. No depende de Prefect: se prueba con scheduler="sync".

En el flow corre dentro de una tarea con `get_dask_client()`, asi que las particiones se reparten entre los workers
del cluster (map -> foldby = MapReduce).
"""
from __future__ import annotations

import dask
import dask.bag as db

TABLES = {"flights": "flight_offers", "hotels": "hotel_offers", "cars": "car_offers"}
PRICE_COLUMN = {"flight_offers": "price_cents", "hotel_offers": "total_price_cents", "car_offers": "total_price_cents"}


def _market(table: str, row: dict) -> str:
    """Clave de agregacion: la ruta para vuelos, la ciudad para hoteles y autos."""
    if table == "flight_offers":
        return f"{row['origin']}→{row['destination']}"
    return row["city_code"]


def _valid(record: tuple[str, dict]) -> bool:
    table, row = record
    price = row.get(PRICE_COLUMN[table])
    return bool(row.get("id")) and isinstance(price, int) and price > 0 and len(str(row.get("currency", ""))) == 3


def _newest(acc: tuple[str, dict] | None, record: tuple[str, dict]) -> tuple[str, dict]:
    """Ante ids repetidos se queda la observacion mas reciente (scraped_at)."""
    if acc is None or record[1].get("scraped_at", "") >= acc[1].get("scraped_at", ""):
        return record
    return acc


def _pick(a, b):
    return _newest(a, b) if b is not None else a


def _add(acc: tuple, record: tuple[str, dict]) -> tuple:
    price = record[1][PRICE_COLUMN[record[0]]]
    count, total, low, high = acc
    return count + 1, total + price, min(low, price), max(high, price)


def _merge(a: tuple, b: tuple) -> tuple:
    return a[0] + b[0], a[1] + b[1], min(a[2], b[2]), max(a[3], b[3])


_EMPTY = (0, 0, float("inf"), float("-inf"))


def consolidate(batches: list[dict], npartitions: int = 6, scheduler: str | None = None) -> dict:
    """`batches` = salida de las tareas de transformacion: [{"vertical": "flights", "rows": [...]}, ...].
    Devuelve {"tables": {tabla: filas}, "stats": [...], "received": n, "invalid": n, "duplicates": n}."""
    records = [(TABLES[b["vertical"]], row) for b in batches for row in b["rows"]]
    if not records:
        return {"tables": {t: [] for t in TABLES.values()}, "stats": [], "received": 0, "invalid": 0, "duplicates": 0}

    bag = db.from_sequence(records, npartitions=max(1, min(npartitions, len(records))))
    valid = bag.filter(_valid)
    unique = valid.foldby(lambda r: (r[0], r[1]["id"]), _newest, None, _pick, None)          # (clave, registro)
    stats = unique.map(lambda kv: kv[1]).foldby(lambda r: (r[0], _market(*r)), _add, _EMPTY, _merge, _EMPTY)
    n_valid, unique_records, market_stats = dask.compute(valid.count(), unique, stats, scheduler=scheduler)

    tables: dict[str, list[dict]] = {t: [] for t in TABLES.values()}
    for (table, _), (_, row) in sorted(unique_records, key=lambda kv: kv[0]):
        tables[table].append(row)
    stat_rows = [
        {"tabla": table, "ruta_o_ciudad": market, "ofertas": count, "min_usd": round(low / 100, 2),
         "promedio_usd": round(total / count / 100, 2), "max_usd": round(high / 100, 2)}
        for (table, market), (count, total, low, high) in sorted(market_stats)
    ]
    return {"tables": tables, "stats": stat_rows, "received": len(records), "invalid": len(records) - n_valid,
            "duplicates": n_valid - len(unique_records)}
