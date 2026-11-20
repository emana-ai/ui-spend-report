"""Validate local analyst copy against the current run: python3 scripts/validate_insights.py --out DIR."""

import argparse
import json
import re
import unicodedata
from pathlib import Path

LIMITS = {"title": 8, "diagnosis": 22}
NUMBER = re.compile(r"(?<!\w)(?:(?:R\$|US\$|€|£)\s*)?[+−-]?\d+(?:[.,]\d+)*(?:%|\s+(?:mil|mi|p\.p\.))?(?!\w)")
LOW = r"(?:baixa|reduzida|inferior|abaixo)"
HIGH = r"(?:alta|elevada|superior|acima)"
# The report diagnoses; it never prescribes. Imperatives and advice phrases, matched without accents.
PRESCRIPTION = re.compile(
    r"\b(?:defina|revise|teste|testem|priorize|considere|recomendamos|recomenda-se|recomendo|sugerimos|sugere-se|"
    r"sugiro|deveria|deveriam|devemos|e preciso|e necessario|vale a pena|convem|reduza|invista|adote|crie|implemente|"
    r"migre|mova|desconte|comece|separe|confira|procure|identifique|compare|reserve|inclua|trate|evite|garanta|"
    r"foque|aplique|elimine|unifique|padronize|automatize|documente|acompanhe|monitore|avalie|corrija|refatore)\b"
)


def plain(text):
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c))


def validate(doc, selection):
    candidates = {f["id"]: f for f in doc["findings"]}
    if not isinstance(selection, list) or not min(3, len(candidates)) <= len(selection) <= 5:
        raise ValueError("Selecione de três a cinco evidências, ou todas se houver menos de três.")
    previous = float("inf")
    seen = set()
    for card in selection:
        if not isinstance(card, dict) or set(card) != {"finding_id", *LIMITS}:
            raise ValueError("Campos de insight inválidos: use finding_id, title e diagnosis")
        fid = card["finding_id"]
        if not isinstance(fid, str) or fid not in candidates:
            raise ValueError("finding_id desconhecido")
        f = candidates[fid]
        pair = (f["scope"], f["kind"])
        if pair in seen or f["money_at_stake"] > previous:
            raise ValueError("Escopo e tipo repetidos ou ordem monetária incorreta")
        seen.add(pair)
        previous = f["money_at_stake"]
        allowed = set(NUMBER.findall(" ".join([*f["formatted"], *f.get("base_formatted", []), f["money_formatted"], f["caption"], f["quanto"]])))
        for field, limit in LIMITS.items():
            text = card[field]
            if not isinstance(text, str) or not 1 <= len(text.split()) <= limit:
                raise ValueError(f"{field}: limite de {limit} palavras")
            if set(NUMBER.findall(text)) - allowed:
                raise ValueError(f"{field}: número sem evidência vinculada")
            word = PRESCRIPTION.search(plain(text))
            if word:
                raise ValueError(f"{field}: o relatório diagnostica, não recomenda («{word[0]}»)")
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
            if median is not None and doc["maintenance_share"] is not None:
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
        # A comparison with the base is stated once per card: the rendered line, or one agent field.
        fields = [card["title"], card["diagnosis"], f.get("comparison", "")]
        base = set(f.get("base_formatted", []))
        if base and sum(bool(base & set(NUMBER.findall(text))) for text in fields) > 1:
            raise ValueError("Comparação com a base repetida no mesmo cartão")
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
