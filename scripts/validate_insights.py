"""Validate local analyst copy against the current run: python3 scripts/validate_insights.py --out DIR."""

import argparse
import json
import re
import unicodedata
from pathlib import Path

LIMITS = {"headline": 28, "lede": 75, "title": 14, "evidence": 40}
NUMBER = re.compile(r"(?<!\w)(?:(?:R\$|US\$|€|£)\s*)?[+−-]?\d+(?:[.,]\d+)*(?:%|\s+(?:mil|mi|p\.p\.))?(?!\w)")
LOW = r"(?:baixa|reduzida|inferior|abaixo)"
HIGH = r"(?:alta|elevada|superior|acima)"
# The report diagnoses; it never prescribes. Imperatives and advice phrases, matched without accents.
TERM = re.compile(r"\bengenharia\b|\besforcos?\b", re.I)
PRESCRIPTION = re.compile(
    r"\b(?:defina|revise|teste|testem|priorize|considere|recomendamos|recomenda-se|recomendo|sugerimos|sugere-se|"
    r"sugiro|deveria|deveriam|devemos|e preciso|e necessario|vale a pena|convem|reduza|invista|adote|crie|implemente|"
    r"migre|mova|desconte|comece|separe|confira|procure|identifique|compare|reserve|inclua|trate|evite|garanta|"
    r"foque|aplique|elimine|unifique|padronize|automatize|documente|acompanhe|monitore|avalie|corrija|refatore)\b"
)


def plain(text):
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c))


def allowed(section, findings):
    """Numbers a section's copy may cite: its facts and the rendered fields of its findings."""
    texts = [*section["facts"].values(), *section.get("base", [])]
    for fid in section["findings"]:
        f = findings[fid]
        texts += [*f["formatted"], f["money_formatted"], f["caption"], f["quanto"], f["evidence"]]
    return set(NUMBER.findall(" ".join(texts)))


def validate(doc, copy):
    """copy = {"headline", "lede", "sections": {id: {"title", "evidence": [1–2 sentences]}}}, one entry per section
    with facts. Every number comes from findings.json; no advice; a base comparison is stated once."""
    sections = {s["id"]: s for s in doc["sections"] if s["facts"]}
    if not isinstance(copy, dict) or set(copy) != {"headline", "lede", "sections"}:
        raise ValueError("Campos de insight inválidos: use headline, lede e sections")
    if not isinstance(copy["sections"], dict) or set(copy["sections"]) != set(sections):
        raise ValueError("Seções esperadas: " + ", ".join(sections))
    findings = {f["id"]: f for f in doc["findings"]}
    per = {k: allowed(s, findings) for k, s in sections.items()}
    everything = set().union(*per.values())
    fields = [("headline", copy["headline"], everything), ("lede", copy["lede"], everything)]
    for k, s in copy["sections"].items():
        if not isinstance(s, dict) or set(s) != {"title", "evidence"} or not isinstance(s["evidence"], list) \
                or not 1 <= len(s["evidence"]) <= 2:
            raise ValueError(f"{k}: use title e evidence (uma ou duas frases)")
        fields += [(k + ".title", s["title"], per[k])] + [(k + ".evidence", t, per[k]) for t in s["evidence"]]
    plain_facts = set(NUMBER.findall(" ".join(v for s in sections.values() for v in s["facts"].values()
                                              if v not in s.get("base", []))))
    base = set(NUMBER.findall(" ".join(sections.get("comparacao", {}).get("base", [])))) - plain_facts
    said = []
    median, share = doc["maintenance_median"], doc["maintenance_share"]
    for name, text, ok in fields:
        kind = name.split(".")[-1]
        if not isinstance(text, str) or not 1 <= len(text.split()) <= LIMITS[kind]:
            raise ValueError(f"{name}: limite de {LIMITS[kind]} palavras")
        numbers = set(NUMBER.findall(text))
        if numbers - ok:
            raise ValueError(f"{name}: número sem evidência vinculada ({', '.join(sorted(numbers - ok))})")
        if kind == "headline" and len(NUMBER.findall(text)) < 2:
            raise ValueError("headline: o achado principal com pelo menos dois números")
        if kind == "title" and not numbers:
            raise ValueError(f"{name}: título precisa de um número da evidência")
        if TERM.search(plain(text)):  # owner 20/11: the measure is "custo de desenvolvimento"
            raise ValueError(f"{name}: use «custo de desenvolvimento», não «esforço» nem «engenharia»")
        word = PRESCRIPTION.search(plain(text))
        if word:
            raise ValueError(f"{name}: o relatório diagnostica, não recomenda («{word[0]}»)")
        if median is not None and share is not None:
            wrong = f"(?:{LOW}|{HIGH})" if share == median else LOW if share > median else HIGH
            if re.search(rf"\bmanutenção\s+(?:(?:é|está|ficou|muito|mais|do|time|de|interface|foi|permanece)\s+)*{wrong}\b"
                         rf"|\b{wrong}\s+manutenção\b", text, re.I):
                raise ValueError(f"{name}: qualificação de manutenção contradiz a mediana")
        said += sorted(numbers & base)
    if len(said) != len(set(said)):
        raise ValueError("Comparação com a base repetida: cada posição ou mediana aparece uma vez")
    return copy


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
