#!/usr/bin/env python3
"""Deterministic file → area classification, plus the ambiguous-files workflow.

  python3 classify.py --out DIR     # writes DIR/ambiguous.json (applies DIR/overrides.json if present)

ambiguous.json groups unmatched files by directory. The agent reads it and writes overrides.json:
  {"<repo>/<path prefix>": "interface|backend|infra|tests|data|docs|other", ...}
Longest prefix wins. Overrides only apply to files the rules left ambiguous.
"""
import argparse, json, re, sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import defaults

AREAS = ("interface", "backend", "infra", "tests", "data", "docs", "other")
_EXCLUDE = [(c, re.compile(r, re.I)) for c, r in defaults.EXCLUDE]
_BINARY = re.compile(defaults.BINARY, re.I)
_RULES = [(a, re.compile(p, re.I), re.compile(e, re.I) if e else None) for a, p, e in defaults.AREAS]
_BOT = re.compile(defaults.BOT_LOGIN, re.I)
_DUP = re.compile(defaults.DUPLICATE_PR, re.I)
_INTEG = re.compile(defaults.INTEGRATION_TESTS, re.I)
_UNIT_DIR = re.compile(defaults.UNIT_TEST_DIR, re.I)
_UNIT_MARK = [re.compile(r) for r in defaults.UNIT_TEST_MARKERS]  # case set inside each regex


def is_bot(pr):
    login = pr.get("author")
    return not login or pr.get("author_type") == "Bot" or bool(_BOT.search(login))


def is_duplicate(pr):
    """Cherry-pick, backport or branch sync: repeats work counted in another PR."""
    return bool(_DUP.search(pr["title"]))


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


def code_area(repo, path, overrides):
    """Area from the rules, then overrides.json; no unit-test handling. None = ambiguous."""
    a = rule_area(path)
    if a:
        return a
    full = f"{repo}/{path}"
    best = max((k for k in overrides if full.startswith(k)), key=len, default=None)
    return overrides[best] if best else None


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
    idx = defaultdict(Counter)
    for repo, prs in sorted(prs_by_repo.items()):
        for pr in prs:
            if is_bot(pr) or is_duplicate(pr):
                continue
            for path, add, dele in pr["files"]:
                cat = exclusion(path)
                a = code_area(repo, path, overrides) if cat in (None, "binary") else None
                if a in (None, "tests", "docs"):  # code only: docs never decide a test's area
                    continue
                dirs = path.split("/")[:-1]
                for i in range(len(dirs) + 1):
                    idx[(repo, "/".join(dirs[:i]))][a] += max(0 if cat else add + dele, 1)
    return idx


def area_kind(repo, path, overrides, index=None):
    """(area, kind). kind: None (not a test), "integration", "unit", "unit_inferred" or
    "unit_unresolved" (no non-test code in the repo to infer from: stays in tests)."""
    a = code_area(repo, path, overrides)
    if a != "tests" or not defaults.UNIT_TESTS_FOLLOW_AREA:
        return a, None
    if rule_area(path) != "tests" or _INTEG.search(path):  # "tests" from overrides.json = an integration suite
        return "tests", "integration"
    bare, pkg = tested_path(path)
    tries = [bare] if bare != path else []
    if pkg != path.rpartition("/")[0]:  # top-level test tree: also try the mirrored source roots
        rest = bare[len(pkg) + 1:] if pkg else bare
        tries += [f"{pkg}/{r}/{rest}".lstrip("/") for r in defaults.MIRROR_ROOTS]
    for t in tries:
        b = code_area(repo, t, overrides)
        if b == "tests":  # the tested code is itself a test suite (agent override): integration
            return "tests", "integration"
        if b:
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
    bad = {k: v for k, v in o.items() if v not in AREAS}
    if bad:
        sys.exit(f"overrides.json: área inválida {bad}; use uma de {AREAS}")
    return o


def load_cache(out):
    cache = Path(out) / "cache"
    manifest = json.loads((cache / "manifest.json").read_text(encoding="utf-8"))
    prs = {r: json.loads((cache / "prs" / f"{r}.json").read_text(encoding="utf-8")) for r in manifest["repos"]}
    return manifest, prs


def ambiguous(prs_by_repo, overrides):
    groups = defaultdict(lambda: {"files": 0, "lines": 0, "exts": defaultdict(int), "paths": set(), "titles": set()})
    for repo, prs in sorted(prs_by_repo.items()):
        for pr in prs:
            if is_bot(pr) or is_duplicate(pr):
                continue
            for path, add, dele in pr["files"]:
                if exclusion(path) not in (None, "binary") or area(repo, path, overrides):
                    continue
                key = "/".join([repo] + path.split("/")[:-1][:defaults.AMBIGUOUS_DEPTH])
                g = groups[key]
                g["files"] += 1
                g["lines"] += add + dele
                g["exts"][Path(path).suffix or Path(path).name] += 1
                g["paths"].add(path)
                g["titles"].add(pr["title"])
    rows = [{"prefix": k + "/", "files": g["files"], "lines": g["lines"], "exts": dict(sorted(g["exts"].items())),
             "sample_paths": sorted(g["paths"])[:6], "sample_titles": sorted(g["titles"])[:4]}
            for k, g in groups.items()]
    return sorted(rows, key=lambda r: (-r["lines"], r["prefix"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    _, prs = load_cache(a.out)
    rows = ambiguous(prs, load_overrides(a.out))
    (Path(a.out) / "ambiguous.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = sum(r["lines"] for r in rows)
    print(f"{len(rows)} diretórios ambíguos, {sum(r['files'] for r in rows)} arquivos, {lines} linhas → "
          f"{Path(a.out) / 'ambiguous.json'}")


if __name__ == "__main__":
    main()
