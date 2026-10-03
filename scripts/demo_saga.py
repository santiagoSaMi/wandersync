#!/usr/bin/env python3
"""Demo de la SAGA por linea de comandos (sirve de respaldo si la UI falla en la sustentacion).

  python3 scripts/demo_saga.py [--url http://localhost:3000] [--fail CAR]

1) Crea un usuario, 2) busca un paquete real en el catalogo (Supabase via GraphQL),
3) reserva SIN fallos (camino feliz) y 4) reserva forzando un fallo (--fail) para mostrar
la compensacion: los pasos ya completados se revierten en orden inverso.
"""
import argparse
import pathlib
import secrets
import sys
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _gql import Client, session_id  # noqa: E402

ORDER_FIELDS = """id status totalCents currency failureReason invoiceNumber
  steps { seq name phase status attempts error }"""
FINAL = {"CONFIRMED", "CANCELLED", "COMPENSATION_FAILED"}


def step_line(s: dict) -> str:
    arrow = "  ->" if s["phase"] == "EXECUTE" else "  <- (compensacion)"
    extra = f" [{s['attempts']} intentos]" if s["attempts"] > 1 else ""
    err = f"  ! {s['error']}" if s.get("error") else ""
    return f"   {s['seq']:>2}. {s['name']:<8}{arrow:<22}{s['status']}{extra}{err}"


def show(order: dict, steps: bool = True) -> None:
    print(f"   orden {order['id'][:8]}  estado = {order['status']}  total = {order['totalCents'] / 100:.2f} {order['currency']}")
    if order.get("invoiceNumber"):
        print(f"   factura: {order['invoiceNumber']}")
    if order.get("failureReason"):
        print(f"   motivo del fallo: {order['failureReason']}")
    if steps:
        for s in order["steps"]:
            print(step_line(s))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:3000")
    ap.add_argument("--fail", default="CAR", choices=["PAYMENT", "FLIGHT", "HOTEL", "CAR"])
    ap.add_argument("--slow", action="store_true",
                    help="modo presentacion: la SAGA avanza paso a paso y cada paso se imprime en vivo")
    args = ap.parse_args()
    c = Client(args.url)

    print("1) Sesion y registro de usuario de prueba")
    _, ck, _ = c.gql("query { me { id } }")
    sid = session_id(ck)
    email = f"demo-{secrets.token_hex(3)}@example.com"
    _, ck, data = c.gql("mutation($i: RegisterInput!) { register(input: $i) { id email } }",
                        {"i": {"email": email, "password": "Demo-Contrasena-2026!", "fullName": "Demo WanderSync"}}, cookie=sid)
    if data.get("errors"):
        print("   error:", data["errors"][0]["message"]); return 1
    sid = session_id(ck)
    print(f"   usuario {email} (sesion rotada)")

    print("2) Catalogo (Supabase) y busqueda de un paquete")
    _, _, data = c.gql("""query { catalogStatus { ready flightOffers hotelOffers carOffers
                         dates routes { origin destination } } }""", cookie=sid)
    cat = data["data"]["catalogStatus"]
    if not cat["ready"]:
        print("   El catalogo aun esta vacio: espera a la primera ingesta (Prefect) y reintenta."); return 1
    print(f"   ofertas: {cat['flightOffers']} vuelos, {cat['hotelOffers']} hoteles, {cat['carOffers']} autos")
    route, day = cat["routes"][1], cat["dates"][0]
    _, _, data = c.gql("""query($o: String!, $d: String!, $day: Date!) {
        packages(origin: $o, destination: $d, departureDate: $day, limit: 2) {
          id totalPriceCents flight { id airline flightNumber } hotel { id name } car { id company model } } }""",
                       {"o": route["origin"], "d": route["destination"], "day": day}, cookie=sid)
    pkgs = data["data"]["packages"]
    if len(pkgs) < 2:
        print("   No hay suficientes paquetes para esa ruta/fecha."); return 1
    for p in pkgs:
        print(f"   {p['flight']['airline']} {p['flight']['flightNumber']} + {p['hotel']['name']} + {p['car']['company']}  = {p['totalPriceCents'] / 100:.2f} USD")

    mutation = f"mutation($i: BookingInput!) {{ createBooking(input: $i) {{ {ORDER_FIELDS} }} }}"

    def balance() -> int:
        return c.gql("query { wallet { balanceCents } }", cookie=sid)[2]["data"]["wallet"]["balanceCents"]

    def money(cents: int) -> str:
        return f"US$ {cents / 100:,.2f}"

    def wallet_line(before: int) -> None:
        after = balance()
        delta = after - before
        how = "sin cambios (la SAGA devolvió el dinero)" if delta == 0 else f"{'+' if delta > 0 else '-'}{money(abs(delta))}"
        print(f"   billetera: {money(before)} -> {money(after)}  ({how})")

    def book(pkg, fail=None):
        r = c.gql(mutation, {"i": {"flightOfferId": pkg["flight"]["id"], "hotelOfferId": pkg["hotel"]["id"],
                                   "carOfferId": pkg["car"]["id"], "idempotencyKey": str(uuid.uuid4()),
                                   "simulateFailure": fail, "slowMotion": args.slow}}, cookie=sid)[2]
        order = (r.get("data") or {}).get("createBooking")
        seen: dict = {}
        deadline = time.time() + 120
        while order and order["status"] not in FINAL and time.time() < deadline:   # respondio PENDING
            if args.slow:                                    # imprime cada paso en cuanto cambia de estado
                for s in order["steps"]:
                    if seen.get((s["name"], s["phase"])) != s["status"]:
                        seen[(s["name"], s["phase"])] = s["status"]
                        print(step_line(s), flush=True)
            else:
                print(f"   ... orden {order['status']}, la SAGA sigue en curso; consultando de nuevo")
            time.sleep(1 if args.slow else 2)
            q = f"query($id: ID!) {{ order(id: $id) {{ {ORDER_FIELDS} }} }}"
            order = (c.gql(q, {"id": order["id"]}, cookie=sid)[2].get("data") or {}).get("order") or order
        if order and args.slow:                              # pasos que terminaron entre la ultima consulta y el final
            for s in order["steps"]:
                if seen.get((s["name"], s["phase"])) != s["status"]:
                    print(step_line(s))
        return {"data": {"createBooking": order}} if order else r

    print("\n3) Reserva SIN fallos (camino feliz)")
    before = balance()
    print(f"   billetera antes: {money(before)}")
    r = book(pkgs[0])
    show(r["data"]["createBooking"], steps=not args.slow) if r.get("data") else print("   error:", r["errors"][0]["message"])
    wallet_line(before)

    print(f"\n4) Reserva forzando un fallo en {args.fail} (la SAGA debe compensar)")
    before = balance()
    print(f"   billetera antes: {money(before)}")
    r = book(pkgs[1], args.fail)
    show(r["data"]["createBooking"], steps=not args.slow) if r.get("data") else print("   error:", r["errors"][0]["message"])
    wallet_line(before)
    return 0


if __name__ == "__main__":
    sys.exit(main())
