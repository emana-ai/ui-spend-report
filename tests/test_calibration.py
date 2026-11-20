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
        self.assertFalse({"capacity", "fix_share"} & {f["kind"] for f in insights.findings(r, base)})
        self.assertEqual((r["hotspots"], r["rework"], r["trend"]), (None, None, None))
        self.assertIsNone(r["platforms"]["shown"][0]["maintenance_share"])
        self.assertIn("Custo mensal de interface. A divisão entre novo e manutenção não aparece", page)
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


def rec(path, day, cost, maint, pr=None, platform="web"):
    from datetime import datetime, timedelta
    at = datetime(2026, 1, 1) + timedelta(days=day)
    return dict(pr=("app", pr if pr is not None else day), file=("app", path), month=at.strftime("%Y-%m"), at=at,
                cost=cost, maint=maint, fix=False, native=False, dir="app", module=report.module("app", path),
                platform=platform, surface=None)


class Round7(unittest.TestCase):
    def test_hotspots_concentration(self):
        recs = [rec("src/modules/a/x.tsx", 1, 80, True), rec("src/modules/b/y.tsx", 2, 15, True),
                rec("src/modules/b/z.tsx", 3, 5, True)] + [rec(f"src/ui/{i}.tsx", 4 + i, 10, False) for i in range(7)]
        h = report.hotspots(recs)
        self.assertEqual((h["files"], h["top_files"], h["maintenance_files"]), (10, 1, 3))
        self.assertAlmostEqual(h["top_file_share"], 0.1)
        self.assertAlmostEqual(h["top_maintenance_share"], 0.8)  # one file of ten carries 80%
        self.assertEqual((h["curve"][0], h["curve"][10], h["curve"][-1]), (0, 0.8, 1))
        self.assertEqual([m["path"] for m in h["modules"]], ["app/src/modules/a", "app/src/modules/b"])
        self.assertEqual((h["modules"][1]["share"], h["modules"][1]["prs"], h["modules"][1]["files"]), (0.2, 2, 2))
        self.assertEqual(report.module("n8n", "packages/frontend/editor-ui/src/features/ai/chat/X.vue"),
                         "n8n/packages/frontend/editor-ui/src/features/ai")
        self.assertEqual(report.module("web", "ui/button/x.tsx"), "web/ui")

    def test_rework_window(self):
        from datetime import datetime
        until = datetime(2026, 4, 1)
        recs = [rec("a.tsx", 0, 10, False, 1), rec("b.tsx", 0, 30, False, 1),   # new work, two files
                rec("a.tsx", 30, 4, True, 2),                                      # day 30: inside the window
                rec("b.tsx", 31, 6, True, 3),                                      # day 31: outside
                rec("c.tsx", 10, 20, False, 4), rec("c.tsx", 10, 5, True, 5),      # same instant: not after
                rec("d.tsx", 80, 50, False, 6), rec("d.tsx", 81, 7, True, 7)]      # merged < 30 days before the end
        r = report.rework(recs, until)
        self.assertEqual((r["new_prs"], r["reworked_prs"]), (2, 1))
        self.assertEqual((r["new_cost"], r["reworked_cost"]), (60, 10))  # file level: only a.tsx, not the whole PR
        self.assertEqual((r["followup_cost"], r["followup_prs"]), (4, 1))
        self.assertIsNone(r["share"])                                     # 2 new-work PRs < 30: not published
        many = recs + [rec(f"n{i}.tsx", 1, 1, False, 100 + i) for i in range(28)]
        self.assertAlmostEqual(report.rework(many, until)["share"], 10 / 88)

    def test_trend_halves(self):
        months = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05"]
        ui = dict.fromkeys(months, 100)
        prs = dict.fromkeys(months, 20)
        maint = {"2026-01": 30, "2026-02": 40, "2026-03": 99, "2026-04": 50, "2026-05": 60}
        t = report.trend(months, ui, maint, prs)
        self.assertEqual((t["first"], t["second"], t["first_months"], t["second_months"]),
                         (0.35, 0.55, months[:2], months[3:]))  # odd count: the middle month is left out
        self.assertFalse(t["stable"])
        maint["2026-04"], maint["2026-05"] = 33, 38
        self.assertTrue(report.trend(months, ui, maint, prs)["stable"])  # +0,5 p.p.
        prs["2026-05"] = 5
        self.assertIsNone(report.trend(months, ui, maint, prs))  # second half: 25 interface PRs < 30

    def test_platforms(self):
        for path, name in (("ios/App/Home.swift", "ios"), ("app-android/src/main/res/layout/a.xml", "android"),
                           ("lib/screens/home.dart", "mobile"), ("apps/mobile/src/Home.tsx", "mobile"),
                           ("apps/desktop/src/Window.tsx", "desktop"), ("src/ui/Main.qml", "desktop"),
                           ("app/views/home/index.html.erb", "server"), ("Pages/Login.cshtml", "server"),
                           ("apps/web/src/Home.tsx", "web"), ("src/studios/List.tsx", "web")):
            self.assertEqual(report.platform("repo", path), name, path)
        self.assertEqual(report.platform("acme-ios", "Sources/Home.swift"), "ios")
        recs = ([rec(f"web/{i}.tsx", i, 10, i % 2 == 0, i) for i in range(30)]
                + [rec("ios/A.swift", 1, 20, True, 100, "ios"), rec("x.qml", 2, 2, False, 101, "desktop")])
        pf = report.platforms(recs, sum(x["cost"] for x in recs), True)
        self.assertEqual([x["platform"] for x in pf["shown"]], ["web", "ios"])  # desktop 0,6% < 3%: hidden
        self.assertAlmostEqual(pf["hidden_share"], 2 / 322)
        self.assertEqual(pf["shown"][0]["maintenance_share"], 0.5)
        self.assertIsNone(pf["shown"][1]["maintenance_share"])  # 1 iOS PR < 30: rate not published
        self.assertIsNone(report.platforms(recs, 322, False)["shown"][0]["maintenance_share"])

    def test_overrides_move_platforms(self):
        files = [["Android/feature/Screen.kt", 10, 0], ["src/components/A.tsx", 10, 0]]
        r = report.analyse(MANIFEST, sample(files), {"app/Android/": {"force": "interface"}}, 1000, 160, "all")
        self.assertEqual({x["platform"] for x in r["platforms"]["shown"]}, {"android", "web"})
        r = report.analyse(MANIFEST, sample(files), {"app/Android/": {"force": "backend"}}, 1000, 160, "all")
        self.assertEqual({x["platform"] for x in r["platforms"]["shown"]}, {"web"})

    def test_android_manifest_and_build_dirs(self):
        for path in ("app/src/main/AndroidManifest.xml", "AndroidManifest.xml", "feature/src/debug/AndroidManifest.xml"):
            self.assertEqual(classify.rule_area(path), "infra", path)
        self.assertEqual(classify.area("app", "app/src/main/AndroidManifest.xml", {"app/app/": "interface"}), "infra")
        for path in ("src/App/obj/Debug/App.AssemblyInfo.cs", "android/.gradle/8.0/checksums.bin", "app/.cxx/x/build.ninja"):
            self.assertEqual(classify.exclusion(path), "build", path)
        files = [["app/src/main/AndroidManifest.xml", 5, 0], ["app/src/main/java/a/HomeActivity.kt", 10, 0]]
        r = report.analyse(MANIFEST, sample(files), {}, 1000, 160, "all")
        self.assertGreater({a["area"]: a["cost"] for a in r["areas"]}["interface"], 0)  # still a module marker

    def test_closing_constant(self):
        import defaults
        self.assertEqual(defaults.CLOSING_TEXT, "Você viu quanto custa manter a interface. A emana.ai mapeia o retorno "
                                                "desse esforço e entrega soluções que aumentam os retornos reduzindo os custos.")
        r = report.analyse(MANIFEST, sample([["src/components/A.tsx", 5, 0]]), {}, 1000, 160, "all")
        page = report.render(r)
        self.assertIn(f'<p class="closing"><a href="{defaults.CLOSING_URL}">{defaults.CLOSING_TEXT}</a></p>', page)
        for gone in ("<details", "title=", "Ação", "Onde agir"):
            self.assertNotIn(gone, page)

    def test_rework_base_metric(self):
        import benchmark
        files = lambda i: [[f"src/components/{i}.tsx", 5, 0], ["src/components/Shared.tsx", 1, 0]]
        prs = {"app": [pr(i, "ana", f"2026-01-{i + 1:02d}T00:00:00Z", files(i)) for i in range(30)]
               + [pr(99, "ana", "2026-01-20T00:00:00Z", [["src/components/Shared.tsx", 3, 0]], title="fix: x")]}
        r = report.analyse(MANIFEST, prs, {}, 1000, 160, "all")
        self.assertGreater(r["rework"]["share"], 0)
        base = benchmark.aggregate([dict(r, org=str(i)) for i in range(8)])
        self.assertEqual(base["percentiles"]["rework_share"]["p50"], round(r["rework"]["share"], 6))
        self.assertIn("Posição na base", report.render(r, base=base))
        asset = __import__("json").loads(benchmark.ASSET.read_text(encoding="utf-8"))
        self.assertNotIn("rework_share", asset["percentiles"])  # Hermes recomputes the base; until then: hidden
        self.assertNotIn("Posição na base", report.render(r, base=asset))
        old = dict(r)
        old.pop("rework")
        self.assertIsNone(benchmark.aggregate([old])["points"][0]["rework_share"])  # pre-r7 reports

    def test_single_platform_line(self):
        recs = [rec(f"web/{i}.tsx", i, 10, i % 2 == 0, i) for i in range(40)]
        r = report.analyse(MANIFEST, sample([["src/components/A.tsx", 5, 0]]), {}, 1000, 160, "all")
        r["platforms"] = report.platforms(recs, 400, True)
        page = report.render(r)
        self.assertIn('<p class="limit">Toda a interface medida é web (100% do custo).</p>', page)
        self.assertNotIn("Plataformas</small>", page)
        r["platforms"] = report.platforms(recs + [rec("ios/A.swift", 1, 8, True, 100, "ios")], 408, True)  # web 98%
        self.assertIn("Quase toda a interface medida é web (98% do custo).", report.render(r))
        r["platforms"] = report.platforms(recs + [rec("ios/A.swift", 1, 40, True, 100, "ios")], 440, True)  # web 91%
        page = report.render(r)
        self.assertIn("Plataformas</small>", page)
        self.assertNotIn("interface medida é", page)


class Round7b(unittest.TestCase):
    def test_hotspots_count_screen_files(self):
        files = [["src/components/A.tsx", 100, 0], ["src/components/A.test.tsx", 10, 0],
                 ["src/components/A.stories.tsx", 10, 0], ["src/__stories__/Gallery.tsx", 10, 0],
                 ["src/assets/logo.png", 0, 0], ["src/components/A.module.scss", 1, 0],
                 ["src/components/A.module.scss.d.ts", 10, 0]]
        prs = {"app": [pr(i, "ana", "2026-01-10T00:00:00Z", files, title="fix: x") for i in range(30)]}
        self.assertEqual(classify.exclusion("src/components/A.module.scss.d.ts"), "generated")
        self.assertEqual(classify.file_exclusion("public/models/robot.glb", 0), "binary")
        r = report.analyse(MANIFEST, prs, {}, 1000, 160, "all")
        self.assertEqual(r["scopes"]["interface"]["files"], 6)  # tests, stories and images still cost interface
        self.assertEqual(r["hotspots"]["files"], 2)              # only A.tsx and A.module.scss are screen files
        page = report.render(r)
        self.assertIn("<b>1 arquivo</b> (50,0% dos que mudaram) concentra 99% do custo de manutenção.", page)
        self.assertIn("2 arquivos de interface mudaram no período, sem contar testes, stories e imagens.", page)

    def test_sentences_not_in_mono(self):
        css = report.CSS[report.CSS.index("/* r7b"):]
        self.assertIn(".limit,.block .limit,.axis small,.stats small{font:14px/1.45 'Hanken Grotesk'", css)
