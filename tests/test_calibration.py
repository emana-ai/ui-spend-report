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
            self.assertFalse(report.is_maintenance({"title": title, "labels": ["bug"]}), title)
        for title in ("[ABC-42] [CR] update modal", "🐛 FIX: modal", "ABC-42 FIX: panel"):
            self.assertTrue(report.is_maintenance({"title": title}), title)
