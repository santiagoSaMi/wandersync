"""MockTravel: sitio de viajes simulado (fuente de scraping de WanderSync).

Incluye "modo caos": latencia variable y fallos aleatorios (500/503/429) configurables
en caliente, para demostrar los reintentos de Prefect de forma controlada.
"""
from __future__ import annotations

import asyncio
import os
import random
from datetime import date, timedelta

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import generator as g
from .rendering import render_cars, render_flights, render_hotels, render_index

app = FastAPI(title="MockTravel", docs_url=None, redoc_url=None, openapi_url=None)


class Chaos:
    failure_rate: float = float(os.getenv("MOCK_FAILURE_RATE", "0.10"))
    latency_min_ms: int = int(os.getenv("MOCK_LATENCY_MIN_MS", "40"))
    latency_max_ms: int = int(os.getenv("MOCK_LATENCY_MAX_MS", "350"))
    served: int = 0
    injected_failures: int = 0


class ChaosUpdate(BaseModel):
    failure_rate: float | None = Field(default=None, ge=0, le=1)
    latency_min_ms: int | None = Field(default=None, ge=0, le=10_000)
    latency_max_ms: int | None = Field(default=None, ge=0, le=10_000)


async def chaos_gate() -> None:
    """Simula la red real: latencia variable y errores transitorios."""
    lo, hi = sorted((Chaos.latency_min_ms, Chaos.latency_max_ms))
    await asyncio.sleep(random.uniform(lo, hi) / 1000)
    Chaos.served += 1
    if random.random() < Chaos.failure_rate:
        Chaos.injected_failures += 1
        status = random.choices([500, 503, 429], [0.4, 0.4, 0.2])[0]
        headers = {"Retry-After": "1"} if status == 429 else None
        raise HTTPException(status_code=status, detail="mocktravel: fallo simulado", headers=headers)


def _airport(code: str) -> str:
    code = code.upper()
    if code not in g.AIRPORTS:
        raise HTTPException(status_code=404, detail=f"Ciudad/aeropuerto desconocido: {code}")
    return code


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return render_index((date.today() + timedelta(days=14)).isoformat())


@app.get("/flights", response_class=HTMLResponse)
async def flights(origin: str, destination: str, date: date, page: int = Query(1, ge=1, le=50)) -> str:
    await chaos_gate()
    o, d = _airport(origin), _airport(destination)
    if o == d:
        raise HTTPException(status_code=400, detail="Origen y destino no pueden ser iguales")
    return render_flights(o, d, date, page)


@app.get("/hotels", response_class=HTMLResponse)
async def hotels(city: str, checkin: date, nights: int = Query(3, ge=1, le=14),
                 page: int = Query(1, ge=1, le=50)) -> str:
    await chaos_gate()
    return render_hotels(_airport(city), checkin, nights, page)


@app.get("/cars", response_class=HTMLResponse)
async def cars(city: str, pickup: date, days: int = Query(3, ge=1, le=30),
               page: int = Query(1, ge=1, le=50)) -> str:
    await chaos_gate()
    return render_cars(_airport(city), pickup, days, page)


@app.get("/admin/chaos")
async def get_chaos() -> dict:
    return {
        "failure_rate": Chaos.failure_rate, "latency_min_ms": Chaos.latency_min_ms,
        "latency_max_ms": Chaos.latency_max_ms, "served": Chaos.served,
        "injected_failures": Chaos.injected_failures,
    }


@app.post("/admin/chaos")
async def set_chaos(body: ChaosUpdate) -> dict:
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(Chaos, field, value)
    return await get_chaos()
