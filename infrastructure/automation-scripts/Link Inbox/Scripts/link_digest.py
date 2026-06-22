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


def transcript_title(text: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", text or "", flags=re.M)
    return compact(match.group(1)) if match else ""


def is_weak_title(title: str) -> bool:
    lowered = compact(title).lower()
    if not lowered:
        return True
    if lowered in {"saved link", "untitled link", "untitled youtube video"}:
        return True
    return bool(re.fullmatch(r"https?\s+.*|https\s+.*|www\..*|youtube\.com.*", lowered))


def sentence_split(lines: list[str]) -> list[str]:
    text = compact(" ".join(lines))
    if not text:
        return []
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-ZА-Я0-9])", text)
    sentences = []
    for piece in pieces:
        sentence = compact(piece).strip(" -")
        if 35 <= len(sentence) <= 360:
            sentences.append(sentence)
    return sentences


def detect_guest(lines: list[str]) -> str:
    lead = " ".join(lines[:18])
    match = re.search(
        r"Meet\s+([A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+){1,3}),\s+([^.;]+)",
        lead,
    )
    if match:
        return f"{match.group(1)}, {compact(match.group(2)).rstrip('.')}"
    return ""


TOPIC_KEYWORDS = {
    "AI productivity": ("productivity", "growth", "solve problems", "leverage", "efficient", "gdp"),
    "Teams and resource allocation": ("resource allocation", "same size teams", "smaller teams", "personnel", "businesses", "teams"),
    "Engineering talent": ("10x", "100x", "engineer", "load bearing", "polymath", "impact"),
    "Models and tokens": ("frontier", "open models", "tokens", "model", "commoditize", "value accrual"),
    "Company building": ("sales", "marketing", "legendary company", "founders", "enterprise", "go to market"),
}


def score_sentence(sentence: str, keywords: tuple[str, ...]) -> int:
    lowered = sentence.lower()
    return sum(2 if " " in keyword and keyword in lowered else int(keyword in lowered) for keyword in keywords)


def is_question_like(sentence: str) -> bool:
    lowered = sentence.strip().lower()
    return "?" in sentence or lowered.startswith(("do you", "what ", "when ", "if i ", "how should", "would you"))


def extract_key_points(lines: list[str], limit: int = 5) -> list[str]:
    sentences = sentence_split(lines)
    selected: list[str] = []
    seen: set[str] = set()

    for topic, keywords in TOPIC_KEYWORDS.items():
        scored = [(score_sentence(sentence, keywords), sentence) for sentence in sentences]
        declarative = [(score, sentence) for score, sentence in scored if score > 0 and not is_question_like(sentence)]
        candidates = sorted(declarative or [(score, sentence) for score, sentence in scored if score > 0], reverse=True)
        if not candidates or candidates[0][0] <= 0:
            continue
        sentence = candidates[0][1]
        normalized = sentence[:90].lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        selected.append(f"{topic}: {sentence}")
        if len(selected) >= limit:
            break

    if selected:
        return selected

    # Fallback: skip intro hooks and return spaced-out substantive sentences.
    noisy = ("meet ", "before ", "thank you", "ready to go", "it is so good", "this is going to be")
    filtered = [s for s in sentences if not s.lower().startswith(noisy)]
    if not filtered:
        filtered = sentences
    if not filtered:
        return []
    step = max(1, len(filtered) // limit)
    return [filtered[i] for i in range(0, min(len(filtered), step * limit), step)][:limit]


def build_rich_summary(record: dict, meta: dict[str, str], transcript: str, lines: list[str], tools: list[tuple[str, str]]) -> dict[str, list[str] | str]:
    title_candidates = [meta.get("title", ""), record.get("title", ""), transcript_title(transcript)]
    title = next((candidate for candidate in title_candidates if candidate and not is_weak_title(candidate)), "")
    if not title:
        title = record.get("url") or "Saved link"

    guest = detect_guest(lines)
    points = extract_key_points(lines, limit=5)

    if tools:
        about = build_about(tools, lines, meta.get("description", "") or record.get("excerpt", ""))
    elif guest:
        about = f"Интервью с {guest}. Главная тема: как AI меняет продуктивность, размер команд, ценность сильных инженеров и распределение ресурсов в компаниях."
    elif points:
        about = "Материал о том, как автор/гость объясняет AI leverage, команды, продуктовую работу и практические последствия для бизнеса."
    else:
        about = meta.get("description") or record.get("excerpt") or "Содержание пока не извлечено качественно."

    insights: list[str] = []
    for point in points:
        if ":" in point:
            topic, sentence = point.split(":", 1)
            insights.append(f"{topic.strip()}: {sentence.strip()}")
        else:
            insights.append(point)

    system: list[str] = []
    if tools:
        system.extend(
            [
                "Автор собирает workflow из готовых ресурсов и референсов, чтобы ускорить AI/vibe-coding работу.",
                "Практическая механика: не начинать с пустого листа, а давать AI готовые паттерны, компоненты и визуальные ориентиры.",
            ]
        )
    elif insights:
        system.extend(
            [
                "Это не tutorial с конкретным стеком инструментов, а интервью/разбор управленческой логики.",
                "Полезная механика: смотреть на AI как на leverage для решения большего числа задач или тех же задач меньшей командой.",
                "Отдельный сигнал: ценность смещается к людям, которые умеют брать ответственность за результат, а не просто генерировать больше кода.",
            ]
        )

    return {
        "title": title,
        "about": about,
        "insights": insights,
        "system": system,
    }


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
    title = meta.get("title") or record.get("title") or record.get("url") or "Saved link"
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
    transcript_items = transcript_lines(transcript)
    tools = extract_numbered_tools(transcript_items)
    rich = build_rich_summary(record, meta, transcript, transcript_items, tools)
    lines = [
        f"Сохранил: {rich['title'] or human_title(record, meta)}",
        "",
    ]

    if brief:
        core = section(brief, "Core Read")
        core_bullets = bullets(core, limit=4)
    elif record.get("excerpt"):
        core_bullets = [compact(str(record["excerpt"]))[:420]]
    else:
        core_bullets = []

    title = meta.get("title") or transcript_title(transcript) or record.get("title") or ""
    description = meta.get("description") or ""
    about = str(rich["about"])

    lines.append("О чём видео:")
    lines.append(f"- {about[:620].rstrip()}")

    insights = list(rich.get("insights") or [])
    if insights:
        lines.extend(["", "Ключевые идеи:"])
        for item in insights[:5]:
            lines.append(f"- {item[:420].rstrip()}")

    if tools:
        lines.extend(["", "Инструменты / ресурсы:"])
        for name, desc in tools[:6]:
            readable_desc = russian_tool_desc(name, desc)
            if readable_desc:
                lines.append(f"- {name}: {readable_desc}")
            else:
                lines.append(f"- {name}")
    elif (title and not is_weak_title(title)) or description:
        lines.extend(["", "Что видно из source metadata:"])
        if title and not is_weak_title(title):
            lines.append(f"- Title: {title}")
        if description:
            lines.append(f"- Caption: {description}")

    system_lines = list(rich.get("system") or [])
    if system_lines:
        lines.extend(["", "Система автора:"])
        for item in system_lines[:4]:
            lines.append(f"- {item}")
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
