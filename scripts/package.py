#!/usr/bin/env python3
"""Empaqueta el proyecto para entregarlo (zip) SIN secretos ni artefactos generados.

  python3 scripts/package.py [--out wandersync.zip]

Por que existe: la carpeta no es un repositorio git, asi que .gitignore no protege nada; comprimir la
carpeta a mano incluiria .env (con la clave secreta de Supabase). Este script excluye .env y todo lo
que no debe viajar, y VERIFICA el zip resultante antes de darlo por bueno.
"""
import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXCLUDED_DIRS = {".git", "node_modules", "dist", "__pycache__", ".venv", "venv", ".pytest_cache",
                 ".idea", ".vscode"}
EXCLUDED_FILES = {".env", ".DS_Store"}
EXCLUDED_SUFFIXES = {".pyc", ".pem", ".zip"}


def is_secret_file(path: Path) -> bool:
    # .env, .env.local, prod.env... pero NO la plantilla .env.example (va vacia a proposito)
    name = path.name
    return name != ".env.example" and (name in EXCLUDED_FILES or name.startswith(".env.") or name.endswith(".env"))


def included(rel: Path) -> bool:
    if any(part in EXCLUDED_DIRS for part in rel.parts[:-1]):
        return False
    return not is_secret_file(rel) and rel.suffix not in EXCLUDED_SUFFIXES


def secret_values() -> list[str]:
    """Valores reales de .env (si existe), para comprobar que no aparecen en ningun archivo empaquetado."""
    env = ROOT / ".env"
    if not env.exists():
        return []
    values = []
    for line in env.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        value = value.strip().strip('"')
        is_secret = key.strip() != "SUPABASE_URL"            # la URL del proyecto no es secreta
        if sep and is_secret and not key.lstrip().startswith("#") and len(value) >= 16:
            values.append(value)
    return values


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT.parent / "wandersync.zip"))
    out = Path(ap.parse_args().out).resolve()

    files = sorted(p for p in ROOT.rglob("*") if p.is_file() and included(p.relative_to(ROOT)))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, Path("wandersync") / path.relative_to(ROOT))

    # Verificacion: ningun archivo de secretos y ningun valor secreto dentro del zip
    secrets = secret_values()
    with zipfile.ZipFile(out) as zf:
        for name in zf.namelist():
            if is_secret_file(Path(name)):
                out.unlink()
                print(f"[x] {name} quedo dentro del zip: abortado.")
                return 1
            data = zf.read(name).decode("utf-8", "ignore")
            if any(s in data for s in secrets):
                out.unlink()
                print(f"[x] {name} contiene un valor secreto de .env: abortado.")
                return 1
    print(f"[ok] {out} ({len(files)} archivos, sin .env ni secretos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
