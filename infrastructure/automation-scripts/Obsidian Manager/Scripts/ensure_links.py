#!/usr/bin/env python3
"""
ensure_links.py — правило «у каждой заметки есть хотя бы одна [[ссылка]]».

Сиротные заметки (без единого `[[...]]`) получают минимальную ссылку —
заголовок-дату `### [[YYYY-MM-DD]]` (идея: заметка всегда привязана к дню,
когда мысль записана; так видно, как она развивается через года).

Дата берётся по приоритету:
  1. дата в имени файла  `… – 2026-08-19.md`
  2. frontmatter `date:` / `created:`
  3. дата создания файла (st_birthtime на macOS, иначе mtime)

Куда вставляется: после frontmatter; если первая содержательная строка — H1,
то после H1. Идемпотентно: заметки с любой [[ссылкой]] не трогаются.

Использование:
  python3 ensure_links.py            # dry-run: что будет изменено
  python3 ensure_links.py --apply    # применить
  python3 ensure_links.py --apply --only transcripts sessions   # только эти папки
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

VAULT = Path("/Users/anton/AI AGENT FOLDER/Second Brain")

# Папки, которые не трогаем никогда.
EXCLUDE_DIRS = {
    ".obsidian",
    ".claude",
    ".stversions",
    ".git",
    "node_modules",
    "__pycache__",
    "medical docs",
}
# Архивы (_archive/, вложенные archive/) НЕ исключаем: Obsidian их показывает в графе,
# значит правило «≥1 ссылка» действует и там (уточнение Антона 2026-08-19).
# Приватная зона психолога Sage — не читаем и не пишем (решение Антона 2026-07-16).
EXCLUDE_PATHS = {VAULT / "context" / "psychology"}

WIKILINK_RE = re.compile(r"\[\[[^\]]+\]\]")
DATE_IN_NAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
FM_DATE_RE = re.compile(r"^(?:date|created)[_a-z]*:\s*[\"']?(\d{4}-\d{2}-\d{2})", re.M | re.I)


def is_excluded(path: Path) -> bool:
    rel_parts = path.relative_to(VAULT).parts
    if any(part in EXCLUDE_DIRS for part in rel_parts[:-1]):
        return True
    return any(path.is_relative_to(p) for p in EXCLUDE_PATHS)


def iter_notes(only: list[str] | None):
    roots = [VAULT / o for o in only] if only else [VAULT]
    for root in roots:
        for path in root.rglob("*.md"):
            if path.is_symlink() or is_excluded(path):
                continue
            yield path


def split_frontmatter(text: str) -> tuple[str, str]:
    """Возвращает (frontmatter_block_with_delims_and_trailing_newline, body)."""
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            nl = text.find("\n", end + 1)
            cut = len(text) if nl == -1 else nl + 1
            return text[:cut], text[cut:]
    return "", text


def pick_date(path: Path, frontmatter: str) -> str:
    m = DATE_IN_NAME_RE.search(path.name)
    if m:
        try:
            date.fromisoformat(m.group(1))
            return m.group(1)
        except ValueError:
            pass
    m = FM_DATE_RE.search(frontmatter)
    if m:
        return m.group(1)
    st = path.stat()
    ts = getattr(st, "st_birthtime", None) or st.st_mtime
    return datetime.fromtimestamp(ts).date().isoformat()


def inject(text: str, day: str) -> str:
    fm, body = split_frontmatter(text)
    link_line = f"### [[{day}]]\n"
    lines = body.split("\n")
    # первая непустая строка
    idx = next((i for i, l in enumerate(lines) if l.strip()), None)
    if idx is not None and lines[idx].startswith("# "):
        # после H1
        head = "\n".join(lines[: idx + 1])
        tail = "\n".join(lines[idx + 1 :])
        return f"{fm}{head}\n\n{link_line}{tail if tail.startswith(chr(10)) else chr(10) + tail}"
    # перед первой содержательной строкой
    body = body.lstrip("\n")
    sep = "\n" if fm else ""
    return f"{fm}{sep}{link_line}\n{body}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="записать изменения (по умолчанию dry-run)")
    ap.add_argument("--only", nargs="*", help="ограничить папками верхнего уровня")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    changed, scanned, skipped = 0, 0, 0
    per_dir: dict[str, int] = {}
    for path in iter_notes(args.only):
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            skipped += 1
            continue
        if WIKILINK_RE.search(text):
            continue
        day = pick_date(path, split_frontmatter(text)[0])
        new_text = inject(text, day)
        top = path.relative_to(VAULT).parts[0]
        per_dir[top] = per_dir.get(top, 0) + 1
        changed += 1
        if not args.quiet:
            print(f"{'APPLY ' if args.apply else 'DRY   '}[[{day}]]  {path.relative_to(VAULT)}")
        if args.apply:
            path.write_text(new_text, encoding="utf-8")

    print(f"\nscanned={scanned} orphans={'fixed' if args.apply else 'found'}={changed} unreadable={skipped}")
    for k, v in sorted(per_dir.items(), key=lambda kv: -kv[1]):
        print(f"  {v:5d}  {k}")
    if not args.apply and changed:
        print("\n(dry-run; добавь --apply чтобы записать)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
