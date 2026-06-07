#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from obsidian_manager_lib import (
    DEFAULT_CONFIG_PATH,
    find_source_targets,
    fuzzy_topic_match,
    is_skipped_path,
    load_config,
    load_index_mentions,
    naming_violations,
    parse_claude_sections,
    parse_frontmatter,
    read_note_metadata,
    setup_logging,
    topic_signature,
)


def report_status(count: int) -> str:
    return "✅" if count == 0 else "⚠️"


KRISP_ID_RE = re.compile(r"\*\*Krisp ID:\*\*\s*`([^`]+)`")


def note_runtime_ids(note) -> set[str]:
    ids = {
        str(note.frontmatter.get("krisp_call_id", "")).strip(),
        str(note.frontmatter.get("krisp_mcp_id", "")).strip(),
    }
    body_match = KRISP_ID_RE.search(note.content)
    if body_match:
        ids.add(body_match.group(1).strip())
    ids.discard("")
    return ids


def all_markdown_notes(vault_path: Path, config: dict) -> list[Path]:
    files: list[Path] = []
    for path in vault_path.rglob("*.md"):
        if is_skipped_path(path, config, vault_path):
            continue
        files.append(path)
    return sorted(files)


def tracked_note_paths(vault_path: Path, config: dict) -> list[Path]:
    tracked_roots = [vault_path / name for name in config.get("index_folders", [])]
    files: list[Path] = []
    for root in tracked_roots:
        for path in root.rglob("*.md"):
            if is_skipped_path(path, config, vault_path):
                continue
            if path.name in {"README.md", "index.md", "CLAUDE.md", "SKILL.md"}:
                continue
            files.append(path)
    return sorted(files)


def find_unprocessed_transcripts(vault_path: Path, config: dict) -> list[str]:
    transcripts_dir = vault_path / "transcripts"
    summary_dirs = [vault_path / "education", vault_path / "meetings"]
    summaries = []
    for summary_dir in summary_dirs:
        for path in sorted(summary_dir.glob("*.md")):
            if path.name in {"README.md", "index.md"}:
                continue
            note = read_note_metadata(path)
            if note is None:
                continue
            sources = find_source_targets(note)
            summaries.append(
                {
                    "path": path,
                    "date": note.date,
                    "signature": topic_signature(path.stem),
                    "sources": sources,
                }
            )

    issues: list[str] = []
    for transcript in sorted(transcripts_dir.glob("*.md")):
        if transcript.name in {"README.md", "index.md"}:
            continue
        note = read_note_metadata(transcript)
        if note is None:
            continue
        signature = topic_signature(transcript.stem)
        matched = False
        for summary in summaries:
            if transcript.stem in summary["sources"]:
                matched = True
                break
            if note.date and summary["date"] == note.date and fuzzy_topic_match(signature, summary["signature"]) >= 0.45:
                matched = True
                break
        if not matched:
            issues.append(str(transcript.relative_to(vault_path)))
    return issues


def find_naming_issues(vault_path: Path, config: dict) -> list[str]:
    eligible_roots = [
        vault_path / "context",
        vault_path / "education",
        vault_path / "meetings",
        vault_path / "transcripts",
        vault_path / "system",
    ]
    issues: list[str] = []
    for root in eligible_roots:
        for path in root.rglob("*.md"):
            if is_skipped_path(path, config, vault_path):
                continue
            if path.name in {"README.md", "index.md", "SKILL.md", "CLAUDE.md"}:
                continue
            if "system/skills" in str(path.relative_to(vault_path)):
                continue
            violations = naming_violations(path)
            for violation in violations:
                issues.append(f"{path.relative_to(vault_path)} — {violation}")
    return issues


def find_frontmatter_issues(vault_path: Path, config: dict) -> list[str]:
    markdown_only_dirs = [vault_path / name for name in config.get("index_folders", [])]
    issues: list[str] = []

    for root in markdown_only_dirs:
        for path in sorted(root.iterdir()):
            if is_skipped_path(path, config, vault_path):
                continue
            if path.is_file() and path.suffix.lower() != ".md":
                issues.append(f"{path.relative_to(vault_path)} — не markdown файл, не должен быть в {root.name}/")

    for root in markdown_only_dirs:
        for path in root.rglob("*.md"):
            if is_skipped_path(path, config, vault_path):
                continue
            if path.name in {"README.md", "index.md", "CLAUDE.md"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                issues.append(f"{path.relative_to(vault_path)} — не удалось прочитать: {exc}")
                continue
            frontmatter, _ = parse_frontmatter(text)
            if not frontmatter:
                issues.append(f"{path.relative_to(vault_path)} — нет YAML frontmatter")
                continue
            if "date" not in frontmatter:
                issues.append(f"{path.relative_to(vault_path)} — frontmatter без поля date")
            if "tags" not in frontmatter:
                issues.append(f"{path.relative_to(vault_path)} — frontmatter без поля tags")
    return issues


def find_orphans(vault_path: Path, config: dict) -> list[str]:
    index_mentions: set[str] = set()
    for folder in list(config.get("index_folders", [])) + list(config.get("index_special", {}).keys()):
        index_mentions.update(load_index_mentions(vault_path / folder / "index.md"))

    inbound_links: defaultdict[str, set[str]] = defaultdict(set)
    candidates = tracked_note_paths(vault_path, config)
    for path in all_markdown_notes(vault_path, config):
        note = read_note_metadata(path)
        if note is None:
            continue
        for target in find_source_targets(note):
            inbound_links[target].add(path.stem)

    orphans: list[str] = []
    for path in candidates:
        reasons = []
        if path.stem not in index_mentions:
            reasons.append("нет в index.md")
        if not inbound_links.get(path.stem):
            reasons.append("нет входящих wiki/source ссылок")
        if reasons:
            orphans.append(f"{path.relative_to(vault_path)} — {'; '.join(reasons)}")
    return orphans


def find_claude_sync_issues(vault_path: Path) -> list[str]:
    claude_path = vault_path / "claude.md"
    text = claude_path.read_text(encoding="utf-8")
    project_names, skill_names, truth_paths = parse_claude_sections(text)
    issues: list[str] = []

    actual_projects = {path.name for path in (vault_path / "infrastructure").iterdir() if path.is_dir() and not path.name.startswith(".")}
    for project in sorted(actual_projects):
        if project not in project_names:
            issues.append(f"infrastructure/{project} — есть на диске, но нет в карте хранилища claude.md")

    tasks_dir = vault_path / "tasks"
    if tasks_dir.exists():
        actual_tasks = {path.name for path in tasks_dir.iterdir() if path.is_dir() and not path.name.startswith(".")}
        for task in sorted(actual_tasks):
            if task == "archive":
                continue
            if task not in project_names:
                issues.append(f"tasks/{task} — есть на диске, но нет в карте хранилища claude.md")

    actual_skills = {path.name for path in (vault_path / "system" / "skills").iterdir() if path.is_dir() and not path.name.startswith(".")}
    for skill in sorted(actual_skills):
        if skill not in skill_names:
            issues.append(f"system/skills/{skill} — есть на диске, но нет в claude.md")

    for raw_path in truth_paths:
        if not Path(raw_path).expanduser().exists():
            issues.append(f"{raw_path} — путь указан в «Источники истины», но не существует на диске")
    return issues


def find_duplicates(vault_path: Path, config: dict) -> list[str]:
    notes = []
    for path in tracked_note_paths(vault_path, config):
        note = read_note_metadata(path)
        if note is None or not note.date:
            continue
        top_folder = path.relative_to(vault_path).parts[0]
        unique_ids = note_runtime_ids(note)
        notes.append((path, top_folder, note.date, topic_signature(path.stem), unique_ids))

    grouped: defaultdict[tuple[str, str], list[tuple[Path, str, set[str]]]] = defaultdict(list)
    for path, top_folder, date, signature, unique_ids in notes:
        grouped[(top_folder, date)].append((path, signature, unique_ids))

    duplicates: set[str] = set()
    for (top_folder, date), items in grouped.items():
        for idx, (left_path, left_sig, left_ids) in enumerate(items):
            for right_path, right_sig, right_ids in items[idx + 1 :]:
                if not left_sig or not right_sig:
                    continue
                if left_ids and right_ids and left_ids.isdisjoint(right_ids):
                    continue
                if fuzzy_topic_match(left_sig, right_sig) >= 0.92:
                    duplicates.add(f"{left_path.relative_to(vault_path)} <-> {right_path.relative_to(vault_path)} — одинаковая тема и дата {date}")
    return sorted(duplicates)


def write_report(report_path: Path, sections: list[tuple[str, list[str]]]) -> None:
    summary_lines = [
        f"# Vault Health Check — {datetime.now().strftime('%Y-%m-%d')}",
        "",
        "## Сводка",
        "",
        "| Проверка | Статус | Количество |",
        "|----------|--------|------------|",
    ]
    for title, items in sections:
        summary_lines.append(f"| {title} | {report_status(len(items))} | {len(items)} |")

    summary_lines.extend(["", "## Детали", ""])
    for title, items in sections:
        summary_lines.append(f"### {title} ({len(items)})")
        if items:
            for item in items:
                summary_lines.append(f"- `{item}`")
        else:
            summary_lines.append("- Ок")
        summary_lines.append("")

    report_path.write_text("\n".join(summary_lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run health checks for the Obsidian vault.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to config.json")
    args = parser.parse_args()

    config = load_config(args.config)
    log_file = setup_logging(config)
    vault_path = Path(config["vault_path"]).expanduser()

    sections = [
        ("Необработанные транскрипты", find_unprocessed_transcripts(vault_path, config)),
        ("Naming convention", find_naming_issues(vault_path, config)),
        ("Frontmatter", find_frontmatter_issues(vault_path, config)),
        ("Сироты", find_orphans(vault_path, config)),
        ("claude.md синхрон", find_claude_sync_issues(vault_path)),
        ("Дубликаты", find_duplicates(vault_path, config)),
    ]

    report_name = f"{{POS}} {{report}} vault health check – {datetime.now().strftime('%Y-%m-%d')}.md"
    report_path = vault_path / config.get("health_report_dir", "system") / report_name
    write_report(report_path, sections)

    logging.info("Health check report written to %s", report_path)
    print("Vault health check complete")
    for title, items in sections:
        print(f"- {title}: {len(items)}")
    print(f"Report: {report_path}")
    print(f"Log file: {log_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
