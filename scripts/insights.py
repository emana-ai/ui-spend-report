"""Evidence selection. All amounts refer to the same observed period."""
import defaults as D


def findings(r, bench):
    ui = r["interface"]
    total = ui["cost"]
    if not total:
        return []
    rows = []
    def add(rule, amount, title, value, action, chart):
        rows.append(dict(rule=rule, amount=amount, title=title, value=value, action=action, chart=chart))

    repos = [(x["repo"], x["interface_cost"]) for x in r["by_repo"] if x["interface_cost"]]
    places = repos
    if len(repos) <= 1:
        places = [(x["path"], x["cost"]) for x in r["top_dirs_by_area"].get("interface", [])]
    if len(places) >= 2:
        place, amount = max(places, key=lambda item: item[1])
        if amount / total > D.CONCENTRATION_MIN:
            add("concentration", amount, place, amount / total,
                "Revise primeiro os componentes desse escopo.", [amount / total])

    maintenance = r["headline"]["interface_maintenance_share"]
    q = bench["percentiles"]["interface_maintenance_share"]
    refs = sum(p["interface_maintenance_share"] is not None for p in bench["points"])
    if refs >= D.BENCH_MIN and maintenance > q["p75"]:
        add("quartile", ui["maintenance_cost"], "Manutenção acima do quartil superior", maintenance,
            "Agrupe correções recorrentes antes de reservar capacidade para novas telas.", [maintenance, q["p75"]])

    hotspots = []
    for d in r["top_dirs_by_area"].get("interface", []):
        amount = d["cost"] * d["maintenance_share"]
        if (amount > 0 and d["maintenance_share"] >= maintenance + D.HOTSPOT_MIN_GAP
                and amount >= ui["maintenance_cost"] * D.HOTSPOT_MIN_MAINT_COST_SHARE):
            hotspots.append((amount, d["path"], d["maintenance_share"]))
    if hotspots:
        amount, path, local = max(hotspots)
        add("hotspot", amount, "Manutenção em " + path, local,
            "Investigue os ajustes recorrentes nesse diretório.", [local, maintenance])

    new_share = ui["new_cost"] / total
    if new_share >= D.WORK_MIX_MIN:
        add("new", ui["new_cost"], "Predomínio de trabalho novo", new_share,
            "Revise o reaproveitamento de componentes no próximo lote de entregas.", [new_share])

    months = r["by_month"]
    if len(months) >= D.TREND_MIN_MONTHS and all(m["cost"] for m in months):
        values = [m["interface_share"] for m in months]
        floor = min(values)
        if floor >= D.PERSISTENT_UI_MIN:
            add("floor", sum(m["cost"] for m in months) * floor,
                "Interface presente em todo o período", floor,
                "Reserve capacidade recorrente para interface no plano trimestral.", values)
        center = (len(values) - 1) / 2
        mean = sum(values) / len(values)
        xx = sum((i-center)**2 for i in range(len(values)))
        yy = sum((v-mean)**2 for v in values)
        slope = sum((i-center)*(v-mean) for i,v in enumerate(values)) / xx
        if yy and abs(slope) >= D.TREND_MIN_SLOPE and slope*slope*xx/yy >= D.TREND_MIN_R2:
            add("trend", months[-1]["interface_cost"], "Participação da interface em " + ("alta" if slope > 0 else "queda"),
                slope, "Confira a mudança de capacidade no planejamento do próximo mês.", values)
        peak = max(months, key=lambda m: m["interface_cost"])
        ratio = peak["interface_cost"] / (total / len(months))
        if ratio >= D.PEAK_MIN_RATIO:
            add("peak", peak["interface_cost"], peak["month"], ratio,
                "Revise as entregas do mês antes de usar o pico como orçamento recorrente.",
                [m["interface_cost"]/peak["interface_cost"] for m in months])

    native = ui["native_cost"]
    if native / total >= D.NATIVE_MIN:
        add("native", native, "Interface em código móvel nativo", native / total,
            "Inclua os módulos nativos na revisão do orçamento de interface.", [native/total])
    return sorted(rows, key=lambda x: (-x["amount"], x["rule"]))[:5]
