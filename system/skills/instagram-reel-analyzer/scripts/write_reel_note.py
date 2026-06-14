#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_OUTPUT_DIR = Path(
    "/Users/anton/AI AGENT FOLDER/Second Brain/resources/instagram-reels/transcripts"
)


def clean_scalar(value: str | None, default: str = "unknown") -> str:
    if value is None:
        return default
    value = " ".join(str(value).strip().split())
    return value or default


def clean_filename(value: str, default: str = "reel") -> str:
    value = value.lower()
    value = re.sub(r"https?://", "", value)
    value = re.sub(r"[^a-z0-9а-яё]+", "-", value, flags=re.IGNORECASE)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:80] or default


def read_lines(path: str | None, inline: list[str]) -> list[str]:
    lines: list[str] = []
    if path:
        text = Path(path).expanduser().read_text(encoding="utf-8").strip()
        if text:
            lines.extend(text.splitlines())
    lines.extend(line for line in inline if line.strip())
    return lines


def split_key_value(items: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for item in items:
        if "=" in item:
            label, url = item.split("=", 1)
            pairs.append((clean_scalar(label, "link"), clean_scalar(url, "")))
        else:
            pairs.append((clean_scalar(item, "link"), clean_scalar(item, "")))
    return pairs


def yaml_list(items: list[str]) -> str:
    deduped = unique(items)
    if not deduped:
        return "[]"
    escaped = [item.replace('"', '\\"') for item in deduped]
    return "[" + ", ".join(f'"{item}"' for item in escaped) + "]"


def unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for item in items:
        item = item.strip()
        key = item.lower()
        if not item or key in seen:
            continue
        seen.add(key)
        values.append(item)
    return values


def bullet_list(items: list[str], fallback: str) -> str:
    values = unique(items)
    if not values:
        values = [fallback]
    return "\n".join(f"- {item}" for item in values)


def link_list(pairs: list[tuple[str, str]]) -> str:
    if not pairs:
        return "- not found"
    rows = []
    for label, url in pairs:
        if url.startswith("http"):
            rows.append(f"- [{label}]({url})")
        else:
            rows.append(f"- {label}: {url}")
    return "\n".join(rows)


def transcript_block(lines: list[str]) -> str:
    if not lines:
        return "_Transcript unavailable._"
    return "\n".join(line.rstrip() for line in lines)


def derive_output_path(args: argparse.Namespace) -> Path:
    if args.output_path:
        return Path(args.output_path).expanduser().resolve()
    published = clean_scalar(args.published_at, "unknown-date")[:10]
    author = clean_filename(clean_scalar(args.author_username, "unknown-author"), "unknown-author")
    shortcode = clean_filename(clean_scalar(args.shortcode, "unknown-shortcode"), "unknown-shortcode")
    slug_source = args.title or (args.summary[0] if args.summary else "reel")
    slug = clean_filename(clean_scalar(slug_source, "reel"))
    return Path(args.output_dir).expanduser().resolve() / f"{published}_{author}_{shortcode}_{slug}.md"


def build_note(args: argparse.Namespace) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    title = clean_scalar(args.title, "Instagram Reel")
    yaml_title = title.replace('"', '\\"')
    source_url = clean_scalar(args.source_url)
    author_url = args.author_url or (
        f"https://www.instagram.com/{args.author_username}/" if args.author_username else "unknown"
    )
    links = split_key_value(args.link)
    transcript_lines = read_lines(args.transcript_file, args.transcript_line)
    caption = args.caption_file and Path(args.caption_file).expanduser().read_text(encoding="utf-8").strip()
    caption = caption or args.caption or "unknown"

    frontmatter = textwrap.dedent(
        f"""\
        ---
        type: instagram-reel-transcript
        title: "{yaml_title}"
        source_url: "{source_url}"
        shortcode: "{clean_scalar(args.shortcode)}"
        media_id: "{clean_scalar(args.media_id)}"
        author_username: "{clean_scalar(args.author_username)}"
        author_name: "{clean_scalar(args.author_name)}"
        author_url: "{author_url}"
        published_at: "{clean_scalar(args.published_at)}"
        captured_at: "{now}"
        language: "{clean_scalar(args.language)}"
        duration_seconds: "{clean_scalar(args.duration_seconds)}"
        likes: "{clean_scalar(args.likes)}"
        comments: "{clean_scalar(args.comments)}"
        views: "{clean_scalar(args.views)}"
        tools: {yaml_list(args.tool)}
        skills: {yaml_list(args.skill)}
        links: {yaml_list([url for _, url in links])}
        tags: {yaml_list(args.tag)}
        ---
        """
    )

    return (
        frontmatter
        + f"\n# {title}\n\n"
        + "## Краткое содержание\n\n"
        + bullet_list(args.summary, "Summary not written yet.")
        + "\n\n## Суть\n\n"
        + clean_scalar(args.essence, "unknown")
        + "\n\n## Полезные ссылки\n\n"
        + link_list(links)
        + "\n\n## Упомянутые инструменты и skills\n\n"
        + bullet_list(args.tool + args.skill, "No tools or skills identified.")
        + "\n\n## Главные инсайты\n\n"
        + bullet_list(args.insight, "Insights not written yet.")
        + "\n\n## Готовые решения / как применить\n\n"
        + bullet_list(args.solution, "Reusable solution not written yet.")
        + "\n\n## Оценка применимости для Антона\n\n"
        + clean_scalar(args.anton_relevance, "unknown")
        + "\n\n## Strategic Board analysis\n\n"
        + bullet_list(args.strategic_analysis, "not applicable")
        + "\n\n## Транскрипт\n\n"
        + transcript_block(transcript_lines)
        + "\n\n## Raw caption / metadata\n\n"
        + f"- Source URL: {source_url}\n"
        + f"- Author: {clean_scalar(args.author_name)} (@{clean_scalar(args.author_username)})\n"
        + f"- Published: {clean_scalar(args.published_at)}\n"
        + f"- Likes: {clean_scalar(args.likes)}\n"
        + f"- Comments: {clean_scalar(args.comments)}\n"
        + f"- Views: {clean_scalar(args.views)}\n\n"
        + "### Caption\n\n"
        + caption
        + "\n"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write a normalized Instagram Reel insight note.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--output-path")
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--shortcode")
    parser.add_argument("--media-id")
    parser.add_argument("--author-username")
    parser.add_argument("--author-name")
    parser.add_argument("--author-url")
    parser.add_argument("--title")
    parser.add_argument("--published-at")
    parser.add_argument("--language")
    parser.add_argument("--duration-seconds")
    parser.add_argument("--likes")
    parser.add_argument("--comments")
    parser.add_argument("--views")
    parser.add_argument("--caption")
    parser.add_argument("--caption-file")
    parser.add_argument("--transcript-file")
    parser.add_argument("--transcript-line", action="append", default=[])
    parser.add_argument("--summary", action="append", default=[])
    parser.add_argument("--essence")
    parser.add_argument("--link", action="append", default=[], help="label=url")
    parser.add_argument("--tool", action="append", default=[])
    parser.add_argument("--skill", action="append", default=[])
    parser.add_argument("--insight", action="append", default=[])
    parser.add_argument("--solution", action="append", default=[])
    parser.add_argument("--anton-relevance")
    parser.add_argument("--strategic-analysis", action="append", default=[])
    parser.add_argument("--tag", action="append", default=["instagram-reels"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path = derive_output_path(args)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_note(args), encoding="utf-8")
    print(f"REEL_NOTE_PATH={output_path}")


if __name__ == "__main__":
    main()
