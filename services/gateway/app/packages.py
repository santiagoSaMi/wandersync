"""Composicion de paquetes (vuelo + hotel + auto). Logica pura y testeable."""
from __future__ import annotations

from collections import Counter


def compose_packages(flights: list[dict], hotels: list[dict], cars: list[dict], limit: int) -> list[dict]:
    """Combina las mejores ofertas de cada vertical y devuelve `limit` paquetes por precio total,
    procurando variedad: ninguna oferta individual aparece en mas de ceil(limit/3) paquetes."""
    if not (flights and hotels and cars):
        return []
    combos = sorted(
        ((f["price_cents"] + h["total_price_cents"] + c["total_price_cents"], f, h, c)
         for f in flights for h in hotels for c in cars),
        key=lambda t: (t[0], t[1]["id"], t[2]["id"], t[3]["id"]),
    )
    cap = max(1, -(-limit // 3))
    used: Counter = Counter()
    chosen, rest = [], []
    for combo in combos:
        _, f, h, c = combo
        keys = (("f", f["id"]), ("h", h["id"]), ("c", c["id"]))
        if all(used[k] < cap for k in keys):
            chosen.append(combo)
            used.update(keys)
        else:
            rest.append(combo)
        if len(chosen) == limit:
            break
    if len(chosen) < limit:                       # relleno si la variedad no alcanzo
        chosen += rest[: limit - len(chosen)]
    chosen.sort(key=lambda t: (t[0], t[1]["id"], t[2]["id"], t[3]["id"]))
    return [
        {
            "id": f"{f['id']}~{h['id']}~{c['id']}",
            "total_price_cents": total,
            "currency": f.get("currency", "USD"),
            "nights": h.get("nights", 0),
            "flight": f, "hotel": h, "car": c,
        }
        for total, f, h, c in chosen
    ]
