"""Tests de los modulos puros del gateway.  Ejecutar desde services/gateway:
    python -m unittest discover -s tests -v"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.netutil import TrustedProxies, client_ip, parse_cidrs  # noqa: E402
from app.packages import compose_packages  # noqa: E402
from app.selection import child_selections, requested_columns, snake  # noqa: E402


class SelectedField:                      # imitan los nodos de Strawberry (se identifican por nombre de clase)
    def __init__(self, name, selections=None):
        self.name, self.selections = name, selections or []


class FragmentSpread:
    def __init__(self, selections):
        self.selections = selections


FLIGHTS = [{"id": f"f{i}", "price_cents": 10000 + i * 500, "currency": "USD"} for i in range(6)]
HOTELS = [{"id": f"h{i}", "total_price_cents": 30000 + i * 1000, "nights": 3} for i in range(6)]
CARS = [{"id": f"c{i}", "total_price_cents": 9000 + i * 300} for i in range(6)]


class Packages(unittest.TestCase):
    def test_total_is_sum_and_sorted(self):
        pk = compose_packages(FLIGHTS, HOTELS, CARS, 6)
        self.assertEqual(len(pk), 6)
        totals = [p["total_price_cents"] for p in pk]
        self.assertEqual(totals, sorted(totals))
        for p in pk:
            self.assertEqual(p["total_price_cents"],
                             p["flight"]["price_cents"] + p["hotel"]["total_price_cents"] + p["car"]["total_price_cents"])
            self.assertEqual(p["nights"], 3)
            self.assertEqual(p["currency"], "USD")

    def test_variety_cap(self):
        pk = compose_packages(FLIGHTS, HOTELS, CARS, 6)
        for kind in ("flight", "hotel", "car"):
            counts = {}
            for p in pk:
                counts[p[kind]["id"]] = counts.get(p[kind]["id"], 0) + 1
            self.assertLessEqual(max(counts.values()), 2)   # ceil(6/3)

    def test_unique_ids_and_empty_inputs(self):
        pk = compose_packages(FLIGHTS, HOTELS, CARS, 12)
        self.assertEqual(len({p["id"] for p in pk}), len(pk))
        self.assertEqual(compose_packages([], HOTELS, CARS, 5), [])

    def test_fewer_offers_than_limit(self):
        pk = compose_packages(FLIGHTS[:1], HOTELS[:1], CARS[:1], 6)
        self.assertEqual(len(pk), 1)


class Selection(unittest.TestCase):
    def test_snake(self):
        self.assertEqual(snake("pricePerNightCents"), "price_per_night_cents")
        self.assertEqual(snake("id"), "id")

    def test_requested_columns_whitelist_and_required(self):
        sels = [SelectedField("airline"), SelectedField("departureTime"), SelectedField("__typename"),
                SelectedField("evil; drop"), FragmentSpread([SelectedField("stops")])]
        cols = requested_columns(sels, frozenset({"airline", "departure_time", "stops", "id", "price_cents"}),
                                 frozenset({"id", "price_cents"}))
        self.assertEqual(cols, ["airline", "departure_time", "id", "price_cents", "stops"])

    def test_child_selections(self):
        top = SelectedField("packages", [SelectedField("flight", [SelectedField("airline")]), SelectedField("hotel")])
        self.assertEqual([f.name for f in child_selections(top, "flight")], ["airline"])
        self.assertEqual(child_selections(top, "car"), [])


class ClientIp(unittest.TestCase):
    TRUSTED = parse_cidrs("172.16.0.0/12,10.0.0.0/8")

    def test_trusted_proxy_uses_real_ip(self):
        self.assertEqual(client_ip("172.18.0.5", {"x-real-ip": "203.0.113.9"}, self.TRUSTED), "203.0.113.9")

    def test_untrusted_peer_ignores_spoofed_header(self):
        self.assertEqual(client_ip("198.51.100.7", {"x-real-ip": "1.2.3.4"}, self.TRUSTED), "198.51.100.7")

    def test_garbage_header_falls_back_to_peer(self):
        self.assertEqual(client_ip("172.18.0.5", {"x-real-ip": "no-ip"}, self.TRUSTED), "172.18.0.5")
        self.assertEqual(client_ip(None, {}, self.TRUSTED), "unknown")


class FakeDns:
    def __init__(self, table):
        self.table, self.calls = dict(table), 0

    async def __call__(self, host):
        self.calls += 1
        if host not in self.table:
            raise OSError("Name or service not known")
        return self.table[host]


class TrustedProxyResolution(unittest.TestCase):
    def setUp(self):
        self.now = 0.0

    def proxies(self, dns, cidrs=()):
        return TrustedProxies(list(cidrs), ["frontend"], ttl_s=30, retry_s=5, resolver=dns, clock=lambda: self.now)

    def test_spoofed_header_from_docker_bridge_is_ignored(self):
        """El bypass original: peticion directa al puerto publicado (peer = bridge 172.18.0.1) con X-Real-IP falso."""
        nets = asyncio.run(self.proxies(FakeDns({"frontend": ["172.18.0.3"]})).networks())
        self.assertEqual(client_ip("172.18.0.1", {"x-real-ip": "203.0.113.50"}, nets), "172.18.0.1")
        self.assertEqual(client_ip("172.18.0.3", {"x-real-ip": "203.0.113.50"}, nets), "203.0.113.50")

    def test_nothing_trusted_by_default(self):
        nets = asyncio.run(TrustedProxies([], []).networks())
        self.assertEqual(client_ip("172.18.0.3", {"x-real-ip": "203.0.113.50"}, nets), "172.18.0.3")

    def test_resolution_is_cached_and_follows_ip_changes(self):
        dns = FakeDns({"frontend": ["172.18.0.3"]})
        tp = self.proxies(dns)

        async def scenario():
            first = await tp.networks()
            self.now = 10
            cached = await tp.networks()                 # dentro del TTL: sin nueva consulta DNS
            dns.table["frontend"] = ["172.18.0.9"]       # el contenedor se recreo con otra IP
            self.now = 31
            refreshed = await tp.networks()
            return first, cached, refreshed
        first, cached, refreshed = asyncio.run(scenario())
        self.assertEqual([str(n) for n in first], ["172.18.0.3/32"])
        self.assertEqual(cached, first)
        self.assertEqual([str(n) for n in refreshed], ["172.18.0.9/32"])
        self.assertEqual(dns.calls, 2)

    def test_unresolvable_proxy_retries_sooner_and_keeps_cidrs(self):
        dns = FakeDns({})                                # el frontend aun no arranca
        tp = self.proxies(dns, cidrs=parse_cidrs("10.9.0.0/16"))

        async def scenario():
            before = await tp.networks()
            self.now = 6                                 # > retry_s (5) pero < ttl_s (30)
            dns.table["frontend"] = ["172.18.0.3"]
            after = await tp.networks()
            return before, after
        before, after = asyncio.run(scenario())
        self.assertEqual([str(n) for n in before], ["10.9.0.0/16"])
        self.assertEqual([str(n) for n in after], ["10.9.0.0/16", "172.18.0.3/32"])


if __name__ == "__main__":
    unittest.main()
