"""Tests de la consolidacion con dask.bag (sin cluster: scheduler local). Varias particiones para ejercitar
tambien las funciones de combinacion entre particiones (MapReduce real).
Ejecutar desde services/ingestion:  python -m unittest discover -s tests -v
Requiere dask (si falta, se omite; con REQUIRE_TEST_DEPS=1 falla en vez de omitirse)."""
import importlib.util
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HAS_DEPS = importlib.util.find_spec("dask") is not None
if not HAS_DEPS and os.getenv("REQUIRE_TEST_DEPS"):
    raise RuntimeError("REQUIRE_TEST_DEPS=1 pero faltan dependencias de test (ver requirements-test.txt)")


def flight(i, price, route=("BOG", "CTG"), scraped_at="2026-10-03T10:00:00"):
    return {"id": f"fl-{i}", "origin": route[0], "destination": route[1], "price_cents": price, "currency": "USD",
            "scraped_at": scraped_at}


def hotel(i, price, city="CTG"):
    return {"id": f"ho-{i}", "city_code": city, "total_price_cents": price, "currency": "USD", "scraped_at": "t"}


@unittest.skipUnless(HAS_DEPS, "requiere dask")
class ConsolidateTests(unittest.TestCase):
    def run_consolidate(self, batches, scheduler="sync", npartitions=4):
        from ingestion.consolidate import consolidate
        return consolidate(batches, npartitions=npartitions, scheduler=scheduler)

    def test_splits_rows_by_table(self):
        out = self.run_consolidate([{"vertical": "flights", "rows": [flight(1, 100), flight(2, 200)]},
                                    {"vertical": "hotels", "rows": [hotel(1, 900)]},
                                    {"vertical": "cars", "rows": []}])
        self.assertEqual([r["id"] for r in out["tables"]["flight_offers"]], ["fl-1", "fl-2"])
        self.assertEqual([r["id"] for r in out["tables"]["hotel_offers"]], ["ho-1"])
        self.assertEqual(out["tables"]["car_offers"], [])
        self.assertEqual((out["received"], out["invalid"], out["duplicates"]), (3, 0, 0))

    def test_duplicates_across_batches_keep_newest_observation(self):
        old, new = flight(7, 100, scraped_at="2026-10-03T09:00"), flight(7, 150, scraped_at="2026-10-03T11:00")
        out = self.run_consolidate([{"vertical": "flights", "rows": [old, flight(8, 120)]},
                                    {"vertical": "flights", "rows": [new]}])
        rows = {r["id"]: r for r in out["tables"]["flight_offers"]}
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows["fl-7"]["price_cents"], 150)
        self.assertEqual(out["duplicates"], 1)

    def test_invalid_rows_are_dropped(self):
        bad = [flight(1, 0), {**flight(2, 100), "currency": "DOLLAR"}, {**flight(3, 100), "id": ""}]
        out = self.run_consolidate([{"vertical": "flights", "rows": [*bad, flight(4, 100)]}])
        self.assertEqual([r["id"] for r in out["tables"]["flight_offers"]], ["fl-4"])
        self.assertEqual(out["invalid"], 3)

    def test_price_statistics_per_route_and_city(self):
        rows = [flight(i, p) for i, p in enumerate((10000, 20000, 30000))] + [flight(9, 5000, ("BOG", "MDE"))]
        out = self.run_consolidate([{"vertical": "flights", "rows": rows},
                                    {"vertical": "hotels", "rows": [hotel(1, 40000), hotel(2, 60000)]}])
        stats = {(s["tabla"], s["ruta_o_ciudad"]): s for s in out["stats"]}
        self.assertEqual(stats[("flight_offers", "BOG→CTG")],
                         {"tabla": "flight_offers", "ruta_o_ciudad": "BOG→CTG", "ofertas": 3,
                          "min_usd": 100.0, "promedio_usd": 200.0, "max_usd": 300.0})
        self.assertEqual(stats[("flight_offers", "BOG→MDE")]["ofertas"], 1)
        self.assertEqual(stats[("hotel_offers", "CTG")]["promedio_usd"], 500.0)

    def test_duplicates_are_not_double_counted_in_statistics(self):
        out = self.run_consolidate([{"vertical": "flights", "rows": [flight(1, 100)]},
                                    {"vertical": "flights", "rows": [flight(1, 100)]}])
        self.assertEqual(out["stats"][0]["ofertas"], 1)

    def test_same_result_with_one_or_many_partitions_and_threads(self):
        """El resultado no depende de como Dask reparta los datos (combinacion correcta entre particiones)."""
        rows = [flight(i % 25, 1000 + i, ("BOG", "CTG") if i % 2 else ("BOG", "MIA")) for i in range(60)]
        batches = [{"vertical": "flights", "rows": rows[i:i + 7]} for i in range(0, 60, 7)]
        one = self.run_consolidate(batches, npartitions=1)
        many = self.run_consolidate(batches, npartitions=8)
        threads = self.run_consolidate(batches, scheduler="threads", npartitions=8)
        for other in (many, threads):
            self.assertEqual(other["tables"], one["tables"])
            self.assertEqual(other["stats"], one["stats"])
            self.assertEqual(other["duplicates"], one["duplicates"])

    def test_empty_input(self):
        out = self.run_consolidate([])
        self.assertEqual(out["stats"], [])
        self.assertEqual(set(out["tables"]), {"flight_offers", "hotel_offers", "car_offers"})


if __name__ == "__main__":
    unittest.main()
