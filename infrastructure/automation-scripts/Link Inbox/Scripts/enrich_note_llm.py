#!/usr/bin/env python3
"""LLM enrichment engine for external-resource notes.

Turns a `pending` auto note into a `done` note by running the analysis (the
"разбор"): краткое содержание, суть, полезные ссылки, инструменты, инсайты,
готовые решения, оценка для Антона, Strategic Board.

Uses headless `claude -p` (the user's Claude Code auth — no separate API key).
Same engine is used by:
  - the bot pipeline (auto-enrich on save), and
  - batch backlog runs (`--all-pending`).

Usage:
  enrich_note_llm.py --path "<note>.md"
  enrich_note_llm.py --all-pending [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from external_resource_note import bullet_block, enrich_note, load_note, _links_block
from link_inbox_common import EXTERNAL_TRANSCRIPTS_DIR

# Resolve the claude CLI by absolute path so it works under a LaunchAgent
# whose PATH may not include ~/.local/bin.
CLAUDE_BIN = (
    shutil.which("claude")
    or next((p for p in (
        os.path.expanduser("~/.local/bin/claude"),
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
    ) if os.path.exists(p)), "claude")
)
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
SKIP = {"index.md", "readme.md"}
MAX_TRANSCRIPT = 6000

PROMPT = """Ты аналитик базы знаний (Second Brain Антона). Разбери короткое видео по его транскрипту и описанию.

Транскрипт может быть искажён авто-распознаванием (особенно русский) — делай выводы по смыслу, НЕ выдумывай фактов, которых нет.

КРИТИЧНО ПРО ССЫЛКИ: НЕ придумывай и НЕ угадывай URL по названию бренда/инструмента. В "links" клади ТОЛЬКО те URL, которые ДОСЛОВНО написаны в транскрипте или описании. Если явных URL нет — верни links: []. Названия инструментов всё равно перечисли в "tools".

ВЕРНИ СТРОГО ОДИН JSON-объект и больше ничего (без markdown, без пояснений), со схемой:
{{
  "summary": ["2-4 кратких пункта по-русски, о чём видео"],
  "essence": "1-2 предложения по-русски: главная мысль",
  "links": [{{"label": "Название", "url": "https://..."}}],   // ТОЛЬКО URL дословно из текста/описания; [] если нет
  "tools": ["названия инструментов/продуктов/сервисов, упомянутых в видео"],
  "insights": ["2-5 пунктов по-русски: неочевидные идеи, механика, приёмы"],
  "solutions": ["1-3 пункта по-русски: что Антон может применить (workflow/промпт/идея)"],
  "anton_relevance": "1 предложение по-русски: насколько и чем полезно Антону (UGC/контент/AI/бизнес)",
  "strategic": "по-русски: решение/риск/следующий шаг, ЕСЛИ есть бизнес/продуктовая значимость; иначе ровно 'not applicable'"
}}

НАЗВАНИЕ: {title}
ПЛАТФОРМА: {platform}
ОПИСАНИЕ (caption): {caption}

ТРАНСКРИПТ:
{transcript}
"""


def extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start : end + 1])


def call_claude(prompt: str, model: str) -> dict:
    res = subprocess.run(
        [CLAUDE_BIN, "-p", prompt, "--output-format", "json", "--model", model],
        capture_output=True, text=True, timeout=180,
    )
    if res.returncode != 0:
        raise RuntimeError(f"claude -p failed ({res.returncode}): {res.stderr[-300:]}")
    envelope = json.loads(res.stdout)
    if envelope.get("is_error"):
        raise RuntimeError(f"claude -p error: {envelope.get('result', '')[:300]}")
    return extract_json(envelope["result"])


def section(note: dict, h: str) -> str:
    return note["sections"].get(h, "")


def enrich_one(path: Path, model: str, force: bool = False) -> str:
    note = load_note(path)
    fm = note["frontmatter"]
    if not force and "enrichment: done" in fm:
        return "skip-done"
    title = note.get("title") or path.stem
    platform = (re.search(r'platform:\s*"?([^"\n]+)', fm) or [None, "web"])[1]
    transcript = section(note, "Транскрипт")[:MAX_TRANSCRIPT]
    caption = section(note, "Raw caption / metadata")[:1500]
    if len(transcript.strip()) < 40:
        return "skip-no-transcript"

    data = call_claude(
        PROMPT.format(title=title, platform=platform, caption=caption or "—", transcript=transcript),
        model,
    )

    links = [f"{l.get('label','link')}={l.get('url','')}" for l in (data.get("links") or []) if l.get("url")]
    updates = {
        "Краткое содержание": bullet_block(data.get("summary") or [], "—"),
        "Суть": (data.get("essence") or "").strip() or "—",
        "Полезные ссылки": _links_block(links) if links else "- not found",
        "Упомянутые инструменты и skills": bullet_block(data.get("tools") or [], "—"),
        "Главные инсайты": bullet_block(data.get("insights") or [], "—"),
        "Готовые решения / как применить": bullet_block(data.get("solutions") or [], "—"),
        "Оценка применимости для Антона": (data.get("anton_relevance") or "").strip() or "—",
        "Strategic Board analysis": (data.get("strategic") or "not applicable").strip(),
    }
    enrich_note(path, updates, fm_tools=data.get("tools") or [],
                fm_links=[l.get("url") for l in (data.get("links") or []) if l.get("url")])
    return "done"


def iter_pending():
    for p in sorted(EXTERNAL_TRANSCRIPTS_DIR.rglob("*.md")):
        if p.name.lower() in SKIP:
            continue
        head = p.read_text(encoding="utf-8", errors="replace")[:600]
        if "type: external-resource" in head and "enrichment: done" not in head:
            yield p


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM-enrich external-resource notes.")
    ap.add_argument("--path")
    ap.add_argument("--all-pending", action="store_true")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    targets = []
    if args.path:
        targets = [Path(args.path).expanduser()]
    elif args.all_pending:
        targets = list(iter_pending())[: args.limit]
    else:
        ap.error("pass --path or --all-pending")

    ok = fail = 0
    for p in targets:
        try:
            status = enrich_one(p, args.model, args.force)
            print(f"[{status}] {p.name}")
            ok += status == "done"
        except Exception as exc:
            fail += 1
            print(f"[FAIL] {p.name}: {exc}")
    print(f"\nenriched={ok} failed={fail} total={len(targets)}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
