#!/usr/bin/env python3
"""Genera .env a partir de .env.example rellenando los secretos internos.

Uso:  python3 scripts/init_env.py
Despues edita .env y pega SUPABASE_URL, SUPABASE_ANON_KEY y SUPABASE_SERVICE_KEY.
"""
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"

AUTO_SECRETS = [
    "POSTGRES_PASSWORD", "FLIGHTS_DB_PASSWORD", "HOTELS_DB_PASSWORD", "CARS_DB_PASSWORD",
    "ORDERS_DB_PASSWORD", "AUTH_DB_PASSWORD", "REDIS_PASSWORD", "INTERNAL_TOKEN",
]


def main() -> int:
    if TARGET.exists():
        print(f"[!] {TARGET} ya existe; no se sobrescribe. Borralo si quieres regenerarlo.")
        return 1
    lines = []
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z0-9_]+)=$", line)
        if m and m.group(1) in AUTO_SECRETS:
            line = f"{m.group(1)}={secrets.token_hex(24)}"  # hex: seguro para URLs y shells
        lines.append(line)
    TARGET.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(TARGET, 0o600)
    print(f"[ok] {TARGET} creado con secretos internos aleatorios.")
    print("     Falta pegar SUPABASE_URL, SUPABASE_ANON_KEY y SUPABASE_SERVICE_KEY.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
