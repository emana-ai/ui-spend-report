#!/usr/bin/env python3
"""Deterministic file → area classification, plus the ambiguous-files workflow.

  python3 classify.py --out DIR     # writes DIR/ambiguous.json (applies DIR/overrides.json if present)

ambiguous.json groups unmatched files by directory. The agent reads it and writes overrides.json:
  {"<repo>/<path prefix>": "interface|backend|infra|tests|data|docs|other", ...}
See references/method.md for scoped globs, force and exclude.
"""
import argparse, json, re, sys
from collections import Counter, defaultdict
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import defaults

AREAS = ("interface", "backend", "infra", "tests", "data", "docs", "other", "tooling")
_EXCLUDE = [(c, re.compile(r, re.I)) for c, r in defaults.EXCLUDE]
_BINARY = re.compile(defaults.BINARY, re.I)
_RULES = [(a, re.compile(p, re.I), re.compile(e, re.I) if e else None) for a, p, e in defaults.AREAS]
_BOT = re.compile(defaults.BOT_LOGIN, re.I)
_DUP = re.compile(defaults.DUPLICATE_PR, re.I)
_AUTO = re.compile(defaults.AUTOMATION_TITLE, re.I)
_RELEASE = re.compile(defaults.RELEASE_TITLE, re.I)
_RELEASE_COPY = re.compile(defaults.RELEASE_COPY, re.I)
_INTEG = re.compile(defaults.INTEGRATION_TESTS, re.I)
_UNIT_DIR = re.compile(defaults.UNIT_TEST_DIR, re.I)
_UNIT_MARK = [re.compile(r) for r in defaults.UNIT_TEST_MARKERS]  # case set inside each regex


def is_automation(pr):
    """Translation syncs, dependency updates, merge-backs run from a human account."""
    return bool(_AUTO.search(pr.get("title", "")))


def is_bot(pr):
    login = pr.get("author")
    return not login or pr.get("author_type") == "Bot" or bool(_BOT.search(login)) or is_automation(pr)


def release_copies(prs_by_repo):
    """{(repo, number)} of version/release/merge PRs whose files other PRs of the repo already changed."""
    out = set()
    for repo, prs in prs_by_repo.items():
        seen = Counter(f for pr in prs for f in {x[0] for x in pr["files"]})
        for pr in prs:
            title, files = pr["title"].strip(), {x[0] for x in pr["files"]}
            if _RELEASE_COPY.search(title) or (
                    _RELEASE.search(title) and len(files) >= defaults.RELEASE_MIN_FILES
                    and sum(seen[f] > 1 for f in files) >= defaults.RELEASE_MIN_OVERLAP * len(files)):
                out.add((repo, pr["number"]))
    return out


def is_duplicate(pr, repo=None, copies=()):
    """Cherry-pick, backport, branch sync or release copy: repeats work counted in another PR."""
    return bool(_DUP.search(pr["title"])) or (repo, pr.get("number")) in copies


@lru_cache(maxsize=None)
def exclusion(path):
    """Exclusion category of a file, 'binary' (file counts, lines don't) or None."""
    for cat, rx in _EXCLUDE:
        if rx.search(path):
            return cat
    return "binary" if _BINARY.search(path) else None


@lru_cache(maxsize=None)
def rule_area(path):
    """Area from the path rules, or None when no rule matches (ambiguous)."""
    for area, rx, ext in _RULES:
        if rx.search(path) and (ext is None or ext.search(path)):
            return area
    return None


def file_exclusion(path, lines):
    if (lines > defaults.MOCK_JSON_LINES and path.lower().endswith(".json")
            and re.search(r"(^|/)_*(?:mocks?|fixtures?)(?:[-_][^/]*)?/", path, re.I)):
        return "generated"
    return exclusion(path)


class CodeIndex(defaultdict):
    def __init__(self, prs):
        super().__init__(Counter)
        self.modules = defaultdict(set)
        for repo, entries in prs.items():
            for pr in entries:
                for path, _, _ in pr["files"]:
                    marker = re.search(r"(?:^|/)(?:src/[^/]+/AndroidManifest\.xml$|AndroidManifest\.xml$|src/main/res/|(?:src/)?androidTest/)", path)
                    if marker:
                        self.modules[repo].add(path[:marker.start()])


def code_area(repo, path, overrides, index=None):
    return _code_area(repo, path, overrides, index)[0]


def _code_area(repo, path, overrides, index=None):
    """(area, True when an override decided it)."""
    a = rule_area(path)
    if index is not None and a in (None, "backend") and path.endswith((".kt", ".java")):
        if any(not root or path.startswith(root + "/") for root in index.modules.get(repo, ())):
            a = "interface"
    full = f"{repo}/{path}"
    matches = []
    for key, value in overrides.items():
        force = isinstance(value, dict)
        if a and not force and value != "exclude":
            continue
        if any(char in key for char in "*?["):
            org_pattern, _, pattern = key.partition("/")
            match = fnmatchcase(repo, org_pattern) and fnmatchcase(path, pattern)
        else:
            match = full.startswith(key)
        if match:
            matches.append((len(key), key, value["force"] if force else value))
    return (max(matches)[2], True) if matches else (a, False)


@lru_cache(maxsize=None)
def tested_path(path):
    """(path of the code a unit test tests, its package dir): test dirs dropped, test marker stripped.
    The package dir is the prefix before the first test dir, or the file's own dir."""
    *dirs, name = path.split("/")
    is_test = [bool(_UNIT_DIR.fullmatch(d)) for d in dirs]
    for rx in _UNIT_MARK:
        name = rx.sub("", name)
    first = is_test.index(True) if True in is_test else len(dirs)
    return "/".join([d for d, t in zip(dirs, is_test) if not t] + [name]), "/".join(dirs[:first])


def code_index(prs_by_repo, overrides):
    """{(repo, dir): Counter(area → lines)} of non-test, non-docs code, every dir level: the fallback for unit tests."""
    idx = CodeIndex(prs_by_repo)
    copies = release_copies(prs_by_repo)
    for repo, prs in sorted(prs_by_repo.items()):
        for pr in prs:
            if is_bot(pr) or is_duplicate(pr, repo, copies):
                continue
            for path, add, dele in pr["files"]:
                cat = file_exclusion(path, add + dele)
                a = code_area(repo, path, overrides, idx) if cat in (None, "binary") else None
                if a in (None, "tests", "docs", "exclude", "tooling"):  # code only: docs never decide a test's area
                    continue
                dirs = path.split("/")[:-1]
                for i in range(len(dirs) + 1):
                    idx[(repo, "/".join(dirs[:i]))][a] += max(0 if cat else add + dele, 1)
    return idx


def area_kind(repo, path, overrides, index=None):
    """(area, kind). kind: None (not a test), "integration", "unit", "unit_inferred" or
    "unit_unresolved" (no non-test code in the repo to infer from: stays in tests)."""
    a, overridden = _code_area(repo, path, overrides, index)
    if not defaults.UNIT_TESTS_FOLLOW_AREA:
        return a, None
    if a != "tests":  # an override moved a unit test to another area: it still counts as a unit test
        unit = overridden and a in AREAS and rule_area(path) == "tests" and not _INTEG.search(path)
        return a, "unit" if unit else None
    if overridden or _INTEG.search(path):  # "tests" from overrides.json = an integration suite
        return "tests", "integration"
    bare, pkg = tested_path(path)
    tries = [bare] if bare != path else []
    if pkg != path.rpartition("/")[0]:  # top-level test tree: also try the mirrored source roots
        rest = bare[len(pkg) + 1:] if pkg else bare
        tries += [f"{pkg}/{r}/{rest}".lstrip("/") for r in defaults.MIRROR_ROOTS]
    for t in tries:
        b = code_area(repo, t, overrides, index)
        if b == "tests":  # the tested code is itself a test suite (agent override): integration
            return "tests", "integration"
        if b and b != "exclude":
            return b, "unit"
    dirs = pkg.split("/") if pkg else []
    for i in range(len(dirs), -1, -1):
        c = (index or {}).get((repo, "/".join(dirs[:i])))
        if c and (sum(c.values()) >= defaults.INFER_MIN_LINES or i == 0):  # repo root decides on any evidence
            return max(sorted(c), key=c.get), "unit_inferred"  # sorted: ties go to the first area name
    return "tests", "unit_unresolved"


def area(repo, path, overrides, index=None):
    return area_kind(repo, path, overrides, index)[0]  # None = still ambiguous (counted as "other")


def load_overrides(out):
    p = Path(out) / "overrides.json"
    if not p.exists():
        return {}
    o = json.loads(p.read_text(encoding="utf-8"))
    bad = {k: v for k, v in o.items() if (v.get("force") if isinstance(v, dict) else v) not in (*AREAS, "exclude")}
    if bad:
        sys.exit(f"overrides.json: área inválida {bad}; use uma de {AREAS}")
    return o


def load_cache(out):
    cache = Path(out) / "cache"
    manifest = json.loads((cache / "manifest.json").read_text(encoding="utf-8"))
    prs = {r: json.loads((cache / "prs" / f"{r}.json").read_text(encoding="utf-8")) for r in manifest["repos"]}
    return manifest, prs


def ambiguous(prs_by_repo, overrides, summary=None):
    index = code_index(prs_by_repo, overrides)
    copies = release_copies(prs_by_repo)
    groups = defaultdict(lambda: {"files": 0, "lines": 0, "exts": defaultdict(int), "paths": Counter(), "titles": set()})
    for repo, prs in sorted(prs_by_repo.items()):
        for pr in prs:
            if is_bot(pr) or is_duplicate(pr, repo, copies):
                continue
            for path, add, dele in pr["files"]:
                cat = file_exclusion(path, add + dele)
                if cat not in (None, "binary"):
                    continue
                bucket = area(repo, path, overrides, index)
                if bucket and bucket != "exclude" and summary is not None:
                    for field, value in (("extensão", Path(path).suffix or "(sem extensão)"), ("repositório", repo)):
                        summary[bucket, field][value] += 0 if cat else add + dele
                if bucket:
                    continue
                key = "/".join([repo] + path.split("/")[:-1][:defaults.AMBIGUOUS_DEPTH])
                g = groups[key]
                g["files"] += 1
                g["lines"] += add + dele
                g["exts"][Path(path).suffix or Path(path).name] += 1
                g["paths"][path] += add + dele
                g["titles"].add(pr["title"])
    rows = [{"weight": min(defaults.AMBIGUOUS_DIR_CAP, sum(min(n, defaults.AMBIGUOUS_FILE_CAP) for n in g["paths"].values())), "prefix": k + "/", "files": g["files"], "lines": g["lines"], "exts": dict(sorted(g["exts"].items())),
             "sample_paths": sorted(g["paths"])[:6], "sample_titles": sorted(g["titles"])[:4]}
            for k, g in groups.items()]
    return sorted(rows, key=lambda r: (-r["weight"], r["prefix"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    _, prs = load_cache(a.out)
    summary = defaultdict(Counter)
    rows = ambiguous(prs, load_overrides(a.out), summary)
    for (bucket, field), counts in sorted(summary.items()):
        print(bucket, field, counts.most_common(5))
    (Path(a.out) / "ambiguous.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = sum(r["lines"] for r in rows)
    print(f"{len(rows)} diretórios ambíguos, {sum(r['files'] for r in rows)} arquivos, {lines} linhas → "
          f"{Path(a.out) / 'ambiguous.json'}")


if __name__ == "__main__":
    main()
