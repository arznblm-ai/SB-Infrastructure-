#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import urlparse

from link_inbox_common import canonicalize_url, load_config, load_state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a Telegram digest for Link Inbox records.")
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--config", default=None)
    return parser.parse_args()


def read_text(path: str | None, max_chars: int = 18000) -> str:
    if not path:
        return ""
    p = Path(path).expanduser()
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")[:max_chars]


def compact(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def section(text: str, heading: str) -> str:
    pattern = rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, text, flags=re.M | re.S)
    return match.group(1).strip() if match else ""


def bullets(text: str, limit: int = 3) -> list[str]:
    result = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("- "):
            result.append(compact(line[2:]))
        if len(result) >= limit:
            break
    return result


def source_fields(brief: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw in section(brief, "Source").splitlines():
        line = raw.strip()
        if not line.startswith("- ") or ":" not in line:
            continue
        key, value = line[2:].split(":", 1)
        fields[key.strip().lower()] = compact(value)
    return fields


def transcript_excerpt(text: str, max_chars: int = 520) -> str:
    body = section(text, "Transcript") or section(text, "Транскрипт")
    if not body:
        body = text
    body = re.sub(r"\*\*\[[^\]]+\]\*\*", "", body)
    return compact(body)[:max_chars].rstrip()


def transcript_lines(text: str) -> list[str]:
    body = section(text, "Transcript") or section(text, "Транскрипт")
    lines: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"^\*\*\[[^\]]+\]\*\*\s*", "", line)
        line = compact(line)
        if line:
            lines.append(line)
    return lines


def normalize_tool_name(name: str) -> str:
    cleaned = compact(name).strip(" .:-")
    aliases = {
        "aesternity ui": "Aceternity UI",
        "aceternity ui": "Aceternity UI",
        "mobin": "Mobbin",
        "godly": "Godly",
        "u-a-i": "UI",
    }
    return aliases.get(cleaned.lower(), cleaned)


def split_tool_line(line: str) -> tuple[str, str]:
    line = compact(line).strip(" .:-")
    match = re.match(r"^(.{2,60}?)\s+(is|pulls|takes|has|gives|turns)\s+(.+)$", line, flags=re.I)
    if not match:
        return normalize_tool_name(line), ""
    name = normalize_tool_name(match.group(1))
    desc = f"{match.group(1)} {match.group(2)} {match.group(3)}"
    return name, desc


def clean_tool_desc(desc: str) -> str:
    desc = compact(desc)
    desc = re.sub(r"\b(?:[A-Z]-){3,}[A-Z]\b\.?", "", desc)
    desc = desc.replace("U-A-I", "UI")
    desc = desc.replace("Aesternity", "Aceternity")
    desc = desc.replace("Mobin", "Mobbin")
    desc = re.sub(r"If you liked this video, follow for more\.?", "", desc, flags=re.I)
    desc = re.sub(r"\s+-State\b", "", desc)
    return compact(desc).rstrip(".")


def russian_tool_desc(name: str, desc: str) -> str:
    key = name.lower()
    if key == "aceternity ui":
        return "анимированные React-компоненты, чтобы быстро собрать интерфейс, который выглядит дорого и production-ready."
    if key in {"referral styles", "refero styles", "refero"}:
        return "дизайн-DNA сильных сайтов, упакованная как reference/input для AI."
    if key == "mobbin":
        return "каталог экранов популярных приложений; можно найти нужный UX-паттерн и отдать его AI как референс."
    if key == "godly":
        return "галерея сильных сайтов для насмотренности и быстрых визуальных референсов перед сборкой."
    if key in {"10x", "10 x"}:
        return "инструмент, который обещает превратить app idea в native iOS app / reusable UI code / assets за минуты."
    return desc[:170].rstrip()


def extract_numbered_tools(lines: list[str], limit: int = 7) -> list[tuple[str, str]]:
    tools: list[tuple[str, str]] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        number_match = re.fullmatch(r"(?:and\s+)?(?:number\s+)?(?:#\s*)?([1-9]|one|two|three|four|five)[.)]?", line, flags=re.I)
        inline_match = re.search(r"\b(?:number\s+)?(?:one|two|three|four|five|[1-9])\s*[,.:]\s*(.+)$", line, flags=re.I)

        if number_match and idx + 1 < len(lines):
            name, first_desc = split_tool_line(lines[idx + 1])
            desc_start = idx + 2
        elif inline_match:
            name, first_desc = split_tool_line(inline_match.group(1))
            desc_start = idx + 1
        else:
            idx += 1
            continue

        desc_parts = [first_desc] if first_desc else []
        cursor = desc_start
        while cursor < len(lines):
            candidate = lines[cursor]
            if re.fullmatch(r"(?:and\s+)?(?:number\s+)?(?:#\s*)?([1-9]|one|two|three|four|five)[.)]?", candidate, flags=re.I):
                break
            if re.search(r"\b(?:number\s+)?(?:one|two|three|four|five|[1-9])\s*[,.:]\s*.+$", candidate, flags=re.I):
                break
            desc_parts.append(candidate)
            if len(" ".join(desc_parts)) > 220:
                break
            cursor += 1

        desc = clean_tool_desc(" ".join(desc_parts))
        if name and not any(existing.lower() == name.lower() for existing, _ in tools):
            tools.append((name, desc))
        idx = max(cursor, idx + 1)
        if len(tools) >= limit:
            break
    return tools


def extract_urls(*texts: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for raw in re.findall(r"https?://[^\s)>\]]+", text or ""):
            url = canonicalize_url(raw.rstrip(".,;:"))
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def build_about(tools: list[tuple[str, str]], transcript_items: list[str], description: str) -> str:
    lowered = " ".join(transcript_items[:6]).lower()
    if len(tools) >= 3 and ("top 5" in lowered or "websites" in lowered):
        return (
            "Подборка 5 ресурсов для vibe coding: готовые UI-компоненты, дизайн-референсы, "
            "каталоги экранов приложений, галереи сильных сайтов и инструмент, который обещает собрать iOS app из идеи."
        )
    if transcript_items:
        return transcript_excerpt_from_lines(transcript_items, max_chars=360)
    return description or "Содержание пока не извлечено."


def transcript_excerpt_from_lines(lines: list[str], max_chars: int = 360) -> str:
    return compact(" ".join(lines))[:max_chars].rstrip()


def human_title(record: dict, meta: dict[str, str]) -> str:
    creator = meta.get("creator") or meta.get("channel")
    platform = meta.get("platform") or record.get("kind", "link")
    if creator:
        return f"{platform}: {creator}"
    title = record.get("title") or meta.get("title") or record.get("url") or "Saved link"
    title = re.sub(r"^\{self\}\s+\{transcript\}\s+UGC\s+", "", title)
    title = re.sub(r"\s+–\s+\d{4}-\d{2}-\d{2}$", "", title)
    return title


def source_host(url: str | None) -> str:
    if not url:
        return ""
    host = urlparse(url).netloc.replace("www.", "")
    return host or url


def short_path(path: str | None) -> str:
    if not path:
        return ""
    return path.replace("/Users/anton/AI AGENT FOLDER/Second Brain/", "")


def build_record_digest(record: dict) -> str:
    status = record.get("status", "unknown")
    kind = record.get("kind", "web")
    brief = read_text(record.get("brief_path"))
    transcript = read_text(record.get("transcript_path"))
    meta = source_fields(brief)
    lines = [
        f"Сохранил: {human_title(record, meta)}",
        "",
    ]

    transcript_items = transcript_lines(transcript)
    tools = extract_numbered_tools(transcript_items)
    if brief:
        core = section(brief, "Core Read")
        core_bullets = bullets(core, limit=4)
    elif record.get("excerpt"):
        core_bullets = [compact(str(record["excerpt"]))[:420]]
    else:
        core_bullets = []

    title = meta.get("title") or record.get("title") or ""
    description = meta.get("description") or ""
    about = build_about(tools, transcript_items, description)

    lines.append("О чём видео:")
    lines.append(f"- {about[:620].rstrip()}")

    if tools:
        lines.extend(["", "Инструменты / ресурсы:"])
        for name, desc in tools[:6]:
            readable_desc = russian_tool_desc(name, desc)
            if readable_desc:
                lines.append(f"- {name}: {readable_desc}")
            else:
                lines.append(f"- {name}")
    elif title or description:
        lines.extend(["", "Что видно из source metadata:"])
        if title:
            lines.append(f"- Title: {title}")
        if description:
            lines.append(f"- Caption: {description}")

    if tools:
        lines.extend(["", "Система автора:"])
        lines.append("- Берёт готовые UI-компоненты, дизайн-референсы, экраны приложений и галереи сильных сайтов как input для AI.")
        if any(name.lower() in {"10x", "10 x"} for name, _ in tools):
            lines.append("- Финальный слой: сервис, который превращает идею приложения в native iOS app / reusable UI code за минуты.")
        lines.append("- Польза: меньше начинать с нуля, быстрее доводить идею до визуально убедительного прототипа.")
    elif status == "failed" and record.get("error"):
        lines.extend(["", "Ошибка:", f"- {record['error']}"])

    urls = extract_urls(brief, record.get("message_text", ""))
    source_url = canonicalize_url(record.get("url", "")) if record.get("url") else ""
    if source_url and source_url not in urls:
        urls.insert(0, source_url)
    if urls:
        lines.extend(["", "Ссылки:"])
        for url in urls[:5]:
            label = "source" if url == source_url else source_host(url)
            lines.append(f"- {label}: {url}")

    lines.extend(["", "Сохранено:"])
    lines.append("- index: transcripts/external resources/index.md")
    if record.get("summary_path"):
        lines.append(f"- summary: {short_path(record['summary_path'])}")
    if record.get("transcript_path"):
        lines.append(f"- transcript: {short_path(record['transcript_path'])}")
    if record.get("brief_path"):
        lines.append(f"- brief: {short_path(record['brief_path'])}")
    if not any(record.get(key) for key in ("transcript_path", "brief_path", "note_path")):
        lines.append("- file path not available")
    return "\n".join(lines).strip()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    state = load_state(config)
    records = []
    for uid in args.ids:
        record = state.get("links", {}).get(uid)
        if record:
            records.append(record)
    if not records and not args.ids:
        records = list(state.get("links", {}).values())[-1:]
    print("\n\n---\n\n".join(build_record_digest(record) for record in records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
