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
from display import num, pct, money, month, companies, rank_text
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
            "curve": [cum[round(n * i / 100)] / total for i in range(101)],  # share of cost in the top 0, 1 … 100% of files
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


# ---------------------------------------------------------------- formatting (pt-BR)
# ---------------------------------------------------------------- HTML
CSS = (Path(__file__).resolve().parents[1] / "assets" / "report.css").read_text(encoding="utf-8")  # one block, inlined


def strip(values, client):
    # Stack near neighbours vertically, retaining their actual x position.
    lanes = []
    dots = []
    for value in values:
        x = 8 + 384 * value
        lane = next((i for i, last in enumerate(lanes) if x-last >= 9), len(lanes))
        if lane == len(lanes):
            lanes.append(x)
        lanes[lane] = x
        dots.append(f'<circle cx="{x:.2f}" cy="{14+lane*9}" r="4" fill="var(--muted)"/>')
    bottom = 26 + len(lanes)*9
    if client is not None:
        x = 8 + 384*client
        dots.append(f'<path d="M{x:.2f} {bottom-7}l6 6 -6 6 -6 -6z" fill="var(--calm)"/>')
    return (f'<svg viewBox="0 0 400 {bottom+8}" role="img" aria-label="Distribuição das empresas e posição do time">'
            f'<path d="M8 {bottom-1}h384" stroke="var(--track)"/>' + "".join(dots) + '</svg>')


def spark(values, peak=False):
    """Monthly sparkline for a card; the highest month gets a dot."""
    top = max(values) if values else 0
    if len(values) < 2 or not top:
        return ""
    pts = [(4 + i * 192 / (len(values) - 1), 34 - 30 * v / top) for i, v in enumerate(values)]
    line = " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)
    x, y = pts[values.index(top)]
    return (f'<svg class="spark" viewBox="0 0 200 38" role="img" aria-label="Custo mensal"><polyline points="{line}" '
            f'fill="none" stroke="var(--calm)" stroke-width="2"/><circle cx="{x:.2f}" cy="{y:.2f}" r="3.5" fill="var(--calm-hi)"/></svg>')


def span(ms):
    a, b = month(ms[0]), month(ms[-1])
    return a if a == b else (a[:3] if a[4:] == b[4:] else a) + " a " + b


def blocks(r, base, M):
    """Hotspots, rework, trend and platforms: deterministic, all visible, small samples say so."""
    e = html.escape
    out = ['<section class="blocks"><div class="eyebrow">Mapa do custo de interface</div><h2>Onde, quando e em que plataforma</h2>']
    small = f"com menos de {defaults.MAINT_MIN_UI_PRS} PRs de interface a taxa não é publicada"
    hs = r["hotspots"]
    out.append('<div class="block hot"><div><small class="label">Pontos de manutenção</small>')
    if hs:
        out.append(f'<p class="lead"><b>{num(hs["top_files"])} {"arquivo" if hs["top_files"] == 1 else "arquivos"}</b> '
                   f'({pct(hs["top_file_share"], 1)} dos que mudaram) {"concentra" if hs["top_files"] == 1 else "concentram"} {pct(hs["top_maintenance_share"])} do custo '
                   f'de manutenção.</p><small class="limit">{num(hs["files"])} arquivos de interface mudaram no período, '
                   'sem contar testes, stories e imagens.</small>')
        curve = " ".join(f"{4 + 192 * i / 100:.2f},{116 - 112 * v:.2f}" for i, v in enumerate(hs["curve"]))
        x = 4 + 192 * hs["top_file_share"]
        y = 116 - 112 * hs["top_maintenance_share"]
        out.append(f'<svg class="lorenz" viewBox="0 0 200 120" role="img" aria-label="Curva de concentração da manutenção">'
                   f'<path d="M4 116L196 4" stroke="var(--track)" stroke-dasharray="3 3"/><path d="M4 116H196" stroke="var(--track)"/>'
                   f'<polyline points="{curve}" fill="none" stroke="var(--calm)" stroke-width="2"/>'
                   f'<path d="M{x:.2f} 116V{y:.2f}" stroke="var(--dim)" stroke-dasharray="2 2"/>'
                   f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3.5" fill="var(--calm-hi)"/></svg>'
                   '<div class="axis"><small>arquivos de interface, do mais caro ao mais barato →</small></div>')
        out.append('</div><div><small class="label">Módulos com mais custo de manutenção</small><ol class="bars">')
        lead = hs["modules"][0]["share"] if hs["modules"] else 1
        for m in hs["modules"]:
            out.append(f'<li><span>{e(insights.short_scope(m["path"]))}</span><i style="width:{100 * m["share"] / lead:.1f}%"></i>'
                       f'<small>{M(m["maintenance_cost"])} · {pct(m["share"], 1)} da manutenção · {num(m["prs"])} PRs · '
                       f'{num(m["files"])} arquivos</small></li>')
        out.append('</ol></div></div>')
    else:
        out.append(f'<p>A manutenção não foi medida: {small}.</p></div></div>')
    out.append('<div class="pair">')
    # Trend: monthly interface cost split new vs maintenance.
    tr, months = r["trend"], r["by_month"]
    measured = r["headline"]["interface_maintenance_share"] is not None
    out.append('<div class="block"><small class="label">Tendência</small>')
    if tr:
        a, b = (pct(v) for v in (tr["first"], tr["second"]))
        when1, when2 = span(tr["first_months"]), span(tr["second_months"])
        out.append(f'<p class="lead">A manutenção ficou <b>estável</b>: {a} em {when1} e {b} em {when2}.</p>' if tr["stable"] else
                   f'<p class="lead">A manutenção passou de {a} em {when1} para <b>{b}</b> em {when2}.</p>')
    elif measured:
        out.append(f'<p class="lead">Uma das metades do período tem menos de {defaults.MAINT_MIN_UI_PRS} PRs de interface: '
                   'a comparação entre as metades não é publicada.</p>')
    else:
        out.append(f'<p class="lead">Custo mensal de interface. A divisão entre novo e manutenção não aparece: {small}.</p>')
    top = max((m["interface_cost"] for m in months), default=0) or 1
    out.append('<div class="stack">')
    for m in months:
        split = measured and m["interface_prs"] >= defaults.MAINT_MIN_UI_PRS
        h, hm = 100 * m["interface_cost"] / top, 100 * m["interface_maintenance_cost"] / top if split else 0
        out.append(f'<div><span style="height:{h - hm:.1f}%" class="{"new" if split else "all"}"></span>'
                   f'<span style="height:{hm:.1f}%" class="maint"></span><small>{month(m["month"])[:3]}</small></div>')
    out.append('</div><small class="legend"><i class="maint"></i> manutenção　<i class="new"></i> trabalho novo　·　'
               f'pico {M(top)}</small>')
    pf = r["platforms"]
    one = pf["shown"][0] if pf["shown"] and pf["shown"][0]["share"] >= defaults.PLATFORM_SINGLE_SHARE else None
    if one:  # one platform: a line here instead of a platform block
        name = defaults.PLATFORM_LABELS[one["platform"]]
        name = name if one["platform"] in ("ios", "android") else name.lower()
        out.append(f'<p class="limit">{"Toda" if pct(one["share"]) == "100%" else "Quase toda"} a interface medida é '
                   f'{e(name)} ({pct(one["share"])} do custo).</p>')
    out.append('</div>')
    # Rework: new work followed by maintenance on the same files.
    rw = r["rework"]
    out.append(f'<div class="block"><small class="label">Retrabalho em {defaults.REWORK_DAYS} dias</small>')
    if rw and rw["share"] is not None:
        out.append(f'<p class="lead"><b>{pct(rw["share"], 1)}</b> do custo de trabalho novo em interface foi em arquivos que '
                   f'receberam um PR de manutenção em até {defaults.REWORK_DAYS} dias.</p>'
                   f'<p>Essa manutenção seguinte custou {M(rw["followup_cost"])}, em {num(rw["followup_prs"])} PRs. '
                   f'Amostra: {num(rw["new_prs"])} PRs de trabalho novo, {num(rw["reworked_prs"])} com manutenção depois em algum arquivo.</p>')
        values = sorted(p["rework_share"] for p in base["points"] if p.get("rework_share") is not None)
        median = base["percentiles"].get("rework_share", {}).get("p50")
        if median is not None and len(values) >= defaults.BENCH_MIN:
            rank = rank_text(values, rw["share"])
            out.append(f'<p>Posição na base: {rank[0].lower() + rank[1:]} (mediana {pct(median, 1)}).</p>')
        out.append(f'<small class="limit">Só entram PRs mergeados até {defaults.REWORK_DAYS} dias antes do fim do período. '
                   'Manutenção vem do título e dos rótulos.</small>')
    elif rw:
        out.append(f'<p class="lead">Amostra pequena: {num(rw["new_prs"])} PRs de trabalho novo com {defaults.REWORK_DAYS} dias '
                   f'de observação; {small}.</p>')
    else:
        out.append(f'<p class="lead">A manutenção não foi medida: {small}.</p>')
    out.append('</div></div>')
    if one:
        out.append('</section>')
        return "".join(out)
    out.append('<div class="block plat"><small class="label">Plataformas</small>')
    shown = pf["shown"]
    if len(shown) == 1:
        out.append(f'<p class="lead"><b>{pct(shown[0]["share"])}</b> do custo de interface é '
                   f'{e(defaults.PLATFORM_LABELS[shown[0]["platform"]])}.</p>')
    out.append('<ol class="bars">' if len(shown) > 1 else '<ol class="bars single">')
    for i, x in enumerate(shown):
        label = defaults.PLATFORM_LABELS[x["platform"]]
        rate = (f'{pct(x["maintenance_share"])} em manutenção' if x["maintenance_share"] is not None else
                f'manutenção não publicada ({num(x["prs"])} PRs)' if measured else f'{num(x["prs"])} PRs')
        value = "" if len(shown) == 1 else (f'<b>{pct(x["share"])}</b>' if i == 0 else pct(x["share"])) + " do custo de interface · "
        out.append(f'<li><span>{e(label)}</span><i style="width:{100 * x["share"]:.1f}%"></i>'
                   f'<small>{value}{M(x["cost"])} · {rate}</small></li>')
    out.append('</ol>')
    if round(pf["hidden_share"], 3):
        out.append(f'<small class="limit">Plataformas abaixo de {pct(defaults.PLATFORM_MIN_SHARE)} do custo de interface somam '
                   f'{pct(pf["hidden_share"], 1)} e não aparecem. Plataforma pelo caminho e pela extensão do arquivo.</small>')
    else:
        out.append('<small class="limit">Plataforma pelo caminho e pela extensão do arquivo.</small>')
    out.append('</div></section>')
    return "".join(out)


def evidence(r, base):
    return dict(period=r["period"], currency=r["params"]["currency"], maintenance_share=r["headline"]["interface_maintenance_share"],
                maintenance_median=base["percentiles"]["interface_maintenance_share"].get("p50"),
                findings=insights.findings(r, base))


def render(r, selection=None, base=None):
    e = html.escape
    cur, h, c, ui, ln = r["params"]["currency"], r["headline"], r["counts"], r["interface"], r["lines"]
    M = lambda x: e(money(x, cur))
    months = r["period"]["months"]
    period = f"{month(months[0])} a {month(months[-1])}"
    org = e(r["org"])
    share = h["interface_share"]
    headline = (f"A interface absorveu <em>{pct(share)}</em> do esforço de engenharia."
                if r["cost"]["team_total"] else "O período não teve PRs com custo atribuído.")
    logo = (Path(__file__).resolve().parents[1] / "assets/wordmark.html").read_text(encoding="utf-8")
    out = [f"""<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>UI Spend Report · {org}</title><style>{CSS}</style><body>
<header>{logo}<span>UI Spend Report</span></header><main>
<div class="hero"><div><div class="eyebrow">{org}　/　{period}　/　{c['repos']} {'repositório' if c['repos'] == 1 else 'repositórios'}</div>
<h1>{headline}</h1></div>
<svg class="ring" viewBox="0 0 120 120" role="img" aria-label="Interface: {pct(share)} do esforço estimado">
<circle cx="60" cy="60" r="47" fill="none" stroke="var(--track)" stroke-width="11"/>
<circle cx="60" cy="60" r="47" fill="none" stroke="var(--calm)" stroke-width="11" pathLength="100"
stroke-dasharray="{100*share:.4f} 100" transform="rotate(-90 60 60)"/>
<text x="60" y="64" text-anchor="middle" fill="currentColor" font-size="13">Interface</text></svg></div>
<div class="stats">"""]
    measured = h["interface_maintenance_share"] is not None
    for value, label in [(pct(share) if r["cost"]["team_total"] else "—", "do esforço em interface"), (M(h['interface_monthly_cost']), "por mês em interface"),
                         (pct(h["interface_maintenance_share"]) if measured and ui["cost"] else "—", "da interface em manutenção"),
                         (M(h['interface_maintenance_annual_cost']) if measured else "—", "por ano em manutenção, no ritmo atual")]:
        out.append(f"<div><b>{value}</b><small>{label}</small></div>")
    n = ui["prs"]
    note = (f'<p class="note">A manutenção não foi medida: o período teve {n} {"PR" if n == 1 else "PRs"} de interface, '
            f'e com menos de {defaults.MAINT_MIN_UI_PRS} a taxa muda muito de um PR para outro.</p>' if ui["cost"] and not measured else "")
    base = base or json.loads(benchmark.ASSET.read_text(encoding="utf-8"))
    points = base["points"]
    size = benchmark.bucket(c["developers"])
    out.append(f"""</div>{note}<section class="reference"><div class="eyebrow">Referência de mercado</div>
<h2>Onde o time se situa</h2><p>{companies(len(points))} de software open source · {num(base['prs_scored'])} {'PR' if base['prs_scored']==1 else 'PRs'} com custo atribuído.</p><div class="strips">""")
    for metric, label in zip(benchmark.METRICS, ("Esforço em interface", "Manutenção da interface")):
        valid = bool(ui["cost"] and measured if metric == benchmark.METRICS[1] else r["cost"]["team_total"])
        value = h[metric] if valid else None
        display = pct(value, 1) if valid else "—"
        out.append(f'<div class="strip"><h3>{label}</h3><strong class="client-value">{display}</strong><small> no time</small>')
        for scope, group in (("Base completa", points), (f"Times · {size} pessoas", [p for p in points if p["team_size"] == size])):
            values = sorted(p[metric] for p in group if p[metric] is not None)
            if scope != "Base completa" and len(values) < defaults.COHORT_MIN:
                continue
            rank = rank_text(values, value)
            out.append(f'<div class="cohort"><small>{scope} · {companies(len(values))}</small>{strip(values, value)}'
                       f'<strong>{rank}</strong></div>')
        out.append('</div>')
    out.append('</div><small>● Empresa da base　 ◆ Seu time　 ·　 Escala de 0 a 100%</small></section>')
    doc = evidence(r, base)
    if selection is not None:
        try:
            validate(doc, selection)
        except ValueError as error:
            print(f"insights.json: {error}. Usando textos determinísticos.", file=sys.stderr)
            selection = None
    if selection is None:
        selection = validate(doc, insights.fallback(doc["findings"]))
    lookup = {f["id"]: f for f in doc["findings"]}
    if selection:
        out.append('<section class="signals"><div class="eyebrow">Diagnóstico · ordem de custo no período</div>'
                   '<h2>O que os números mostram</h2><div class="cards">')
        for card in selection:
            f = lookup[card["finding_id"]]
            q = f["quando"]
            facts = [("Escopo", e(f["scope_label"]) if f["scope"] != r["org"] else ""),
                     ("Onde", "<br>".join(e(x) for x in f["onde"])),
                     ("Quando", spark(q["values"]) + f'<small>{e(q["labels"][0])} a {e(q["labels"][-1])}</small>' if q["values"] else ""),
                     ("Comparação", "<br>".join(e(f[k]) for k in ("comparison", "comparison_fix") if k in f)),
                     ("Evidência", e(f["evidence"]))]
            limit = f'<small class="limit">{e(f["limit"])}</small>' if "limit" in f else ""
            out.append(f'<article class="card"><div><b class="quanto">{e(f["quanto"])}</b><h3>{e(card["title"])}</h3>'
                       f'<p>{e(card["diagnosis"])}</p>{limit}</div><dl>'
                       + "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in facts if v) + "</dl></article>")
        out.append('</div></section>')
    out.append(blocks(r, base, M))
    out.append(f"""<section class="method"><div class="eyebrow">Método de atribuição</div><h2>Do PR ao custo de interface</h2>
<svg class="flow" viewBox="0 0 100 3" preserveAspectRatio="none" aria-hidden="true"><path d="M12.5 1.5h75" stroke="var(--muted)" stroke-width=".4"/>
<path d="M12 1.5l.5 -1 .5 1 -.5 1z M37 1.5l.5 -1 .5 1 -.5 1z M62 1.5l.5 -1 .5 1 -.5 1z M87 1.5l.5 -1 .5 1 -.5 1z" fill="var(--calm)"/></svg><div class="steps"><div><small>01 · Mudança efetiva</small><h3>{num(c['prs_scored'])} {'PR' if c['prs_scored']==1 else 'PRs'}</h3><p>Linhas e arquivos após exclusões.</p></div>
<div><small>02 · Peso do PR</small><h3>Score logarítmico</h3><p>log₂(1 + linhas) + 0,5 × log₂(1 + arquivos)</p></div>
<div><small>03 · Rateio mensal</small><h3>{M(r['params']['monthly_cost'])} / pessoa</h3><p>Custo proporcional ao score dos PRs da pessoa. Meses sem PR passam ao próximo mês ativo.</p></div>
<div><small>04 · Divisão por área</small><h3>{M(ui['cost'])} em interface</h3><p>Custo por linhas efetivas de cada área. Arquivo sem linhas recebe peso um.</p></div></div>
<p>{'Testes unitários contam com o código que testam. ' if ln['tests']['unit_tests_follow_area'] else ''}Testes de integração têm área própria. Manutenção vem do rótulo do PR (bug ou funcionalidade) e, sem rótulo, de palavras de correção no título. Refatorações sem essas palavras contam como trabalho novo.</p>
<p>Ficaram fora do custo {num(c['prs_bot'])} {'PR' if c['prs_bot']==1 else 'PRs'} de bots e automações (traduções, dependências) e {num(c['prs_duplicate'])} {'PR duplicado' if c['prs_duplicate']==1 else 'PRs duplicados'}, que repetem trabalho de outro PR.{(" Desses, " + num(c['prs_release_copy']) + (" é cópia" if c['prs_release_copy']==1 else " são cópias") + " de release ou merge com os mesmos arquivos de outros PRs.") if c['prs_release_copy'] else ""}</p></section>
<p class="closing"><a href="{e(defaults.CLOSING_URL)}">{e(defaults.CLOSING_TEXT)}</a></p>
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
    selection = None
    if copy_path.exists():
        try:
            selection = json.loads(copy_path.read_text(encoding="utf-8"))
        except (ValueError,OSError) as error:
            print(f"insights.json: {error}. Usando textos determinísticos.", file=sys.stderr)
    page.write_text(render(r,selection,base), encoding="utf-8")
    h = r["headline"]
    maint = (f"manutenção {pct(h['interface_maintenance_share'])} · {money(h['interface_maintenance_annual_cost'], a.currency)}/ano"
             if h["interface_maintenance_share"] is not None else
             f"manutenção não medida ({r['interface']['prs']} PRs de interface, mínimo {defaults.MAINT_MIN_UI_PRS})")
    print(f"{page}\n  interface: {pct(h['interface_share'])} do tempo · {money(h['interface_monthly_cost'], a.currency)}/mês · {maint}"
          f"\n  ambíguo: {pct(r['lines']['ambiguous_share'], 1)} das linhas efetivas · PRs com score: {r['counts']['prs_scored']}")


if __name__ == "__main__":
    main()
