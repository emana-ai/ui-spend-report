"""Every tunable parameter of the diagnostic lives here. Edit this file, not the scripts."""

# --- Questions asked to the user (defaults) ---------------------------------
MONTHS = 6                 # period: last N full calendar months
CURRENCY = "BRL"
HOURS_PER_MONTH = 160
AUTHORS = "all"            # all | members | min-prs:K

# --- Collection --------------------------------------------------------------
PAGE_SIZE = 50             # PRs per GraphQL page (halved automatically on timeouts)
RATE_LIMIT_FLOOR = 100     # sleep until reset when fewer GraphQL points remain

# --- Bots: author type "Bot" or login matching this ---------------------------
BOT_LOGIN = r"(\[bot\]$|^dependabot|^renovate|^github-actions|^snyk-|^greenkeeper|^mergify|^imgbot|^allcontributors|^weblate|^crowdin|^transifex|^lingohub|^codecov|^semantic-release|-bot$|^bot-|^.*-ci$)"

# --- Duplicate PRs (cherry-picks, backports, branch syncs): same work already counted elsewhere ---
DUPLICATE_PR = r"(^merge (branch|pull request|remote-tracking)|\b(sync|syncs|syncing|resync|fast-forward|merge|merging)\b.{0,30}\b(main|master|develop|dev|staging|release|epic)\b.{0,10}\b(into|with|to)\b|\b(into|with) (main|master|develop|epic)\b.*\b(sync|merge)|\bbackport\b|\bcherry[- ]?pick)"

# --- Automation run from human accounts: treated like bot PRs ------------------
AUTOMATION_TITLE = r"(new crowdin (updates|translations)|update(d)? translations from (crowdin|weblate|transifex)|^translations? update|translations update from|^sync strings|automatic merge back|automatic pr to merge|auto-update documentation|\[automatic composer updates\]|^(fix|chore)\(deps\): update|^update (npm |action |all |github actions |)dependencies$|prepare packages for release|^import new tokens|^update (the )?changelog|weblate|^l10n:? (update|sync|import))"

# --- Release/merge copies: a version, release or merge title AND files already changed by other PRs ---
RELEASE_TITLE = r"(^(\W*(release|chore\(release\)|chore: release|version packages)?[:\s-]*v?\d+(\.\d+)+([-.\w]*)?\s*$|^release[:\s]+v?\d|^(release|stage|cascade)\b.{0,40}(→|->| to | into )\s*(main|master|stage|staging|prod|production|release)|automatic pr to merge|^merge (release|dev|develop|staging)|\bbackported\b|\b(up|down)lift\b|update trunk to what was released))"
RELEASE_COPY = r"^release-\d+(\.\d+)*:"   # "release-26.1: <original title>": always a copy, no overlap needed
RELEASE_MIN_FILES = 3
RELEASE_MIN_OVERLAP = 0.80  # share of the PR's files also changed by another PR of the same repo in the period

# --- Maintenance PR -------------------------------------------------------------
# Labels decide first: a bug-type label = maintenance, a feature label = new work. Otherwise the title
# decides, after the issue-link prefix ("Fix #123:", "Closes #12 -") is stripped.
BUG_LABEL = r"(^([\w .-]*[:/]\s*)?(🐛\s*)?(bugs?|bug ?fix|bugfix|fix|fixes|regression|defect|hotfix)\b|🐛)"
FEATURE_LABEL = r"(^([\w .-]*[:/]\s*)?(✨\s*)?(feature|feat|new feature|enhancement)s?\b|✨)"
ISSUE_PREFIX = r"^\W*(fix(es|ed)?|close[sd]?|resolve[sd]?)\s+(part of\s+)?#\d+(\s*(,|and|&)\s*#\d+)*\s*[:\-–—]?\s*"
MAINT_MIN_UI_PRS = 30      # below this many interface PRs the maintenance rate is not published
MAINTENANCE = r"(\[cr\]|\bfix(es|ed)?\b|\bbug|\bhotfix|\bregress|\bajuste|\bcorre[cç](ão|ao|ões|oes)|\blayout\b|\bcss\b|\btypo|\bcopy\b|\bstyl(e|es|ing)\b|\bui tweak|\btweak)"

# Titles typed as new work (conventional commits) are never maintenance, whatever words follow.
NEW_WORK = r"^(?:\s|[^\w\s\[]|\[[^\]]+\]|[a-z]+-\d+|:\w+:)*(?:feat|feature|docs?|add)\s*(?:\([^)]*\)\s*)?!?:"

# --- Excluded files: (category, regex). Lines don't count, file doesn't count.
MOCK_JSON_LINES = 10000
AMBIGUOUS_FILE_CAP = 1000
AMBIGUOUS_DIR_CAP = 10000
EXCLUDE = [
    ("build", r"(\.(xcframework|framework)/|\.(swiftinterface|swiftdoc|car)$|(^|/)(_CodeSignature/|CodeResources$|embedded\.mobileprovision$))"),
    ("lockfile", r"(^|/)([\w.-]*\.lock|package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lockb?|poetry\.lock|pipfile\.lock|uv\.lock|gemfile\.lock|cargo\.lock|composer\.lock|go\.sum|mix\.lock|podfile\.lock|packages\.lock\.json|gradle\.lockfile|flake\.lock|pubspec\.lock|deps\.lock)$"),
    ("vendored", r"(^|/)(vendor|vendors|third[_-]party|thirdparty|3rd[-_]?party|3party|node_modules|bower_components|\.yarn)/"),
    ("build", r"((^|/)(dist|\.next|\.nuxt|\.turbo|coverage|storybook-static)/|\.min\.(js|css)$|\.(js|css)\.map$|\.bundle\.js$)"),
    ("snapshot", r"((^|/)(__(snapshots|screenshots|golden)__|cassettes)/|\.(snap|ambr|har)$)"),
    ("migration", r"((^|/)(migrations?|migrate|alembic)/|\.migration\.[jt]s$)"),
    # Translations into other languages: usually catalog-extraction churn or translators, not dev work.
    # Only the source locale (en, en-US, Base) stays and counts as interface (i18n/copy); en-GB, en-AU… are translations.
    ("translation", r"((^|/)(locales?|i18n|translations?|lang|langs|languages|l10n|messages|localization)/(?!(en|en[-_]us|base|source)[/.])(?![a-z]{2,3}[-_]en([-_]us)?[/.])[a-z]{2,3}([-_][a-z0-9]{2,4}){0,2}[/.]|(^|/)(?!en([-_]us)?\.po)[a-z]{2}([-_][a-z]{2,4})?\.po$)"),
    ("translation", r"(^|/)(locales?|i18n|translations?|lang|l10n|messages|res/lang|config/locales)/(.+/)?[\w-]+[._](?!en([_-]us)?\.)[a-z]{2}([_-][a-z]{2,4})?\.(xlf|xliff|ya?ml|json|properties|arb|po|strings)$"),  # client.pt_BR.yml, app_zh.arb
    ("translation", r"((^|/)(?!(en|en-us|base)\.lproj/)[\w-]+\.lproj/|(^|/)_locales/(?!en(_us)?/)[\w-]+/|\.(xtb|mo)$)"),  # Apple, Chrome extensions, gettext/Chromium binaries
    ("translation", r"(^|/)res/values-(?!night|v\d|land|port|sw\d|w\d|h\d)[a-z]{2,3}(-r[a-z]{2})?(-[\w]+)?/(strings|plurals|arrays)[^/]*\.xml$|(^|/)res/values-b\+[\w+]+/"),  # Android
    ("translation", r"(^|/)(translations?|i18n|l10n|lang)/[^/]*_(?!en(_us)?\.ts$)[a-z]{2}(_[a-z]{2})?\.ts$"),  # Qt Linguist catalogs (XML, not TypeScript)
    ("generated", r"((^|/)(__generated__|generated[\w-]*|[\w-]+-generated|codegen)/|\.generated\.|\.gen\.(ts|js|go)$|_pb2(_grpc)?\.pyi?$|\.pb(\.gw)?\.go$|\.g\.dart$|\.freezed\.dart$|(^|/)changelog(\.md)?$|(^|/)db/(schema\.rb|structure\.sql)$|(^|/)mocks/.*\.go$|_mock\.go$|(^|/)(package\.resolved|next-env\.d\.ts|skills-lock\.json|llms(-full)?\.txt|gradle-wrapper\.jar|mix-manifest\.json|sitemap[\w-]*\.xml)$|\.tsbuildinfo$|(^|/)schemas/[\w.]+/\d+\.json$)"),  # last: Room schemas
]

# Binary/image churn: the file counts (weight 1), its lines don't.
_BIN_EXT = "png|jpe?g|gif|webp|avif|ico|icns|bmp|tiff?|svg|pdf|woff2?|ttf|otf|eot|mp3|mp4|mov|webm|wav|ogg|zip|gz|tgz|jar|wasm|exe|dll|so|dylib|bin|lottie|riv|psd|sketch|fig|glyph"
BINARY = r"(\.(" + _BIN_EXT + r")$|\.(glyphs|glyphspackage|ufo)(/|$))"  # font sources are design assets too

# --- Area rules: (area, path regex, extension regex or None). First match wins.
# Files matching no rule are "ambiguous": the agent classifies them via overrides.json.
_JS = r"\.([cm]?[jt]sx?|vue|svelte|astro|json|ya?ml|css|scss|sass|less|styl|html?|mdx|" + _BIN_EXT + r")$"
_BACKEND_EXT = r"\.(go|py|rb|java|kt|kts|scala|clj|cljc|ex|exs|erl|rs|php|cs|fs|c|cc|cpp|h|hpp|groovy|lua|pl|proto|rake|jbuilder|rabl)$"
AREAS = [
    ("tooling", r"(^|/)(\.(claude|cursor)/|\.github/copilot|(?:[\w.-]+-)?(?:claude|cursor|copilot)-skills/)", None),
    ("tests", r"(^|/)androidTest/", None),
    ("tests", r"((^|/)(__tests__|__specs__|__mocks__|__fixtures__|tests?|spec|specs|e2e|cypress|playwright|detox|maestro|testdata|test[_-]resources|test-utils|test_utils|testutils?|testlib|test-helpers?|testhelpers?|storetest|testing|fixtures|[\w.-]+-(e2e|tests?)[\w.-]*|e2e-[\w.-]+)/|[._-](test|spec|e2e|cy)\.[a-z]+$|(^|/)test_[^/]+\.py$|_test\.(go|py|exs?)$|_spec\.rb$|tests?\.(java|kt)$|(^|/)(playwright|cypress)\.config\.[cm]?[jt]s$)", None),
    # build and tool config, whatever folder or extension: always infra (incl. unit-test runner config)
    ("infra", r"(^|/)(pnpm-workspace\.yaml|\.gitattributes|\.gitmodules|renovate\.json5?|gradle\.properties|action\.ya?ml|rust-toolchain(\.toml)?|\.yarnrc(\.yml)?|\.(ruby|python|java)-version|\.goreleaser\.ya?ml|\.golangci\.ya?ml|compose\.ya?ml|mise\.toml|knip\.json|composer\.json|components\.json|\.release-please-manifest\.json|release-please-config\.json|\.git-blame-ignore-revs|pytest\.ini|mypy\.ini|tox\.ini|metro\.config\.[cm]?js|analysis_options\.yaml|\.rubocop\.yml|sonar-project\.properties|proguard-rules\.pro|phpunit\.xml(\.dist)?|manifest\.in|lefthook\.ya?ml|crowdin\.ya?ml|cmakelists\.txt|deny\.toml|angular\.json|\.oxlintrc\.json|\.oxfmtrc\.jsonc?|\.coderabbit\.ya?ml|pubspec\.yaml|package\.swift|[\w.-]+\.(entitlements|xcconfig|pbxproj|xcscheme|xctestplan|xcprivacy|podspec|gemspec|csproj|props|neon)|tsdown\.config\.[cm]?[jt]s|_config\.yml|docusaurus\.config\.[cm]?[jt]s|(jest|vitest)\.(config|setup|workspace)[\w.-]*\.[cm]?[jt]s|setupTests\.[cm]?[jt]s)$|(^|/)(fastlane|buildsrc|gradle|\.githooks)/", None),
    ("interface", r"((^|/)(tailwind|postcss)\.config\.[cm]?[jt]s$|\.(swift|xcstrings|dart|po|pot|xliff|xlf|arb|strings|resx)$)", None),
    ("docs", r"((^|/)(docs?|documentation|handbook|adr|rfcs?)/|\.(md|mdx|rst|adoc)$|(^|/)(license|notice|authors|codeowners)(?![^/]*\.([cm]?[jt]sx?|go|php|py|rb|java|kt|rs|vue|svelte|swift|cs)$)[^/]*$)", None),  # License.go is code
    ("infra", r"(^\.github/|(^|/)\.(circleci|husky|devcontainer|vscode|idea|buildkite|gitlab|docker|changeset|cursor|claude|agents|codex|windsurf)/|(^|/)(\.[\w-]+ignore|owners)$|\.(tf|tfvars|hcl|sh|bash|zsh|ps1|bat|nix|patch|mdc)$)", None),
    ("infra", r"(^|/)(dockerfile[^/]*|docker-compose[^/]*|makefile|procfile|jenkinsfile|vercel\.json|netlify\.toml|turbo\.json|nx\.json|lerna\.json|package\.json|tsconfig[^/]*\.json|requirements[^/]*\.txt|pyproject\.toml|setup\.(py|cfg)|go\.mod|cargo\.toml|gemfile|deps\.edn|project\.clj|shadow-cljs\.edn|pom\.xml|(build|settings)\.gradle(\.kts)?|\.env[^/]*|\.npmrc|\.nvmrc|\.node-version|\.tool-versions|\.prettierrc[^/]*|\.eslintrc[^/]*|\.editorconfig|biome\.json|(eslint|prettier|vite|webpack|rollup|babel|esbuild|tsup|turbo|next|nuxt|svelte|astro)\.config\.[cm]?[jt]s|\.babelrc|codecov\.ya?ml|\.pre-commit-config\.yaml)$", None),
    # infra dirs anywhere (incl. scripts/); generic names (bin, build, tools, config) only at the repo root
    ("interface", r"(^|/)charts?/(.+/)?[^/]+\.([cm]?[jt]sx?|vue|svelte|css|scss)$", None),  # chart components, not Helm
    ("infra", r"((^|/)(docker|deploy|deployment|deployments|helm|charts|k8s|kubernetes|terraform|infra|infrastructure|ansible|\.ci)/|(^|/)scripts/|^(ci|bin|build|tools|config|configs|conf|dev|release)/)", None),
    ("data", r"((^|/)(seeds?|seeders|prisma|sql|dbt|etl)/|\.(sql|prisma|csv|parquet)$)", None),
    ("backend", r"(^|/)(mcp|mcp-server|[\w.@-]+-mcp)/", None),  # MCP servers, even inside a client/ folder
    # front-end roots: any JS/style/markup/asset file below them is interface
    ("interface", r"(^|/)(webapp|frontend|front|editor|editor-ui|client|client-only|web-app|design-system|[\w.@-]+-(front|frontend|ui|website)(-[\w.-]+)?|[\w.@-]+-(webapp|client|editor|design-system|emails?|components|landing))/", _JS),
    ("backend", r"((^|/)(pages|app|routes)/api/|(^|/)(api|apiserver|server|servers|server-only|backend|trpc|worker|workers|jobs|cron|queues?|lambdas?|swagger|openapi|[\w.@-]+-(server|backend|api|worker))/)", None),
    ("infra", r"(^|/)[\w.-]*(open-?api|codegen|generators?|sdks?)[\w.-]*/(.+/)?[^/]+\.mustache$", None),  # SDK generator templates
    # markup and server/desktop templates
    ("interface", r"\.(tsx|jsx|vue|svelte|astro|css|scss|sass|less|styl|html?|hbs|handlebars|ejs|erb|haml|slim|liquid|mjml|xib|storyboard|twig|blade\.php|cshtml|razor|jsp|gsp|heex|leex|tmpl|gohtml|njk|jinja2?|mustache|underscore|gjs|gts|tss|qml|ui|xaml|axaml)$", None),
    ("interface", BINARY, None),
    ("interface", r"(^|/)(components?|pages|views?|screens?|layouts?|styles?|themes?|ui|widgets|hooks|composables|contexts?|stores?|redux|reducers|selectors|slices|atoms|i18n|locales?|translations?|lang|emails?|email-templates|templates|assets|icons|public|static|storybook|\.storybook|app/javascript)/", _JS),
    ("interface", r"(^|/)src/main/res/|(^|/)res/(layout|drawable|values|mipmap|menu|navigation|animator|anim|color|font|interpolator|raw|transition|xml)[^/]*/", None),
    ("interface", r"(^|/)app/(helpers|views|components|mailers)/.*\.rb$|(^|/)resources/views/|_web/(components|live)/.*\.exs?$|(^|/)view/components/.*\.php$", None),  # server-side view code
    ("backend", _BACKEND_EXT, None),
    ("backend", r"(^|/)(controllers?|middlewares?|resolvers|handlers|webhooks|nodes|credentials|migrations)/", r"\.[cm]?[jt]s$"),
]

# --- Unit tests belong to the area of the code they test ----------------------
# A file the rules put in "tests" is a unit test unless it matches INTEGRATION_TESTS. A unit test is
# classified by its tested path: test dirs (UNIT_TEST_DIR, whole segment) dropped and the test marker
# stripped from the file name (UNIT_TEST_MARKERS, applied in order). If that path still doesn't
# resolve and the test sat in a test tree, the mirrored source roots (MIRROR_ROOTS) are tried:
# tests/unit/api/x.py → api/x.py, then src/api/x.py, lib/api/x.py… If nothing resolves, it takes the
# dominant area of the non-test, non-docs code changed in its package (the directory before the first
# test dir, or its own directory; parents, up to the repo root, are tried while a level has fewer than
# INFER_MIN_LINES lines of such code) — "unit, area inferred".
# A "tests" override in overrides.json means an integration suite. False = round 1: every test file is "tests".
UNIT_TESTS_FOLLOW_AREA = True
INTEGRATION_TESTS = r"((^|/)([\w.-]*e2e[\w.-]*|androidTest|integration|integration[-_]tests?|[\w.-]+[-_]integration[-_]tests?|it|cypress|playwright|detox|maestro|selenium|puppeteer|load|loadtests?|load[-_]tests?|perf|performance|stress|benchmarks?|k6|locust|smoke)/|[._-](e2e|cy)\.[^/]*$|[._-](integration|int)[._-](test|spec)s?\.[^/]*$|(?-i:[a-z0-9]IT)\.(java|kt)$|(^|/)(playwright|cypress)\.config\.[cm]?[jt]s$)"
UNIT_TEST_DIR = r"(__tests__|__specs__|__mocks__|__fixtures__|tests?|specs?|unit|unit[-_]tests?|testdata|test[_-]resources|test-utils|test_utils|testutils?|testlib|test-helpers?|testhelpers?|storetest|testing|fixtures|[\w.-]+-tests?[\w.-]*)"
MIRROR_ROOTS = ["src", "lib", "app"]
INFER_MIN_LINES = 50       # a directory decides an inferred area only with at least this many lines of code changed
UNIT_TEST_MARKERS = [
    r"^test_(?=[^/]+\.py$)",                                          # test_foo.py → foo.py
    r"(?i:([._-]unit)?[._-](test|spec)s?)(?=\.[^.]+$)",               # Button.test.tsx, foo_test.go, user_spec.rb, x.unit.spec.ts
    r"(?<=[a-z0-9])(Test|Tests|Spec)(?=\.(java|kt|kts|scala|groovy|cs|swift)$)",  # FooTest.java → Foo.java
]

# --- Report --------------------------------------------------------------------
TOP_DIR_DEPTH = 4          # directory depth for "top interface paths"
TOP_DIRS = 12
AMBIGUOUS_DEPTH = 4        # directory depth used to group ambiguous files

# Editorial thresholds. Stakes use costs from the observed period.
CONCENTRATION_MIN = 0.30
TREND_MIN_MONTHS = 4
TREND_MIN_SLOPE = 0.015
TREND_MIN_R2 = 0.55
WORK_MIX_MIN = 0.55
NATIVE_MIN = 0.10
BENCH_MIN = 4
CLOSING_TEXT = "As telas que mais custam podem mudar sem entrar no roadmap. É isso que a emana.ai faz."
CLOSING_URL = "https://emana.ai"
HOTSPOT_MIN_MAINT_COST_SHARE = 0.05
COHORT_MIN = 8
FIX_MIN_PRS = 10
FIX_GAP = 0.05
FIX_MARKER = r"\b(fix(?:es|ed)?|bug(?:fix)?|hotfix|regression|correção|corrigir)\b"
PEAK_MIN_RATIO = 1.25
