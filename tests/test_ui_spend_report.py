"""Unit tests: classification, exclusions, PR score, cost split with carry-over. Run:
    python3 -m unittest discover -s skill/tests
"""
import math, sys, unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import classify, report


def pr(number, author, merged_at, files, title="feat: x", labels=(), author_type="User"):
    return {"number": number, "author": author, "author_type": author_type, "merged_at": merged_at,
            "title": title, "labels": list(labels), "files": files}


MANIFEST = {"org": "acme", "collected_on": "2026-11-01", "since": "2026-01-01", "until": "2026-04-01",
            "months": ["2026-01", "2026-02", "2026-03"], "repos": ["app"], "members": ["ana"], "max_prs": 0}


class Classify(unittest.TestCase):
    def test_areas(self):
        cases = {
            "src/components/Button.tsx": "interface",
            "apps/web/pages/settings.tsx": "interface",
            "packages/ui/styles/theme.css": "interface",
            "webapp/channels/src/actions/posts.ts": "interface",
            "app/views/users/show.html.erb": "interface",
            "src/i18n/en.json": "interface",
            "packages/emails/templates/welcome.html": "interface",
            "android/app/src/main/res/layout/main.xml": "interface",
            "public/logo.png": "interface",
            "apps/web/pages/api/users.ts": "backend",
            "server/channels/app/post.go": "backend",
            "apps/api/plane/views.py": "backend",
            "src/app/query_processor.clj": "backend",
            "src/components/Button.test.tsx": "interface",  # unit tests follow the tested code (round 2)
            "spec/models/user_spec.rb": "backend",
            "server/app/post_test.go": "backend",
            "e2e/specs/login.spec.ts": "tests",
            ".github/workflows/ci.yml": "infra",
            "Dockerfile": "infra",
            "package.json": "infra",
            "deploy/helm/values.yaml": "infra",
            "packages/prisma/schema.prisma": "data",
            "docs/setup.md": "docs",
            "README.md": "docs",
        }
        for path, area in cases.items():
            with self.subTest(path=path):
                self.assertEqual(classify.area("app", path, {}), area)

    def test_ambiguous_and_overrides(self):
        self.assertIsNone(classify.area("app", "packages/lib/utils/date.ts", {}))
        ov = {"app/packages/lib/": "backend", "app/packages/lib/utils/": "interface"}
        self.assertEqual(classify.area("app", "packages/lib/utils/date.ts", ov), "interface")  # longest prefix
        self.assertEqual(classify.area("app", "packages/lib/db.ts", ov), "backend")
        # overrides never touch files a rule already classified
        self.assertEqual(classify.area("app", "src/components/A.tsx", {"app/src/": "backend"}), "interface")

    def test_exclusions(self):
        cases = {
            "yarn.lock": "lockfile", "apps/web/package-lock.json": "lockfile", "go.sum": "lockfile",
            "vendor/github.com/x/y.go": "vendored", "node_modules/a/index.js": "vendored",
            "dist/app.js": "build", "static/app.min.js": "build",
            "src/__snapshots__/A.test.tsx.snap": "snapshot",
            "db/migrate/2026_add_users.rb": "migration", "prisma/migrations/1/migration.sql": "migration",
            "src/__generated__/graphql.ts": "generated", "api/v1/user.pb.go": "generated",
            "src/i18n/de.json": "translation", "translations/pl/web.po": "translation",
            "assets/logo.png": "binary", "icons/close.svg": "binary",
        }
        for path, cat in cases.items():
            with self.subTest(path=path):
                self.assertEqual(classify.exclusion(path), cat)
        for path in ("src/i18n/en.json", "src/components/Button.tsx", "translations/en/web.po"):
            self.assertIsNone(classify.exclusion(path), path)

    def test_bots(self):
        for login, typ in (("dependabot", "Bot"), ("renovate[bot]", "User"), ("github-actions[bot]", "Bot"),
                           ("weblate", "User"), (None, None)):
            self.assertTrue(classify.is_bot({"author": login, "author_type": typ}), login)
        self.assertFalse(classify.is_bot({"author": "ana", "author_type": "User"}))


class UnitTests(unittest.TestCase):
    """Round 2: unit tests take the area of the code they test; integration/e2e stay in tests."""

    def test_markers(self):
        cases = {
            "src/components/Button.test.tsx": "src/components/Button.tsx",
            "src/components/Button.spec.tsx": "src/components/Button.tsx",
            "src/components/Button.unit.spec.tsx": "src/components/Button.tsx",
            "pkg/test_foo.py": "pkg/foo.py",
            "server/app/foo_test.go": "server/app/foo.go",
            "spec/models/user_spec.rb": "models/user.rb",
            "src/test/java/com/acme/FooTest.java": "src/java/com/acme/Foo.java",
            "src/components/__tests__/Button.tsx": "src/components/Button.tsx",
            "src/views/__specs__/Home.spec.tsx": "src/views/Home.tsx",
            "src/components/__mocks__/api.ts": "src/components/api.ts",
            "tests/unit/api/x.py": "api/x.py",
            "app/latest.java": "app/latest.java",  # no separator: not a marker
        }
        for path, bare in cases.items():
            with self.subTest(path=path):
                self.assertEqual(classify.tested_path(path)[0], bare)
        self.assertEqual(classify.tested_path("packages/core/tests/unit/x.py")[1], "packages/core")
        self.assertEqual(classify.tested_path("src/utils/a.test.ts")[1], "src/utils")

    def test_areas(self):
        cases = {
            "src/components/Button.test.tsx": ("interface", "unit"),
            "src/components/__tests__/Button.tsx": ("interface", "unit"),
            "src/components/__mocks__/icons.tsx": ("interface", "unit"),
            "src/test-utils/render.tsx": ("interface", "unit"),
            "server/app/foo_test.go": ("backend", "unit"),
            "pkg/test_foo.py": ("backend", "unit"),
            "src/test/java/com/acme/FooTest.java": ("backend", "unit"),
            "tests/unit/api/x.py": ("backend", "unit"),               # mirrored tree
            "tests/unit/components/Nav.test.tsx": ("interface", "unit"),
            ".github/workflows/__tests__/ci.test.yml": ("infra", "unit"),
            # integration and e2e stay in tests, incl. their fixtures/helpers
            "e2e/specs/login.spec.ts": ("tests", "integration"),
            "apps/web/playwright/booking.spec.ts": ("tests", "integration"),
            "cypress/e2e/login.cy.ts": ("tests", "integration"),
            "src/components/Button.cy.tsx": ("tests", "integration"),
            "tests/e2e/test_flow.py": ("tests", "integration"),
            "tests/integration/api/x.py": ("tests", "integration"),
            "server/integration-tests/db_test.go": ("tests", "integration"),
            "src/api/users.e2e.test.ts": ("tests", "integration"),
            "src/api/sso.integration.test.ts": ("tests", "integration"),
            "server/test/integration/graphql/a.integration-spec.ts": ("tests", "integration"),
            "server/db_integration_test.go": ("tests", "integration"),
            "server/integrations/__tests__/chat-integration.service.test.ts": ("backend", "unit"),  # name, not a marker
            "src/main/java/FooIT.java": ("backend", None),           # not a test path: normal rules
            "src/test/java/FooIT.java": ("tests", "integration"),
            "tests/load/k6/checkout.js": ("tests", "integration"),
            "packages/twenty-e2e-testing/fixtures/user.ts": ("tests", "integration"),
            "playwright.config.ts": ("tests", "integration"),
            # not tests at all
            "src/components/Button.tsx": ("interface", None),
            "server/integrations/slack/x.go": ("backend", None),
        }
        for path, want in cases.items():
            with self.subTest(path=path):
                self.assertEqual(classify.area_kind("app", path, {}), want)

    def test_overrides_reach_tested_path(self):
        ov = {"app/packages/lib/": "backend"}
        self.assertEqual(classify.area_kind("app", "packages/lib/date.test.ts", ov), ("backend", "unit"))
        self.assertEqual(classify.area_kind("app", "packages/lib/__tests__/date.ts", ov), ("backend", "unit"))
        # mirrored tree: tests/thread.test.ts → thread.ts (ambiguous) → src/thread.ts (override)
        ov = {"app/src/": "backend", "app/src/web/": "interface"}
        self.assertEqual(classify.area_kind("app", "tests/thread.test.ts", ov), ("backend", "unit"))
        self.assertEqual(classify.area_kind("app", "tests/web/page.test.ts", ov), ("interface", "unit"))
        self.assertEqual(classify.area_kind("app", "packages/x/test/unit/a.test.ts", {"app/packages/x/lib/": "data"}),
                         ("data", "unit"))
        # co-located tests never try mirror roots
        self.assertEqual(classify.area_kind("app", "util.test.ts", ov), ("tests", "unit_unresolved"))
        # an agent's "tests" override marks an integration suite, it doesn't go through the unit rule
        self.assertEqual(classify.area_kind("app", "evals/src/run.ts", {"app/evals/": "tests"}), ("tests", "integration"))
        self.assertEqual(classify.area_kind("app", "evals/tests/a.test.ts", {"app/evals/": "tests"}), ("tests", "integration"))

    def test_inferred_fallback(self):
        prs = {"app": [pr(1, "ana", "2026-01-10T00:00:00Z", [
            ["packages/lib/src/a.tsx", 90, 0], ["packages/lib/src/b.go", 10, 0], ["server/x.go", 500, 0],
            ["packages/lib/tests/helpers.ts", 5, 0], ["tests/setup.ts", 5, 0]])]}
        idx = classify.code_index(prs, {})
        # tested path packages/lib/helpers.ts is ambiguous → dominant area of packages/lib (interface 90 vs backend 10)
        self.assertEqual(classify.area_kind("app", "packages/lib/tests/helpers.ts", {}, idx), ("interface", "unit_inferred"))
        # top-level tree, unresolved → dominant of the whole repo (backend 510 vs interface 90)
        self.assertEqual(classify.area_kind("app", "tests/setup.ts", {}, idx), ("backend", "unit_inferred"))
        self.assertEqual(classify.area_kind("app", "tests/conftest.py", {}, idx), ("backend", "unit"))  # .py rule
        # co-located ambiguous test: its own directory first
        self.assertEqual(classify.area_kind("app", "packages/lib/src/util.test.ts", {}, idx), ("interface", "unit_inferred"))
        # docs are not code: a README-only package doesn't make its tests "docs"
        idx = classify.code_index({"app": [pr(1, "ana", "2026-01-10T00:00:00Z", [
            ["packages/pay/README.md", 900, 0], ["packages/core/x.go", 5, 0]])]}, {})
        self.assertEqual(classify.area_kind("app", "packages/pay/lib/__tests__/svc.test.ts", {}, idx), ("backend", "unit_inferred"))
        # too little evidence in the package (7 lines < INFER_MIN_LINES): its parent decides
        idx = classify.code_index({"app": [pr(1, "ana", "2026-01-10T00:00:00Z", [
            ["packages/billing/x.tsx", 7, 0], ["packages/core/y.go", 279, 0], ["packages/core/z.tsx", 189, 0]])]}, {})
        self.assertEqual(classify.area_kind("app", "packages/billing/cal/lib/__tests__/svc.test.ts", {}, idx),
                         ("backend", "unit_inferred"))
        # no code to infer from: stays in tests
        self.assertEqual(classify.area_kind("app", "tests/setup.ts", {}), ("tests", "unit_unresolved"))

    def test_switch_off(self):
        with patch.object(classify.defaults, "UNIT_TESTS_FOLLOW_AREA", False):
            self.assertEqual(classify.area_kind("app", "src/components/Button.test.tsx", {}), ("tests", None))
            r = report.analyse(MANIFEST, {"app": [pr(1, "ana", "2026-01-10T00:00:00Z", [["src/A.test.tsx", 9, 0]])]},
                               {}, 1000, 160, "all")
            self.assertEqual(r["areas"][0]["area"], "tests")
            self.assertNotIn("Testes unitários contam", report.render(r))

    def test_analyse_counts(self):
        prs = {"app": [pr(1, "ana", "2026-01-10T00:00:00Z", [
            ["src/components/A.tsx", 50, 0], ["src/components/A.test.tsx", 30, 0], ["e2e/a.spec.ts", 20, 0],
            ["tests/setup.ts", 10, 0]])]}
        r = report.analyse(MANIFEST, prs, {}, 1000, 160, "all")
        t = r["lines"]["tests"]
        self.assertEqual((t["unit"], t["unit_inferred"], t["integration"]), (30, 10, 20))
        self.assertAlmostEqual(t["unit_inferred_share"], 0.25)
        areas = {a["area"]: a["share"] for a in r["areas"]}
        self.assertAlmostEqual(areas["interface"], 90 / 110, places=4)  # A.tsx + A.test.tsx + setup.ts inferred
        self.assertAlmostEqual(areas["tests"], 20 / 110, places=4)      # only the e2e spec
        page = report.render(r)
        self.assertIn("Testes de integração", page)
        self.assertIn("Testes unitários contam com o código que testam", page)


class Score(unittest.TestCase):
    def test_formula(self):
        self.assertEqual(report.score(0, 0), 0)
        self.assertAlmostEqual(report.score(7, 3), math.log2(8) + 0.5 * math.log2(4))  # 3 + 1
        self.assertGreater(report.score(1000, 10), report.score(30, 10))

    def test_maintenance(self):
        self.assertTrue(report.is_maintenance({"title": "fix(ui): button overlap", "labels": []}))
        self.assertTrue(report.is_maintenance({"title": "Ajuste no layout", "labels": []}))
        self.assertTrue(report.is_maintenance({"title": "Update modal", "labels": ["bug"]}))
        self.assertFalse(report.is_maintenance({"title": "feat: new billing page", "labels": ["feature"]}))
        self.assertFalse(report.is_maintenance({"title": "Add prefix support", "labels": []}))
        self.assertFalse(report.is_maintenance({"title": "feat: add copy button", "labels": []}))
        self.assertFalse(report.is_maintenance({"title": "feat(ui): custom CSS", "labels": []}))
        self.assertTrue(report.is_maintenance({"title": "style: align nav icons", "labels": []}))


class Cost(unittest.TestCase):
    MONTHS = ["2026-01", "2026-02", "2026-03"]

    def test_split_by_score(self):
        prs = [{"author": "ana", "month": "2026-01", "score": 3.0}, {"author": "ana", "month": "2026-01", "score": 1.0}]
        dev_months, carried = report.split_costs(prs, self.MONTHS, 1000)
        self.assertEqual((dev_months, carried), (1, 0))
        self.assertAlmostEqual(prs[0]["cost"], 750)
        self.assertAlmostEqual(prs[1]["cost"], 250)

    def test_carry_over(self):
        # PRs in jan and mar: feb has none, so its cost moves to march; no activity outside jan..mar.
        prs = [{"author": "ana", "month": "2026-01", "score": 1.0}, {"author": "ana", "month": "2026-03", "score": 2.0},
               {"author": "ana", "month": "2026-03", "score": 2.0}, {"author": "bia", "month": "2026-02", "score": 1.0}]
        dev_months, carried = report.split_costs(prs, self.MONTHS, 1000)
        self.assertEqual(dev_months, 4)  # ana 3 months, bia 1 (only feb: active from first to last PR)
        self.assertEqual(carried, 1000)
        self.assertAlmostEqual(prs[0]["cost"], 1000)
        self.assertAlmostEqual(prs[1]["cost"], 1000)  # (1000 march + 1000 carried) / 2
        self.assertAlmostEqual(sum(p["cost"] for p in prs), 4000)  # = team cost

    def test_analyse_excludes_bots_and_lockfiles(self):
        prs = {"app": [
            pr(1, "ana", "2026-01-10T00:00:00Z", [["src/components/A.tsx", 30, 10], ["yarn.lock", 5000, 0]]),
            pr(2, "ana", "2026-01-20T00:00:00Z", [["server/api.go", 40, 0]], title="fix: crash"),
            pr(3, "dependabot", "2026-01-21T00:00:00Z", [["src/components/B.tsx", 999, 0]], author_type="Bot"),
            pr(4, "ana", "2026-02-02T00:00:00Z", [["yarn.lock", 10, 10]]),  # no effective change
            pr(6, "ana", "2026-02-03T00:00:00Z", [["src/components/A.tsx", 30, 10]], title="Automated cherry pick of #1"),
            pr(7, "ana", "2026-02-04T00:00:00Z", [["server/api.go", 40, 0]], title="chore: sync main into epic/x"),
            pr(5, "ana", "2026-03-02T00:00:00Z", [["src/components/C.tsx", 5, 5]], title="fix: typo"),
        ]}
        r = report.analyse(MANIFEST, prs, {}, 1000, 160, "all")
        self.assertEqual(r["counts"]["prs_bot"], 1)
        self.assertEqual(r["counts"]["prs_duplicate"], 2)
        self.assertEqual(r["counts"]["prs_no_effective_change"], 1)
        self.assertEqual(r["counts"]["prs_scored"], 3)
        self.assertEqual(r["lines"]["excluded"]["lockfile"], 5020)
        self.assertEqual(r["cost"]["team_total"], 3000)
        self.assertEqual(r["cost"]["carried"], 1000)  # february had only a lockfile PR
        ui = r["interface"]
        # jan: 1000 split by score between PR1 (interface) and PR2 (backend); mar: 2000 to PR5 (interface, maint)
        s1, s2 = report.score(40, 1), report.score(40, 1)
        self.assertAlmostEqual(ui["cost"], round(1000 * s1 / (s1 + s2) + 2000, 4), places=3)
        self.assertAlmostEqual(ui["maintenance_cost"], 2000, places=3)
        self.assertNotIn("ana", str(r))  # aggregates only

    def test_authors_filter(self):
        prs = [pr(i, a, "2026-01-10T00:00:00Z", []) for i, a in enumerate(["ana", "ana", "ana", "bob", "ana"])]
        self.assertEqual(len(report.team(prs, "min-prs:3", [])), 4)
        self.assertEqual(len(report.team(prs, "members", ["bob"])), 1)
        self.assertEqual(len(report.team(prs, "all", [])), 5)

    def test_render_has_no_logins(self):
        prs = {"app": [pr(1, "zelogin", "2026-01-10T00:00:00Z", [["src/components/A.tsx", 30, 10]])]}
        r = report.analyse(MANIFEST, prs, {}, 1000, 160, "all")
        page = report.render(r)
        self.assertNotIn("zelogin", page)
        self.assertIn("Nenhum dado saiu dele", page)
        self.assertEqual(page, report.render(report.analyse(MANIFEST, prs, {}, 1000, 160, "all")))


if __name__ == "__main__":
    unittest.main()
