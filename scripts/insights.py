"""Produce auditable candidates. Monetary priority always uses the observed period."""

import hashlib
import re
from pathlib import PurePosixPath

import defaults as D
from display import money, month, num, pct, rank_text


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


# Each title states the measured result; each sentence says what the number is made of. No advice.
COPY = {
    "capacity": (
        "Manutenção ocupa o tempo de {value} pessoas",
        "Esse é o tempo integral equivalente gasto em ajustes e correções de telas que já existiam.",
    ),
    "concentration": (
        "{value} do investimento em interface fica aqui",
        "Essa fatia das mudanças de tela do período passou pelos módulos listados.",
    ),
    "platforms": (
        "Outras plataformas custaram {stake}",
        "A mesma tela foi construída em mais de uma plataforma no período.",
    ),
    "peak": (
        "O pico chegou a {stake}",
        "Nesse mês o custo de interface ficou bem acima da média dos outros meses.",
    ),
    "interface_trend": (
        "Interface varia {value}",
        "A fatia do esforço de engenharia dedicada às telas mudou de forma contínua no período.",
    ),
    "fix_share": (
        "Correções representam {value} dos PRs",
        "Os títulos e rótulos indicam correção nessa fração dos PRs que mexeram em interface.",
    ),
    "native": (
        "Interface nativa recebe {value} do orçamento",
        "Parte do custo das telas vem de código específico de iOS, Android ou multiplataforma.",
    ),
}


def fallback(rows):
    cards = []
    for f in rows[:5]:
        title, diagnosis = COPY[f["kind"]]
        cards.append(dict(finding_id=f["id"], title=title.format(value=f["formatted"][0], stake=f["money_formatted"]),
                          diagnosis=diagnosis))
    return cards
