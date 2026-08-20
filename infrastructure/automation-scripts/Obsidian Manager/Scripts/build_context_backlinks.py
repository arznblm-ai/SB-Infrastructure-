#!/usr/bin/env python3
"""
build_context_backlinks.py — секция «Встречи и материалы по проекту» в файлах `context/`.

Каждый бизнес/личный контекст в `context/` получает автогенерируемый блок со списком
summary из `meetings/` и `education/`, которые ссылаются на него в своей секции «Связи».
Так контекст проекта становится хабом (проект → все встречи), а не тупиком.

Блок ограничен маркерами и перезаписывается целиком — ручной текст файла не трогается:

    <!-- backlinks:auto:start -->
    ## N.0 Встречи и материалы по проекту
    ...
    <!-- backlinks:auto:end -->

Использование:
  python3 build_context_backlinks.py           # dry-run
  python3 build_context_backlinks.py --apply
"""

from __future__ import annotations

import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

VAULT = Path("/Users/anton/AI AGENT FOLDER/Second Brain")
CONTEXT = VAULT / "context"
SOURCES = ["meetings", "education"]
START = "<!-- backlinks:auto:start -->"
END = "<!-- backlinks:auto:end -->"
MAX_ROWS = 40  # длинные хвосты не тащим в контекст — берём свежие

N = lambda s: unicodedata.normalize("NFC", s)
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def thesis_of(text: str) -> str:
    m = re.search(r"\*\*Thesis:\*\*\s*(.+)", text)
    if m:
        return m.group(1).strip().rstrip(".")[:150]
    # frontmatter — не источник thesis: берём первую содержательную строку тела
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        text = text[end + 4 :] if end != -1 else text
    for line in text.split("\n"):
        s = line.strip()
        if not s or s.startswith(("#", "-", ">", "|", "---", "<!--", "**")):
            continue
        if re.match(r"^[a-zA-Zа-яА-Я_]+:\s", s):  # хвост yaml-подобных строк
            continue
        return s[:150]
    return ""


def main() -> int:
    apply = "--apply" in sys.argv

    # Источник привязки — ключ `track:` во frontmatter (ADR-025), а не проза «Связей».
    # Карта строится из самих контекст-файлов: у каждого хаба свой `track:`.
    track_to_ctx: dict[str, list[Path]] = {}
    for cpath in sorted(CONTEXT.glob("*.md")):
        if cpath.name in ("index.md", "CLAUDE.md", "README.md"):
            continue
        tm = re.search(r"^track:\s*(\S+)", cpath.read_text(encoding="utf-8"), re.M)
        if tm:
            track_to_ctx.setdefault(tm.group(1), []).append(cpath)

    # backlinks: context stem -> list of (date, source stem, thesis)
    back: dict[str, list[tuple[str, str, str]]] = {}
    for folder in SOURCES:
        for path in sorted((VAULT / folder).glob("*.md")):
            if path.name in ("index.md", "README.md"):
                continue
            text = path.read_text(encoding="utf-8")
            tm = re.search(r"^track:\s*(\S+)", text, re.M)
            if not tm or tm.group(1) in ("none", "archive"):
                continue
            hubs = track_to_ctx.get(tm.group(1), [])
            if len(hubs) != 1:
                print(f"  ! трек {tm.group(1)}: контекст-файлов {len(hubs)}, пропускаю {path.name[:40]}")
                continue
            th = thesis_of(text)
            dm = DATE_RE.search(path.name)
            day = dm.group(1) if dm else ""
            back.setdefault(N(hubs[0].stem), []).append((day, path.stem, th))

    changed = 0
    for cpath in sorted(CONTEXT.glob("*.md")):
        if cpath.name in ("index.md", "CLAUDE.md", "README.md"):
            continue
        rows = sorted(set(back.get(N(cpath.stem), [])), reverse=True)
        text = cpath.read_text(encoding="utf-8")
        if not rows:
            # нет входящих — если блок был, убираем (проект мог быть переименован)
            if START in text:
                new = re.sub(re.escape(START) + r".*?" + re.escape(END) + r"\n?", "", text, flags=re.S)
                changed += 1
                print(f"{'APPLY' if apply else 'DRY  '} remove block: {cpath.name}")
                if apply:
                    cpath.write_text(new.rstrip() + "\n", encoding="utf-8")
            continue

        stripped = re.sub(re.escape(START) + r".*?" + re.escape(END) + r"\n?", "", text, flags=re.S).rstrip()
        nums = [int(x) for x in re.findall(r"^## (\d+)\.0 ", stripped, re.M)]
        n = (max(nums) + 1) if nums else 1
        shown, extra = rows[:MAX_ROWS], max(0, len(rows) - MAX_ROWS)
        lines = [
            START,
            f"## {n}.0 Встречи и материалы по проекту",
            "",
            f"> Автогенерация Obsidian Manager (`build_context_backlinks.py`), {date.today().isoformat()}. "
            f"Источник — секции «Связи» в `meetings/` и `education/`. Не редактировать вручную.",
            "",
            "| Дата | Заметка | О чём |",
            "|------|---------|-------|",
        ]
        for day, stem, th in shown:
            lines.append(f"| {day or '—'} | [[{stem}]] | {th or '—'} |")
        if extra:
            lines.append(f"| … | _ещё {extra} заметок ссылаются на этот контекст_ | — |")
        lines += ["", END, ""]

        new = stripped + "\n\n" + "\n".join(lines)
        if new != text:
            changed += 1
            print(f"{'APPLY' if apply else 'DRY  '} {cpath.name}: {len(rows)} backlinks")
            if apply:
                cpath.write_text(new, encoding="utf-8")

    print(f"\n{'APPLIED' if apply else 'DRY-RUN'}: files changed={changed}")
    if not apply and changed:
        print("(добавь --apply чтобы записать)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
