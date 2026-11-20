import unittest
from collections import Counter, defaultdict

from test_ui_spend_report import MANIFEST, classify, pr, report


def sample(files):
    return {"app": [pr(1, "sample-author", "2026-01-10T00:00:00Z", files)]}


class ClassificationCalibration(unittest.TestCase):
    def test_native_modules(self):
        for marker in ("src/main/AndroidManifest.xml", "src/main/res/menu/nav.xml",
                       "src/androidTest/java/Screen.java", "AndroidManifest.xml"):
            index = classify.code_index(sample([["phone/" + marker, 2, 0]]), {})
            for ext in ("java", "kt"):
                self.assertEqual(classify.area("app", "phone/src/main/A." + ext, {}, index), "interface")
                self.assertEqual(classify.area("app", "service/src/main/A." + ext, {}, index), "backend")
            self.assertEqual(classify.area_kind("app", "phone/src/test/java/ScreenTest.kt", {}, index),
                             ("interface", "unit"))
            self.assertEqual(classify.area_kind("app", "phone/src/androidTest/java/Screen.kt", {}, index),
                             ("tests", "integration"))
        for path in ("Screen.swift", "Text.xcstrings", "Main.storyboard", "View.xib",
                     "home.dart", "src/main/res/navigation/home.xml"):
            self.assertEqual(classify.rule_area(path), "interface", path)

    def test_excluded_artifacts(self):
        for path in ("SDK.xcframework/blob", "lib.framework/module", "A.swiftinterface",
                     "A.swiftdoc", "Assets.car", "ios/_CodeSignature/files", "CodeResources"):
            self.assertEqual(classify.exclusion(path), "build", path)
        for folder in ("mock_review", "fixtures", "__mocks__"):
            path = folder + "/dump.json"
            self.assertEqual(classify.file_exclusion(path, 10001), "generated")
            self.assertIsNone(classify.file_exclusion(path, 10000))
        self.assertIsNone(classify.file_exclusion("production/data.json", 10001))

    def test_scoped_force_and_exclusion(self):
        rules = {"a*/*.kt": {"force": "interface"}, "app/omit/": "exclude"}
        self.assertEqual(classify.area("service", "A.kt", rules), "backend")
        self.assertEqual(classify.area("app", "A.kt", {"app/": "interface"}), "backend")
        result = report.analyse(MANIFEST, sample([["A.kt", 20, 0], ["omit/X.go", 80, 0]]),
                                rules, 1000, 160, "all")
        self.assertEqual(result["headline"]["interface_share"], 1)
        self.assertEqual(result["lines"]["excluded"]["override"], 80)

    def test_capped_ranking_and_contributors(self):
        files = [["huge/blob.zz", 50000, 0]] * 2
        files += [[f"real/{i}.zz", 1500, 0] for i in range(12)]
        files += [["screen.swift", 90, 1], ["src/main/B.go", 7, 2]]
        counts = defaultdict(Counter)
        rows = classify.ambiguous(sample(files), {}, counts)
        self.assertEqual([(r["prefix"], r["weight"]) for r in rows],
                         [("app/real/", 10000), ("app/huge/", 1000)])
        self.assertEqual(counts["interface", "extensão"][".swift"], 91)
        self.assertEqual(counts["backend", "repositório"]["app"], 9)

    def test_agent_tooling(self):
        for path in (".claude/skills/readme.md", ".cursor/rules/a.mdc", ".github/copilot-instructions.md"):
            self.assertEqual(classify.rule_area(path), "tooling")
        self.assertEqual(classify.rule_area(".github/workflows/ci.yml"), "infra")

    def test_title_styles(self):
        for title in ("Feat (abc): fix copy", "✨ feat: CSS control", "ABC-42 ADD: layout",
                      "[ABC-42] ✨ feat(ui): copy"):
            self.assertFalse(report.is_maintenance({"title": title, "labels": []}), title)
            self.assertTrue(report.is_maintenance({"title": title, "labels": ["bug"]}), title)  # labels decide first
        for title in ("[ABC-42] [CR] update modal", "🐛 FIX: modal", "ABC-42 FIX: panel"):
            self.assertTrue(report.is_maintenance({"title": title}), title)


class Round6(unittest.TestCase):
    """Fixes from the 100-org calibration (round 6)."""

    def test_tests_override_on_test_path_is_integration(self):
        ov = {"app/spec/requests/": {"force": "tests"}}
        self.assertEqual(classify.area_kind("app", "spec/requests/x_spec.rb", {}), ("backend", "unit"))
        self.assertEqual(classify.area_kind("app", "spec/requests/x_spec.rb", ov), ("tests", "integration"))
        moved = {"app/src/components/": {"force": "backend"}}  # another area: still counted as a unit test
        self.assertEqual(classify.area_kind("app", "src/components/A.test.tsx", moved), ("backend", "unit"))

    def test_release_copies_need_overlap(self):
        shared = [["a.ts", 5, 0], ["b.ts", 5, 0], ["c.ts", 5, 0]]
        prs = {"app": [pr(1, "ana", "2026-01-10T00:00:00Z", shared, title="Add billing"),
                       pr(2, "ana", "2026-01-11T00:00:00Z", shared, title="v1.2.3"),
                       pr(3, "ana", "2026-01-12T00:00:00Z", [["d.ts", 5, 0], ["e.ts", 5, 0], ["f.ts", 5, 0]], title="Release 2.57"),
                       pr(4, "ana", "2026-01-13T00:00:00Z", [["g.ts", 1, 0]], title="release-26.1: Add billing")]}
        self.assertEqual(classify.release_copies(prs), {("app", 2), ("app", 4)})  # 3 brings its own files
        r = report.analyse(MANIFEST, prs, {}, 1000, 160, "all")
        self.assertEqual((r["counts"]["prs_duplicate"], r["counts"]["prs_release_copy"]), (2, 2))
        self.assertIn("2 são cópias de release ou merge", report.render(r))

    def test_automation_titles_are_bots(self):
        for title in ("New Crowdin updates", "Translations update from Weblate", "Sync Strings",
                      "chore(deps): update dependency x", "Update dependencies"):
            self.assertTrue(classify.is_bot({"author": "ana", "author_type": "User", "title": title}), title)
        self.assertFalse(classify.is_bot({"author": "ana", "author_type": "User", "title": "Update translations UI"}))

    def test_translations(self):
        for path in ("App/de.lproj/Localizable.strings", "app/src/main/res/values-pt-rBR/strings.xml",
                     "res/values-b+sr+Latn/strings.xml", "extension/_locales/fr/messages.json", "chrome/app_de.xtb",
                     "share/translations/keepassxc_pt_BR.ts", "shared/i18n/locales/en_GB/translation.json",
                     "src/locale/de/app.json", "locales/zh-Hans-CN/common.json", "lang/es-419.json",
                     "config/locales/client.pt_BR.yml", "lib/l10n/app_zh.arb", "po/en_GB.po"):
            self.assertEqual(classify.exclusion(path), "translation", path)
        for path in ("App/en.lproj/Localizable.strings", "App/Base.lproj/Main.storyboard", "res/values/strings.xml",
                     "res/values-night/colors.xml", "extension/_locales/en/messages.json", "src/i18n/en-US.json",
                     "lib/l10n/app_en.arb", "src/i18n/index.ts", "share/translations/keepassxc_en.ts"):
            self.assertNotEqual(classify.exclusion(path), "translation", path)

    def test_issue_prefix_and_labels(self):
        m = report.is_maintenance
        self.assertFalse(m({"title": "Fix #123: Add Nepali", "labels": []}))
        self.assertFalse(m({"title": "Fix part of #12, #13: Support exponents", "labels": []}))
        self.assertTrue(m({"title": "Fix #123: Fix crash on save", "labels": []}))
        self.assertFalse(m({"title": "Closes #9 - New export", "labels": []}))
        self.assertTrue(m({"title": "Resolve #9: typo in header", "labels": []}))
        self.assertTrue(m({"title": "Update modal", "labels": ["type: bug"]}))
        self.assertTrue(m({"title": "feat: modal", "labels": ["kind/bug"]}))
        self.assertTrue(m({"title": "Modal", "labels": ["🐛 Regression"]}))
        self.assertFalse(m({"title": "Fix modal spacing", "labels": ["enhancement"]}))
        self.assertTrue(m({"title": "Fix modal spacing", "labels": ["area: web"]}))  # no typed label: title decides
        self.assertFalse(m({"title": "Update modal", "labels": ["css"]}))           # only type labels decide

    def test_small_sample_hides_maintenance(self):
        prs = {"app": [pr(i, "ana", "2026-01-10T00:00:00Z", [[f"src/components/{i}.tsx", 5, 0]], title="fix: x")
                       for i in range(29)]}
        r = report.analyse(MANIFEST, prs, {}, 1000, 160, "all")
        self.assertIsNone(r["headline"]["interface_maintenance_share"])
        self.assertIsNone(r["headline"]["interface_maintenance_annual_cost"])
        page = report.render(r)
        self.assertIn("A manutenção não foi medida: o período teve 29 PRs de interface", page)
        import benchmark, insights
        base = benchmark.aggregate([dict(r, org=str(i)) for i in range(8)])
        self.assertFalse({"capacity", "annual_gap", "module", "trend", "fix_share", "new"}
                         & {f["kind"] for f in insights.findings(r, base)})
        prs["app"].append(pr(99, "ana", "2026-01-10T00:00:00Z", [["src/components/z.tsx", 5, 0]], title="fix: y"))
        self.assertEqual(report.analyse(MANIFEST, prs, {}, 1000, 160, "all")["headline"]["interface_maintenance_share"], 1)

    def test_config_is_infra(self):
        for path in ("pnpm-workspace.yaml", ".gitattributes", "renovate.json", "Package.swift", "ios/App.xcconfig",
                     "ios/fastlane/Fastfile", "android/buildSrc/src/main/kotlin/Deps.kt", "web/composer.json",
                     "packages/ui/vitest.config.ts", "apps/web/jest.config.js", "App.xcodeproj/project.pbxproj"):
            self.assertEqual(classify.rule_area(path), "infra", path)
        self.assertEqual(classify.area_kind("app", "e2e/playwright.config.ts", {}), ("tests", "integration"))

    def test_templates_and_folder_names(self):
        for path in ("resources/views/home.blade.php", "Views/Home/Index.cshtml", "Pages/Login.razor",
                     "WEB-INF/index.jsp", "lib/app_web/live/page.html.heex", "templates/x.tmpl", "views/a.njk",
                     "templates/a.mustache", "app/components/x.gjs", "src/ui/Main.qml", "src/gui/Dialog.ui",
                     "app/views/cells/card.rb", "src/charts/LineChart.ts", "web/charts/Bar.tsx"):
            self.assertEqual(classify.rule_area(path), "interface", path)
        self.assertEqual(classify.rule_area("deploy/charts/app/values.yaml"), "infra")
        self.assertEqual(classify.rule_area("openapi-generator/templates/model.mustache"), "infra")
        self.assertEqual(classify.rule_area("enterprise/coderd/license/license.go"), "backend")
        self.assertEqual(classify.rule_area("LICENSE"), "docs")
        self.assertEqual(classify.rule_area("NOTICE.txt"), "docs")
        self.assertEqual(classify.rule_area("docs/guide/a.njk"), "docs")

    def test_native_cost_follows_overrides(self):
        files = [["ios/Screens/Home.m", 10, 0], ["Android/feature/Screen.kt", 10, 0]]
        ov = {"app/ios/": {"force": "interface"}, "app/Android/": {"force": "interface"}}
        r = report.analyse(MANIFEST, sample(files), ov, 1000, 160, "all")
        self.assertEqual(r["interface"]["native_cost"], r["interface"]["cost"])
