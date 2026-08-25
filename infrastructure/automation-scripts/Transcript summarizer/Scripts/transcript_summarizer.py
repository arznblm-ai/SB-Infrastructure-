#!/usr/bin/env python3
"""
transcript_summarizer.py
Ежедневно создаёт summary для новых транскриптов из vault/transcripts/
→ education/ или meetings/

Запускается LaunchAgent: com.user.transcript-summarizer (ежедневно в 09:00)
Лог: ~/Library/Logs/transcript-summarizer.log
"""

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# ── Пути ──────────────────────────────────────────────────────────────────
VAULT         = Path("/Users/anton/AI AGENT FOLDER/Second Brain")
TRANSCRIPTS   = VAULT / "transcripts"
EDUCATION     = VAULT / "education"
MEETINGS      = VAULT / "meetings"
SKILL_PATH    = VAULT / "system/skills/transcript-summarizer/SKILL.md"
LOG_PATH      = Path.home() / "Library/Logs/transcript-summarizer.log"

NOTE_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
VALID_TRANSCRIPT_PREFIXES = ("{meeting}", "{course}", "{self}")
PENDING_MARKERS = (
    "Транскрипт загружается из Krisp",
    "Транскрипт не получен",
    "Транскрипт не получен. Откройте Krisp",
)

# ── Логирование ───────────────────────────────────────────────────────────
def log(msg: str):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# ── Поиск новых транскриптов ──────────────────────────────────────────────
def extract_date(name: str) -> Optional[str]:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", name)
    return m.group(1) if m else None


def transcript_stem(path: Path) -> str:
    stem = path.stem
    stem = stem.replace("{course} {transcript} ", "")
    stem = stem.replace("{meeting} {transcript} ", "")
    stem = stem.replace("{self} {transcript} ", "")
    stem = re.sub(r"\s+[–-]\s+\d{4}-\d{2}-\d{2}$", "", stem)
    return stem.strip().lower()


def sync_note_timestamp(path: Path) -> None:
    date_str = extract_date(path.name)
    if not date_str:
        return
    timestamp = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=12, minute=0, second=0, microsecond=0
    ).timestamp()
    os.utime(path, (timestamp, timestamp))

def find_existing_summary(path: Path) -> Optional[Path]:
    """Ищет summary для конкретного transcript по source-link или точному stem+date."""
    date = extract_date(path.name)
    if not date:
        return None
    source_markers = [path.name, path.stem]
    wanted_stem = transcript_stem(path)
    for folder in [EDUCATION, MEETINGS]:
        for f in folder.glob("*.md"):
            if "{summary}" not in f.name or date not in f.name:
                continue
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                content = ""
            if any(marker in content for marker in source_markers):
                return f
            if transcript_stem(f) == wanted_stem:
                return f
    return None


def summary_exists(path: Path) -> bool:
    return find_existing_summary(path) is not None

def is_transcript_ready(path: Path) -> bool:
    content = path.read_text(encoding="utf-8", errors="replace")
    if path.name in {"README.md", "index.md"}:
        return False
    if not path.name.startswith(VALID_TRANSCRIPT_PREFIXES):
        return False
    if not content.strip():
        return False
    if any(marker in content for marker in PENDING_MARKERS):
        return False
    return bool(re.search(r"(?im)^#+\s*(Transcript|Транскрипт)\s*$", content))


def find_new_transcripts() -> List[Path]:
    skipped_empty = []
    new = []
    for path in sorted(TRANSCRIPTS.glob("*.md")):
        if not is_transcript_ready(path):
            skipped_empty.append(path.name)
            continue
        if summary_exists(path):
            continue
        new.append(path)
    if skipped_empty:
        log(f"Пропущены пустые/загружающиеся: {', '.join(skipped_empty)}")
    return new

# ── Локальное summary без внешних моделей ──────────────────────────────────
def frontmatter_value(content: str, field: str) -> Optional[str]:
    match = re.search(rf"(?m)^{re.escape(field)}:\s*(.+)$", content)
    if not match:
        return None
    return match.group(1).strip().strip('"').strip("'") or None


def note_title(content: str, fallback: str) -> str:
    match = re.search(r"(?m)^#\s+(.+)$", content)
    return match.group(1).strip() if match else fallback


def strip_note_prefixes(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"^\{(meeting|course|self)\}\s+", "", stem)
    stem = re.sub(r"^\{transcript\}\s+", "", stem)
    stem = stem.replace("{transcript} ", "")
    stem = re.sub(r"\s+[–-]\s+\d{4}-\d{2}-\d{2}$", "", stem)
    return re.sub(r"\s+", " ", stem).strip()


def clean_filename_part(text: str, max_len: int = 70) -> str:
    text = re.sub(r'[<>:"/\\|?*\n\r\t]', " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0].strip()


def extract_section_items(content: str, headers: list[str]) -> list[str]:
    header_pattern = "|".join(re.escape(h) for h in headers)
    match = re.search(
        rf"(?ims)^#+\s*(?:{header_pattern})\s*\n(.*?)(?=^#+\s|\Z)",
        content,
    )
    if not match:
        return []
    block = match.group(1).strip()
    items = []
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("-", "*")):
            item = line.lstrip("-* ").strip()
            if item:
                items.append(item)
    if items:
        return items
    return [p.strip() for p in re.split(r"\n{2,}", block) if p.strip()]


def transcript_excerpt(content: str, limit: int = 5) -> list[str]:
    match = re.search(r"(?ims)^#+\s*(?:Transcript|Транскрипт)\s*\n(.*)", content)
    if not match:
        return []
    lines = []
    for raw in match.group(1).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("[Download Recording]"):
            continue
        if line.startswith("**") or len(line) > 30:
            lines.append(line)
        if len(lines) >= limit:
            break
    return lines


def classify_folder(path: Path, content: str, preferred_folder: Optional[str]) -> str:
    if preferred_folder:
        return preferred_folder
    name = path.name.lower()
    if "{course}" in name:
        return "education"
    if "{meeting}" in name:
        return "meetings"
    speakers = len(set(re.findall(r"\*\*([^|*\n]+)\s*\|", content)))
    return "education" if speakers <= 1 and len(content.splitlines()) > 300 else "meetings"


def summary_filename(path: Path, title: str, folder_name: str) -> str:
    date = extract_date(path.name) or datetime.now().strftime("%Y-%m-%d")
    prefix = "{course}" if folder_name == "education" else "{meeting}"
    topic = clean_filename_part(title if title else strip_note_prefixes(path.name))
    return f"{prefix} {{summary}} {topic} – {date}.md"


def infer_track(content: str, title: str) -> str:
    """Детерминированная эвристика трека (ADR-025): consulting|producer|ugsee|none.

    Считает совпадения ключевых слов в тексте+заголовке (регистронезависимо);
    при конфликте побеждает больший счёт, при равенстве — фиксированный
    приоритет consulting > ugsee > producer.
    """
    text = f"{title}\n{content}".lower()

    consulting_keywords = [
        "vitalik", "виталик", "макс пономар", "friends", "иинизация", "консалтинг",
    ]
    ugsee_keywords = ["ugsee", "минаев", "ugc media", "creative hub"]
    producer_keywords = ["portal", "смет", "тендер", "раскадровк", "продакшн"]

    scores = {
        "consulting": sum(text.count(kw) for kw in consulting_keywords),
        "ugsee": sum(text.count(kw) for kw in ugsee_keywords),
        "producer": sum(text.count(kw) for kw in producer_keywords),
    }
    # «пилот» считается сигналом консалтинга только рядом с агентской темой
    if "пилот" in text and "агентств" in text:
        scores["consulting"] += 1

    # Порядок перебора задаёт приоритет при равном счёте
    for track in ("consulting", "ugsee", "producer"):
        if scores[track] > 0 and scores[track] == max(scores.values()):
            return track
    return "none"


def build_local_summary(path: Path, content: str, preferred_folder: Optional[str]) -> tuple[str, str, str]:
    folder_name = classify_folder(path, content, preferred_folder)
    date = frontmatter_value(content, "date") or extract_date(path.name) or datetime.now().strftime("%Y-%m-%d")
    source = path.name
    title = note_title(content, strip_note_prefixes(path.name))
    filename = summary_filename(path, title, folder_name)
    key_points = extract_section_items(content, ["Ключевые моменты", "Key Points"])
    action_items = extract_section_items(content, ["Action Items", "Задачи"])
    excerpts = transcript_excerpt(content)
    mcp_id = frontmatter_value(content, "krisp_mcp_id") or frontmatter_value(content, "krisp_id")
    call_id = frontmatter_value(content, "krisp_call_id")
    project_tag = "project/course" if folder_name == "education" else "project/meeting"
    topic_tag = "topic/transcript"
    thesis_source = key_points[0] if key_points else f"Summary создано локально из транскрипта «{title}»."

    lines = [
        "---",
        "tags:",
        "  - type/summary",
        f"  - {project_tag}",
        f"  - {topic_tag}",
        "  - status/active",
        f"date: {date}",
        "status: active",
        f"track: {infer_track(content, title)}",
        f"source: \"[[{Path(source).stem}]]\"",
        "generated_by: local-transcript-summarizer",
        "---",
        "",
        f"# {title}",
        "",
        f"> **Thesis:** {thesis_source}",
        "",
    ]

    if key_points:
        overview = " ".join(key_points[:3])
    else:
        overview = "Summary создано без внешней модели: использованы метаданные, доступные key points/action items и первые содержательные реплики транскрипта."
    lines.extend([
        f"**Overview:** {overview}",
        "",
        "---",
        "",
        "## 1.0 Structure — ключевые факты",
        "",
    ])

    if key_points:
        for idx, item in enumerate(key_points, start=1):
            lines.extend([f"### 1.{idx} Факт {idx}", item, ""])
    else:
        lines.extend([
            "### 1.1 Доступный контекст",
            "В транскрипте не найден блок key points, поэтому summary ограничено extractive-выжимкой из доступного текста.",
            "",
        ])

    lines.extend(["## 2.0 Process — ход обсуждения", ""])
    if excerpts:
        for item in excerpts:
            lines.append(f"- {item}")
    else:
        lines.append("- Содержательные реплики не извлечены автоматически.")
    lines.append("")

    lines.extend(["## 3.0 Action Items", ""])
    if action_items:
        for item in action_items:
            normalized = item
            if not normalized.startswith("["):
                normalized = f"[ ] {normalized}"
            lines.append(f"- {normalized}")
    else:
        lines.append("- [ ] Action items не найдены в транскрипте.")
    lines.append("")

    lines.extend([
        "## 4.0 Ground Truth",
        "",
        f"- **Источник:** `transcripts/{source}`",
        f"- **Дата:** {date}",
        f"- **Объём транскрипта:** {len(content.splitlines())} строк",
    ])
    if call_id:
        lines.append(f"- **Krisp Call ID:** `{call_id}`")
    if mcp_id:
        lines.append(f"- **Krisp MCP ID:** `{mcp_id}`")
    lines.append("")

    return folder_name, filename, "\n".join(lines)


def process(path: Path, preferred_folder: Optional[str] = None) -> Optional[Path]:
    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    log(f"  Обрабатываю: {path.name} ({len(lines)} строк)")

    folder_name, summary_name, summary_body = build_local_summary(path, content, preferred_folder)

    target_dir = EDUCATION if folder_name == "education" else MEETINGS
    target = target_dir / summary_name

    if target.exists():
        log(f"  ПРОПУЩЕН (уже существует): {summary_name}")
        return target

    target.write_text(summary_body + "\n", encoding="utf-8")
    sync_note_timestamp(target)
    log(f"  ✅ {folder_name}/{summary_name}")
    return target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create structured summaries for transcript files in Second Brain."
    )
    parser.add_argument(
        "transcript_paths",
        nargs="*",
        help="Optional explicit transcript paths to process. Default: scan new files in transcripts/.",
    )
    parser.add_argument(
        "--preferred-folder",
        choices=["education", "meetings"],
        help="Soft hint for the target summary folder when processing explicit transcripts.",
    )
    return parser.parse_args()


def resolve_requested_transcripts(raw_paths: List[str]) -> List[Path]:
    resolved = []
    for raw in raw_paths:
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            log(f"ПРОПУЩЕН: файл не найден: {path}")
            continue
        if path.suffix.lower() != ".md":
            log(f"ПРОПУЩЕН: не markdown-файл: {path}")
            continue
        resolved.append(path)
    return resolved

# ── Главная функция ───────────────────────────────────────────────────────
def main():
    log("=" * 60)
    log("Запуск transcript-summarizer")

    if not SKILL_PATH.exists():
        log(f"ОШИБКА: SKILL.md не найден: {SKILL_PATH}")
        sys.exit(1)

    args = parse_args()
    if args.transcript_paths:
        transcripts = resolve_requested_transcripts(args.transcript_paths)
        targets = []
        for path in transcripts:
            if not is_transcript_ready(path):
                log(f"ПРОПУЩЕН: пустой или ещё загружается: {path.name}")
                continue
            existing = find_existing_summary(path)
            if existing:
                log(f"ПРОПУЩЕН: summary уже существует: {existing.name}")
                print(f"SUMMARY_PATH={existing}")
                continue
            targets.append(path)
        log(f"Явно запрошенных транскриптов для обработки: {len(targets)}")
    else:
        targets = find_new_transcripts()
        log(f"Новых транскриптов для обработки: {len(targets)}")

    if not targets:
        log("Нечего делать. Завершение.")
        log("=" * 60)
        return

    created, failed = 0, 0
    for t in targets:
        created_path = process(t, args.preferred_folder)
        if created_path:
            created += 1
            print(f"SUMMARY_PATH={created_path}")
        else:
            failed += 1

    log(f"Готово. Создано: {created}, ошибок: {failed}")
    log("=" * 60)

if __name__ == "__main__":
    main()
