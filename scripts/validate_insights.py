"""Validate local analyst copy against the current run: python3 scripts/validate_insights.py --out DIR."""

import argparse
import json
import re
from pathlib import Path

LIMITS = {"title": 8, "so_what": 22, "action": 16}
NUMBER = re.compile(r"(?<!\w)(?:(?:R\$|US\$|€|£)\s*)?[+−-]?\d+(?:[.,]\d+)*(?:%|\s+(?:mil|mi|p\.p\.))?(?!\w)")
LOW = r"(?:baixa|reduzida|inferior|abaixo)"
HIGH = r"(?:alta|elevada|superior|acima)"


def validate(doc, selection):
    candidates = {f["id"]: f for f in doc["findings"]}
    if not isinstance(selection, list) or not min(3, len(candidates)) <= len(selection) <= 5:
        raise ValueError("Selecione de três a cinco evidências, ou todas se houver menos de três.")
    previous = float("inf")
    seen = set()
    comparisons = {}
    for card in selection:
        if not isinstance(card, dict) or set(card) != {"finding_id", *LIMITS}:
            raise ValueError("Campos de insight inválidos")
        fid = card["finding_id"]
        if not isinstance(fid, str) or fid not in candidates:
            raise ValueError("finding_id desconhecido")
        f = candidates[fid]
        direction = f["direction"]
        family = f["metric_family"]
        if direction != "neutral":
            previous_direction = comparisons.get(family)
            if previous_direction and (previous_direction != direction or direction == "mixed"):
                raise ValueError("Comparações opostas da mesma família exigem um achado conjunto")
            comparisons[family] = direction
        if direction == "better" and card["action"] != "":
            raise ValueError("Ponto forte deve ter ação vazia")
        pair = (f["scope"], f["kind"])
        if pair in seen or f["money_at_stake"] > previous:
            raise ValueError("Escopo e tipo repetidos ou ordem monetária incorreta")
        seen.add(pair)
        previous = f["money_at_stake"]
        allowed = set(NUMBER.findall(" ".join([*f["formatted"], f["money_formatted"], f["caption"]])))
        for field, limit in LIMITS.items():
            text = card[field]
            if field == "action" and text == "" and direction in {"better", "mixed"}:
                continue
            if not isinstance(text, str) or not 1 <= len(text.split()) <= limit:
                raise ValueError(f"{field}: limite de {limit} palavras")
            if set(NUMBER.findall(text)) - allowed:
                raise ValueError(f"{field}: número sem evidência vinculada")
            if field == "title" and not NUMBER.search(text):
                names = f["scope"].strip("/").split("/")
                if not any(
                    re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.I)
                    for name in names
                    if name not in {"src", "packages", "lib", "apps", "main"}
                ):
                    raise ValueError("Título precisa de um número da evidência ou do nome do escopo")
            if field == "title" and "/" in text and "p.p./mês" not in text:
                raise ValueError("Título deve descrever o achado, sem caminhos")
            median = doc["maintenance_median"]
            if median is not None:
                if doc["maintenance_share"] == median:
                    wrong = f"(?:{LOW}|{HIGH})"
                else:
                    wrong = LOW if doc["maintenance_share"] > median else HIGH
                if re.search(
                    rf"\bmanutenção\s+(?:(?:é|está|ficou|muito|mais|do|time|de|interface|foi|permanece)\s+)*{wrong}\b|\b{wrong}\s+manutenção\b",
                    text,
                    re.I,
                ):
                    raise ValueError("Qualificação de manutenção contradiz a mediana")
    return selection


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    folder = parser.parse_args().out
    try:
        validate(
            *(
                json.loads((folder / f"{name}.json").read_text(encoding="utf-8"))
                for name in ("findings", "insights")
            )
        )
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print("Insights válidos.")
