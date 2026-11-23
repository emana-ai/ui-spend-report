#!/usr/bin/env python3
"""Score PRs, split developer cost, aggregate by area/repo/month and write the HTML + JSON diagnostic.

  python3 report.py --out DIR --monthly-cost 20000 [--currency BRL] [--hours 160] [--authors all|members|min-prs:K]

Reads DIR/cache (from collect.py) and DIR/overrides.json. Same inputs → byte-identical outputs.
Aggregates only: author logins are used in memory for the cost split and never written.
"""
import argparse, bisect, html, json, math, re, sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import classify, defaults, benchmark, insights
from display import num, pct, money, month, companies, rank_text, span
from validate_insights import validate

_MAINT = re.compile(defaults.MAINTENANCE, re.I)
_NEW = re.compile(defaults.NEW_WORK, re.I)
_BUG_LABEL = re.compile(defaults.BUG_LABEL, re.I)
_FEATURE_LABEL = re.compile(defaults.FEATURE_LABEL, re.I)
_ISSUE = re.compile(defaults.ISSUE_PREFIX, re.I)
_NATIVE = (".swift", ".m", ".mm", ".kt", ".java", ".dart", ".qml", ".xib", ".storyboard", ".xcstrings")
_NOT_SCREEN = re.compile(defaults.HOTSPOT_NOT_SCREEN + "|" + defaults.BINARY, re.I)
_PLATFORMS = [(name, re.compile(rx, re.I)) for name, rx in defaults.PLATFORMS]


def platform(repo, path):
    full = f"{repo}/{path}"
    return next((name for name, rx in _PLATFORMS if rx.search(full)), "web")


def module(repo, path):
    """Directory one level below the last structural folder (src, modules, features…): a readable hotspot."""
    dirs = path.split("/")[:-1]
    last = max((i for i, d in enumerate(dirs) if d.lower() in defaults.MODULE_CONTAINERS), default=-1)
    return "/".join([repo] + dirs[:last + 2])


def when(pr):
    return datetime.strptime(pr["merged_at"], "%Y-%m-%dT%H:%M:%SZ")



# ---------------------------------------------------------------- model
def score(lines, files):
    return math.log2(1 + lines) + 0.5 * math.log2(1 + files)


def is_maintenance(pr):
    """Bug label → maintenance; feature label → new work; otherwise the title without its issue-link prefix."""
    labels = pr.get("labels", [])
    if any(_BUG_LABEL.search(l) for l in labels):
        return True
    if any(_FEATURE_LABEL.search(l) for l in labels):
        return False
    title = _ISSUE.sub("", pr["title"])
    return not _NEW.search(title) and bool(_MAINT.search(title))


def team(prs, mode, members):
    """Keep PRs whose author counts as team under the --authors option."""
    if mode == "all":
        return prs
    if mode == "members":
        ms = set(members)
        return [p for p in prs if p["author"] in ms]
    if mode.startswith("min-prs:"):
        k, n = int(mode.split(":")[1]), Counter(p["author"] for p in prs)
        return [p for p in prs if n[p["author"]] >= k]
    sys.exit(f"--authors inválido: {mode} (use all, members ou min-prs:K)")


def split_costs(prs, months, monthly_cost):
    """Set pr['cost']: each developer's monthly cost split over that month's PRs by score.
    A month without PRs carries its cost to the developer's next PR month.
    Returns (active developer-months, cost carried)."""
    by = defaultdict(list)
    for p in prs:
        by[p["author"]].append(p)
    dev_months, carried = 0, 0.0
    for author in sorted(by):
        items = by[author]
        first, last = min(p["month"] for p in items), max(p["month"] for p in items)
        budget = 0.0
        for m in (m for m in months if first <= m <= last):
            dev_months += 1
            budget += monthly_cost
            these = [p for p in items if p["month"] == m]
            if not these:
                carried += monthly_cost
                continue
            total = sum(p["score"] for p in these)
            for p in these:
                p["cost"] = budget * p["score"] / total
            budget = 0.0
    return dev_months, carried


def analyse(manifest, prs_by_repo, overrides, monthly_cost, hours, authors, currency=defaults.CURRENCY):
    months = manifest["months"]
    excluded, lines_total = Counter(), 0
    amb_lines = eff_lines = 0
    n_merged = n_bot = n_auto = n_dup = n_release = n_empty = 0
    human = []
    copies = classify.release_copies(prs_by_repo)
    for repo in sorted(prs_by_repo):
        for pr in sorted(prs_by_repo[repo], key=lambda p: p["number"]):
            n_merged += 1
            if classify.is_bot(pr):
                n_bot += 1
                n_auto += classify.is_automation(pr)
                continue
            if classify.is_duplicate(pr, repo, copies):
                n_dup += 1
                n_release += not classify.is_duplicate(pr)
                continue
            human.append(dict(pr, repo=repo, month=pr["merged_at"][:7]))
    kept = team(human, authors, manifest.get("members", []))
    index = classify.code_index(prs_by_repo, overrides)
    test_lines = Counter()
    scored = []
    for pr in kept:
        files = []
        for path, add, dele in pr["files"]:
            lines = add + dele
            lines_total += lines
            cat = classify.file_exclusion(path, lines)
            a, kind = classify.area_kind(pr["repo"], path, overrides, index)
            if a == "exclude" and cat in (None, "binary"):
                cat = "override"
            if cat and cat != "binary":
                excluded["third_party" if cat == "vendored" else cat] += lines
                continue
            if cat == "binary":
                excluded["binary"] += lines
                lines = 0
            if kind:
                test_lines[kind] += lines
            if a is None:
                amb_lines += lines
            eff_lines += lines
            files.append((a or "other", path, lines, kind))
        pr["score"] = score(sum(f[2] for f in files), len(files))
        pr["files_eff"] = files
        if pr["score"] == 0:
            n_empty += 1
            continue
        scored.append(pr)

    dev_months, carried = split_costs(scored, months, monthly_cost)
    total = dev_months * monthly_cost
    by_area, maint_area = Counter(), Counter()
    repo_cost, repo_ui, repo_ui_m, repo_prs = Counter(), Counter(), Counter(), Counter()
    mon_cost, mon_ui, mon_ui_m, mon_prs = Counter(), Counter(), Counter(), Counter()
    dir_cost, dir_m = defaultdict(Counter), defaultdict(Counter)
    surface_costs = defaultdict(Counter)
    surface_scopes = defaultdict(lambda: defaultdict(set))
    fix_cost = 0.0
    interface_prs = fix_prs = 0
    native_cost = 0.0
    n_maint = n_typed = 0
    recs = []  # one per interface file in a PR: the evidence behind every scope, block and card
    mon_ui_prs = Counter()
    for pr in scored:
        maint = is_maintenance(pr)
        n_maint += maint
        n_typed += any(_BUG_LABEL.search(l) or _FEATURE_LABEL.search(l) for l in pr.get("labels", []))
        fix = maint and re.search(defaults.FIX_MARKER, " ".join([pr["title"], *pr.get("labels", [])]), re.I)
        if any(f[0] == "interface" for f in pr["files_eff"]):
            interface_prs += 1
            fix_prs += bool(fix)
            mon_ui_prs[pr["month"]] += 1
        weights = [max(f[2], 1) for f in pr["files_eff"]]
        wsum = sum(weights)
        repo_cost[pr["repo"]] += pr["cost"]
        repo_prs[pr["repo"]] += 1
        mon_cost[pr["month"]] += pr["cost"]
        mon_prs[pr["month"]] += 1
        for (a, path, _, kind), w in zip(pr["files_eff"], weights):
            c = pr["cost"] * w / wsum
            by_area[a] += c
            d = "/".join([pr["repo"]] + path.split("/")[:-1][:defaults.TOP_DIR_DEPTH])
            dir_cost[a][d] += c
            if maint:
                maint_area[a] += c
                dir_m[a][d] += c
            if a == "interface":
                native = path.endswith(_NATIVE) or "/src/main/res/" in "/" + path  # the area (rule or override) is decided
                if native:
                    native_cost += c
                if fix:
                    fix_cost += c
                surface = None if maint else insights.surface_key(pr["repo"], path)
                if surface:
                    surface_costs[surface[0]][surface[1]] += c
                    surface_scopes[surface[0]][surface[1]].add(d)
                recs.append(dict(pr=(pr["repo"], pr["number"]), file=(pr["repo"], path), month=pr["month"], at=when(pr),
                                 cost=c, maint=maint, fix=bool(fix), native=native,
                                 module=module(pr["repo"], path), platform=platform(pr["repo"], path),
                                 surface=surface and surface[0], screen=not kind and not _NOT_SCREEN.search(path)))
                repo_ui[pr["repo"]] += c
                mon_ui[pr["month"]] += c
                if maint:
                    repo_ui_m[pr["repo"]] += c
                    mon_ui_m[pr["month"]] += c

    def share(a, b):
        return a / b if b else 0.0

    ui, ui_m, n_m = by_area["interface"], maint_area["interface"], len(months)
    unit = test_lines["unit"] + test_lines["unit_inferred"] + test_lines["unit_unresolved"]
    to_hours = hours / monthly_cost
    measured = interface_prs >= defaults.MAINT_MIN_UI_PRS  # smaller samples: no maintenance rate
    scopes = {"interface": recs, "maintenance": [x for x in recs if x["maint"]], "fix": [x for x in recs if x["fix"]],
              "native": [x for x in recs if x["native"]]}
    for x in recs:
        scopes.setdefault("month:" + x["month"], []).append(x)
        scopes.setdefault("repo:" + x["file"][0], []).append(x)
        if x["surface"] in surface_costs and len(surface_costs[x["surface"]]) > 1:
            scopes.setdefault("surface:" + x["surface"], []).append(x)
    # One repository with most of the interface: its top modules are the concentration scope, not the repository.
    lead = min(repo_ui, key=lambda k: (-repo_ui[k], k), default=None)
    if lead and repo_ui[lead] >= defaults.SINGLE_REPO_SHARE * ui:
        top = {m for m, _ in scope_stats(scopes["repo:" + lead], months)["top"]}
        scopes["modules:" + lead] = [x for x in scopes["repo:" + lead] if x["module"] in top]
    screens = [x for x in recs if x["screen"]]  # hotspots: screen files, no unit tests, stories or images
    until = datetime.strptime(manifest["until"], "%Y-%m-%d")
    return rounded({
        "org": manifest["org"], "collected_on": manifest["collected_on"],
        "period": {"since": manifest["since"], "until_exclusive": manifest["until"], "months": months},
        "params": {"monthly_cost": monthly_cost, "currency": currency, "hours_per_month": hours,
                   "authors": authors, "max_prs_per_repo": manifest.get("max_prs", 0), "repos": manifest["repos"]},
        "headline": {
            "interface_share": share(ui, total),
            "interface_monthly_cost": ui / n_m,
            "interface_maintenance_share": share(ui_m, ui) if measured else None,
            "interface_maintenance_annual_cost": ui_m / n_m * 12 if measured else None,
        },
        "counts": {"prs_merged": n_merged, "prs_bot": n_bot, "prs_automation": n_auto, "prs_duplicate": n_dup,
                   "prs_release_copy": n_release, "prs_not_team": len(human) - len(kept),
                   "prs_no_effective_change": n_empty, "prs_scored": len(scored), "prs_maintenance": n_maint,
                   "prs_typed_label": n_typed, "developers": len({p["author"] for p in scored}), "developer_months": dev_months,
                   "repos": len(manifest["repos"])},
        "lines": {"total": lines_total, "effective": eff_lines, "excluded": dict(sorted(excluded.items())),
                  "excluded_share": share(sum(excluded.values()) - excluded["binary"], lines_total),
                  "ambiguous": amb_lines, "ambiguous_share": share(amb_lines, eff_lines),
                  "tests": {"unit_tests_follow_area": defaults.UNIT_TESTS_FOLLOW_AREA,
                            **{k: test_lines[k] for k in ("integration", "unit", "unit_inferred", "unit_unresolved")},
                            "unit_inferred_share": share(test_lines["unit_inferred"], unit)}},
        "cost": {"team_total": total, "team_monthly": total / n_m, "carried": carried,
                 "carried_share": share(carried, total)},
        "surface_scopes": {name: {platform: sorted(paths) for platform, paths in surface_scopes[name].items()}
                           for name, costs in surface_costs.items() if len(costs) > 1},
        "surfaces": {k: dict(v) for k, v in sorted(surface_costs.items()) if len(v) > 1},
        "interface": {"fix_cost": fix_cost, "prs": interface_prs, "fix_prs": fix_prs, "cost": ui, "native_cost": native_cost, "new_cost": ui - ui_m, "maintenance_cost": ui_m,
                      "monthly_hours": ui / n_m * to_hours, "fte": ui / n_m / monthly_cost},
        "areas": [{"area": a, "cost": by_area[a], "share": share(by_area[a], total),
                   "monthly_hours": by_area[a] / n_m * to_hours, "maintenance_share": share(maint_area[a], by_area[a])}
                  for a in sorted(classify.AREAS, key=lambda a: (-by_area[a], a))],
        "by_repo": [{"repo": r, "prs": repo_prs[r], "cost": repo_cost[r], "share_of_total": share(repo_cost[r], total),
                     "interface_cost": repo_ui[r], "interface_share": share(repo_ui[r], repo_cost[r]),
                     "interface_maintenance_share": share(repo_ui_m[r], repo_ui[r])}
                    for r in sorted(repo_cost, key=lambda r: (-repo_cost[r], r))],
        "by_month": [{"month": m, "prs": mon_prs[m], "cost": mon_cost[m], "interface_cost": mon_ui[m],
                      "interface_prs": mon_ui_prs[m], "interface_maintenance_cost": mon_ui_m[m],
                      "interface_share": share(mon_ui[m], mon_cost[m]),
                      "interface_maintenance_share": share(mon_ui_m[m], mon_ui[m])} for m in months],
        "top_dirs_by_area": {a: [{"path": d + "/", "cost": dc[d], "share_of_area": share(dc[d], by_area[a]),
                                  "maintenance_share": share(dir_m[a][d], dc[d])}
                                 for d in sorted(dc, key=lambda d: (-dc[d], d))[:defaults.TOP_DIRS]]
                             for a, dc in sorted(dir_cost.items()) if a != "tooling"},
        "scopes": {k: scope_stats(v, months) for k, v in sorted(scopes.items())},
        "hotspots": hotspots(screens) if measured and any(x["maint"] for x in screens) else None,
        "rework": rework(recs, until) if measured else None,
        "trend": trend(months, mon_ui, mon_ui_m, mon_ui_prs) if measured else None,
        "platforms": platforms(recs, ui, measured),
    })


def scope_stats(recs, months):
    """Cost, PRs, files, monthly cost and the three modules that carry most of it."""
    by_month, mods = Counter(), Counter()
    for x in recs:
        by_month[x["month"]] += x["cost"]
        mods[x["module"]] += x["cost"]
    return {"cost": sum(x["cost"] for x in recs), "prs": len({x["pr"] for x in recs}),
            "files": len({x["file"] for x in recs}), "by_month": [by_month[m] for m in months],
            "top": [[m, mods[m]] for m in sorted(mods, key=lambda m: (-mods[m], m))[:3]]}


def hotspots(recs):
    """Maintenance cost by interface file: concentration curve, files that carry HOTSPOT_COST_SHARE, top modules."""
    files, mods, mod_prs, mod_files = Counter(), Counter(), defaultdict(set), defaultdict(set)
    for x in recs:
        files[x["file"]] += x["cost"] if x["maint"] else 0
        if x["maint"]:
            mods[x["module"]] += x["cost"]
            mod_prs[x["module"]].add(x["pr"])
            mod_files[x["module"]].add(x["file"])
    costs = sorted(files.values(), reverse=True)
    total, n = sum(costs), len(costs)
    cum = [0.0]
    for c in costs:
        cum.append(cum[-1] + c)
    top = next(i for i, c in enumerate(cum) if c >= defaults.HOTSPOT_COST_SHARE * total)
    return {"files": n, "top_files": top, "top_file_share": top / n, "top_maintenance_share": cum[top] / total,
            "maintenance_files": sum(c > 0 for c in costs),
            "curve": [cum[round(n * i / 1000)] / total for i in range(1001)],  # share of cost in the top 0, 0,1 … 100% of files
            "modules": [{"path": m, "maintenance_cost": mods[m], "share": mods[m] / total, "prs": len(mod_prs[m]),
                         "files": len(mod_files[m])}
                        for m in sorted(mods, key=lambda m: (-mods[m], m))[:defaults.HOTSPOT_TOP]]}


def rework(recs, until):
    """Cost of new interface work on files that receive a maintenance PR within REWORK_DAYS after it merged,
    file by file (one shared file does not mark a whole PR). Only new work merged at least REWORK_DAYS before
    the period ends: later PRs have no full window."""
    window = timedelta(days=defaults.REWORK_DAYS)
    fixes = defaultdict(list)
    for x in recs:
        if x["maint"]:
            fixes[x["file"]].append((x["at"], x["pr"], x["cost"]))
    for v in fixes.values():
        v.sort()
    times = {f: [at for at, _, _ in v] for f, v in fixes.items()}
    new_cost = hit_cost = 0.0
    new_prs, hit_prs, follow = set(), set(), {}
    for x in recs:
        if x["maint"] or x["at"] + window > until:
            continue
        new_cost += x["cost"]
        new_prs.add(x["pr"])
        v = fixes.get(x["file"], [])
        later = [(pr, c) for at, pr, c in v[bisect.bisect_right(times.get(x["file"], []), x["at"]):]  # strictly after
                 if at <= x["at"] + window]
        if later:
            hit_cost += x["cost"]
            hit_prs.add(x["pr"])
            follow.update(((pr, x["file"]), c) for pr, c in later)  # each maintenance file-cost counted once
    published = len(new_prs) >= defaults.MAINT_MIN_UI_PRS
    return {"days": defaults.REWORK_DAYS, "new_prs": len(new_prs), "reworked_prs": len(hit_prs), "new_cost": new_cost,
            "reworked_cost": hit_cost, "share": hit_cost / new_cost if published and new_cost else None,
            "followup_cost": sum(follow.values()), "followup_prs": len({pr for pr, _ in follow})}


def trend(months, ui, ui_m, ui_prs):
    """Maintenance share of interface cost, first half of the window vs second half (odd middle month left out)."""
    half = len(months) // 2
    parts = [months[:half], months[len(months) - half:]]
    if not half or any(sum(ui_prs[m] for m in p) < defaults.MAINT_MIN_UI_PRS or not sum(ui[m] for m in p) for p in parts):
        return None
    first, second = (sum(ui_m[m] for m in p) / sum(ui[m] for m in p) for p in parts)
    return {"first": first, "second": second, "first_months": parts[0], "second_months": parts[1],
            "delta": second - first, "stable": abs(second - first) <= defaults.TREND_STABLE_PP}


def platforms(recs, ui, measured):
    """Interface cost and maintenance share per platform; below PLATFORM_MIN_SHARE of interface cost: hidden."""
    cost, maint, prs = Counter(), Counter(), defaultdict(set)
    for x in recs:
        cost[x["platform"]] += x["cost"]
        maint[x["platform"]] += x["cost"] if x["maint"] else 0
        prs[x["platform"]].add(x["pr"])
    shown = [p for p in sorted(cost, key=lambda p: (-cost[p], p)) if ui and cost[p] / ui >= defaults.PLATFORM_MIN_SHARE]
    return {"shown": [{"platform": p, "cost": cost[p], "share": cost[p] / ui, "prs": len(prs[p]),
                       "maintenance_share": maint[p] / cost[p] if measured and len(prs[p]) >= defaults.MAINT_MIN_UI_PRS else None}
                      for p in shown],
            "hidden_share": sum(cost[p] for p in cost if p not in shown) / ui if ui else 0.0}


def rounded(o):
    if isinstance(o, float):
        return round(o, 4)
    if isinstance(o, dict):
        return {k: rounded(v) for k, v in o.items()}
    if isinstance(o, list):
        return [rounded(v) for v in o]
    return o


# ---------------------------------------------------------------- HTML (pt-BR article)
ASSETS = Path(__file__).resolve().parents[1] / "assets"
CSS = (ASSETS / "report.css").read_text(encoding="utf-8")  # one block, inlined
# Motion only enhances: without JS, with reduced motion and in print the page is already in its final state.
JS = """(function(){var d=document.documentElement;if(!window.IntersectionObserver||matchMedia('(prefers-reduced-motion: reduce)').matches)return;
d.classList.add('motion');var all=[];
function fmt(v,dec){var s=v.toFixed(dec).split('.');return s[0].replace(/\\B(?=(\\d{3})+(?!\\d))/g,'.')+(dec?','+s[1]:'')}
function count(el){var t=el.textContent,dec=(t.split(',')[1]||'').length,v=parseFloat(t.replace(/\\./g,'').replace(',','.')),t0=0;
 all.push([el,t]);function step(ts){t0=t0||ts;var p=d.classList.contains('motion')?Math.min(1,(ts-t0)/1400):1,e=1-Math.pow(1-p,3);el.textContent=p<1?fmt(v*e,dec):t;if(p<1)requestAnimationFrame(step)}
 requestAnimationFrame(step)}
function done(){d.classList.remove('motion');all.forEach(function(x){x[0].textContent=x[1]})}
addEventListener('beforeprint',done);
addEventListener('DOMContentLoaded',function(){var io=new IntersectionObserver(function(es){es.forEach(function(x){
 if(!x.isIntersecting)return;io.unobserve(x.target);x.target.classList.add('in');[].forEach.call(x.target.querySelectorAll('.n'),count)})},{threshold:.12});
 [].forEach.call(document.querySelectorAll('.rv'),function(s){io.observe(s)})})})();"""
NUMBER = __import__("validate_insights").NUMBER
_CORE = re.compile(r"\d[\d.]*(?:,\d+)?")
AREA_LABELS = {"interface": "Interface", "backend": "Backend", "infra": "Infraestrutura", "tests": "Testes de integração",
               "data": "Dados", "docs": "Documentação", "other": "Outros", "tooling": "Ferramentas internas"}
KIND_LABELS = {"capacity": "Manutenção", "platforms": "Mesma tela", "native": "Nativo", "concentration": "Concentração",
               "fix_share": "Correções", "peak": "Pico mensal", "interface_trend": "Tendência"}


def counted(text):
    """Escaped text with its first number wrapped for the count-up; the static HTML keeps the final value."""
    return _CORE.sub(lambda m: f'<span class="n">{m[0]}</span>', html.escape(text), count=1)


def lit(text):
    """The one lit quantity: the first number of a sentence, in light, counted up on scroll."""
    t = html.escape(text)
    return NUMBER.sub(lambda m: f"<em>{counted(m[0])}</em>", t, count=1)


def kpi(value):
    """Big number, small unit in light: 'R$ 176 mil' → R$ 176 + mil."""
    m = re.match(r"(\D*?)(\d[\d.]*(?:,\d+)?)(.*)", value)
    if not m:
        return html.escape(value)
    pre, core, rest = m.groups()
    return (f"<small>{html.escape(pre.strip())}</small> " if pre.strip() else "") + f'<span class="n">{core}</span><small>{html.escape(rest)}</small>'


def pt(x, y, size, lit_=False, i=0):
    """A point of light (the product's data mark) at x%, y% of its box."""
    return (f'<span class="pt{" on" if lit_ else ""}" style="left:{x:.2f}%;top:{y:.2f}%;--size:{size:.1f}px;--i:{i}">'
            '<i></i></span>')


def fig(title, caption, body, legend=""):
    return (f'<figure><div class="ft">{title}</div><div class="fs">{caption}</div>{body}'
            + (f'<div class="legend">{legend}</div>' if legend else "") + "</figure>")


def bars(rows):
    """rows: (label, share 0–1 of the longest bar, value text, lit). HTML bars that grow on scroll."""
    return ('<div class="bars">' + "".join(
        f'<div class="row{" on" if on else ""}"><span>{html.escape(label)}</span><div class="track">'
        f'<i style="width:{100 * w:.1f}%;--i:{i}"></i></div><b>{value}</b></div>'
        for i, (label, w, value, on) in enumerate(rows)) + "</div>")


def split(parts):
    """One 100% bar in parts: (label, share, value text, lit)."""
    seg = "".join(f'<i class="{"on" if on else "off"}" style="width:{100 * s:.2f}%;--i:{i}"></i>'
                  for i, (_, s, _, on) in enumerate(parts))
    keys = "".join(f'<div><span class="key {"on" if on else "off"}"></span><b>{html.escape(label)}</b> {value}</div>'
                   for label, _, value, on in parts)
    return f'<div class="split">{seg}</div><div class="split-keys">{keys}</div>'


def nice(top, ticks=4):
    """Round axis step: 1, 2, 2,5 or 5 × 10^k, at most `ticks` steps up to top."""
    raw = top / ticks if top else 1
    mag = 10 ** math.floor(math.log10(raw))
    return next(k * mag for k in (1, 2, 2.5, 5, 10) if k * mag >= raw)


def lorenz(hs):
    """Concentration curve with real axes, zoomed on the files that matter: the x axis ends at a round share that is
    at least four times the hotspot share (10% when it is small), so the knee of the curve is readable."""
    dom = next(d for d in (.1, .2, .25, .5, 1) if d >= min(1, 4 * hs["top_file_share"]))
    X0, X1, Y0, Y1 = 56, 744, 16, 256
    X = lambda v: X0 + (X1 - X0) * v / dom
    Y = lambda v: Y1 - (Y1 - Y0) * v
    grid = "".join(f'<path d="M{X0} {Y(v):.1f}H{X1}" class="grid"/><text x="{X0 - 10}" y="{Y(v) + 4:.1f}" text-anchor="end">'
                   f'{pct(v)}</text>' for v in (0, .25, .5, .75, 1))
    xt = "".join(f'<text x="{X(dom * k / 4):.1f}" y="{Y1 + 22}" text-anchor="middle">{pct(dom * k / 4, 0 if dom * k * 100 % 4 == 0 else 1)}</text>'
                 for k in range(5))
    pts = hs["curve"][:round(1000 * dom) + 1]
    curve = " ".join(f"{X(i / 1000):.1f},{Y(v):.1f}" for i, v in enumerate(pts))
    x, y = X(hs["top_file_share"]), Y(hs["top_maintenance_share"])
    svg = (f'<svg viewBox="0 0 760 290" role="img" aria-label="Curva de concentração da manutenção">{grid}{xt}'
           f'<path d="M{X0} {Y1}L{X1} {Y(dom):.1f}" class="even"/>'
           f'<polyline points="{curve}" class="draw" pathLength="1"/>'
           f'<path d="M{x:.1f} {Y1}V{y:.1f}H{X0}" class="guide"/></svg>')
    return dom, f'<div class="plot">{svg}{pt(100 * x / 760, 100 * y / 290, 26, True)}</div>'


def columns(months, measured, M):
    """Monthly interface cost, maintenance below new work, quiet axis in money."""
    X0, X1, Y0, Y1 = 78, 748, 22, 236
    top = max((m["interface_cost"] for m in months), default=0) or 1
    step = nice(top)
    ymax = step * math.ceil(top / step)
    Y = lambda v: Y1 - (Y1 - Y0) * v / ymax
    out = [f'<svg viewBox="0 0 760 266" role="img" aria-label="Custo mensal de interface">']
    v = 0.0
    while v <= ymax + 1e-9:
        out.append(f'<path d="M{X0} {Y(v):.1f}H{X1}" class="grid"/><text x="{X0 - 10}" y="{Y(v) + 4:.1f}" text-anchor="end">'
                   f'{M(v) if v else "0"}</text>')
        v += step
    w = (X1 - X0) / len(months)
    for i, m in enumerate(months):
        shown = measured and m["interface_prs"] >= defaults.MAINT_MIN_UI_PRS
        x, bw = X0 + i * w + w * .22, w * .56
        hm = m["interface_maintenance_cost"] if shown else 0
        out.append(f'<g class="col" style="--i:{i}"><rect x="{x:.1f}" y="{Y(m["interface_cost"]):.1f}" width="{bw:.1f}" '
                   f'height="{Y(hm) - Y(m["interface_cost"]):.1f}" class="{"new" if shown else "all"}"/>'
                   + (f'<rect x="{x:.1f}" y="{Y(hm):.1f}" width="{bw:.1f}" height="{Y1 - Y(hm):.1f}" class="maint"/>' if hm else "")
                   + f'</g><text x="{x + bw / 2:.1f}" y="{Y(m["interface_cost"]) - 8:.1f}" text-anchor="middle" class="val">'
                   f'{M(m["interface_cost"])}</text><text x="{x + bw / 2:.1f}" y="{Y1 + 22}" text-anchor="middle">'
                   f'{month(m["month"])[:3]}</text>')
    out.append(f'<path d="M{X0} {Y1}H{X1}" class="axis"/></svg>')
    return "".join(out)


def field(row, i0=0):
    """The base as a field of quiet points; the team is the one lit point. Ticks on a 0–max% axis, median marked."""
    values, value = row["values"], row["value"]
    hi = max(values + [value])
    step = .1 if hi > .2 else .05
    dom = step * math.ceil(hi / step + 1e-9)
    X0, X1 = 20, 740
    X = lambda v: X0 + (X1 - X0) * v / dom
    lanes, dots = [], []
    for v in values:  # beeswarm: the first free lane, alternating above and below the axis
        x = X(v)
        lane = next((k for k, last in enumerate(lanes) if x - last >= 8), len(lanes))
        lanes.append(x) if lane == len(lanes) else lanes.__setitem__(lane, x)
        dots.append((x, (lane + 1) // 2 * (1 if lane % 2 else -1)))
    half = max((abs(d) for _, d in dots), default=0) * 8 + 12
    cy, height = half + 4, 2 * half + 34
    out = [f'<svg viewBox="0 0 760 {height:.0f}" role="img" aria-label="{html.escape(row["label"])}: empresas da base e o time">']
    t = 0.0
    while t <= dom + 1e-9:
        out.append(f'<path d="M{X(t):.1f} {cy - half:.1f}V{cy + half:.1f}" class="grid"/>'
                   f'<text x="{X(t):.1f}" y="{height - 6:.0f}" text-anchor="middle">{pct(t)}</text>')
        t += step
    xm = X(row["median"])
    out.append(f'<path d="M{xm:.1f} {cy - half:.1f}V{cy + half:.1f}" class="median"/>')
    out += [f'<circle cx="{x:.1f}" cy="{cy + 8 * d:.1f}" r="3.2" style="--i:{k}"/>' for k, (x, d) in enumerate(dots)]
    out.append("</svg>")
    return f'<div class="plot field">{"".join(out)}{pt(100 * X(value) / 760, 100 * cy / height, 34, True, len(dots))}</div>'


def findings_list(items):
    out = []
    for f in items:
        onde = f'<p class="onde">Módulos: {html.escape(", ".join(f["onde"]))}.</p>' if f["onde"] else ""
        limit = f'<p class="limit">{html.escape(f["limit"])}</p>' if "limit" in f else ""
        money_, fte = f["quanto"].split(" · ")
        quanto = money_ if fte.split()[0] in f["caption"] else f["quanto"]  # the caption already says the people
        out.append(f'<li><span class="k">{KIND_LABELS[f["kind"]]}</span><p>{html.escape(f["caption"])}. '
                   f'{html.escape(quanto)} · {html.escape(f["evidence"])}.</p>{onde}{limit}</li>')
    return f'<ul class="find">{"".join(out)}</ul>' if out else ""


def evidence(r, base):
    rows = insights.findings(r, base)
    return dict(org=r["org"], period=r["period"], currency=r["params"]["currency"],
                maintenance_share=r["headline"]["interface_maintenance_share"],
                maintenance_median=base["percentiles"]["interface_maintenance_share"].get("p50"),
                findings=rows, sections=insights.sections(r, base, rows))


def render(r, copy=None, base=None):
    e = html.escape
    cur, h, c, ui, ln = r["params"]["currency"], r["headline"], r["counts"], r["interface"], r["lines"]
    M = lambda x: e(money(x, cur))
    months = r["period"]["months"]
    period = f"{month(months[0])} a {month(months[-1])}"
    org = e(r["org"])
    measured = h["interface_maintenance_share"] is not None
    base = base or json.loads(benchmark.ASSET.read_text(encoding="utf-8"))
    doc = evidence(r, base)
    if copy is not None:
        try:
            validate(doc, copy)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            print(f"insights.json: {error}. Usando textos determinísticos.", file=sys.stderr)
            copy = None
    copy = copy or insights.fallback(doc)
    lookup = {f["id"]: f for f in doc["findings"]}
    secs = {s["id"]: s for s in doc["sections"]}
    small = f"com menos de {defaults.MAINT_MIN_UI_PRS} PRs de interface a taxa não é publicada"
    logo = (ASSETS / "wordmark.html").read_text(encoding="utf-8")
    y, mth, d = r["collected_on"].split("-")
    out = [f"""<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>UI Spend Report · {org}</title><style>{CSS}</style><script>{JS}</script><body>
<header><div class="wrap bar">{logo}<span>UI Spend Report</span></div></header><main class="wrap">
<div class="head rv"><div class="cat">Diagnóstico de interface · {org}</div>
<h1>{lit(copy["headline"])}</h1>{f'<p class="lede">{e(copy["lede"])}</p>' if copy["lede"] else ""}
<div class="meta"><b>UI Spend Report</b><span>Período: {period}</span><span>Fontes: {num(c['prs_scored'])} {'PR' if c['prs_scored'] == 1 else 'PRs'} com custo atribuído em {c['repos']} {'repositório' if c['repos'] == 1 else 'repositórios'} de {org} no GitHub</span><span>Gerado em: {d}/{mth}/{y}</span></div>
<div class="kpis">"""]
    for value, label in [(pct(h["interface_share"]) if r["cost"]["team_total"] else "—", "do custo de desenvolvimento em interface"),
                         (money(h["interface_monthly_cost"], cur), "por mês em interface"),
                         (pct(h["interface_maintenance_share"]) if measured and ui["cost"] else "—", "da interface em manutenção"),
                         (money(h["interface_maintenance_annual_cost"], cur) if measured else "—", "de manutenção por ano, no ritmo atual")]:
        out.append(f"<div><b>{kpi(value)}</b><p>{label}</p></div>")
    n = ui["prs"]
    out.append("</div>" + (f'<p class="note">A manutenção não foi medida: o período teve {n} {"PR" if n == 1 else "PRs"} de interface, '
                           f'e com menos de {defaults.MAINT_MIN_UI_PRS} a taxa muda muito de um PR para outro.</p>'
                           if ui["cost"] and not measured else "") + "</div>")

    def section(i, sid, body, title=None, text=()):
        s = copy["sections"].get(sid)
        title, text = (s["title"], s["evidence"]) if s else (title, text)
        eyebrow = secs[sid]["eyebrow"] if sid in secs else "Como medimos"
        items = findings_list([lookup[x] for x in secs[sid]["findings"]]) if sid in secs else ""
        out.append(f'<section class="sec rv" id="{sid}"><div class="sec-n"><span>{i:02d}</span> · {eyebrow}</div>'
                   f'<h2>{lit(title)}</h2>' + (f"<p>{e(' '.join(text))}</p>" if text else "") + body + items + "</section>")

    # 01 · cost
    body = []
    if r["cost"]["team_total"]:
        areas = [a for a in r["areas"] if a["cost"] > 0]
        lead = max((a["share"] for a in areas), default=1) or 1
        body.append(fig("Para onde foi o custo de desenvolvimento",
                        f"Custo atribuído aos PRs de {period}, dividido pelas linhas efetivas de cada área. "
                        f"Total: {M(r['cost']['team_monthly'])} por mês. Interface em luz.",
                        bars([(AREA_LABELS[a["area"]], a["share"] / lead,
                               f"{pct(a['share'], 1)} · {M(a['cost'] / len(months))}/mês", a["area"] == "interface")
                              for a in areas])))
    if measured and ui["cost"]:
        share = ui["maintenance_cost"] / ui["cost"]
        fte = ui["fte"]
        body.append(fig("Interface: manutenção e trabalho novo",
                        "Manutenção vem do rótulo do PR (bug ou funcionalidade) e, sem rótulo, de palavras de correção no "
                        "título. Pessoas equivalentes = custo mensal dividido pelo custo de uma pessoa.",
                        split([("Manutenção", share, f"{pct(share)} · {M(ui['maintenance_cost'] / len(months))}/mês · "
                                                     f"{num(fte * share, 1)} pessoas", True),
                               ("Trabalho novo", 1 - share, f"{pct(1 - share)} · {M(ui['new_cost'] / len(months))}/mês · "
                                                           f"{num(fte * (1 - share), 1)} pessoas", False)])))
    pf = r["platforms"]
    one = pf["shown"][0] if pf["shown"] and pf["shown"][0]["share"] >= defaults.PLATFORM_SINGLE_SHARE else None
    if one:  # one platform: a line instead of a platform chart
        name = defaults.PLATFORM_LABELS[one["platform"]]
        name = name if one["platform"] in ("ios", "android") else name.lower()
        body.append(f'<p class="limit">{"Toda" if pct(one["share"]) == "100%" else "Quase toda"} a interface medida é '
                    f'{e(name)} ({pct(one["share"])} do custo).</p>')
    elif pf["shown"]:
        rows = []
        for x in pf["shown"]:
            rate = (f'{pct(x["maintenance_share"])} em manutenção' if x["maintenance_share"] is not None else
                    f'manutenção não publicada ({num(x["prs"])} PRs)' if measured else f'{num(x["prs"])} PRs')
            rows.append((defaults.PLATFORM_LABELS[x["platform"]], x["share"], f'{pct(x["share"])} · {M(x["cost"])} · {rate}', x is pf["shown"][0]))
        hidden = (f'Plataformas abaixo de {pct(defaults.PLATFORM_MIN_SHARE)} do custo de interface somam {pct(pf["hidden_share"], 1)} '
                  'e não aparecem. ' if round(pf["hidden_share"], 3) else "")
        body.append(fig("Interface por plataforma", hidden + "Plataforma pelo caminho e pela extensão do arquivo.", bars(rows)))
    section(1, "custo", "".join(body), "O período não teve PRs com custo atribuído")

    # 02 · where maintenance concentrates
    hs, body = r["hotspots"], []
    if hs:
        one_file = hs["top_files"] == 1
        dom, body_l = lorenz(hs)
        body.append(fig("Poucos arquivos carregam a maior parte da manutenção",
                        f'{num(hs["top_files"])} {"arquivo" if one_file else "arquivos"} ({pct(hs["top_file_share"], 1)} dos que mudaram) '
                        f'{"concentra" if one_file else "concentram"} {pct(hs["top_maintenance_share"])} do custo de manutenção. '
                        f'{num(hs["files"])} arquivos de interface mudaram no período, sem contar testes, stories e imagens. '
                        f'Arquivos ordenados do mais caro ao mais barato.' ,
                        body_l, '<span><i class="ln"></i>custo de manutenção acumulado</span><span><i class="dash"></i>distribuição uniforme</span>'
                                    f'<span>eixo horizontal: os {pct(dom)} de arquivos mais caros</span>'))
        lead = max(m["share"] for m in hs["modules"])
        rows = "".join(f'<div class="mod"><div class="spot">{pt(50, 50, 10 + 22 * math.sqrt(m["share"] / lead), k == 0, k)}</div>'
                       f'<div><span>{e(insights.short_scope(m["path"]))}</span><p>{M(m["maintenance_cost"])} · '
                       f'{pct(m["share"], 1)} da manutenção · {num(m["prs"])} PRs · {num(m["files"])} arquivos</p></div></div>'
                       for k, m in enumerate(hs["modules"]))
        body.append(fig("Módulos com mais custo de manutenção",
                        "Tamanho do ponto proporcional ao custo de manutenção do módulo no período.", f'<div class="mods">{rows}</div>'))
    section(2, "manutencao", "".join(body), "A manutenção não foi medida", [f"Amostra pequena: {small}."])

    # 03 · time
    tr = r["trend"]
    if tr:
        a, b = (pct(v) for v in (tr["first"], tr["second"]))
        when1, when2 = span(tr["first_months"]), span(tr["second_months"])
        cap = (f"A manutenção ficou estável: {a} em {when1} e {b} em {when2}." if tr["stable"] else
               f"A manutenção passou de {a} em {when1} para {b} em {when2}.")
    elif measured:
        cap = (f"Uma das metades do período tem menos de {defaults.MAINT_MIN_UI_PRS} PRs de interface: "
               "a comparação entre as metades não é publicada.")
    else:
        cap = f"Custo mensal de interface. A divisão entre novo e manutenção não aparece: {small}."
    legend = ('<span><i class="sq maint"></i>manutenção</span><span><i class="sq new"></i>trabalho novo</span>' if measured
              else '<span><i class="sq new"></i>custo de interface</span>')
    section(3, "tempo", fig("Custo mensal de interface", e(cap), columns(r["by_month"], measured, M), legend),
            "Custo mensal de interface")

    # 04 · rework
    rw, body = r["rework"], ""
    if rw and rw["share"] is not None:
        body = fig(f"Trabalho novo seguido de manutenção em até {defaults.REWORK_DAYS} dias",
                   f'Custo de trabalho novo em interface, arquivo por arquivo. Amostra: {num(rw["new_prs"])} PRs de trabalho novo, '
                   f'{num(rw["reworked_prs"])} com manutenção depois em algum arquivo. Essa manutenção seguinte custou '
                   f'{M(rw["followup_cost"])}, em {num(rw["followup_prs"])} PRs.',
                   split([("Com manutenção depois", rw["share"], f'{pct(rw["share"], 1)} · {M(rw["reworked_cost"])}', True),
                          ("Sem manutenção depois", 1 - rw["share"], f'{pct(1 - rw["share"], 1)} · {M(rw["new_cost"] - rw["reworked_cost"])}', False)])
                   + f'<p class="limit">Só entram PRs mergeados até {defaults.REWORK_DAYS} dias antes do fim do período. '
                     'Manutenção vem do título e dos rótulos.</p>')
        section(4, "retrabalho", body)
    elif rw:
        section(4, "retrabalho", "", "O retrabalho não foi publicado",
                [f"Amostra pequena: {num(rw['new_prs'])} PRs de trabalho novo com {defaults.REWORK_DAYS} dias de observação; {small}."])
    else:
        section(4, "retrabalho", "", "O retrabalho não foi medido", [f"A manutenção não foi medida: {small}."])

    # 05 · base
    rows = insights.comparisons(r, base)
    body = "".join(f'<div class="metric"><div class="mh"><span>{e(x["label"])}</span><b>{pct(x["value"], 1)}</b>'
                   f'<small>{e(x["rank"][0].upper() + x["rank"][1:])} · mediana {pct(x["median"], 1)}</small></div>{field(x)}</div>'
                   for x in rows)
    section(5, "comparacao", fig(f"Posição de {org} entre {companies(len(base['points']))} de software open source",
                                 f"Cada ponto é uma empresa da base ({num(base['prs_scored'])} PRs com custo atribuído). "
                                 f"O ponto aceso é {org}; o traço vertical marca a mediana.", body) if rows else "",
            "Sem comparação disponível", ["A base não tem empresas suficientes para essas métricas."])

    # 06 · method
    body = f"""<svg class="flow" viewBox="0 0 100 3" preserveAspectRatio="none" aria-hidden="true"><path d="M12.5 1.5h75" class="draw" pathLength="1"/>
<path d="M12 1.5l.5 -1 .5 1 -.5 1z M37 1.5l.5 -1 .5 1 -.5 1z M62 1.5l.5 -1 .5 1 -.5 1z M87 1.5l.5 -1 .5 1 -.5 1z" class="node"/></svg><div class="steps"><div><small>01 · Mudança</small><h3>{num(c['prs_scored'])} {'PR' if c['prs_scored']==1 else 'PRs'}</h3><p>Linhas e arquivos após exclusões.</p></div>
<div><small>02 · Peso</small><h3>Score logarítmico</h3><p>log₂(1 + linhas) + 0,5 × log₂(1 + arquivos)</p></div>
<div><small>03 · Rateio</small><h3>{M(r['params']['monthly_cost'])} / pessoa</h3><p>Custo proporcional ao score dos PRs da pessoa. Meses sem PR passam ao próximo mês ativo.</p></div>
<div><small>04 · Área</small><h3>{M(ui['cost'])} em interface</h3><p>Custo por linhas efetivas de cada área. Arquivo sem linhas recebe peso um.</p></div></div>
<p>{'Testes unitários contam com o código que testam. ' if ln['tests']['unit_tests_follow_area'] else ''}Testes de integração têm área própria. Manutenção vem do rótulo do PR (bug ou funcionalidade) e, sem rótulo, de palavras de correção no título. Refatorações sem essas palavras contam como trabalho novo.</p>
<p>Ficaram fora do custo {num(c['prs_bot'])} {'PR' if c['prs_bot']==1 else 'PRs'} de bots e automações (traduções, dependências) e {num(c['prs_duplicate'])} {'PR duplicado' if c['prs_duplicate']==1 else 'PRs duplicados'}, que repetem trabalho de outro PR.{(" Desses, " + num(c['prs_release_copy']) + (" é cópia" if c['prs_release_copy']==1 else " são cópias") + " de release ou merge com os mesmos arquivos de outros PRs.") if c['prs_release_copy'] else ""}</p>"""
    out.append(f'<section class="sec method rv" id="metodo"><div class="sec-n"><span>06</span> · Como medimos</div>'
               f'<h2>Do PR ao custo de interface</h2>{body}</section>')
    out.append(f"""<p class="closing"><a href="{e(defaults.CLOSING_URL)}">{e(defaults.CLOSING_TEXT)}</a></p>
<footer>Relatório gerado no seu ambiente. Nenhum dado saiu dele.</footer></main></body></html>""")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--monthly-cost", type=float, required=True, help="average monthly cost per developer, with charges")
    ap.add_argument("--currency", default=defaults.CURRENCY)
    ap.add_argument("--hours", type=float, default=float(defaults.HOURS_PER_MONTH))
    ap.add_argument("--authors", default=defaults.AUTHORS)
    ap.add_argument("--findings", action="store_true", help="Grava findings.json com todas as evidências do período")
    a = ap.parse_args()
    manifest, prs = classify.load_cache(a.out)
    r = analyse(manifest, prs, classify.load_overrides(a.out), a.monthly_cost, a.hours, a.authors, a.currency)
    out = Path(a.out)
    (out / f"ui-spend-report-{r['org']}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, sort_keys=True),
                                                     encoding="utf-8")
    page = out / f"ui-spend-report-{r['org']}-{r['collected_on']}.html"
    base = json.loads(benchmark.ASSET.read_text(encoding="utf-8"))
    if a.findings:
        (out / "findings.json").write_text(json.dumps(evidence(r,base), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    copy_path = out / "insights.json"
    copy = None
    if copy_path.exists():
        try:
            copy = json.loads(copy_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            print(f"insights.json: {error}. Usando textos determinísticos.", file=sys.stderr)
    page.write_text(render(r, copy, base), encoding="utf-8")
    h = r["headline"]
    maint = (f"manutenção {pct(h['interface_maintenance_share'])} · {money(h['interface_maintenance_annual_cost'], a.currency)}/ano"
             if h["interface_maintenance_share"] is not None else
             f"manutenção não medida ({r['interface']['prs']} PRs de interface, mínimo {defaults.MAINT_MIN_UI_PRS})")
    print(f"{page}\n  interface: {pct(h['interface_share'])} do tempo · {money(h['interface_monthly_cost'], a.currency)}/mês · {maint}"
          f"\n  ambíguo: {pct(r['lines']['ambiguous_share'], 1)} das linhas efetivas · PRs com score: {r['counts']['prs_scored']}")


if __name__ == "__main__":
    main()
