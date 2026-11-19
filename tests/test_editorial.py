import copy
import json
import unittest

from test_ui_spend_report import report
import benchmark
import insights


def sample():
    months = [dict(month=f"2025-0{i}", cost=100, interface_cost=20, interface_share=.2) for i in range(1, 5)]
    return dict(org="sample", by_month=months, headline=dict(interface_share=.2, interface_maintenance_share=.5),
                counts=dict(developers=20, prs_scored=10), interface=dict(cost=80, new_cost=40, maintenance_cost=40,
                native_cost=0), by_repo=[dict(repo="only", interface_cost=80)], top_dirs_by_area={})


class Benchmark(unittest.TestCase):
    def test_population(self):
        a = sample()
        b = copy.deepcopy(a)
        b.update(org="second")
        b["counts"]["developers"] = 50
        b["headline"]["interface_share"] = .4
        base = benchmark.aggregate([a, b])
        self.assertEqual(base, benchmark.aggregate([b, a]))
        self.assertEqual(base["percentiles"]["interface_share"]["p25"], .25)
        self.assertEqual(base["prs_scored"], 20)
        self.assertEqual([p["team_size"] for p in base["points"]], ["20–49", "50 ou mais"])
        self.assertNotIn('"org"', json.dumps(base))
        with self.assertRaises(ValueError):
            benchmark.aggregate([a, a])

    def test_large_base_and_overlapping_points(self):
        rows = [dict(sample(), org=f"product-{i}") for i in range(100)]
        base = benchmark.aggregate(rows, {"product-0": "Colaboração"})
        self.assertEqual(len(base["points"]), 100)
        self.assertIn("Colaboração", [p["sector"] for p in base["points"]])
        strip = report.strip([p["interface_share"] for p in base["points"]], .5)
        self.assertEqual(strip.count("<circle "), 100)

    def test_missing_denominator(self):
        r = sample()
        r["interface"]["cost"] = 0
        b = benchmark.aggregate([r])
        self.assertIsNone(b["points"][0]["interface_maintenance_share"])
        self.assertEqual(b["percentiles"]["interface_maintenance_share"], {})


class Insights(unittest.TestCase):
    def setUp(self):
        self.r = sample()
        self.base = benchmark.aggregate([dict(sample(), org=str(i)) for i in range(4)])

    def rules(self):
        return {x["rule"]: x for x in insights.findings(self.r, self.base)}

    def test_concentration(self):
        self.assertNotIn("concentration", self.rules())
        self.r["by_repo"] = [dict(repo=k, interface_cost=20) for k in "abcd"]
        self.assertNotIn("concentration", self.rules())
        self.r["by_repo"][0]["interface_cost"] += 20
        self.assertIn("concentration", self.rules())

    def test_single_repo_uses_directories(self):
        self.r["top_dirs_by_area"]["interface"] = [dict(path=p, cost=c, maintenance_share=.5)
                                                  for p,c in [("only/a/",50),("only/b/",30)]]
        self.assertEqual(self.rules()["concentration"]["title"], "only/a/")

    def test_hotspot(self):
        directory = dict(path="only/a/", cost=50, maintenance_share=.5)
        self.r["top_dirs_by_area"]["interface"] = [directory]
        self.assertNotIn("hotspot", self.rules())
        directory["maintenance_share"] = .8
        self.assertEqual(self.rules()["hotspot"]["amount"], 40)

    def test_benchmark_threshold(self):
        self.assertNotIn("quartile", self.rules())
        self.r["headline"]["interface_maintenance_share"] = .8
        self.assertIn("quartile", self.rules())
        self.base["points"].pop()
        self.assertNotIn("quartile", self.rules())

    def test_native_and_new(self):
        self.assertFalse({"native", "new"}.intersection(self.rules()))
        self.r["interface"].update(native_cost=12, new_cost=50)
        self.assertTrue({"native", "new"}.issubset(self.rules()))

    def test_monthly_floor(self):
        self.assertNotIn("floor", self.rules())
        for m in self.r["by_month"]:
            m["interface_share"] = .35
        self.assertEqual(self.rules()["floor"]["amount"], 140)

    def test_peak_and_slope(self):
        self.assertNotIn("peak", self.rules())
        self.assertNotIn("trend", self.rules())
        for i,m in enumerate(self.r["by_month"]):
            m.update(interface_share=i*.1+.05, interface_cost=i*10+5)
        self.assertAlmostEqual(self.rules()["trend"]["value"], .1)
        self.assertEqual(self.rules()["peak"]["amount"], 35)
        self.r["by_month"][1]["cost"] = 0
        self.assertNotIn("trend", self.rules())
