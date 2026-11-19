"""Produce auditable candidates. Monetary priority always uses the observed period."""

import hashlib
import re
from pathlib import PurePosixPath

import defaults as D
from display import money, month, num, pct


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
    maint = ui["maintenance_cost"]
    rate = maint / total
    org = r["org"]

    def add(kind, stake, scope, numbers, formatted, caption, series, labels=()):
        key = hashlib.sha256((kind + "\0" + scope).encode()).hexdigest()[:12]
        rows.append(
            dict(
                id=f"{kind}-{key}",
                kind=kind,
                scope=scope,
                scope_label=short_scope(scope),
                money_at_stake=stake,
                money_formatted=money(stake, cur),
                numbers=numbers,
                formatted=formatted,
                caption=caption,
                series=series,
                labels=list(labels),
            )
        )

    if maint:
        fte = total / (count * r["params"]["monthly_cost"])
        upkeep = fte * rate
        formatted = [num(upkeep, 1), num(fte, 1)]
        add(
            "capacity",
            maint,
            org,
            dict(interface_fte=fte, maintenance_fte=upkeep),
            formatted,
            f"Manutenção equivale a {formatted[0]} pessoas em tempo integral · interface total: {formatted[1]}",
            [rate],
        )

    median = base["percentiles"]["interface_maintenance_share"].get("p50")
    if (
        median is not None
        and sum(p["interface_maintenance_share"] is not None for p in base["points"]) >= D.BENCH_MIN
    ):
        expected = total * median
        delta = maint - expected
        if abs(delta) * 12 / count >= 1:
            annual = (maint * 12 / count, expected * 12 / count, abs(delta) * 12 / count)
            formatted = [money(v, cur) for v in annual] + [pct(median, 1), pct(rate, 1)]
            add(
                "annual_gap",
                abs(delta),
                org,
                dict(
                    actual_annual=annual[0],
                    reference_annual=annual[1],
                    delta_annual=delta * 12 / count,
                    median=median,
                    share=rate,
                ),
                formatted,
                f"{formatted[2]}/ano {'acima' if delta > 0 else 'abaixo'} da mediana aplicada ao orçamento de interface",
                [rate, median],
            )

    repos = [(x["repo"], x["interface_cost"]) for x in r["by_repo"] if x["interface_cost"]]
    places = (
        repos
        if len(repos) > 1
        else [(x["path"].rstrip("/"), x["cost"]) for x in r["top_dirs_by_area"].get("interface", [])]
    )
    leader = r.get("maintenance_module")
    if leader and maint and leader["maintenance_cost"] / maint >= D.HOTSPOT_MIN_MAINT_COST_SHARE:
        path, amount = leader["path"], leader["maintenance_cost"]
        local = amount / leader["cost"]
        formatted = [pct(amount / maint, 1), pct(local, 1), pct(leader["cost"] / total, 1)]
        add(
            "module",
            amount,
            path,
            dict(
                maintenance_cost=amount,
                module_cost=leader["cost"],
                maintenance_concentration=amount / maint,
                local_maintenance=local,
                interface_concentration=leader["cost"] / total,
            ),
            formatted,
            f"{formatted[0]} de toda a manutenção de interface neste escopo",
            [amount / maint],
        )
    if len(places) >= 2:
        place, amount = max(places, key=lambda x: x[1])
        if amount / total > D.CONCENTRATION_MIN and not any(
            f["scope"] == place for f in rows if f["kind"] == "module"
        ):
            value = pct(amount / total, 1)
            add(
                "concentration",
                amount,
                place,
                dict(interface_share=amount / total),
                [value],
                f"{value} do investimento em interface",
                [amount / total],
            )

    for name, costs in r.get("surfaces", {}).items():
        ordered = sorted(costs, key=lambda p: (-costs[p], p))
        if len(ordered) < 2:
            continue
        extra = sum(costs[p] for p in ordered[1:])
        formatted = [money(costs[p], cur) for p in ordered]
        add(
            "platforms",
            extra,
            name,
            dict(build_cost=costs, primary=ordered[0], additional_cost=extra),
            formatted,
            " · ".join(f"{p}: {v}" for p, v in zip(ordered, formatted)),
            [costs[p] / sum(costs.values()) for p in ordered],
            ordered,
        )

        rows[-1]["platform_scopes"] = r.get("surface_scopes", {}).get(name, {})
        rows[-1]["formatted"].append(money(extra, cur))
    if len(months) >= D.TREND_MIN_MONTHS and all(m["cost"] for m in months):
        peak = max(months, key=lambda m: m["interface_cost"])
        ratio = peak["interface_cost"] * count / total
        if ratio >= D.PEAK_MIN_RATIO:
            amounts = [m["interface_cost"] for m in months]
            labels = [month(m["month"]) for m in months]
            caption = f"{month(peak['month'])} · {num(ratio, 1)} vezes o custo mensal médio de interface"
            add(
                "peak",
                peak["interface_cost"],
                org,
                dict(monthly_costs=amounts, ratio=ratio),
                [num(ratio, 1), *labels, *(money(v, cur) for v in amounts)],
                caption,
                [v / peak["interface_cost"] for v in amounts],
                labels,
            )

    trends = (
        ("trend", "interface_maintenance_share", "interface_cost", maint, "na manutenção"),
        ("interface_trend", "interface_share", "cost", total, "na participação de interface"),
    )
    for kind, metric, denominator, amount, subject in trends:
        if len(months) < D.TREND_MIN_MONTHS or not all(m[denominator] for m in months):
            continue
        values = [m[metric] for m in months]
        center = (count - 1) / 2
        mean = sum(values) / count
        xx = sum((i - center) ** 2 for i in range(count))
        slope = sum((i - center) * (v - mean) for i, v in enumerate(values)) / xx
        yy = sum((v - mean) ** 2 for v in values)
        if yy and abs(slope) >= D.TREND_MIN_SLOPE and slope * slope * xx / yy >= D.TREND_MIN_R2:
            labels = [month(m["month"]) for m in months]
            value = num(slope * 100, 1) + " p.p./mês"
            add(
                kind,
                amount,
                org,
                dict(slope=slope, monthly_shares=values),
                [value, *labels, *(pct(v, 1) for v in values)],
                f"{value} {subject} de {labels[0]} a {labels[-1]}",
                values,
                labels,
            )

    fix_median = base["percentiles"].get("fix_share", {}).get("p50")
    if (
        fix_median is not None
        and ui.get("prs", 0) >= D.FIX_MIN_PRS
        and sum(p.get("fix_share") is not None for p in base["points"]) >= D.BENCH_MIN
    ):
        share = ui["fix_prs"] / ui["prs"]
        if abs(share - fix_median) >= D.FIX_GAP:
            formatted = [pct(share, 1), pct(fix_median, 1), num(ui["fix_prs"]), num(ui["prs"])]
            add(
                "fix_share",
                ui["fix_cost"],
                org,
                dict(fix_share=share, base_median=fix_median, fixes=ui["fix_prs"], prs=ui["prs"]),
                formatted,
                f"{formatted[0]} dos PRs de interface indicam correção · mediana {formatted[1]}",
                [share, fix_median],
            )
    new = ui["new_cost"]
    if new / total >= D.WORK_MIX_MIN:
        value = pct(new / total, 1)
        add(
            "new",
            new,
            org,
            dict(new_share=new / total),
            [value],
            f"{value} do custo de interface em trabalho novo",
            [new / total],
        )
    native = ui["native_cost"]
    if native / total >= D.NATIVE_MIN:
        value = pct(native / total, 1)
        add(
            "native",
            native,
            org,
            dict(native_share=native / total),
            [value],
            f"{value} do custo em interface nativa",
            [native / total],
        )
    return sorted(rows, key=lambda f: (-f["money_at_stake"], f["id"]))


# Copy contains no inferred product semantics. The agent can replace it with scoped analysis.
COPY = {
    "peak": (
        "Mês de maior investimento",
        "As entregas desse mês elevam a média usada no orçamento de interface.",
        "Isole as entregas pontuais antes de projetar o orçamento recorrente.",
    ),
    "interface_trend": (
        "Mudança na alocação de engenharia",
        "A participação de interface no orçamento varia de forma persistente ao longo do período.",
        "Confronte a mudança mensal com o calendário de entregas de produto.",
    ),
    "annual_gap": (
        "Distância da mediana de manutenção",
        "A comparação mantém o orçamento de interface do time e varia apenas a parcela dedicada à manutenção.",
        "Use a diferença anual para dimensionar uma investigação de retrabalho.",
    ),
    "capacity": (
        "Capacidade comprometida com manutenção",
        "Correções e ajustes consomem parte da capacidade que também atende à fila de novas telas.",
        "Separe manutenção e construção na próxima previsão de capacidade.",
    ),
    "module": (
        "O módulo que concentra manutenção",
        "As correções deste módulo concentram a maior parcela do orçamento de manutenção da interface.",
        "Agrupe os ajustes por componente antes de priorizar uma intervenção.",
    ),
    "concentration": (
        "O maior destino do orçamento",
        "Este escopo reúne a maior parcela do investimento em interface.",
        "Examine os fluxos mais alterados antes de ampliar o escopo de entrega.",
    ),
    "platforms": (
        "Construção em plataformas distintas",
        "A mesma identificação de tela aparece em implementações distintas com custo de construção.",
        "Compare requisitos e componentes antes de replicar a próxima alteração.",
    ),
    "trend": (
        "Mudança de ritmo na manutenção",
        "A série mensal indica mudança persistente na composição do orçamento de interface.",
        "Confronte as entregas de cada mês com a fila de ajustes.",
    ),
    "fix_share": (
        "Correções no fluxo de entrega",
        "A frequência de PRs de correção difere da mediana observada nas empresas de referência.",
        "Separe regressões recorrentes dos ajustes pontuais na triagem.",
    ),
    "new": (
        "Construção domina o investimento",
        "A maior parte do orçamento de interface acompanha entregas classificadas como trabalho novo.",
        "Identifique componentes repetidos entre as novas entregas antes da implementação.",
    ),
    "native": (
        "Custo da implementação nativa",
        "Arquivos específicos das plataformas móveis absorvem parte mensurável do investimento em interface.",
        "Inclua as variantes nativas na estimativa de mudanças de tela.",
    ),
}


def fallback(rows):
    return [
        dict(finding_id=f["id"], **dict(zip(("title", "so_what", "action"), COPY[f["kind"]])))
        for f in rows[:5]
    ]
