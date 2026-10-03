"""Flow de Prefect que ORQUESTA la ingesta distribuida en Dask, como un pipeline ETL con dependencias explicitas.

    Extraer (x consulta) ─► Transformar (x consulta) ─┐
                 ...  ×120 en paralelo en Dask   ...  ├─► Consolidar con Dask ─► Cargar x3 tablas ─► Purgar ─► Registrar
                                                      ┘      (dask.bag)

Cada etapa recibe la SALIDA de la anterior, por eso la UI de Prefect dibuja el grafo con flechas. Cada etapa tiene su
politica de reintentos: la red falla (Extract y Load reintentan con backoff), el parseo es determinista (no reintenta).
La ejecucion es IDEMPOTENTE (upsert por id estable): relanzar el flow nunca duplica datos.
"""
from __future__ import annotations

import time
import uuid
from collections import Counter
from datetime import datetime, timezone

from prefect import allow_failure, flow, get_run_logger, task
from prefect.artifacts import create_markdown_artifact, create_table_artifact
from prefect.futures import as_completed
from prefect.runtime import flow_run, task_run
from prefect_dask import DaskTaskRunner, get_dask_client

from .config import CITY_NAMES, build_queries, get_settings
from .consolidate import TABLES, consolidate
from .scraper import fetch_pages, parse_pages
from .supabase_io import SupabaseWriter


def _label(q: dict) -> str:
    v = q["vertical"]
    if v == "flights":
        return f"vuelos {q['origin']}→{q['destination']} {q['date']}"
    if v == "hotels":
        return f"hoteles {q['city']} {q['checkin']}"
    return f"autos {q['city']} {q['pickup']}"


def _worker() -> str:
    """Worker de Dask que ejecuta la tarea (para mostrar el reparto del trabajo en el resumen)."""
    try:
        from distributed import get_worker
        return get_worker().address.replace("tcp://", "")
    except (ImportError, ValueError):
        return "local"


# ------------------------------------------------------------------------------------------------ etapas
@task(name="extraer", task_run_name="Extraer {label}", tags=["dask", "extract"],
      retries=4, retry_delay_seconds=[2, 5, 10, 20], retry_jitter_factor=0.3)   # red/fuente: fallos transitorios
def extract(query: dict, label: str) -> dict:
    """EXTRACT en un worker de Dask: descarga todas las paginas de la consulta."""
    logger = get_run_logger()
    attempt = task_run.run_count
    logger.info("Extract %s (intento %d) en el worker %s", label, attempt, _worker())
    pages = fetch_pages(query, get_settings())            # 5xx/429/HTML roto => excepcion => reintento
    return {"query": query, "pages": pages, "attempts": attempt, "worker": _worker(),
            "scraped_at": datetime.now(timezone.utc).isoformat()}


@task(name="transformar", task_run_name="Transformar {label}", tags=["dask", "transform"])   # determinista
def transform(extracted: dict, label: str) -> dict:
    """TRANSFORM en un worker de Dask: precios en 4 formatos, duraciones, escalas, ids estables..."""
    rows, dropped = parse_pages(extracted["query"], extracted["pages"], get_settings(), extracted["scraped_at"])
    get_run_logger().info("Transform %s: %d filas limpias, %d descartadas por calidad", label, len(rows), dropped)
    return {"vertical": extracted["query"]["vertical"], "label": label, "rows": rows, "dropped": dropped,
            "attempts": extracted["attempts"], "worker": extracted["worker"]}


@task(name="consolidar", task_run_name="Consolidar con Dask", tags=["dask", "consolidate"], retries=1)
def consolidate_all(batches: list[dict]) -> dict:
    """Consolidacion distribuida (dask.bag sobre el cluster): valida, deduplica, separa por tabla y calcula
    estadisticas de precio por ruta/ciudad. Publica la tabla de precios como artefacto de Prefect."""
    logger = get_run_logger()
    with get_dask_client() as client:                      # dask.compute dentro usa el cluster
        workers = len(client.scheduler_info()["workers"])
        result = consolidate(batches, npartitions=max(2, workers * 2))
    logger.info("Consolidacion en %d workers de Dask: %d filas recibidas, %d invalidas, %d duplicadas",
                workers, result["received"], result["invalid"], result["duplicates"])
    create_table_artifact(key="precios-por-ruta", table=result["stats"],
                          description="Precios del catalogo por ruta (vuelos) y ciudad (hoteles/autos), en USD")
    result["dask_workers"] = workers
    return result


@task(name="cargar", task_run_name="Cargar {table}", tags=["supabase", "load"],
      retries=3, retry_delay_seconds=[5, 15, 30], retry_jitter_factor=0.3)        # Supabase: red/limites
def load(consolidated: dict, table: str) -> dict:
    """LOAD: upsert por lotes en Supabase (idempotente, on_conflict=id)."""
    rows = consolidated["tables"][table]
    writer = SupabaseWriter(get_settings())
    loaded = writer.upsert(table, rows)
    get_run_logger().info("Load %s: %d filas en %d lotes", table, loaded, -(-loaded // writer.chunk) if loaded else 0)
    return {"table": table, "rows": loaded, "batches": -(-loaded // writer.chunk) if loaded else 0}


@task(name="purgar", task_run_name="Purgar fechas pasadas", tags=["supabase"], retries=2, retry_delay_seconds=5)
def purge_past() -> None:
    SupabaseWriter(get_settings()).purge_past()


@task(name="registrar", task_run_name="Registrar ingesta", tags=["supabase"], retries=2, retry_delay_seconds=5)
def record(run: dict) -> None:
    SupabaseWriter(get_settings()).record_run(run)


# ------------------------------------------------------------------------------------------------ flow
def _run_name() -> str:
    return f"ingesta {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"


@flow(name="wandersync-catalog-ingestion", flow_run_name=_run_name,
      task_runner=DaskTaskRunner(address=get_settings().dask_address), log_prints=True)
def ingest_catalog(date_offsets: list[int] | None = None) -> dict:
    """Reune vuelos, hoteles y autos de la fuente y los persiste en Supabase."""
    logger = get_run_logger()
    settings = get_settings()
    started, t0 = datetime.now(timezone.utc), time.monotonic()
    queries = build_queries(settings, date_offsets=date_offsets)
    logger.info("Plan de ingesta: %d consultas repartidas en el cluster Dask", len(queries))

    # Extract en paralelo; cada extraccion que termina bien alimenta su Transform (flecha Extraer -> Transformar)
    labels = {}
    extracts = []
    for q in queries:
        future = extract.submit(q, _label(q))
        labels[future.task_run_id] = _label(q)
        extracts.append(future)
    transforms, failed = [], []
    for future in as_completed(extracts):
        label = labels[future.task_run_id]
        future.wait()   # fija el estado FINAL desde Dask; sin esto .state consulta la API, que puede ir atrasada
        if future.state.is_completed():
            transform_future = transform.submit(future, label)
            labels[transform_future.task_run_id] = label
            transforms.append(transform_future)
        else:
            failed.append((label, f"extraccion: {future.state.message}"))
            logger.error("Consulta fallida tras reintentos (%s): %s", label, future.state.message)
    ok_transforms = []
    for future in transforms:
        future.wait()   # idem: estado final real, no el que reporta la API
        if future.state.is_completed():
            ok_transforms.append(future)
        else:
            failed.append((labels[future.task_run_id], f"transformacion: {future.state.message}"))

    # Fan-in: todas las transformaciones -> consolidacion con Dask -> fan-out a las cargas por tabla
    consolidated = consolidate_all.submit(ok_transforms)
    loads = [load.submit(consolidated, table) for table in TABLES.values()]
    for future in loads:
        future.wait()                                      # estado final real (ver arriba)
    batches = [f.result() for f in ok_transforms]
    summary = consolidated.result()
    load_results = {f.result()["table"]: f.result() for f in loads if f.state.is_completed()}
    failed += [(f"carga {t}", "agoto los reintentos") for t in TABLES.values() if t not in load_results]

    loaded = sum(r["rows"] for r in load_results.values())
    status = "SUCCESS" if not failed else ("PARTIAL" if loaded > 0 else "FAILED")
    finished = datetime.now(timezone.utc)
    # allow_failure: purgar/registrar esperan a las cargas aunque alguna haya fallado (sin el, no se ejecutarian
    # y una ingesta PARTIAL/FAILED quedaria sin registrar)
    after_loads = [allow_failure(f) for f in loads]
    tail = [purge_past.submit(wait_for=after_loads)] if loaded > 0 else []
    days = sorted({q.get("date") or q.get("checkin") for q in queries if q["vertical"] != "cars"})
    totals = {v: load_results.get(t, {}).get("rows", 0) for v, t in TABLES.items()}
    dropped = sum(b["dropped"] for b in batches)
    record.submit({
        "id": str(flow_run.id or uuid.uuid4()),
        "started_at": started.isoformat(), "finished_at": finished.isoformat(), "status": status,
        "flights_count": totals["flights"], "hotels_count": totals["hotels"], "cars_count": totals["cars"],
        "failed_queries": len(failed),
        "meta": {"routes": [list(r) for r in settings.routes], "dates": days,
                 "cities": dict(CITY_NAMES), "nights": settings.stay_nights},
    }, wait_for=[allow_failure(f) for f in tail] or after_loads).result()

    _publish_summary(status, queries, batches, failed, summary, load_results, dropped, time.monotonic() - t0)
    result = {"status": status, "queries": len(queries), "failed": len(failed), "dropped": dropped, **totals}
    logger.info("Ingesta terminada: %s", result)
    if status == "FAILED":
        raise RuntimeError(f"Ingesta fallida: nada se pudo cargar ({len(failed)} fallos)")
    return result


def _publish_summary(status, queries, batches, failed, summary, load_results, dropped, seconds) -> None:
    """Artefacto Markdown con el resumen de la ejecucion (pestana Artifacts de la UI de Prefect)."""
    per_worker = Counter(b["worker"] for b in batches)
    retried = sorted((b for b in batches if b["attempts"] > 1), key=lambda b: -b["attempts"])
    lines = [
        f"## Ingesta del catálogo: **{status}**", "",
        f"- Consultas: **{len(queries)}** planificadas, **{len(batches)}** completas, **{len(failed)}** fallidas",
        f"- Duración: **{seconds:.0f} s** · Workers de Dask en la consolidación: **{summary.get('dask_workers', '?')}**",
        f"- Filas: {summary['received']} recibidas, {summary['invalid']} inválidas, {summary['duplicates']} duplicadas, "
        f"{dropped} descartadas al parsear", "",
        "### Reparto del trabajo en el cluster Dask (extracciones por worker)", "",
        "| Worker | Consultas |", "|---|---|",
        *[f"| `{w}` | {n} |" for w, n in sorted(per_worker.items())], "",
        f"### Reintentos: {len(retried)} consultas necesitaron más de un intento", "",
    ]
    if retried:
        lines += ["| Consulta | Intentos |", "|---|---|", *[f"| {b['label']} | {b['attempts']} |" for b in retried[:15]]]
        if len(retried) > 15:
            lines.append(f"| … y {len(retried) - 15} más | |")
    lines += ["", "### Carga en Supabase (upsert por lotes)", "", "| Tabla | Filas | Lotes |", "|---|---|---|",
              *[f"| `{t}` | {r['rows']} | {r['batches']} |" for t, r in sorted(load_results.items())]]
    if failed:
        lines += ["", "### Fallos definitivos", "", *[f"- **{label}**: {reason}" for label, reason in failed]]
    create_markdown_artifact(key="resumen-ingesta", markdown="\n".join(lines),
                             description="Resumen de la ingesta: reparto en Dask, reintentos y carga")
