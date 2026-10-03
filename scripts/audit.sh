#!/usr/bin/env bash
# Auditoria de cadena de suministro (supply chain): pip-audit por servicio + npm audit del frontend.
# Corre dentro de contenedores efimeros: no necesitas instalar nada salvo Docker.
# Los reportes quedan en docs/security/audit-reports/ (evidencia para el documento tecnico).
#   Uso:  ./scripts/audit.sh          (exit code != 0 si hay vulnerabilidades conocidas)
set -uo pipefail
cd "$(dirname "$0")/.."
OUT="docs/security/audit-reports"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"
status=0

for dir in services/*/; do
  name="$(basename "$dir")"
  [ -f "$dir/requirements.txt" ] || continue
  echo "=== pip-audit: $name"
  docker run --rm -v "$PWD/$dir:/src:ro" python:3.12-slim bash -c \
    "pip install --quiet --disable-pip-version-check pip-audit && pip-audit -r /src/requirements.txt --progress-spinner off" \
    2>&1 | tee "$OUT/pip-audit-$name-$STAMP.txt"
  [ "${PIPESTATUS[0]}" -eq 0 ] || status=1
done

echo "=== npm audit: frontend"
docker run --rm -v "$PWD/frontend:/src:ro" node:20-alpine sh -c \
  "mkdir /w && cp /src/package*.json /w/ && cd /w && npm install --package-lock-only --ignore-scripts >/dev/null 2>&1; npm audit --omit=dev" \
  2>&1 | tee "$OUT/npm-audit-frontend-$STAMP.txt"
[ "${PIPESTATUS[0]}" -eq 0 ] || status=1

echo
if [ "$status" -eq 0 ]; then echo "OK: sin vulnerabilidades conocidas."; else echo "ATENCION: hay hallazgos. Revisa $OUT/ (actualiza la dependencia o documenta la excepcion)."; fi
exit "$status"
