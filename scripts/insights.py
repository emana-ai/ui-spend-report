"""Produce auditable candidates. Monetary priority always uses the observed period."""

import hashlib
import re
from pathlib import PurePosixPath

import defaults as D
from display import money, month, num, pct, rank_text, span


def surface_key(repo, path):
    """Match named screens under explicit platform roots, never the platform name alone."""
    parts = (repo + "/" + path).lower().split("/")
    platforms = {
        p for part in parts[:-1] for p in re.split("[-_.]", part) if p in {"android", "ios", "web", "desktop"}
    }
    if len(platforms) != 1:
        return None
    platform = platforms.pop()
    family = "-".join(t for t in re.split("[-_.]", repo.lower()) if t != platform)
    name = ""
    for i, part in enumerate(parts[:-1]):
        if part in {"screens", "pages"}:
            name = PurePosixPath(parts[i + 1]).stem
            break
    if not name:
        # Named UI entry points also occur directly inside platform modules.
        match = re.fullmatch(
            r"(.+?)(screen|page|viewcontroller|activity)\.(swift|kt|java|tsx|jsx|vue)", parts[-1]
        )
        if not match:
            return None
        name = match[1]
    name = re.sub(r"[-_]?(?:screen|page|viewcontroller|activity)$", "", name)
    generic = {"index", "main", "home", "app", "src", "components", "shared", "common", "api"}
    if re.fullmatch("[a-z][a-z0-9_-]+", name) and name not in generic:
        return (family or "produto") + "/" + name, platform
    return None


def short_scope(scope):
    repo, *dirs = scope.strip("/").split("/")
    meaningful = [d for d in dirs if d not in {"src", "main", "lib", "packages", "apps"}]
    return repo + (" · " + "/".join((meaningful or dirs)[-2:]) if dirs else "")


def findings(r, base):
    rows = []
    ui = r["interface"]
    total = ui["cost"]
    if not total:
        return rows
    months = r["by_month"]
    count = len(months)
    cur = r["params"]["currency"]
    monthly_cost = r["params"]["monthly_cost"]
    measured = r["headline"].get("interface_maintenance_share", 0) is not None  # small samples: no maintenance findings
    maint = ui["maintenance_cost"] if measured else 0
    org = r["org"]
    labels = [month(m["month"]) for m in months]
    scopes = r.get("scopes", {})
    counts = r.get("counts", {})
    title_based = counts.get("prs_typed_label", 0) < 0.5 * counts.get("prs_scored", 0)
    title_limit = "Manutenção identificada pelo título dos PRs: refatoração sem palavra de correção conta como trabalho novo."

    def rank(metric, value):
        values = sorted(p[metric] for p in base["points"] if p.get(metric) is not None)
        text = rank_text(values, value)
        return text[0].lower() + text[1:] if len(values) >= D.BENCH_MIN else None

    def position(metric, value, wording):
        median = base["percentiles"].get(metric, {}).get("p50")
        if median is None or not rank(metric, value):
            return {}
        return dict(comparison=f"{wording}: {rank(metric, value)} (mediana {pct(median, 1)})",
                    base_formatted=[pct(median, 1)])

    def add(kind, stake, scope, numbers, formatted, caption, key, span=None, extra=None):
        stats = scopes.get(key, dict(prs=0, files=0, by_month=[], top=[]))
        digest = hashlib.sha256((kind + "\0" + scope).encode()).hexdigest()[:12]
        fte = stake / ((span or count) * monthly_cost)
        row = dict(
            id=f"{kind}-{digest}",
            kind=kind,
            scope=scope,
            scope_label=short_scope(scope),
            money_at_stake=stake,
            money_formatted=money(stake, cur),
            numbers=numbers,
            formatted=formatted,
            caption=caption,
            quanto=f"{money(stake, cur)} {'no mês' if span == 1 else 'no período'} · {num(fte, 1)} "
            + ("pessoa equivalente" if round(fte, 1) == 1 else "pessoas equivalentes"),
            onde=[short_scope(m) for m, _ in stats["top"]],
            quando=dict(labels=labels, values=stats["by_month"]),
            evidence=f"{num(stats['prs'])} {'PR' if stats['prs'] == 1 else 'PRs'} · "
            f"{num(stats['files'])} {'arquivo' if stats['files'] == 1 else 'arquivos'}",
        )
        if stats["prs"] < D.MAINT_MIN_UI_PRS:
            row["limit"] = f"Amostra pequena: {row['evidence']}."
        row.update(extra or {})
        rows.append(row)

    if maint:
        fte = total / (count * monthly_cost)
        rate = maint / total
        formatted = [num(fte * rate, 1), num(fte, 1)]
        add(
            "capacity",
            maint,
            org,
            dict(interface_fte=fte, maintenance_fte=fte * rate),
            formatted,
            f"Manutenção equivale a {formatted[0]} pessoas em tempo integral · interface total: {formatted[1]}",
            "maintenance",
            extra=dict(position("interface_maintenance_share", rate, "Fatia de manutenção"),
                       **({"limit": title_limit} if title_based else {})),
        )

    repos = [("repo:" + x["repo"], x["repo"], x["interface_cost"]) for x in r["by_repo"] if x["interface_cost"]]
    if repos:
        key, place, amount = max(repos, key=lambda x: x[2])
        modules = scopes.get("modules:" + place)
        if modules:  # one repository holds most of the interface: its top modules, never "repository X"
            key, amount = "modules:" + place, modules["cost"]
        if (modules or len(repos) > 1) and amount / total > D.CONCENTRATION_MIN:
            value = pct(amount / total, 1)
            where = f" em {len(modules['top'])} módulos de {place}" if modules else ""
            add("concentration", amount, place, dict(interface_share=amount / total), [value],
                f"{value} do investimento em interface{where}", key)

    for name, costs in r.get("surfaces", {}).items():
        ordered = sorted(costs, key=lambda p: (-costs[p], p))
        if len(ordered) < 2 or scopes.get("surface:" + name, {}).get("prs", 0) < D.MAINT_MIN_UI_PRS:
            continue  # owner rule: nothing published on fewer than 30 interface PRs
        extra = sum(costs[p] for p in ordered[1:])
        formatted = [money(costs[p], cur) for p in ordered] + [money(extra, cur)]
        add(
            "platforms",
            extra,
            name,
            dict(build_cost=costs, primary=ordered[0], additional_cost=extra),
            formatted,
            " · ".join(f"{p}: {v}" for p, v in zip(ordered, formatted)),
            "surface:" + name,
            extra=dict(platform_scopes=r.get("surface_scopes", {}).get(name, {}),
                       limit="Telas pareadas pelo nome no caminho; a equivalência vem dos títulos dos PRs."),
        )

    if len(months) >= D.TREND_MIN_MONTHS and all(m["cost"] for m in months):
        peak = max(months, key=lambda m: m["interface_cost"])
        ratio = peak["interface_cost"] * count / total
        if ratio >= D.PEAK_MIN_RATIO:
            amounts = [m["interface_cost"] for m in months]
            add(
                "peak",
                peak["interface_cost"],
                org,
                dict(monthly_costs=amounts, ratio=ratio),
                [num(ratio, 1), *labels, *(money(v, cur) for v in amounts)],
                f"{month(peak['month'])} · {num(ratio, 1)} vezes o custo mensal médio de interface",
                "month:" + peak["month"],
                span=1,
            )
            rows[-1]["quando"] = dict(labels=labels, values=amounts)

    if len(months) >= D.TREND_MIN_MONTHS and all(m["cost"] for m in months):
        values = [m["interface_share"] for m in months]
        center = (count - 1) / 2
        mean = sum(values) / count
        xx = sum((i - center) ** 2 for i in range(count))
        slope = sum((i - center) * (v - mean) for i, v in enumerate(values)) / xx
        yy = sum((v - mean) ** 2 for v in values)
        if yy and abs(slope) >= D.TREND_MIN_SLOPE and slope * slope * xx / yy >= D.TREND_MIN_R2:
            value = num(slope * 100, 1) + " p.p./mês"
            add(
                "interface_trend",
                total,
                org,
                dict(slope=slope, monthly_shares=values),
                [value, *labels, *(pct(v, 1) for v in values)],
                f"{value} na participação de interface de {labels[0]} a {labels[-1]}",
                "interface",
            )
            rows[-1]["quando"] = dict(labels=labels, values=values)

    fix_median = base["percentiles"].get("fix_share", {}).get("p50")
    if measured and fix_median is not None and ui.get("prs", 0) >= D.FIX_MIN_PRS:
        share = ui["fix_prs"] / ui["prs"]
        comparison = position("fix_share", share, "Frequência de correções")
        if comparison and abs(share - fix_median) >= D.FIX_GAP:
            formatted = [pct(share, 1), num(ui["fix_prs"]), num(ui["prs"])]
            add(
                "fix_share",
                ui["fix_cost"],
                org,
                dict(fix_share=share, base_median=fix_median, fixes=ui["fix_prs"], prs=ui["prs"]),
                formatted,
                f"{formatted[0]} dos PRs de interface indicam correção ({formatted[1]} de {formatted[2]})",
                "fix",
                extra=dict(comparison, limit="Correção = fix ou bug no título ou rótulo; o dado não mede a gravidade.",
                           comparison_fix=f"Frequência de correções: {formatted[0]} dos PRs · {rank('fix_share', share)}"),
            )

    native = ui["native_cost"]
    if native / total >= D.NATIVE_MIN:
        value = pct(native / total, 1)
        add("native", native, org, dict(native_share=native / total), [value], f"{value} do custo em interface nativa",
            "native")
    # Maintenance time and fix frequency on the same modules: one card, the fix frequency as a comparison row.
    kinds = {f["kind"]: f for f in rows}
    cap, fix = kinds.get("capacity"), kinds.get("fix_share")
    if cap and fix and len(set(cap["onde"]) & set(fix["onde"])) >= 2:
        rows.remove(fix)
        cap["comparison_fix"] = fix["comparison_fix"]
        cap["numbers"]["fix_share"] = fix["numbers"]["fix_share"]
        cap["formatted"].append(fix["formatted"][0])
    for f in rows:
        if f["kind"] == "fix_share":
            f.pop("comparison_fix")  # its own comparison line already says it
    return sorted(rows, key=lambda f: (-f["money_at_stake"], f["id"]))


# The article: sections in order, and which findings each one carries.
SECTIONS = (
    ("custo", "Quanto custa a interface", ("capacity", "platforms", "native")),
    ("manutencao", "Onde a manutenção se concentra", ("concentration", "fix_share")),
    ("tempo", "Como evolui no tempo", ("peak", "interface_trend")),
    ("retrabalho", "Retrabalho", ()),
    ("comparacao", "Como vocês se comparam", ()),
)
BASE_METRICS = (
    ("interface_share", "Custo de desenvolvimento em interface"),
    ("interface_maintenance_share", "Manutenção da interface"),
    ("rework_share", f"Retrabalho em {D.REWORK_DAYS} dias"),
    ("fix_share", "Frequência de correções"),
)


def client_metrics(r):
    """The team's value for each base metric; None where the sample does not publish it."""
    h, ui, rw = r["headline"], r["interface"], r.get("rework") or {}
    measured = h.get("interface_maintenance_share") is not None and bool(ui["cost"])
    return {"interface_share": h["interface_share"] if ui["cost"] else None,
            "interface_maintenance_share": h["interface_maintenance_share"] if measured else None,
            "rework_share": rw.get("share"),
            "fix_share": ui["fix_prs"] / ui["prs"] if measured and ui.get("prs") else None}


def comparisons(r, base):
    """Per base metric: sorted base values, the team's value, its position and the median."""
    rows = []
    for key, label in BASE_METRICS:
        values = sorted(p[key] for p in base["points"] if p.get(key) is not None)
        value = client_metrics(r)[key]
        median = base["percentiles"].get(key, {}).get("p50")
        if value is None or median is None or len(values) < D.BENCH_MIN:
            continue
        text = rank_text(values, value)
        rows.append(dict(key=key, label=label, values=values, value=value, median=median,
                         rank=text[0].lower() + text[1:]))
    return rows


def facts(r, base):
    """Formatted numbers per section: the only numbers the headline, lede and section copy may cite."""
    out = {k: {} for k, _, _ in SECTIONS}
    h, ui, months = r["headline"], r["interface"], r["period"]["months"]
    if not ui["cost"]:
        return out, []
    cur, n, mc = r["params"]["currency"], len(months), r["params"]["monthly_cost"]
    c = out["custo"]
    c.update(inicio=month(months[0]), fim=month(months[-1]), fatia=pct(h["interface_share"]),
             mensal=money(h["interface_monthly_cost"], cur), periodo=money(ui["cost"], cur),
             pessoas=num(ui["fte"], 1), prs=num(ui["prs"]), equipe_mensal=money(r["cost"]["team_monthly"], cur))
    if h["interface_maintenance_share"] is not None:
        c.update(manutencao=pct(h["interface_maintenance_share"]), manutencao_mensal=money(ui["maintenance_cost"] / n, cur),
                 manutencao_ano=money(h["interface_maintenance_annual_cost"], cur),
                 manutencao_pessoas=num(ui["maintenance_cost"] / n / mc, 1))
    if r["platforms"]["shown"]:
        c["plataforma"] = pct(r["platforms"]["shown"][0]["share"])
    hs = r.get("hotspots")
    if hs:
        out["manutencao"].update(arquivos=num(hs["top_files"]), fatia_arquivos=pct(hs["top_file_share"], 1),
                                 custo=pct(hs["top_maintenance_share"]), arquivos_tela=num(hs["files"]))
        for i, m in enumerate(hs["modules"], 1):
            out["manutencao"].update({f"modulo{i}": pct(m["share"], 1), f"modulo{i}_custo": money(m["maintenance_cost"], cur)})
    t = out["tempo"]
    peak = max(r["by_month"], key=lambda m: m["interface_cost"])
    t.update(pico=money(peak["interface_cost"], cur), pico_mes=month(peak["month"]),
             **{m["month"]: money(m["interface_cost"], cur) for m in r["by_month"]})
    tr = r.get("trend")
    if tr:
        t.update(primeira=pct(tr["first"]), segunda=pct(tr["second"]), primeira_meses=span(tr["first_months"]),
                 segunda_meses=span(tr["second_months"]))
    rw = r.get("rework")
    if rw and rw["share"] is not None:
        out["retrabalho"].update(fatia=pct(rw["share"], 1), dias=str(D.REWORK_DAYS), seguinte=money(rw["followup_cost"], cur),
                                 prs_seguinte=num(rw["followup_prs"]), prs_novos=num(rw["new_prs"]),
                                 prs_com_manutencao=num(rw["reworked_prs"]), custo=money(rw["reworked_cost"], cur),
                                 custo_novo=money(rw["new_cost"], cur))
    rows = comparisons(r, base)
    cmp_base = []
    if rows:
        out["comparacao"].update(empresas=num(len(base["points"])), prs_base=num(base["prs_scored"]))
        for x in rows:
            out["comparacao"].update({x["key"]: pct(x["value"], 1), x["key"] + "_posicao": x["rank"],
                                      x["key"] + "_mediana": pct(x["median"], 1)})
            cmp_base += [x["rank"], pct(x["median"], 1)]
    return out, cmp_base


def sections(r, base, rows):
    """findings.json sections: id, eyebrow, the formatted facts and the findings each one carries."""
    found, cmp_base = facts(r, base)
    return [dict(id=k, eyebrow=label, facts=found[k], findings=[f["id"] for f in rows if f["kind"] in kinds],
                 **({"base": cmp_base} if k == "comparacao" else {}))
            for k, label, kinds in SECTIONS]


def fallback(doc):
    """Deterministic headline, lede and section copy from the facts alone. No advice, no causes."""
    f = {s["id"]: s["facts"] for s in doc["sections"]}
    c, m, t, rw, cmp = (f[k] for k in ("custo", "manutencao", "tempo", "retrabalho", "comparacao"))
    if not c:
        return dict(headline="O período não teve PRs com custo atribuído.", lede="", sections={})
    measured = "manutencao" in c
    headline = (f"A interface ficou com {c['fatia']} do custo de desenvolvimento, e {c['manutencao']} desse custo foi "
                "manutenção do que já existia." if measured else
                f"A interface ficou com {c['fatia']} do custo de desenvolvimento, {c['mensal']} por mês.")
    lede = [f"Entre {c['inicio']} e {c['fim']}, {doc['org']} gastou {c['periodo']} em interface, o equivalente a "
            f"{c['pessoas']} pessoas em tempo integral."]
    if measured:
        lede.append(f"A manutenção custou {c['manutencao_mensal']} por mês.")
    if m:
        lede.append(f"{m['arquivos']} {'arquivo concentra' if m['arquivos'] == '1' else 'arquivos concentram'} "
                    f"{m['custo']} do custo de manutenção.")
    if rw:
        lede.append(f"{rw['fatia']} do custo de trabalho novo foi em arquivos que receberam manutenção em até {rw['dias']} dias.")
    out = {}
    out["custo"] = dict(
        title=(f"Manutenção ocupa {c['manutencao_pessoas']} das {c['pessoas']} pessoas equivalentes da interface"
               if measured else f"A interface ocupa {c['pessoas']} pessoas equivalentes"),
        evidence=[f"A interface custou {c['periodo']} no período, {c['fatia']} do custo de desenvolvimento atribuído aos PRs."]
        + ([f"Manutenção foi {c['manutencao']} desse custo, {c['manutencao_ano']} por ano no ritmo atual."] if measured else []))
    if m:
        out["manutencao"] = dict(
            title=f"{m['arquivos']} {'arquivo concentra' if m['arquivos'] == '1' else 'arquivos concentram'} "
                  f"{m['custo']} do custo de manutenção",
            evidence=[f"São {m['fatia_arquivos']} dos {m['arquivos_tela']} arquivos de tela que mudaram no período."])
    if "primeira" in t:
        out["tempo"] = dict(
            title=(f"A fatia de manutenção passou de {t['primeira']} para {t['segunda']}" if t["primeira"] != t["segunda"]
                   else f"A fatia de manutenção ficou em {t['primeira']}"),
            evidence=[f"O mês mais caro foi {t['pico_mes']}, com {t['pico']} em interface."])
    elif t:
        out["tempo"] = dict(title=f"O custo mensal de interface chegou a {t['pico']} em {t['pico_mes']}",
                            evidence=["A divisão entre trabalho novo e manutenção não é publicada nesta amostra."])
    if rw:
        out["retrabalho"] = dict(
            title=f"{rw['fatia']} do trabalho novo em interface recebeu manutenção em até {rw['dias']} dias",
            evidence=[f"Essa manutenção seguinte custou {rw['seguinte']}, em {rw['prs_seguinte']} PRs."])
    if cmp:
        key = "interface_maintenance_share" if "interface_maintenance_share" in cmp else "interface_share"
        name = "fatia de manutenção" if key != "interface_share" else "fatia de interface"
        out["comparacao"] = dict(
            title=f"A {name} de {doc['org']} fica {cmp[key + '_posicao']}",
            evidence=[f"A base reúne {cmp['empresas']} empresas de software open source e {cmp['prs_base']} PRs com custo atribuído."])
    return dict(headline=headline, lede=" ".join(lede), sections=out)
