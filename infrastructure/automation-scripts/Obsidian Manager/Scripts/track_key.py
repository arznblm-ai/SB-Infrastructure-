#!/usr/bin/env python3
"""
track_key.py — ключ `track:` во frontmatter заметок (ADR-024/025).

Ключ отвечает на вопрос «к какому делу относится заметка» и заменяет догадку по прозе.
Закрытый словарь (источник истины — `context/{self} {plan} карта треков – 2026-08-19.md`):

    consulting | producer | ugsee | archive | none

Режимы:
  --apply-map <file.json>   проставить ключ по карте {"meetings/файл.md": "consulting", ...}
  --check                   валидация: у каждой заметки в meetings/ и education/ есть
                            ключ из словаря; печатает нарушения, код возврата 1 при ошибках
  --stats                   распределение по трекам

Без --apply-map режим --apply-map работает как dry-run, если добавить --dry.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

VAULT = Path("/Users/anton/AI AGENT FOLDER/Second Brain")
FOLDERS = ["meetings", "education"]
VOCAB = {"consulting", "producer", "ugsee", "archive", "none"}
SKIP = {"index.md", "README.md"}


def notes():
    for d in FOLDERS:
        for p in sorted((VAULT / d).glob("*.md")):
            if p.name not in SKIP:
                yield p


def split_fm(text: str):
    """→ (frontmatter_body_without_delims, rest_including_delims) или (None, text)."""
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---", 4)
    if end == -1:
        return None, text
    return text[4:end], text[end:]


def get_track(text: str) -> str | None:
    fm, _ = split_fm(text)
    if fm is None:
        return None
    m = re.search(r"^track:\s*(\S+)", fm, re.M)
    return m.group(1) if m else None


def set_track(text: str, value: str) -> str:
    fm, rest = split_fm(text)
    if fm is None:
        # frontmatter нет — создаём минимальный
        return f"---\ntrack: {value}\n---\n\n{text.lstrip()}"
    if re.search(r"^track:\s*\S+", fm, re.M):
        fm_new = re.sub(r"^track:\s*\S+", f"track: {value}", fm, count=1, flags=re.M)
    else:
        # после status:, иначе после date:, иначе в конец frontmatter
        for anchor in (r"^status:.*$", r"^date:.*$"):
            m = re.search(anchor, fm, re.M)
            if m:
                fm_new = fm[: m.end()] + f"\ntrack: {value}" + fm[m.end():]
                break
        else:
            fm_new = fm.rstrip("\n") + f"\ntrack: {value}\n"
    return "---\n" + fm_new + rest


def cmd_apply(map_path: Path, dry: bool) -> int:
    mapping = json.load(open(map_path, encoding="utf-8"))
    bad_vals = {v for v in mapping.values() if v not in VOCAB}
    if bad_vals:
        print("ОШИБКА: значения вне словаря:", bad_vals)
        return 1
    changed = missing = same = 0
    for rel, value in mapping.items():
        p = VAULT / rel
        if not p.exists():
            print("нет файла:", rel); missing += 1; continue
        text = p.read_text(encoding="utf-8")
        if get_track(text) == value:
            same += 1; continue
        if not dry:
            p.write_text(set_track(text, value), encoding="utf-8")
        changed += 1
    print(f"{'DRY' if dry else 'APPLIED'}: изменено {changed}, уже стояло {same}, нет файла {missing}")
    return 0


def cmd_check() -> int:
    missing, invalid, total = [], [], 0
    for p in notes():
        total += 1
        t = get_track(p.read_text(encoding="utf-8"))
        if t is None:
            missing.append(p)
        elif t not in VOCAB:
            invalid.append((p, t))
    for p in missing:
        print("  ✗ нет ключа track:", p.relative_to(VAULT))
    for p, t in invalid:
        print(f"  ✗ значение вне словаря ({t}):", p.relative_to(VAULT))
    ok = total - len(missing) - len(invalid)
    print(f"\nпроверено {total}; с корректным ключом {ok}; без ключа {len(missing)}; вне словаря {len(invalid)}")
    return 1 if (missing or invalid) else 0


def cmd_stats() -> int:
    counts: dict[str, int] = {}
    per_folder: dict[str, dict[str, int]] = {}
    for p in notes():
        t = get_track(p.read_text(encoding="utf-8")) or "—нет—"
        counts[t] = counts.get(t, 0) + 1
        f = p.parent.name
        per_folder.setdefault(f, {})[t] = per_folder.setdefault(f, {}).get(t, 0) + 1
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {v:4d}  {k}")
    print()
    for folder, c in per_folder.items():
        print(f"  {folder}: " + ", ".join(f"{k}={v}" for k, v in sorted(c.items(), key=lambda kv: -kv[1])))
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--check" in args:
        return cmd_check()
    if "--stats" in args:
        return cmd_stats()
    if "--apply-map" in args:
        return cmd_apply(Path(args[args.index("--apply-map") + 1]), dry="--dry" in args)
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
