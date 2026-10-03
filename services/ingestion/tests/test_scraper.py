"""Tests de la etapa Extract (scraper) sin red: httpx.MockTransport sirve las paginas de fixtures.
Ejecutar desde services/ingestion:  python -m unittest discover -s tests -v
Requiere httpx (si falta, se omite; con REQUIRE_TEST_DEPS=1 falla en vez de omitirse)."""
import importlib.util
import os
import pickle
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HAS_DEPS = all(importlib.util.find_spec(m) for m in ("httpx", "bs4"))
if not HAS_DEPS and os.getenv("REQUIRE_TEST_DEPS"):
    raise RuntimeError("REQUIRE_TEST_DEPS=1 pero faltan dependencias de test (ver requirements-test.txt)")

FX = Path(__file__).parent / "fixtures"
QUERY = {"vertical": "flights", "origin": "BOG", "destination": "CTG", "date": "2026-10-15"}


@unittest.skipUnless(HAS_DEPS, "requiere httpx y beautifulsoup4")
class ScraperTests(unittest.TestCase):
    def scrape(self, responder):
        import httpx

        from ingestion.config import Settings
        from ingestion.scraper import scrape_query
        self.pages = []

        def handler(request):
            page = int(request.url.params["page"])
            self.pages.append(page)
            status, fixture = responder(page)
            return httpx.Response(status, text=(FX / fixture).read_text(encoding="utf-8") if fixture else "error")
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return scrape_query(QUERY, Settings(mock_source_url="http://mock"), client=client)

    def test_follows_pagination_until_last_page(self):
        rows, dropped = self.scrape(lambda page: (200, "flights_p3.html" if page == 3 else "flights_p1.html"))
        self.assertEqual(self.pages, [1, 2, 3])               # flights_p1 anuncia 3 paginas
        from ingestion import parsing
        last = parsing.parse_flights((FX / "flights_p3.html").read_text(encoding="utf-8"), origin="BOG",
                                     destination="CTG", day="2026-10-15", city_names={}, source="s", scraped_at="t")
        self.assertEqual((len(rows), dropped), (10 + 10 + len(last.rows), 0))

    def test_source_error_raises_picklable_exception(self):
        """Dask devuelve la excepcion del worker al flow con pickle: debe sobrevivir con su mensaje real."""
        from ingestion.scraper import SourceHTTPError
        with self.assertRaises(SourceHTTPError) as ctx:
            self.scrape(lambda page: (500, None))
        self.assertIn("HTTP 500", str(ctx.exception))
        self.assertIn("/flights", str(ctx.exception))
        clone = pickle.loads(pickle.dumps(ctx.exception))
        self.assertIsInstance(clone, SourceHTTPError)
        self.assertEqual(str(clone), str(ctx.exception))

    def test_error_on_a_later_page_fails_the_whole_query(self):
        """Prefect reintenta la consulta completa (es idempotente): no se devuelven paginas a medias."""
        from ingestion.scraper import SourceHTTPError
        with self.assertRaises(SourceHTTPError):
            self.scrape(lambda page: (200, "flights_p1.html") if page == 1 else (503, None))
        self.assertEqual(self.pages, [1, 2])

    def test_why_httpx_status_errors_are_not_raised(self):
        """Documenta el bug original: HTTPStatusError no se puede des-serializar."""
        import httpx
        req = httpx.Request("GET", "http://mock/flights")
        error = httpx.HTTPStatusError("boom", request=req, response=httpx.Response(500, request=req))
        with self.assertRaises(TypeError):
            pickle.loads(pickle.dumps(error))


if __name__ == "__main__":
    unittest.main()
