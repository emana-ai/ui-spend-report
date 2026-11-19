"""Build an anonymous reference from one aggregate report per organization."""
import argparse
import json
import math
from pathlib import Path

METRICS = ("interface_share", "interface_maintenance_share")
ASSET = Path(__file__).resolve().parents[1] / "assets/benchmark.json"


def bucket(size):
    if size <= 19:
        return "até 19"
    if size <= 49:
        return "20–49"
    return "50 ou mais"


def quantile(values, fraction):
    ordered = sorted(values)
    pos = (len(ordered) - 1) * fraction
    lower = int(pos)
    return round(ordered[lower] + (ordered[math.ceil(pos)] - ordered[lower]) * (pos - lower), 6)


def aggregate(reports, sectors=None):
    points, seen = [], set()
    prs = 0
    for r in reports:
        if r["org"].casefold() in seen:
            raise ValueError("Use um único relatório por organização")
        seen.add(r["org"].casefold())
        if not r["counts"]["prs_scored"]:
            raise ValueError("Relatório sem PRs com custo")
        point = {key: r["headline"][key] for key in METRICS}
        if not r["interface"]["cost"]:
            point[METRICS[1]] = None
        ui = r["interface"]
        point["fix_share"] = ui["fix_prs"] / ui["prs"] if ui.get("prs") else None
        point.update(team_size=bucket(r["counts"]["developers"]), sector=(sectors or {}).get(r["org"], "Software"))
        points.append(point)
        prs += r["counts"]["prs_scored"]
    if not points:
        raise ValueError("Base vazia")
    points.sort(key=lambda p: (p["interface_share"], p["interface_maintenance_share"] or 0,
                               p["interface_maintenance_share"] is None, p["team_size"], p["sector"], p["fix_share"] or 0, p["fix_share"] is None))
    percentiles = {}
    for metric in (*METRICS, "fix_share"):
        values = [p[metric] for p in points if p[metric] is not None]
        percentiles[metric] = {f"p{q}": quantile(values, q / 100) for q in (25, 50, 75)} if values else {}
    return dict(points=points, prs_scored=prs, percentiles=percentiles)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, default=ASSET)
    parser.add_argument("--sectors", type=Path, help="JSON local: organização → setor")
    args = parser.parse_args()
    sectors = json.loads(args.sectors.read_text(encoding="utf-8")) if args.sectors else {}
    result = aggregate((json.loads(p.read_text(encoding="utf-8")) for p in args.reports), sectors)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
