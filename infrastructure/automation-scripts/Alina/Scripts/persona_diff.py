#!/usr/bin/env python3
"""Persona diff: ядро личности Алины в vault vs персона TG-бота.

Детерминированно, без LLM. Сравнивает секции `## …` по смыслу абзацев:
переносы строк внутри абзаца игнорируются (TG-персона hard-wrapped на 80 символов),
лишние пробелы схлопываются. Показывает: секции только в ядре, только в TG,
и абзацы, которые расходятся внутри общих секций.

Ядро правится в vault первым (ADR-022); расхождение здесь — сигнал обновить
TG-персону ранбуком (руками Антона), а не наоборот.

Usage:
  python3 persona_diff.py                      # дефолтные пути
  python3 persona_diff.py --tg ~/dev/alina/persona/alina.md
  python3 persona_diff.py --sections            # только список секций обеих сторон
Exit code 0 — совпадают по общим секциям, 1 — есть расхождения, 2 — файл не найден.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

VAULT = Path("/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Alina/persona")
DEFAULT_CORE = VAULT / "alina-core.md"
DEFAULT_TG = Path.home() / "dev/alina/persona/alina.md"

# Секции ядра, которые в TG-персоне намеренно отсутствуют или отличаются по замыслу
# (оверлеи режимов). Их не считаем расхождением.
CORE_ONLY_OK = {"Правила Антона (глобальные, накапливаются)"}
TG_ONLY_OK = {"Что ты умеешь", "Обещания - только те, которые система правда выполнит"}


def sections(text: str) -> dict[str, list[str]]:
    """Имя секции -> список нормализованных абзацев. Преамбула до первого `##` — секция ''."""
    out: dict[str, list[str]] = {}
    name = ""
    buf: list[str] = []
    paras: list[str] = []

    def flush_para() -> None:
        if buf:
            paras.append(re.sub(r"\s+", " ", " ".join(buf)).strip())
            buf.clear()

    for line in text.splitlines():
        if line.startswith("## "):
            flush_para()
            out[name] = [p for p in paras if p]
            name, paras = line[3:].strip(), []
            continue
        if line.startswith(">"):  # служебные цитаты-примечания vault не сравниваем
            continue
        if not line.strip():
            flush_para()
            continue
        if line.lstrip().startswith("- ") and buf:  # каждый пункт списка — свой абзац
            flush_para()
        buf.append(line.strip())
    flush_para()
    out[name] = [p for p in paras if p]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Diff ядра персоны Алины и TG-персоны")
    ap.add_argument("--core", type=Path, default=DEFAULT_CORE)
    ap.add_argument("--tg", type=Path, default=DEFAULT_TG)
    ap.add_argument("--sections", action="store_true", help="только перечислить секции")
    args = ap.parse_args()

    for p in (args.core, args.tg):
        if not p.exists():
            print(f"нет файла: {p}", file=sys.stderr)
            return 2

    core, tg = sections(args.core.read_text("utf-8")), sections(args.tg.read_text("utf-8"))
    if args.sections:
        print("ядро:", " | ".join(k or "(преамбула)" for k in core))
        print("TG:  ", " | ".join(k or "(преамбула)" for k in tg))
        return 0

    diverged = False
    only_core = [k for k in core if k not in tg and k not in CORE_ONLY_OK]
    only_tg = [k for k in tg if k not in core and k not in TG_ONLY_OK]
    if only_core:
        print("Секции только в ядре (нет в TG):", "; ".join(only_core))
    if only_tg:
        print("Секции только в TG (нет в ядре):", "; ".join(only_tg))

    for name in core:
        if name not in tg or name == "":
            continue
        a, b = core[name], tg[name]
        missing_in_tg = [p for p in a if p not in b]
        missing_in_core = [p for p in b if p not in a]
        if missing_in_tg or missing_in_core:
            diverged = True
            print(f"\n## {name}")
            for p in missing_in_tg:
                print(f"  ядро, нет в TG:  {p[:140]}{'…' if len(p) > 140 else ''}")
            for p in missing_in_core:
                print(f"  TG, нет в ядре:  {p[:140]}{'…' if len(p) > 140 else ''}")

    if not diverged and not only_core and not only_tg:
        print("Общие секции совпадают по абзацам.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
