#!/usr/bin/env python3
"""Verificaciones de seguridad automatizadas contra el stack en ejecucion (evidencia para el informe).

  python3 scripts/security_checks.py [--url http://localhost:3000]

Comprueba: mitigacion de session fixation, flags de la cookie, autenticacion requerida,
rate limiting de login y GET deshabilitado. Ojo: la prueba de rate limiting consume el cupo de
login de tu IP durante ~1 minuto. Guarda un reporte en docs/security/audit-reports/.
"""
import argparse
import pathlib
import secrets
import sys
import time
import urllib.parse
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _gql import Client, error_code, session_id  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:3000")
    args = ap.parse_args()
    c = Client(args.url)
    email = f"sec-{secrets.token_hex(4)}@example.com"
    password = "Contrasena-Segura-2026!"

    # 1) Sesion anonima inicial
    st, cookies, _ = c.gql("query { me { id } }")
    s1 = session_id(cookies)
    check("Se emite una sesion anonima al primer contacto", st == 200 and s1 is not None)
    flags = (cookies[0].lower() if cookies else "")
    check("Cookie de sesion con HttpOnly", "httponly" in flags)
    check("Cookie de sesion con SameSite=Lax", "samesite=lax" in flags)

    # 2) SESSION FIXATION: registrar/loguear con la cookie S1 debe emitir un id NUEVO y matar S1
    st, cookies, data = c.gql(
        "mutation($i: RegisterInput!) { register(input: $i) { id email } }",
        {"i": {"email": email, "password": password, "fullName": "Prueba Seguridad"}}, cookie=s1)
    s2 = session_id(cookies)
    check("Registro/login exitoso", st == 200 and not data.get("errors"), str(error_code(data) or ""))
    check("SESSION FIXATION: el id de sesion cambia al autenticarse", s2 is not None and s2 != s1,
          f"antes={str(s1)[:8]}... despues={str(s2)[:8]}...")

    st, cookies, data = c.gql("query { me { id } }", cookie=s1)
    check("SESSION FIXATION: el id anterior (anonimo) ya no autentica",
          (data.get("data") or {}).get("me") is None)
    st, _, data = c.gql("query { me { email } }", cookie=s2)
    check("El id nuevo si esta autenticado", ((data.get("data") or {}).get("me") or {}).get("email") == email)

    # 3) Logout invalida la sesion en el servidor
    c.gql("mutation { logout }", cookie=s2)
    _, _, data = c.gql("query { me { id } }", cookie=s2)
    check("Logout destruye la sesion en el servidor (el id viejo ya no sirve)",
          (data.get("data") or {}).get("me") is None)

    # 4) Recursos protegidos exigen autenticacion
    _, _, data = c.gql("query { myOrders { id } }")
    check("myOrders sin sesion -> UNAUTHENTICATED", error_code(data) == "UNAUTHENTICATED", str(error_code(data)))
    _, _, data = c.gql(
        "mutation($i: BookingInput!) { createBooking(input: $i) { id } }",
        {"i": {"flightOfferId": "x", "hotelOfferId": "y", "carOfferId": "z", "idempotencyKey": "abcdefgh1234"}})
    check("createBooking sin sesion -> UNAUTHENTICATED", error_code(data) == "UNAUTHENTICATED", str(error_code(data)))

    # 5) Consultas por GET deshabilitadas (mitiga CSRF)
    q = urllib.parse.quote("{ me { id } }")
    st, _, raw = c.request("GET", f"{c.path}?query={q}", headers={"Accept": "application/json"})
    check("Queries por GET rechazadas", '"me"' not in raw, f"HTTP {st}")

    # 6) Rate limiting de login (fuerza bruta): >10 intentos/min por IP
    limited = False
    for i in range(16):
        _, _, data = c.gql("mutation($i: LoginInput!) { login(input: $i) { id } }",
                           {"i": {"email": f"nadie{i}@example.com", "password": "incorrecta-123456"}})
        if error_code(data) == "RATE_LIMITED":
            limited = True
            break
    check("Rate limiting: el login se bloquea tras demasiados intentos (RATE_LIMITED)", limited, f"intento {i + 1}")

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} verificaciones OK")
    out = ROOT / "docs" / "security" / "audit-reports" / f"security-checks-{datetime.now():%Y%m%d-%H%M%S}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Verificaciones de seguridad ({datetime.now():%Y-%m-%d %H:%M})", f"Objetivo: `{args.url}`", "",
             "| Resultado | Verificacion | Detalle |", "|---|---|---|"]
    lines += [f"| {'PASS' if ok else 'FAIL'} | {name} | {detail} |" for name, ok, detail in results]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Reporte: {out.relative_to(ROOT)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
