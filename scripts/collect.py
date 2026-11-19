#!/usr/bin/env python3
"""Collect merged-PR metadata of a GitHub org through the `gh` CLI. Python 3 stdlib only.

Lists merged PRs with GitHub search by merge date. Fetches number, title, labels, author, mergedAt and per-file path/additions/deletions.
Never fetches file contents or diffs. Raw JSON is cached in <out>/cache so reruns are offline.

  python3 collect.py --org ORG --check            # prerequisites only
  python3 collect.py --org ORG [--months 6] [--until 2026-10] [--repos a,b] [--exclude-repos c]
                     [--max-repos N] [--max-prs N] [--out DIR]
"""
import argparse, datetime as dt, json, shutil, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import defaults

SETUP = "references/gh-setup.md"
# Exit codes the agent maps to a step of the setup guide.
NO_GH, NO_LOGIN, NO_SCOPES, NO_SSO, NO_ORG = 2, 3, 4, 5, 6

REPOS_Q = """query($org:String!,$after:String){rateLimit{remaining resetAt}
organization(login:$org){repositories(first:100,after:$after,isFork:false,orderBy:{field:NAME,direction:ASC}){
pageInfo{hasNextPage endCursor} nodes{name isArchived pushedAt}}}}"""
PR_FIELDS = """... on PullRequest{number title mergedAt author{login __typename} labels(first:20){nodes{name}}
files(first:100){pageInfo{hasNextPage endCursor} nodes{path additions deletions}}}"""
COUNT_Q = "query($q:String!){search(query:$q,type:ISSUE,first:0){issueCount}}"
SEARCH_Q = """query($q:String!,$n:Int!,$after:String){rateLimit{remaining resetAt}
search(query:$q,type:ISSUE,first:$n,after:$after){pageInfo{hasNextPage endCursor} nodes{%s}}}""" % PR_FIELDS
FILES_Q = """query($owner:String!,$name:String!,$number:Int!,$after:String){rateLimit{remaining resetAt}
repository(owner:$owner,name:$name){pullRequest(number:$number){files(first:100,after:$after){
pageInfo{hasNextPage endCursor} nodes{path additions deletions}}}}}"""


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def die(code, msg):
    log(f"\nERRO: {msg}\nVeja {SETUP} (código {code}).")
    sys.exit(code)


def fatal_hint(msg):
    m = msg.lower()
    if "saml" in m or "sso" in m:
        return NO_SSO, "a organização exige SSO: autorize o token do gh para ela (Configure SSO → Authorize)."
    if "could not resolve to an organization" in m or "not found" in m:
        return NO_ORG, "organização não encontrada ou sem acesso. Confira o nome e os escopos read:org,repo."
    if "bad credentials" in m or "gh auth login" in m or "401" in m:
        return NO_LOGIN, "o gh não está autenticado. Rode: gh auth login"
    if "insufficient_scopes" in m or "scope" in m:
        return NO_SCOPES, "faltam escopos. Rode: gh auth refresh -s read:org,repo"
    return None


def gh(args, stdin=None):
    p = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, encoding="utf-8")
    return p.returncode, p.stdout, p.stderr


def graphql(query, **variables):
    """POST a GraphQL query; retry transient failures, sleep on rate limits, stop on auth errors."""
    body = json.dumps({"query": query, "variables": variables})
    for attempt in range(6):
        code, out, err = gh(["api", "graphql", "--input", "-"], body)
        try:
            data = json.loads(out) if out.strip() else {}
        except ValueError:
            data = {}
        msg = err + " ".join(e.get("message", "") for e in data.get("errors", []) or [])
        if code == 0 and data.get("data") is not None and not data.get("errors"):
            rl = data["data"].get("rateLimit") or {}
            if rl.get("remaining", 10**6) < defaults.RATE_LIMIT_FLOOR:
                wait_until(rl["resetAt"])
            return data["data"]
        hint = fatal_hint(msg)
        if hint:
            die(*hint)
        if "rate limit" in msg.lower():
            log("  limite de requisições atingido, aguardando 60 s…")
            time.sleep(60)
            continue
        wait = 5 * 2 ** attempt
        log(f"  erro transitório ({msg.strip()[:160]}), nova tentativa em {wait} s")
        time.sleep(wait)
    raise RuntimeError(msg.strip()[:500])


def wait_until(reset_at):
    reset = dt.datetime.strptime(reset_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    secs = max(0, (reset - dt.datetime.now(dt.timezone.utc)).total_seconds()) + 5
    log(f"  cota GraphQL baixa, aguardando {int(secs)} s até {reset_at}…")
    time.sleep(secs)


def check(org):
    """Prerequisites, in the order of the setup guide. Exits with a code on the first failure."""
    if not shutil.which("gh"):
        die(NO_GH, "o GitHub CLI (gh) não está instalado.")
    code, out, err = gh(["auth", "status"])
    if code:
        die(NO_LOGIN, "o gh não está autenticado. Rode: gh auth login")
    code, out, err = gh(["api", "-i", "user"])
    scopes = next((l.split(":", 1)[1] for l in out.splitlines() if l.lower().startswith("x-oauth-scopes:")), None)
    if scopes is not None:  # classic/OAuth token; fine-grained tokens have no scope header
        have = {s.strip() for s in scopes.split(",")}
        missing = [s for s, ok in (("repo", "repo" in have), ("read:org", have & {"read:org", "admin:org", "write:org"}))
                   if not ok]
        if missing:
            die(NO_SCOPES, f"faltam escopos {', '.join(missing)}. Rode: gh auth refresh -s read:org,repo")
    graphql("query($org:String!){organization(login:$org){repositories(first:1){totalCount}}}", org=org)
    log(f"ok: gh instalado, autenticado e com acesso a {org}.")


def period(months, until):
    """Last `months` full calendar months ending at `until` (YYYY-MM, default: previous month)."""
    if until:
        y, m = map(int, until.split("-"))
    else:
        today = dt.date.today()
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    end = dt.date(y + (m == 12), m % 12 + 1, 1)          # exclusive
    ms = []
    for _ in range(months):
        ms.insert(0, f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return f"{ms[0]}-01", end.isoformat(), ms


def list_repos(org, since):
    repos, after = [], None
    while True:
        d = graphql(REPOS_Q, org=org, after=after)["organization"]["repositories"]
        repos += [r["name"] for r in d["nodes"] if not r["isArchived"] and (r["pushedAt"] or "") >= since]
        if not d["pageInfo"]["hasNextPage"]:
            return sorted(repos)
        after = d["pageInfo"]["endCursor"]


def most_active(org, repos, since, until, n):
    """Top-n repos by merged PRs in the period (GitHub search counts, 20 per query)."""
    last_day = (dt.date.fromisoformat(until) - dt.timedelta(days=1)).isoformat()
    counts = {}
    for i in range(0, len(repos), 20):
        chunk = repos[i:i + 20]
        parts = [f'r{j}: search(query:{json.dumps(f"repo:{org}/{r} is:pr is:merged merged:{since}..{last_day}")},'
                 f' type:ISSUE, first:0){{issueCount}}' for j, r in enumerate(chunk)]
        d = graphql("query{" + " ".join(parts) + "}")
        counts.update({r: d[f"r{j}"]["issueCount"] for j, r in enumerate(chunk)})
    ranked = sorted(repos, key=lambda r: (-counts[r], r))
    log("  PRs mergeados por repositório: " + ", ".join(f"{r} {counts[r]}" for r in ranked[:n + 4]))
    return sorted(ranked[:n])


def windows(org, repo, first, last):
    """Split [first, last] (dates, inclusive) until each search window has <= 1000 results (GitHub cap)."""
    q = f"repo:{org}/{repo} is:pr is:merged merged:{first}..{last}"
    n = graphql(COUNT_Q, q=q)["search"]["issueCount"]
    if n <= 1000 or first == last:
        return [q] if n else []
    mid = first + (last - first) // 2
    return windows(org, repo, mid + dt.timedelta(days=1), last) + windows(org, repo, first, mid)  # newest first


def fetch_prs(org, repo, since, until, max_prs):
    """Merged PRs of the period via search by merge date (only PRs merged in the period are fetched)."""
    prs, seen = [], set()
    last = dt.date.fromisoformat(until) - dt.timedelta(days=1)
    for q in windows(org, repo, dt.date.fromisoformat(since), last):
        after, n = None, defaults.PAGE_SIZE
        while True:
            try:
                d = graphql(SEARCH_Q, q=q, n=n, after=after)["search"]
            except RuntimeError:
                if n <= 5:
                    raise
                n //= 2
                log(f"  página menor ({n} PRs) por causa de timeouts")
                continue
            for pr in d["nodes"]:
                if not pr or pr["number"] in seen or not (since <= pr["mergedAt"] < until):
                    continue
                seen.add(pr["number"])
                # files is null when GitHub can't list them inline: page them from the start
                pf = pr["files"] or {"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": None}}
                files, fa = pf["nodes"], pf["pageInfo"]
                while fa["hasNextPage"]:  # PRs with >100 files
                    f = graphql(FILES_Q, owner=org, name=repo, number=pr["number"], after=fa["endCursor"])
                    f = f["repository"]["pullRequest"]["files"]
                    if f is None:
                        log(f"  aviso: GitHub não listou os arquivos do PR {repo}#{pr['number']}")
                        break
                    files, fa = files + f["nodes"], f["pageInfo"]
                author = pr["author"] or {}
                prs.append({
                    "number": pr["number"], "title": pr["title"], "merged_at": pr["mergedAt"],
                    "author": author.get("login"), "author_type": author.get("__typename"),
                    "labels": sorted(l["name"] for l in pr["labels"]["nodes"]),
                    "files": [[f["path"], f["additions"], f["deletions"]] for f in files],
                })
            log(f"  {repo}: {len(prs)} PRs ({q.split('merged:')[1]})")
            if max_prs and len(prs) >= max_prs:
                prs = sorted(prs, key=lambda p: p["merged_at"], reverse=True)[:max_prs]
                return sorted(prs, key=lambda p: p["number"])
            if not d["pageInfo"]["hasNextPage"]:
                break
            after = d["pageInfo"]["endCursor"]
    return sorted(prs, key=lambda p: p["number"])


def members(org):
    code, out, err = gh(["api", "--paginate", f"orgs/{org}/members?per_page=100", "--jq", ".[].login"])
    if code:
        log(f"  aviso: lista de membros indisponível ({err.strip()[:120]})")
        return []
    return sorted(set(out.split()))


def write_json(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8")
    tmp.replace(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--org", required=True)
    ap.add_argument("--check", action="store_true", help="only check gh, login, scopes and SSO")
    ap.add_argument("--months", type=int, default=defaults.MONTHS)
    ap.add_argument("--until", help="last month included, YYYY-MM (default: previous month)")
    ap.add_argument("--repos", default="", help="comma-separated repos to include (default: all)")
    ap.add_argument("--exclude-repos", default="")
    ap.add_argument("--max-repos", type=int, default=0, help="keep only the N repos with most merged PRs")
    ap.add_argument("--max-prs", type=int, default=0, help="cap PRs per repo (most recent first)")
    ap.add_argument("--out", help="output dir (default ./ui-spend-report-ORG)")
    a = ap.parse_args()

    check(a.org)
    if a.check:
        return
    since, until, months = period(a.months, a.until)
    cache = Path(a.out or f"ui-spend-report-{a.org}") / "cache"
    key = {"org": a.org, "since": since, "until": until, "repos": a.repos, "exclude_repos": a.exclude_repos,
           "max_repos": a.max_repos, "max_prs": a.max_prs}
    mpath = cache / "manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else None
    if manifest and manifest["key"] != key:
        log(f"parâmetros mudaram desde a última coleta; refazendo o cache em {cache}")
        shutil.rmtree(cache)
        manifest = None
    (cache / "prs").mkdir(parents=True, exist_ok=True)

    if not manifest:
        log(f"listando repositórios de {a.org} com push desde {since}…")
        repos = list_repos(a.org, since)
        inc = {r for r in a.repos.split(",") if r}
        exc = {r for r in a.exclude_repos.split(",") if r}
        repos = [r for r in repos if (not inc or r in inc) and r not in exc]
        if a.max_repos and len(repos) > a.max_repos:
            repos = most_active(a.org, repos, since, until, a.max_repos)
        manifest = {"key": key, "org": a.org, "since": since, "until": until, "months": months, "repos": repos,
                    "collected_on": dt.date.today().isoformat(), "max_prs": a.max_prs, "members": members(a.org)}
        write_json(mpath, manifest)
    log(f"{len(manifest['repos'])} repositórios, período {since} a {until} (exclusivo)")

    for i, repo in enumerate(manifest["repos"], 1):
        path = cache / "prs" / f"{repo}.json"
        if path.exists():
            log(f"[{i}/{len(manifest['repos'])}] {repo}: em cache")
            continue
        log(f"[{i}/{len(manifest['repos'])}] {repo}")
        write_json(path, fetch_prs(a.org, repo, since, until, a.max_prs))
    log("coleta completa.")


if __name__ == "__main__":
    main()
