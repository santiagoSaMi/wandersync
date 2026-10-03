"""Tests de la etapa Transform. Ejecutar:  python -m unittest discover -s tests -v
(requiere solo beautifulsoup4; no usa red ni Prefect)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion import parsing as p  # noqa: E402
from ingestion.config import CITY_NAMES, Settings, auth_headers, build_queries  # noqa: E402

FX = Path(__file__).parent / "fixtures"
COMMON = dict(city_names=CITY_NAMES, source="mocktravel", scraped_at="2026-09-27T00:00:00+00:00")


class PriceParsing(unittest.TestCase):
    def test_formats(self):
        cases = {
            "US$ 234.50": 23450, "USD 1,234.50": 123450, "1.234,50 US$": 123450,
            "234,50 US$": 23450, "US$1,234": 123400, "US$ 45": 4500, "US$ 1,234,567.89": 123456789,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(p.parse_price_to_cents(text), expected)

    def test_invalid(self):
        with self.assertRaises(p.ParseError):
            p.parse_price_to_cents("Consultar")

    def test_currency(self):
        self.assertEqual(p.detect_currency("US$ 10"), "USD")
        with self.assertRaises(p.ParseError):
            p.detect_currency("€ 10")


class FieldParsing(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(p.parse_duration_minutes("2h 15m"), 135)
        self.assertEqual(p.parse_duration_minutes("45m"), 45)
        self.assertEqual(p.parse_duration_minutes("12h 00m"), 720)
        with self.assertRaises(p.ParseError):
            p.parse_duration_minutes("largo")

    def test_stops(self):
        self.assertEqual(p.parse_stops("Directo"), 0)
        self.assertEqual(p.parse_stops("1 escala"), 1)
        self.assertEqual(p.parse_stops("2 escalas"), 2)

    def test_rating(self):
        self.assertEqual(p.parse_rating("8,7"), 8.7)
        self.assertIsNone(p.parse_rating("n/a"))

    def test_stable_id_is_deterministic(self):
        self.assertEqual(p.stable_id("fl", "a", 1), p.stable_id("fl", "a", 1))
        self.assertNotEqual(p.stable_id("fl", "a", 1), p.stable_id("fl", "a", 2))


class PageParsing(unittest.TestCase):
    def test_flights_page(self):
        page = p.parse_flights((FX / "flights_p1.html").read_text(encoding="utf-8"),
                               origin="BOG", destination="CTG", day="2026-10-15", **COMMON)
        self.assertEqual(len(page.rows), 10)
        self.assertEqual(page.dropped, 0)
        self.assertEqual(page.total_pages, 3)
        row = page.rows[0]
        self.assertEqual(row["origin"], "BOG")
        self.assertEqual(row["destination_city"], "Cartagena")
        self.assertGreater(row["price_cents"], 0)
        self.assertRegex(row["departure_time"], r"^\d\d:\d\d$")
        self.assertEqual(row["currency"], "USD")
        # columnas EXACTAS de public.flight_offers (si no, PostgREST responde 400)
        self.assertEqual(set(row), {
            "id", "source", "origin", "destination", "origin_city", "destination_city", "airline",
            "flight_number", "departure_date", "departure_time", "arrival_time", "duration_minutes",
            "stops", "cabin", "price_cents", "currency", "scraped_at"})

    def test_flights_last_page_is_shorter(self):
        page = p.parse_flights((FX / "flights_p3.html").read_text(encoding="utf-8"),
                               origin="BOG", destination="CTG", day="2026-10-15", **COMMON)
        self.assertLess(len(page.rows), 10)
        self.assertGreater(len(page.rows), 0)

    def test_hotels_page(self):
        page = p.parse_hotels((FX / "hotels_p1.html").read_text(encoding="utf-8"),
                              city="CTG", checkin="2026-10-15", nights=3, **COMMON)
        self.assertEqual(len(page.rows), 10)
        row = page.rows[0]
        self.assertEqual(row["checkout"], "2026-10-18")
        self.assertEqual(row["total_price_cents"], row["price_per_night_cents"] * 3)
        self.assertTrue(1 <= row["stars"] <= 5)
        self.assertEqual(set(row), {
            "id", "source", "city_code", "city_name", "name", "stars", "rating", "amenities", "checkin",
            "checkout", "nights", "price_per_night_cents", "total_price_cents", "currency", "scraped_at"})

    def test_cars_page(self):
        page = p.parse_cars((FX / "cars_p1.html").read_text(encoding="utf-8"),
                            city="CTG", pickup="2026-10-15", days=3, **COMMON)
        self.assertEqual(len(page.rows), 10)
        row = page.rows[0]
        self.assertIn(row["transmission"], {"MANUAL", "AUTOMATIC"})
        self.assertNotIn("o similar", row["model"])
        self.assertEqual(row["dropoff_date"], "2026-10-18")
        self.assertEqual(set(row), {
            "id", "source", "city_code", "city_name", "company", "model", "category", "transmission",
            "seats", "pickup_date", "dropoff_date", "days", "price_per_day_cents", "total_price_cents",
            "currency", "scraped_at"})

    def test_broken_page_raises(self):
        with self.assertRaises(p.ParseError):
            p.parse_flights((FX / "broken.html").read_text(encoding="utf-8"),
                            origin="BOG", destination="CTG", day="2026-10-15", **COMMON)

    def test_ids_are_unique_and_stable(self):
        html = (FX / "flights_p1.html").read_text(encoding="utf-8")
        kw = dict(origin="BOG", destination="CTG", day="2026-10-15", **COMMON)
        a, b = p.parse_flights(html, **kw), p.parse_flights(html, **kw)
        self.assertEqual([r["id"] for r in a.rows], [r["id"] for r in b.rows])
        self.assertEqual(len({r["id"] for r in a.rows}), len(a.rows))

    def test_dedupe(self):
        rows = [{"id": "x", "v": 1}, {"id": "x", "v": 2}, {"id": "y", "v": 3}]
        self.assertEqual(len(p.dedupe_by_id(rows)), 2)


class Planning(unittest.TestCase):
    def test_build_queries_counts(self):
        s = Settings()
        from datetime import date
        qs = build_queries(s, today=date(2026, 9, 27))
        flights = [q for q in qs if q["vertical"] == "flights"]
        hotels = [q for q in qs if q["vertical"] == "hotels"]
        cars = [q for q in qs if q["vertical"] == "cars"]
        self.assertEqual(len(flights), len(s.routes) * len(s.date_offsets))
        self.assertEqual(len(hotels), len(cars))
        self.assertEqual(flights[0]["date"], "2026-10-04")

    def test_auth_headers_new_and_legacy_keys(self):
        self.assertEqual(auth_headers("sb_secret_abc"), {"apikey": "sb_secret_abc"})
        legacy = auth_headers("eyJhbGciOi.payload.sig")
        self.assertEqual(legacy["Authorization"], "Bearer eyJhbGciOi.payload.sig")


if __name__ == "__main__":
    unittest.main()
