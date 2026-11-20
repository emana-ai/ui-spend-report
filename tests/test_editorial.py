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
        self.base["points"][0]["fix_share"] = 0.1  # median 0.4: the team is away from it only when fix_prs moves
        self.base["percentiles"]["fix_share"]["p50"] = 0.4

    def rows(self):
        return {f["kind"]: f for f in insights.findings(self.r, self.base)}

    def test_capacity_and_no_savings_scenario(self):
        self.assertEqual(self.rows()["capacity"]["numbers"], dict(interface_fte=2, maintenance_fte=1))
        self.assertEqual(self.rows()["capacity"]["quanto"], "R$ 40 no período · 1,0 pessoa equivalente")
        self.r["interface"]["maintenance_cost"] = 60
        doc = json.dumps(report.evidence(self.r, self.base), ensure_ascii=False)
        for gone in ("annual_gap", "gastaria", "a menos", "a mais por ano"):
            self.assertNotIn(gone, doc)
        self.r["interface"]["maintenance_cost"] = 0
        self.assertNotIn("capacity", self.rows())

    def test_fix_frequency(self):
        self.assertNotIn("fix_share", self.rows())
        self.r["interface"]["fix_prs"] = 14
        f = self.rows()["fix_share"]
        self.assertEqual(f["numbers"]["fix_share"], 0.7)
        self.assertEqual(f["comparison"], "Frequência de correções: acima de 8 de 8 empresas (mediana 40,0%)")
        self.r["interface"]["prs"] = 8
        self.assertNotIn("fix_share", self.rows())

    def test_single_repo_concentration(self):
        self.assertNotIn("concentration", self.rows())  # one repository, no module scope: nothing to say
        mods = ["only/src/modules/record-table", "only/src/modules/settings", "only/src/ui"]
        self.r["scopes"] = {"modules:only": dict(cost=60, prs=40, files=9, by_month=[1, 2, 3, 4],
                                                 top=[[m, 20] for m in mods])}
        f = self.rows()["concentration"]
        self.assertEqual((f["scope"], f["money_at_stake"], f["formatted"]), ("only", 60, ["75,0%"]))
        self.assertEqual(f["caption"], "75,0% do investimento em interface em 3 módulos de only")
        self.assertEqual(f["onde"], ["only · modules/record-table", "only · modules/settings", "only · ui"])
        self.assertEqual(f["evidence"], "40 PRs · 9 arquivos")
        self.assertNotIn("limit", f)
        self.assertNotIn("Repositório", insights.fallback([f])[0]["title"])
        self.r["scopes"]["modules:only"]["prs"] = 12
        self.assertEqual(self.rows()["concentration"]["limit"], "Amostra pequena: 12 PRs · 9 arquivos.")
        self.r["scopes"]["modules:only"]["cost"] = 20  # the top modules carry 25% ≤ 30%: no card
        self.assertNotIn("concentration", self.rows())
        self.r["scopes"] = {}
        self.r["by_repo"] = [dict(repo="web", interface_cost=50), dict(repo="app", interface_cost=30)]
        self.assertEqual(self.rows()["concentration"]["caption"], "62,5% do investimento em interface")  # < 70%: repository

    def test_dominant_repo_module_scope(self):
        from test_ui_spend_report import MANIFEST, pr
        files = lambda big: [[f"src/modules/{m}/A.tsx", big, 0] for m in ("a", "b", "c", "d")]
        prs = {"web": [pr(1, "ana", "2026-01-10T00:00:00Z", files(30))],
               "app": [pr(2, "ana", "2026-01-10T00:00:00Z", [["src/components/B.tsx", 1, 0]])]}  # web ≈ 84%
        r = report.analyse(MANIFEST, prs, {}, 1000, 160, "all")
        self.assertEqual([m for m, _ in r["scopes"]["modules:web"]["top"]],
                         ["web/src/modules/a", "web/src/modules/b", "web/src/modules/c"])
        self.assertNotIn("modules:app", r["scopes"])
        prs["app"][0]["files"] = [["src/components/B.tsx", 3000, 0]]  # no repository reaches 70%
        self.assertFalse([k for k in report.analyse(MANIFEST, prs, {}, 1000, 160, "all")["scopes"] if k.startswith("modules:")])

    def test_maintenance_and_fix_cards_merge(self):
        self.r["interface"]["fix_prs"] = 14
        same = dict(prs=40, files=9, by_month=[1, 2, 3, 4], top=[["sample/src/a", 3], ["sample/src/b", 2], ["sample/src/c", 1]])
        other = dict(same, top=[["sample/src/a", 3], ["sample/src/x", 2], ["sample/src/y", 1]])
        self.r["scopes"] = {"maintenance": same, "fix": other}  # one module in common: two cards
        rows = self.rows()
        self.assertIn("fix_share", rows)
        self.assertNotIn("comparison_fix", rows["capacity"])
        self.assertNotIn("comparison_fix", rows["fix_share"])
        self.r["scopes"]["fix"] = dict(other, top=same["top"][:2] + [["sample/src/z", 1]])  # two in common: one card
        rows = self.rows()
        self.assertNotIn("fix_share", rows)
        self.assertEqual(rows["capacity"]["comparison_fix"], "Frequência de correções: 70,0% dos PRs · acima de 8 de 8 empresas")
        self.assertEqual(rows["capacity"]["formatted"][-1], "70,0%")
        from validate_insights import validate
        doc = report.evidence(self.r, self.base)
        cards = insights.fallback(doc["findings"])
        cards[0]["title"] = "Correções aparecem em 70,0% dos PRs"
        validate(doc, cards)

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
        self.r["scopes"] = {"surface:app/billing": dict(prs=29, files=3, by_month=[1, 1, 1, 1], top=[])}
        self.assertNotIn("platforms", self.rows())  # owner rule: fewer than 30 PRs → not published
        self.r["scopes"]["surface:app/billing"]["prs"] = 30
        f = self.rows()["platforms"]
        self.assertEqual(f["money_at_stake"], 15)
        self.assertEqual(f["numbers"]["primary"], "web")

    def test_monthly_investment(self):
        self.assertNotIn("peak", self.rows())
        self.assertNotIn("interface_trend", self.rows())
        for i, m in enumerate(self.r["by_month"]):
            m.update(interface_cost=5 + 10 * i, interface_share=0.05 + 0.1 * i)
        self.assertEqual(self.rows()["peak"]["money_at_stake"], 35)
        self.assertEqual(self.rows()["peak"]["quanto"], "R$ 35 no mês · 3,5 pessoas equivalentes")
        self.assertAlmostEqual(self.rows()["interface_trend"]["numbers"]["slope"], 0.1)

    def test_native(self):
        self.assertNotIn("native", self.rows())
        self.r["interface"].update(native_cost=12)
        self.assertIn("native", self.rows())
        self.assertFalse({"new", "trend", "module", "annual_gap"} & set(self.rows()))

    def test_validation(self):
        from validate_insights import validate

        self.r["interface"].update(native_cost=12)
        self.r["headline"]["interface_maintenance_share"] = 0.7
        doc = report.evidence(self.r, self.base)
        cards = insights.fallback(doc["findings"])
        self.assertEqual(validate(doc, cards), cards)
        for field, value in [
            ("diagnosis", "Gaste R$ 999 mil"),
            ("title", "manutenção baixa"),
            ("title", "palavra " * 9),
            ("diagnosis", "palavra " * 23),
            ("finding_id", "inexistente"),
        ]:
            changed = copy.deepcopy(cards)
            changed[0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate(doc, changed)
        for invalid in ([cards[0]] * 3, list(reversed(cards)), cards[:1]):
            with self.assertRaises(ValueError):
                validate(doc, invalid)

    def test_no_action_field(self):
        from validate_insights import validate

        doc = report.evidence(self.r, self.base)
        cards = insights.fallback(doc["findings"])
        self.assertTrue(all(set(c) == {"finding_id", "title", "diagnosis"} for c in cards))
        self.assertFalse(any("action" in f for f in doc["findings"]))
        with_action = copy.deepcopy(cards)
        with_action[0]["action"] = ""
        with self.assertRaisesRegex(ValueError, "Campos"):
            validate(doc, with_action)

    def test_prescriptive_language(self):
        from validate_insights import validate

        doc = report.evidence(self.r, self.base)
        cards = insights.fallback(doc["findings"])
        for text in ("Priorize testes de regressão nas telas", "Recomendamos separar os ajustes",
                     "É preciso olhar a fila de correções", "E PRECISO olhar a fila", "Vale a pena rever os filtros",
                     "O time deveria rever os filtros", "Considere reservar capacidade", "Revise primeiro os componentes",
                     "Defina um teto de manutenção", "Implemente testes nos filtros", "Comece pelos filtros"):
            changed = copy.deepcopy(cards)
            changed[0]["diagnosis"] = text
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "não recomenda"):
                validate(doc, changed)
        for text in ("Os títulos sugerem correções em testes de filtros", "Testes e definições de tela dominam os títulos"):
            changed = copy.deepcopy(cards)
            changed[0]["diagnosis"] = text
            validate(doc, changed)  # descriptive words that look like advice stay allowed

    def test_repeated_comparison(self):
        from validate_insights import validate

        self.r["interface"]["fix_prs"] = 14
        doc = report.evidence(self.r, self.base)
        cards = insights.fallback(doc["findings"])
        f = next(f for f in doc["findings"] if f["kind"] == "fix_share")
        self.assertEqual(f["base_formatted"], ["40,0%"])
        card = next(c for c in cards if c["finding_id"] == f["id"])
        card["title"] = "Correções aparecem em 70,0% dos PRs"
        validate(doc, cards)
        card["diagnosis"] = "Correções são 70,0% dos PRs, acima da base de 40,0%."
        with self.assertRaisesRegex(ValueError, "repetida"):
            validate(doc, cards)
        f.pop("comparison")  # without the rendered line, title and sentence still cannot both say it
        card["title"] = "Correções: 70,0% contra 40,0%"
        with self.assertRaisesRegex(ValueError, "repetida"):
            validate(doc, cards)

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
        invalid[0]["diagnosis"] = "Gaste 99999"
        self.assertEqual(report.render(r, invalid, b), report.render(r, base=b))
        self.assertEqual(report.companies(1), "1 empresa")
        self.assertEqual(report.companies(2), "2 empresas")
