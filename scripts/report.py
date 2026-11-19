#!/usr/bin/env python3
"""Score PRs, split developer cost, aggregate by area/repo/month and write the HTML + JSON diagnostic.

  python3 report.py --out DIR --monthly-cost 20000 [--currency BRL] [--hours 160] [--authors all|members|min-prs:K]

Reads DIR/cache (from collect.py) and DIR/overrides.json. Same inputs → byte-identical outputs.
Aggregates only: author logins are used in memory for the cost split and never written.
"""
import argparse, html, json, math, re, sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import classify, defaults, benchmark, insights

_MAINT = re.compile(defaults.MAINTENANCE, re.I)
_NEW = re.compile(defaults.NEW_WORK, re.I)
SYMBOL = {"BRL": "R$", "USD": "US$", "EUR": "€", "GBP": "£"}
MESES = "jan fev mar abr mai jun jul ago set out nov dez".split()


# ---------------------------------------------------------------- model
def score(lines, files):
    return math.log2(1 + lines) + 0.5 * math.log2(1 + files)


def is_maintenance(pr):
    return not _NEW.search(pr["title"]) and bool(_MAINT.search(" ".join([pr["title"], *pr.get("labels", [])])))


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
    n_merged = n_bot = n_dup = n_empty = 0
    human = []
    for repo in sorted(prs_by_repo):
        for pr in sorted(prs_by_repo[repo], key=lambda p: p["number"]):
            n_merged += 1
            if classify.is_bot(pr):
                n_bot += 1
                continue
            if classify.is_duplicate(pr):
                n_dup += 1
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
            files.append((a or "other", path, lines))
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
    native_cost = 0.0
    n_maint = 0
    for pr in scored:
        maint = is_maintenance(pr)
        n_maint += maint
        weights = [max(f[2], 1) for f in pr["files_eff"]]
        wsum = sum(weights)
        repo_cost[pr["repo"]] += pr["cost"]
        repo_prs[pr["repo"]] += 1
        mon_cost[pr["month"]] += pr["cost"]
        mon_prs[pr["month"]] += 1
        for (a, path, _), w in zip(pr["files_eff"], weights):
            c = pr["cost"] * w / wsum
            by_area[a] += c
            d = "/".join([pr["repo"]] + path.split("/")[:-1][:defaults.TOP_DIR_DEPTH])
            dir_cost[a][d] += c
            if maint:
                maint_area[a] += c
                dir_m[a][d] += c
            if a == "interface":
                if path.endswith((".swift", ".xib", ".storyboard", ".xcstrings")) or "/src/main/res/" in "/" + path or (
                        path.endswith((".kt", ".java")) and any(not root or path.startswith(root + "/")
                        for root in index.modules.get(pr["repo"], ()))):
                    native_cost += c
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
    return rounded({
        "org": manifest["org"], "collected_on": manifest["collected_on"],
        "period": {"since": manifest["since"], "until_exclusive": manifest["until"], "months": months},
        "params": {"monthly_cost": monthly_cost, "currency": currency, "hours_per_month": hours,
                   "authors": authors, "max_prs_per_repo": manifest.get("max_prs", 0), "repos": manifest["repos"]},
        "headline": {
            "interface_share": share(ui, total),
            "interface_monthly_cost": ui / n_m,
            "interface_maintenance_share": share(ui_m, ui),
            "interface_maintenance_annual_cost": ui_m / n_m * 12,
        },
        "counts": {"prs_merged": n_merged, "prs_bot": n_bot, "prs_duplicate": n_dup, "prs_not_team": len(human) - len(kept),
                   "prs_no_effective_change": n_empty, "prs_scored": len(scored), "prs_maintenance": n_maint,
                   "developers": len({p["author"] for p in scored}), "developer_months": dev_months,
                   "repos": len(manifest["repos"])},
        "lines": {"total": lines_total, "effective": eff_lines, "excluded": dict(sorted(excluded.items())),
                  "excluded_share": share(sum(excluded.values()) - excluded["binary"], lines_total),
                  "ambiguous": amb_lines, "ambiguous_share": share(amb_lines, eff_lines),
                  "tests": {"unit_tests_follow_area": defaults.UNIT_TESTS_FOLLOW_AREA,
                            **{k: test_lines[k] for k in ("integration", "unit", "unit_inferred", "unit_unresolved")},
                            "unit_inferred_share": share(test_lines["unit_inferred"], unit)}},
        "cost": {"team_total": total, "team_monthly": total / n_m, "carried": carried,
                 "carried_share": share(carried, total)},
        "interface": {"cost": ui, "native_cost": native_cost, "new_cost": ui - ui_m, "maintenance_cost": ui_m,
                      "monthly_hours": ui / n_m * to_hours, "fte": ui / n_m / monthly_cost},
        "areas": [{"area": a, "cost": by_area[a], "share": share(by_area[a], total),
                   "monthly_hours": by_area[a] / n_m * to_hours, "maintenance_share": share(maint_area[a], by_area[a])}
                  for a in sorted(classify.AREAS, key=lambda a: (-by_area[a], a))],
        "by_repo": [{"repo": r, "prs": repo_prs[r], "cost": repo_cost[r], "share_of_total": share(repo_cost[r], total),
                     "interface_cost": repo_ui[r], "interface_share": share(repo_ui[r], repo_cost[r]),
                     "interface_maintenance_share": share(repo_ui_m[r], repo_ui[r])}
                    for r in sorted(repo_cost, key=lambda r: (-repo_cost[r], r))],
        "by_month": [{"month": m, "prs": mon_prs[m], "cost": mon_cost[m], "interface_cost": mon_ui[m],
                      "interface_share": share(mon_ui[m], mon_cost[m]),
                      "interface_maintenance_share": share(mon_ui_m[m], mon_ui[m])} for m in months],
        "top_dirs_by_area": {a: [{"path": d + "/", "cost": dc[d], "share_of_area": share(dc[d], by_area[a]),
                                  "maintenance_share": share(dir_m[a][d], dc[d])}
                                 for d in sorted(dc, key=lambda d: (-dc[d], d))[:defaults.TOP_DIRS]]
                             for a, dc in sorted(dir_cost.items()) if a != "tooling"},
    })


def rounded(o):
    if isinstance(o, float):
        return round(o, 4)
    if isinstance(o, dict):
        return {k: rounded(v) for k, v in o.items()}
    if isinstance(o, list):
        return [rounded(v) for v in o]
    return o


# ---------------------------------------------------------------- formatting (pt-BR)
def num(x, d=0):
    return f"{x:,.{d}f}".replace(",", "§").replace(".", ",").replace("§", ".")


def pct(x, d=0):
    return num(100 * x, d) + "%"


def money(x, cur):
    s = SYMBOL.get(cur, cur)
    if abs(x) >= 1e6:
        return f"{s} {num(x / 1e6, 1)} mi"
    if abs(x) >= 1e4:
        return f"{s} {num(x / 1e3)} mil"
    if abs(x) >= 1e3:
        return f"{s} {num(x / 1e3, 1)} mil"
    return f"{s} {num(x)}"


def month(m):
    y, mm = m.split("-")
    return f"{MESES[int(mm) - 1]}/{y}"


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
        dots.append(f'<circle cx="{x:.2f}" cy="{14+lane*9}" r="4" fill="#768f91"/>')
    bottom = 26 + len(lanes)*9
    if client is not None:
        x = 8 + 384*client
        dots.append(f'<path d="M{x:.2f} {bottom-7}l6 6 -6 6 -6 -6z" fill="#007771"/>')
    return (f'<svg viewBox="0 0 400 {bottom+8}" role="img" aria-label="Distribuição das empresas e posição do time">'
            f'<path d="M8 {bottom-1}h384" stroke="#baccca"/>' + "".join(dots) + '</svg>')


def mini_chart(item):
    values = item["chart"]
    if item["rule"] in ("trend", "peak", "floor"):
        coords = " ".join(f"{3+i*94/(len(values)-1):.2f},{28-25*v:.2f}" for i,v in enumerate(values))
        return f'<svg viewBox="0 0 100 32"><polyline points="{coords}" fill="none" stroke="#217973" stroke-width="2"/></svg>'
    marker = (f'<path d="M{100*values[1]:.2f} 7v17" stroke="#162e32" stroke-width="1.5"/>'
              if len(values)>1 else "")
    return (f'<svg viewBox="0 0 100 32"><rect y="12" width="100" height="7" fill="#d9e4df"/>'
            f'<rect y="12" width="{100*values[0]:.2f}" height="7" fill="#217973"/>{marker}</svg>')


def render(r):
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
<circle cx="60" cy="60" r="47" fill="none" stroke="#294149" stroke-width="11"/>
<circle cx="60" cy="60" r="47" fill="none" stroke="#94e4dd" stroke-width="11" pathLength="100"
stroke-dasharray="{100*share:.4f} 100" transform="rotate(-90 60 60)"/>
<text x="60" y="64" text-anchor="middle" fill="currentColor" font-size="13">Interface</text></svg></div>
<div class="stats">"""]
    for value, label in [(pct(share) if r["cost"]["team_total"] else "—", "do esforço em interface"), (M(h['interface_monthly_cost']), "por mês em interface"),
                         (pct(h["interface_maintenance_share"]) if ui["cost"] else "—", "da interface em manutenção"), (M(h['interface_maintenance_annual_cost']), "por ano em manutenção, no ritmo atual")]:
        out.append(f"<div><b>{value}</b><small>{label}</small></div>")
    base = json.loads(benchmark.ASSET.read_text(encoding="utf-8"))
    points = base["points"]
    size = benchmark.bucket(c["developers"])
    out.append(f"""</div><section class="reference"><div class="eyebrow">Referência de mercado</div>
<h2>Onde o time se situa</h2><p>{len(points)} empresas de software open source · {num(base['prs_scored'])} PRs com custo atribuído.</p><div class="strips">""")
    for metric, label in zip(benchmark.METRICS, ("Esforço em interface", "Manutenção da interface")):
        valid = bool(ui["cost"] if metric == benchmark.METRICS[1] else r["cost"]["team_total"])
        value = h[metric] if valid else None
        display = pct(value, 1) if valid else "—"
        out.append(f'<div class="strip"><h3>{label}</h3><strong class="client-value">{display}</strong><small> no time</small>')
        for scope, group in (("Base completa", points), (f"Times · {size} pessoas", [p for p in points if p["team_size"] == size])):
            values = sorted(p[metric] for p in group if p[metric] is not None)
            rank = (f"Acima de {pct(sum(v < value for v in values) / len(values))} das empresas" if values and valid
                    else "Sem comparação disponível")
            out.append(f'<div class="cohort"><small>{scope} · {len(values)} empresas</small>{strip(values, value)}'
                       f'<strong>{rank}</strong></div>')
        out.append('</div>')
    out.append('</div><small>● Empresa da base　 ◆ Seu time　 ·　 Escala de 0 a 100%</small></section>')
    selected = insights.findings(r, base)
    if selected:
        out.append('<section class="signals"><div class="eyebrow">Prioridades por custo observado</div><h2>Onde agir primeiro</h2><div class="cards">')
        for item in selected:
            rule, value = item["rule"], item["value"]
            title = e(item["title"]).replace("/", "/<wbr>")
            label = pct(value, 1)
            if rule == "peak":
                title = "Pico de investimento · " + month(item["title"])
                label = num(value, 1) + "× a média mensal"
            elif rule == "trend":
                label = num(value * 100, 1) + " p.p./mês"
            elif rule == "quartile":
                label += " · quartil superior " + pct(item["chart"][1], 1)
            elif rule == "floor":
                label += " ou mais em cada mês"
            elif rule == "hotspot":
                label += " local · interface " + pct(item["chart"][1], 1)
            elif rule == "concentration":
                label += " do custo de interface"
                title = "Concentração em " + title
            amount_label = "de piso no período" if rule == "floor" else "no último mês" if rule == "trend" else "no mês de pico" if rule == "peak" else "no período"
            out.append(f'<div class="card"><small>{M(item["amount"])} {amount_label}</small><h3>{title}</h3>'
                       f'<strong>{label}</strong>{mini_chart(item)}<p>{e(item["action"])}</p></div>')
        out.append('</div></section>')
    out.append(f"""<section class="method"><div class="eyebrow">Método de atribuição</div><h2>Do PR ao custo de interface</h2>
<svg class="flow" viewBox="0 0 100 3" preserveAspectRatio="none" aria-hidden="true"><path d="M12.5 1.5h75" stroke="#70938d" stroke-width=".4"/>
<path d="M12 1.5l.5 -1 .5 1 -.5 1z M37 1.5l.5 -1 .5 1 -.5 1z M62 1.5l.5 -1 .5 1 -.5 1z M87 1.5l.5 -1 .5 1 -.5 1z" fill="#217973"/></svg><div class="steps"><div><small>01 · Mudança efetiva</small><h3>{num(c['prs_scored'])} PRs</h3><p>Linhas e arquivos após exclusões.</p></div>
<div><small>02 · Peso do PR</small><h3>Score logarítmico</h3><p>log₂(1 + linhas) + 0,5 × log₂(1 + arquivos)</p></div>
<div><small>03 · Rateio mensal</small><h3>{M(r['params']['monthly_cost'])} / pessoa</h3><p>Custo proporcional ao score dos PRs da pessoa. Meses sem PR passam ao próximo mês ativo.</p></div>
<div><small>04 · Divisão por área</small><h3>{M(ui['cost'])} em interface</h3><p>Custo por linhas efetivas de cada área. Arquivo sem linhas recebe peso um.</p></div></div>
<p>{'Testes unitários contam com o código que testam. ' if ln['tests']['unit_tests_follow_area'] else ''}Testes de integração têm área própria. Títulos e rótulos indicam manutenção.</p></section>
<aside><a href="{e(defaults.CLOSING_URL)}">{e(defaults.CLOSING_TEXT)}</a></aside>
<footer>Relatório gerado no seu ambiente. Nenhum dado saiu dele.</footer></main></body></html>""")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--monthly-cost", type=float, required=True, help="average monthly cost per developer, with charges")
    ap.add_argument("--currency", default=defaults.CURRENCY)
    ap.add_argument("--hours", type=float, default=float(defaults.HOURS_PER_MONTH))
    ap.add_argument("--authors", default=defaults.AUTHORS)
    a = ap.parse_args()
    manifest, prs = classify.load_cache(a.out)
    r = analyse(manifest, prs, classify.load_overrides(a.out), a.monthly_cost, a.hours, a.authors, a.currency)
    out = Path(a.out)
    (out / f"ui-spend-report-{r['org']}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, sort_keys=True),
                                                     encoding="utf-8")
    page = out / f"ui-spend-report-{r['org']}-{r['collected_on']}.html"
    page.write_text(render(r), encoding="utf-8")
    h = r["headline"]
    print(f"{page}\n  interface: {pct(h['interface_share'])} do tempo · {money(h['interface_monthly_cost'], a.currency)}/mês · "
          f"manutenção {pct(h['interface_maintenance_share'])} · {money(h['interface_maintenance_annual_cost'], a.currency)}/ano"
          f"\n  ambíguo: {pct(r['lines']['ambiguous_share'], 1)} das linhas efetivas · PRs com score: {r['counts']['prs_scored']}")


if __name__ == "__main__":
    main()
