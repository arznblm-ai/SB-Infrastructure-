#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from link_inbox_common import canonicalize_url, load_config, load_state, paths, save_state, url_id, write_link_note
from link_digest import build_record_digest
from link_summary_note import write_summary_note


VAULT = Path("/Users/anton/AI AGENT FOLDER/Second Brain")
INDEX_SCRIPT = Path(__file__).resolve().parent / "build_external_resources_index.py"
OBSIDIAN_INDEX_SCRIPT = VAULT / "infrastructure" / "Obsidian Manager" / "Scripts" / "build_indexes.py"
REQUIRED_SUMMARY_SECTIONS = [
    "## Краткое содержание",
    "## Инструменты / ресурсы",
    "## Система автора",
    "## Где лежит сырьё",
]
WEAK_SUMMARY_MARKERS = [
    "Инструменты явно не извлечены",
    "Система автора не выделена автоматически",
    "Transcript unavailable",
    "Содержание пока не извлечено",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Weekly quality audit for Link Inbox saved resources.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-obsidian-index", action="store_true")
    return parser.parse_args()


def rel(path: str | Path | None) -> str:
    if not path:
        return ""
    p = Path(path)
    try:
        return str(p.resolve().relative_to(VAULT))
    except Exception:
        return str(path)


def read_text(path: str | Path | None, max_chars: int = 500_000) -> str:
    if not path:
        return ""
    p = Path(path).expanduser()
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")[:max_chars]


def normalize_transcript(text: str) -> str:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("# "):
            continue
        line = re.sub(r"^\*\*\[[^\]]+\]\*\*\s*", "", line)
        lines.append(re.sub(r"\s+", " ", line))
    return "\n".join(lines).strip()


def file_hash(path: Path) -> str:
    text = normalize_transcript(path.read_text(encoding="utf-8", errors="replace"))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def quarantine_path(config: dict, source: Path) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d")
    target_dir = paths(config)["quarantine"] / stamp
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    suffix = 2
    while target.exists():
        target = target_dir / f"{source.stem} {suffix}{source.suffix}"
        suffix += 1
    return target


def move_to_quarantine(config: dict, source: Path, actions: list[str], dry_run: bool) -> None:
    target = quarantine_path(config, source)
    actions.append(f"quarantine: `{rel(source)}` -> `{rel(target)}`")
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))


def canonicalize_state(config: dict, state: dict, actions: list[str], dry_run: bool) -> dict:
    merged: dict[str, dict] = {}
    status_rank = {"processed": 4, "needs_manual_processing": 3, "failed": 2, "new": 1}

    def score(record: dict) -> tuple:
        artifact_count = sum(bool(record.get(key)) for key in ("summary_path", "transcript_path", "brief_path", "video_path"))
        return (
            status_rank.get(record.get("status"), 0),
            artifact_count,
            str(record.get("created_at") or record.get("date") or ""),
        )

    for old_uid, record in state.get("links", {}).items():
        original_url = record.get("url", "")
        canonical = canonicalize_url(original_url) if original_url else original_url
        if canonical and canonical != original_url:
            actions.append(f"canonicalized URL: `{original_url}` -> `{canonical}`")
            record["url"] = canonical
        new_uid = url_id(canonical or original_url or old_uid)
        if new_uid != old_uid:
            record["id"] = new_uid
            actions.append(f"canonicalized state id: `{old_uid}` -> `{new_uid}`")
        existing = merged.get(new_uid)
        if existing is None or score(record) >= score(existing):
            merged[new_uid] = record
    removed = len(state.get("links", {})) - len(merged)
    if removed:
        actions.append(f"merged duplicate state records: {removed}")
    if not dry_run:
        state["links"] = merged
        save_state(config, state)
    else:
        state = {**state, "links": merged}
    return state


def audit_records(config: dict, state: dict, issues: list[str], actions: list[str], dry_run: bool) -> None:
    for uid, record in state.get("links", {}).items():
        expected_uid = url_id(record.get("url", uid))
        if expected_uid != uid:
            issues.append(f"state id mismatch: `{uid}` expected `{expected_uid}`")

        if not record.get("note_path") or not Path(record["note_path"]).exists():
            issues.append(f"missing card for `{uid}`")

        if record.get("status") != "processed":
            continue

        for key in ("transcript_path", "brief_path"):
            value = record.get(key)
            if value and not Path(value).exists():
                issues.append(f"broken {key} for `{uid}`: `{rel(value)}`")

        summary_path = record.get("summary_path")
        summary_text = read_text(summary_path)
        summary_missing = not summary_path or not Path(summary_path).exists()
        missing_sections = [section for section in REQUIRED_SUMMARY_SECTIONS if section not in summary_text]
        missing_source_links = bool(record.get("transcript_path") and rel(record.get("transcript_path")) not in summary_text)

        if summary_missing or missing_sections or missing_source_links:
            reason = "missing" if summary_missing else "incomplete"
            issues.append(f"{reason} summary for `{uid}`")
            if not summary_missing and missing_sections:
                issues.append(f"summary missing sections for `{uid}`: {', '.join(missing_sections)}")
            actions.append(f"regenerate summary: `{uid}`")
            if not dry_run:
                write_summary_note(config, record)
                write_link_note(config, record)

        for marker in WEAK_SUMMARY_MARKERS:
            if marker in summary_text:
                issues.append(f"weak summary marker for `{uid}`: {marker}")
                break

        try:
            digest = build_record_digest(record)
            if "Сохранено:" not in digest:
                issues.append(f"digest missing saved section for `{uid}`")
        except Exception as exc:
            issues.append(f"digest build failed for `{uid}`: {exc}")


def cleanup_stale_artifacts(config: dict, state: dict, actions: list[str], dry_run: bool) -> None:
    referenced = set()
    for record in state.get("links", {}).values():
        for key in ("note_path", "summary_path", "transcript_path"):
            if record.get(key):
                referenced.add(str(Path(record[key]).resolve()))

    for folder_key, pattern in (("links", "*.md"), ("summaries", "*.md")):
        for path in paths(config)[folder_key].glob(pattern):
            if str(path.resolve()) not in referenced:
                move_to_quarantine(config, path, actions, dry_run)


def cleanup_duplicate_transcripts(config: dict, state: dict, issues: list[str], actions: list[str], dry_run: bool) -> None:
    transcript_dir = paths(config)["transcripts"]
    referenced = {
        str(Path(record["transcript_path"]).resolve())
        for record in state.get("links", {}).values()
        if record.get("transcript_path")
    }
    groups: dict[str, list[Path]] = {}
    for path in transcript_dir.glob("*.md"):
        if path.name.lower() in {"readme.md", "index.md"}:
            continue
        groups.setdefault(file_hash(path), []).append(path)

    for members in groups.values():
        if len(members) < 2:
            continue
        keep = None
        for path in members:
            if str(path.resolve()) in referenced:
                keep = path
                break
        keep = keep or sorted(members, key=lambda p: p.stat().st_mtime, reverse=True)[0]
        issues.append(f"duplicate transcript group: keep `{rel(keep)}`, duplicates={len(members) - 1}")
        for path in members:
            if path == keep:
                continue
            if str(path.resolve()) in referenced:
                issues.append(f"duplicate transcript is referenced, not moving: `{rel(path)}`")
                continue
            move_to_quarantine(config, path, actions, dry_run)


def audit_index(config: dict, state: dict, issues: list[str], actions: list[str], dry_run: bool) -> None:
    index_path = paths(config)["transcripts"] / "index.md"
    index_text = read_text(index_path)
    if not index_text:
        issues.append("missing external resources index")
        actions.append("rebuild external resources index")
    else:
        for uid, record in state.get("links", {}).items():
            if record.get("status") != "processed":
                continue
            if uid not in index_text:
                issues.append(f"index missing id `{uid}`")
            if record.get("summary_path") and rel(record["summary_path"]) not in index_text:
                issues.append(f"index missing summary for `{uid}`")
    if not dry_run:
        subprocess.run([sys.executable, str(INDEX_SCRIPT), "--config", str(config["config_file"])], check=False)


def write_report(config: dict, issues: list[str], actions: list[str], dry_run: bool) -> Path:
    now = datetime.now(timezone.utc)
    report_path = paths(config)["quality_reports"] / f"{{link}} {{audit}} saved links quality – {now.date().isoformat()}.md"
    status = "needs review" if issues else "clean"
    lines = [
        "---",
        "tags:",
        "  - type/audit",
        "  - source/link-inbox",
        "  - status/active",
        f"date: {now.date().isoformat()}",
        f"status: {status}",
        f"dry_run: {str(dry_run).lower()}",
        "---",
        "",
        f"# Saved Links Quality Audit – {now.date().isoformat()}",
        "",
        f"> **Thesis:** Link Inbox quality status is `{status}`. The audit checked state, cards, summaries, transcripts, duplicate content, and the external resources index.",
        "",
        "## Issues",
        "",
    ]
    lines.extend(f"- {issue}" for issue in issues) if issues else lines.append("- No issues found.")
    lines.extend(["", "## Actions", ""])
    lines.extend(f"- {action}" for action in actions) if actions else lines.append("- No cleanup or repair actions were needed.")
    lines.extend(
        [
            "",
            "## Retrieval Contract Checked",
            "",
            "- Agents should use `transcripts/external resources/index.md` first.",
            "- Agents should read `resources/link-inbox/summaries/` before opening full transcripts.",
            "- Full transcripts are only for exact wording, quotes, timestamps, or missing details.",
            "",
        ]
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    state = load_state(config)
    issues: list[str] = []
    actions: list[str] = []

    state = canonicalize_state(config, state, actions, args.dry_run)
    audit_records(config, state, issues, actions, args.dry_run)
    cleanup_stale_artifacts(config, state, actions, args.dry_run)
    cleanup_duplicate_transcripts(config, state, issues, actions, args.dry_run)
    audit_index(config, state, issues, actions, args.dry_run)

    if not args.dry_run:
        save_state(config, state)
        if not args.skip_obsidian_index and OBSIDIAN_INDEX_SCRIPT.exists():
            subprocess.run([sys.executable, str(OBSIDIAN_INDEX_SCRIPT)], check=False)

    report_path = write_report(config, issues, actions, args.dry_run)
    print(f"QUALITY_REPORT={report_path}")
    print(f"ISSUES={len(issues)}")
    print(f"ACTIONS={len(actions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
