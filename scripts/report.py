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
import classify, defaults

_MAINT = re.compile(defaults.MAINTENANCE, re.I)
_NEW = re.compile(defaults.NEW_WORK, re.I)
AREA_PT = {"tooling": "Ferramentas de IA", "interface": "Interface", "backend": "Backend", "infra": "Infra e build", "tests": "Testes de integração",
           "data": "Dados", "docs": "Documentação", "other": "Outros / não classificado"}
SYMBOL = {"BRL": "R$", "USD": "US$", "EUR": "€", "GBP": "£"}
EXCL_PT = {"lockfile": "lockfiles", "third_party": "código de terceiros", "override": "exceções locais", "build": "build/minificado",
           "snapshot": "snapshots", "migration": "migrations", "translation": "traduções para outros idiomas",
           "generated": "código gerado"}
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
        "interface": {"cost": ui, "new_cost": ui - ui_m, "maintenance_cost": ui_m,
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


def date_br(iso):
    y, m, d = iso.split("-")
    return f"{d}/{m}/{y}"


# ---------------------------------------------------------------- HTML
CSS = (Path(__file__).resolve().parents[1] / "assets" / "report.css").read_text(encoding="utf-8")  # one block, inlined


def render(r):
    e = html.escape
    cur, h, c, ui, ln = r["params"]["currency"], r["headline"], r["counts"], r["interface"], r["lines"]
    M = lambda x: e(money(x, cur))
    months = r["period"]["months"]
    period = f"{month(months[0])} a {month(months[-1])}"
    n_m = len(months)
    org = e(r["org"])
    top_dirs = r["top_dirs_by_area"].get("interface", [])
    top_dir = top_dirs[0] if top_dirs else None
    repos = sorted((x for x in r["by_repo"] if x["interface_cost"]), key=lambda x: (-x["interface_cost"], x["repo"]))
    share = h["interface_share"]
    ms = h["interface_maintenance_share"]
    logo = (Path(__file__).resolve().parents[1] / "assets/wordmark.html").read_text(encoding="utf-8")
    out = [f"""<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>UI Spend Report · {org}</title><style>{CSS}</style><body>
<header>{logo}<span>UI Spend Report</span></header><main>
<div class="hero"><div><div class="eyebrow">{org}　/　{period}</div>
<h1>A interface absorveu <em>{pct(share)}</em> do esforço de engenharia.</h1></div>
<svg class="ring" viewBox="0 0 120 120" role="img" aria-label="Interface: {pct(share)} do esforço estimado">
<circle cx="60" cy="60" r="47" fill="none" stroke="#294149" stroke-width="11"/>
<circle cx="60" cy="60" r="47" fill="none" stroke="#94e4dd" stroke-width="11" pathLength="100"
stroke-dasharray="{100*share:.4f} 100" transform="rotate(-90 60 60)"/>
<text x="60" y="64" text-anchor="middle" fill="currentColor" font-size="13">Interface</text></svg></div>
<div class="stats">"""]
    for value, label in [(pct(share), "do esforço em interface"), (M(h['interface_monthly_cost']), "por mês em interface"),
                         (pct(ms), "da interface em manutenção"), (M(h['interface_maintenance_annual_cost']), "por ano em manutenção, no ritmo atual")]:
        out.append(f"<div><b>{value}</b><small>{label}</small></div>")
    out.append("</div><article><section><div class='eyebrow'>Evolução mensal</div><h2>Quanto da engenharia foi para interface?</h2><div class='trend'>")
    for m in r["by_month"]:
        value = m["interface_share"]
        out.append(f"<div><strong>{pct(value)}</strong><svg viewBox='0 0 24 100' aria-hidden='true'><rect width='24' height='100' fill='#e1e8e6'/><rect y='{100-100*value:.4f}' width='24' height='{100*value:.4f}' fill='#217973'/></svg><small>{month(m['month'])}</small></div>")
    out.append("</div></section><section><div class='eyebrow'>Distribuição por repositório</div><h2>Onde se concentra o investimento em interface?</h2>")
    for repo in repos[:5]:
        fraction = repo["interface_cost"] / ui["cost"]
        out.append(f"<div class='rank'><span>{e(repo['repo'])}</span><strong>{pct(fraction)}</strong><svg viewBox='0 0 100 1' preserveAspectRatio='none' aria-hidden='true'><rect width='100' height='1' fill='#e1e8e6'/><rect width='{100*fraction:.4f}' height='1' fill='#217973'/></svg></div>")
    if not repos:
        out.append("<p>Nenhum custo atribuído à interface no período.</p>")
    out.append(f"""</section><section class="maintenance"><div class="eyebrow">Composição do custo de interface</div>
<h2>Quanto custa manter o que já existe?</h2><p>No período, {M(ui['maintenance_cost'])} foram para correções e ajustes.</p>
<svg viewBox="0 0 100 1" preserveAspectRatio="none" aria-label="Manutenção: {pct(ms)}" role="img">
<rect width="100" height="1" fill="#dee6e3"/><rect width="{100*ms:.4f}" height="1" fill="#217973"/></svg>
<div class="legend"><span>Manutenção · {pct(ms)}</span><span>Trabalho novo · {M(ui['new_cost'])}</span></div></section>""")
    out.append("</article><details><summary>Apêndice · dados completos e método</summary><section><h2>Custo por área</h2><dl>")
    for row in r["areas"]:
        out.append(f"<dt>{AREA_PT[row['area']]}</dt><dd>{pct(row['share'], 1)} · {M(row['cost'])}</dd>")
    out.append("</dl></section>")
    # 02 repos
    out.append(f"""<section><div class="kicker">02 · Por repositório</div>
<h2>Todos os repositórios</h2>
<table><tr><th>Repositório</th><th class="n">PRs</th><th class="n">Custo no período</th><th class="n">% interface</th>
<th class="n">Manutenção na interface</th></tr>""")
    for x in r["by_repo"]:
        out.append(f"<tr><td><code>{e(x['repo'])}</code></td><td class='n'>{num(x['prs'])}</td><td class='n'>{M(x['cost'])}</td>"
                   f"<td class='n'>{pct(x['interface_share'])}</td><td class='n'>{pct(x['interface_maintenance_share'])}</td></tr>")
    out.append("</table></section>")

    # 05 directories
    out.append(f"""<section><div class="kicker">05 · Onde a interface pesa</div>
<h2>{('Diretório que mais consome: ' + '<code>' + e(top_dir['path']) + '</code>') if top_dir else 'Diretórios'}</h2>
<table><tr><th>Diretório</th><th class="n">Custo no período</th><th class="n">% da interface</th><th class="n">Manutenção</th></tr>""")
    for d in top_dirs:
        out.append(f"<tr><td><code>{e(d['path'])}</code></td><td class='n'>{M(d['cost'])}</td>"
                   f"<td class='n'>{pct(d['share_of_area'])}</td><td class='n'>{pct(d['maintenance_share'])}</td></tr>")
    out.append("</table></section>")

    # 06 limits
    out.append(f"""<section><div class="kicker">06 · O que os dados não respondem</div>
<h2>O que este número não vê</h2><ul>
<li><b>Revisão de código.</b> O tempo de quem revisa não aparece; o custo fica com quem mergeou.</li>
<li><b>Design e produto.</b> Só entra trabalho de engenharia que virou PR. Tempo de design, discovery e QA manual fica fora.</li>
<li><b>Reuniões e suporte.</b> Entram no custo mensal de cada pessoa e são divididos pelos PRs como se fossem trabalho neles.</li>
<li><b>Trabalho não mergeado.</b> PRs fechados sem merge, branches abandonadas e PRs ainda abertos não contam.
Quem não mergeou nada no período não entra no time.</li>
<li><b>{num(r['params']['hours_per_month'])} horas por mês.</b> As horas são uma conversão do custo; a jornada real pode ser outra.</li>
<li><b>Limites do score.</b> O score cresce com o log das linhas e dos arquivos: um PR de 1.000 linhas pesa só ~2× um de 30.
Linhas não medem dificuldade. Um PR que mistura áreas é dividido pelas linhas de cada uma.</li>
<li><b>Manutenção pelo título.</b> A separação entre manutenção e interface nova depende de como o time nomeia PRs.</li>
<li><b>Classificação por caminho.</b> {pct(ln['ambiguous_share'], 1)} das linhas ficaram sem área definida e contam como "outros".</li>
</ul></section>""")

    # 07 method
    ex = ln["excluded"]
    t = ln["tests"]
    tests_txt = (f" Testes unitários contam com o código que testam (interface, backend…); testes de integração e "
                 f"e2e são uma área à parte. Em {pct(t['unit_inferred_share'], 1)} das linhas de teste unitário a área "
                 f"do código testado foi inferida pelo pacote.") if t["unit_tests_follow_area"] else ""
    ex_txt = ", ".join(f"{EXCL_PT.get(k, k)} {num(v)}" for k, v in ex.items() if k != "binary" and v) or "nenhuma"
    out.append(f"""<section><div class="kicker">07 · Método e parâmetros</div>
<h2>Como o número foi calculado</h2><dl>
<dt>Período</dt><dd>{date_br(r['period']['since'])} a {period.split(' a ')[1]} ({n_m} meses completos)</dd>
<dt>Repositórios</dt><dd>{', '.join('<code>' + e(x) + '</code>' for x in r['params']['repos'])}</dd>
<dt>Custo por pessoa</dt><dd>{M(r['params']['monthly_cost'])} por mês, com encargos ({e(cur)})</dd>
<dt>Horas por mês</dt><dd>{num(r['params']['hours_per_month'])}</dd>
<dt>Quem conta como time</dt><dd><code>{e(r['params']['authors'])}</code>; {num(c['developers'])} pessoas,
{num(c['developer_months'])} pessoas-mês ativas (do primeiro ao último PR de cada uma no período)</dd>
<dt>PRs</dt><dd>{num(c['prs_merged'])} mergeados; {num(c['prs_bot'])} de bots e {num(c['prs_not_team'])} de fora do time
excluídos; {num(c['prs_duplicate'])} cherry-picks, backports e sincronizações de branch excluídos (repetem trabalho
já contado); {num(c['prs_no_effective_change'])} sem mudança efetiva; {num(c['prs_scored'])} com score
{'; até ' + num(r['params']['max_prs_per_repo']) + ' PRs mais recentes por repositório' if r['params']['max_prs_per_repo'] else ''}</dd>
<dt>Score do PR</dt><dd>log2(1 + linhas efetivas) + 0,5 × log2(1 + arquivos efetivos)</dd>
<dt>Custo</dt><dd>O custo mensal de cada pessoa é dividido entre os PRs que ela mergeou no mês, pelo score. Mês sem PR
passa para o próximo mês com PR ({M(r['cost']['carried'])} carregados). Dentro do PR, o custo vai para cada área pelas
linhas (arquivo sem linhas conta 1).</dd>
<dt>Linhas excluídas</dt><dd>{pct(ln['excluded_share'], 1)} de {num(ln['total'])} linhas: {ex_txt}. Imagens e binários
contam como arquivo, sem linhas.</dd>
<dt>Áreas</dt><dd>Regras determinísticas por caminho e extensão; caminhos ambíguos classificados pelo agente
(caminho + título do PR) num arquivo de exceções. Os números vêm sempre do script.{tests_txt}</dd>
</dl></section></details>
<footer>Gerado no seu ambiente. Nenhum dado saiu dele.</footer></main></body></html>
""")
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
