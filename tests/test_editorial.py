import copy
import json
import unittest
from unittest.mock import patch

from test_ui_spend_report import report
import benchmark
import insights


def sample():
    months = [dict(month=f"2025-0{i}", cost=100, interface_cost=20, interface_share=0.2) for i in range(1, 5)]
    return dict(
        org="sample",
        by_month=months,
        headline=dict(interface_share=0.2, interface_maintenance_share=0.5),
        counts=dict(developers=20, prs_scored=10),
        interface=dict(cost=80, new_cost=40, maintenance_cost=40, native_cost=0),
        by_repo=[dict(repo="only", interface_cost=80)],
        top_dirs_by_area={},
    )


class Benchmark(unittest.TestCase):
    def test_population(self):
        a = sample()
        b = copy.deepcopy(a)
        b.update(org="second")
        b["counts"]["developers"] = 50
        b["headline"]["interface_share"] = 0.4
        base = benchmark.aggregate([a, b])
        self.assertEqual(base, benchmark.aggregate([b, a]))
        self.assertEqual(base["percentiles"]["interface_share"]["p25"], 0.25)
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
        strip = report.strip([p["interface_share"] for p in base["points"]], 0.5)
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
        self.r["interface"].update(prs=20, fix_prs=8, fix_cost=12)
        self.r["params"] = {"currency": "BRL", "monthly_cost": 10}
        self.r["period"] = {"months": [m["month"] for m in self.r["by_month"]]}
        for m in self.r["by_month"]:
            m["interface_maintenance_share"] = 0.5
        self.base = benchmark.aggregate([dict(self.r, org=str(i)) for i in range(8)])

    def rows(self):
        return {f["kind"]: f for f in insights.findings(self.r, self.base)}

    def test_capacity_and_gap(self):
        self.assertNotIn("annual_gap", self.rows())
        self.assertEqual(self.rows()["capacity"]["numbers"], dict(interface_fte=2, maintenance_fte=1))
        self.r["interface"]["maintenance_cost"] = 60
        self.assertEqual(self.rows()["annual_gap"]["numbers"]["delta_annual"], 60)
        self.r["interface"]["maintenance_cost"] = 0
        self.assertNotIn("capacity", self.rows())
        self.assertEqual(self.rows()["annual_gap"]["numbers"]["delta_annual"], -120)

    def test_fix_frequency(self):
        self.assertNotIn("fix_share", self.rows())
        self.r["interface"]["fix_prs"] = 14
        self.assertEqual(self.rows()["fix_share"]["numbers"]["fix_share"], 0.7)
        self.r["interface"]["prs"] = 8
        self.assertNotIn("fix_share", self.rows())

    def test_single_repo_and_merged_module(self):
        self.assertNotIn("concentration", self.rows())
        path = "only/packages/product/src/modules"
        self.r["top_dirs_by_area"]["interface"] = [
            dict(path=path + "/", cost=60),
            dict(path="only/ui/", cost=20),
        ]
        self.assertEqual(self.rows()["concentration"]["scope_label"], "only · product/modules")
        self.assertNotIn("module", self.rows())
        self.r["maintenance_module"] = dict(path=path, cost=60, maintenance_cost=30)
        self.assertNotIn("concentration", self.rows())
        self.assertEqual(self.rows()["module"]["money_at_stake"], 30)

    def test_platform_evidence(self):
        self.assertIsNone(insights.surface_key("app", "screens/billing/view.ts"))
        self.assertNotEqual(
            insights.surface_key("other-web", "screens/billing/view.ts")[0],
            insights.surface_key("app-ios", "screens/billing/view.swift")[0],
        )
        for platform in ("web", "ios", "android", "desktop"):
            self.assertEqual(
                insights.surface_key("app-" + platform, "screens/billing/view.ts"), ("app/billing", platform)
            )
        self.r["surfaces"] = {"app/billing": {"ios": 10}}
        self.assertNotIn("platforms", self.rows())
        self.r["surfaces"]["app/billing"].update(web=20, desktop=5)
        f = self.rows()["platforms"]
        self.assertEqual(f["money_at_stake"], 15)
        self.assertEqual(f["labels"], ["web", "ios", "desktop"])

    def test_monthly_investment(self):
        self.assertNotIn("peak", self.rows())
        self.assertNotIn("interface_trend", self.rows())
        for i, m in enumerate(self.r["by_month"]):
            m.update(interface_cost=5 + 10 * i, interface_share=0.05 + 0.1 * i)
        self.assertEqual(self.rows()["peak"]["money_at_stake"], 35)
        self.assertAlmostEqual(self.rows()["interface_trend"]["numbers"]["slope"], 0.1)

    def test_trend(self):
        self.assertNotIn("trend", self.rows())
        for i, m in enumerate(self.r["by_month"]):
            m["interface_maintenance_share"] = 0.8 - 0.1 * i
        self.assertAlmostEqual(self.rows()["trend"]["numbers"]["slope"], -0.1)
        self.assertEqual(len(self.rows()["trend"]["labels"]), 4)
        self.r["by_month"][2]["interface_cost"] = 0
        self.assertNotIn("trend", self.rows())

    def test_native_and_new(self):
        self.assertFalse({"native", "new"}.intersection(self.rows()))
        self.r["interface"].update(native_cost=12, new_cost=50)
        self.assertTrue({"native", "new"}.issubset(self.rows()))

    def test_validation(self):
        from validate_insights import validate

        self.r["interface"].update(new_cost=60, native_cost=12)
        self.r["headline"]["interface_maintenance_share"] = 0.7
        doc = report.evidence(self.r, self.base)
        cards = insights.fallback(doc["findings"])
        self.assertEqual(validate(doc, cards), cards)
        for field, value in [
            ("action", "Gaste R$ 999 mil"),
            ("title", "manutenção baixa"),
            ("title", "palavra " * 9),
            ("so_what", "palavra " * 23),
            ("action", "palavra " * 17),
            ("finding_id", "inexistente"),
        ]:
            changed = copy.deepcopy(cards)
            changed[0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate(doc, changed)
        for invalid in ([cards[0]] * 3, list(reversed(cards)), cards[:1]):
            with self.assertRaises(ValueError):
                validate(doc, invalid)

    def test_strength_and_plain_language(self):
        from validate_insights import validate

        for cost, direction, wording in [(20, "better", "a mais por ano"), (60, "worse", "a menos por ano")]:
            self.r["interface"]["maintenance_cost"] = cost
            doc = report.evidence(self.r, self.base)
            f = next(f for f in doc["findings"] if f["kind"] == "annual_gap")
            self.assertEqual(f["direction"], direction)
            self.assertIn(wording, f["caption"])
            cards = validate(doc, insights.fallback(doc["findings"]))
            copy = json.dumps([cards, doc], ensure_ascii=False).lower()
            for phrase in ("mediana aplicada", "diferença no período", "distância da mediana"):
                self.assertNotIn(phrase, copy)
            if direction == "better":
                card = next(c for c in cards if c["finding_id"] == f["id"])
                self.assertEqual(card["action"], "")
                card["action"] = "Separe regressões recorrentes."
                with self.assertRaisesRegex(ValueError, "Ponto forte"):
                    validate(doc, cards)

    def test_conflicting_frequency_and_cost(self):
        from validate_insights import validate

        self.r["interface"]["fix_prs"] = 2
        single = self.rows()["fix_share"]
        self.assertEqual(single["direction"], "better")
        self.r["interface"]["maintenance_cost"] = 60
        doc = report.evidence(self.r, self.base)
        rows = doc["findings"]
        joint = next(f for f in rows if f["kind"] == "annual_gap")
        self.assertEqual(joint["direction"], "mixed")
        self.assertIn("10,0% dos PRs", joint["comparison"])
        self.assertIn("75,0% do gasto", joint["comparison"])
        self.assertFalse(any(f["kind"] == "fix_share" for f in rows))
        validate(doc, insights.fallback(rows))
        # An unmerged pair cannot be published as two opposite verdicts.
        joint["direction"] = "worse"
        rows.append(single)
        rows.sort(key=lambda f: -f["money_at_stake"])
        with self.assertRaisesRegex(ValueError, "opostas"):
            validate(doc, insights.fallback(rows))

    def test_title_evidence_and_scope(self):
        from validate_insights import validate

        doc = report.evidence(self.r, self.base)
        cards = insights.fallback(doc["findings"])
        cards[0]["title"] = "Quanto cabe no roadmap do CRM"
        with self.assertRaisesRegex(ValueError, "Título"):
            validate(doc, cards)
        cards[0]["title"] = "Manutenção ocupa capacidade em sample"
        validate(doc, cards)

    def test_small_base_uses_counts(self):
        self.assertEqual(report.rank_text([0.1] * 8 + [0.5, 0.8], 0.5), "Acima de 8 de 10 empresas")
        self.assertEqual(report.rank_text([0.1] * 29, 0.5), "Acima de 29 de 29 empresas")
        self.assertEqual(report.rank_text([0.1] * 15 + [0.5] * 15, 0.5), "Acima de 50% das empresas")
        self.assertEqual(report.rank_text([], None), "Sem comparação disponível")

    def test_cohort_and_editorial_render(self):
        from test_ui_spend_report import MANIFEST, pr

        with patch.object(report.defaults, "MAINT_MIN_UI_PRS", 1):  # one PR: allow the maintenance rate
            r = report.analyse(
                MANIFEST,
                {"app": [pr(1, "private", "2026-01-10T00:00:00Z", [["src/X.tsx", 5, 1]], title="fix: x")]},
                {},
                1000,
                160,
                "all",
            )
        self.assertEqual((r["interface"]["prs"], r["interface"]["fix_prs"]), (1, 1))
        b = benchmark.aggregate([dict(r, org=str(i)) for i in range(8)])
        doc = report.evidence(r, b)
        cards = insights.fallback(doc["findings"])
        cards[0]["title"] = "Decisão editorial em " + doc["findings"][0]["scope"].split("/")[0]
        page = report.render(r, cards, b)
        self.assertIn("Decisão editorial", page)
        self.assertEqual(page, report.render(r, json.loads(json.dumps(cards)), b))
        self.assertIn("Times ·", page)
        b["points"].pop()
        self.assertNotIn("Times ·", report.render(r, cards, b))
        invalid = copy.deepcopy(cards)
        invalid[0]["action"] = "Gaste 99999"
        self.assertEqual(report.render(r, invalid, b), report.render(r, base=b))
        self.assertEqual(report.companies(1), "1 empresa")
        self.assertEqual(report.companies(2), "2 empresas")
