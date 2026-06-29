#!/usr/bin/env python3
"""One-off migration: unify external-resource notes into the single rich format.

Actions:
1. Reformat every legacy raw transcript in `transcripts/external resources/`
   (top level) into the unified rich note (auto tier, NO re-transcription).
2. Adopt analyzer-format notes from `resources/instagram-reels/transcripts/`
   into the canonical folder, converting their frontmatter to the unified
   schema while preserving already-written rich sections (enrichment: done).

Always run a filesystem backup BEFORE this (no git in this vault).
Use --dry-run to preview.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from external_resource_note import build_auto_note
from link_inbox_common import canonicalize_url, link_kind, load_config, load_state

VAULT = Path("/Users/anton/AI AGENT FOLDER/Second Brain")
EXTERNAL_DIR = VAULT / "transcripts" / "external resources"
ANALYZER_DIR = VAULT / "resources" / "instagram-reels" / "transcripts"
SKIP_NAMES = {"index.md", "readme.md"}
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def platform_from_name(name: str) -> str:
    low = name.lower()
    if "instagram" in low:
        return "instagram"
    if "tiktok" in low:
        return "tiktok"
    if "youtube" in low or "www.youtube" in low:
        return "youtube"
    if low.startswith("x video") or " x video" in low:
        return "x"
    return "web"


def record_for(path: Path, by_name: dict) -> dict:
    rec = dict(by_name.get(path.name, {}))
    if not rec.get("date"):
        m = DATE_RE.search(path.name)
        if m:
            rec["date"] = m.group(1)
    if not rec.get("title"):
        rec["title"] = path.stem
    if not rec.get("kind"):
        rec["kind"] = platform_from_name(path.name)
    return rec


def adapt_analyzer_note(text: str) -> str:
    """Convert an `instagram-reel-transcript` note to the unified schema."""
    url_match = re.search(r'^source_url:\s*"?([^"\n]+)"?\s*$', text, flags=re.M)
    platform = link_kind(url_match.group(1)) if url_match else "instagram"
    inject = (
        "type: external-resource\n"
        f'platform: "{platform}"\n'
        'transcription_model: "unknown"\n'
        "enrichment: done\n"
    )
    return re.sub(r"^type:\s*instagram-reel-transcript\s*$", inject.rstrip(), text, count=1, flags=re.M)


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate external-resource notes to the unified format.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repair-state", action="store_true", help="Repoint each processed record's summary_path to its unified note.")
    args = parser.parse_args()

    config = load_config(args.config)
    state = load_state(config)

    if args.repair_state:
        from link_inbox_common import save_state

        repaired = 0
        for rec in state.get("links", {}).values():
            tp = rec.get("transcript_path")
            if rec.get("status") == "processed" and tp and Path(tp).exists():
                if rec.get("summary_path") != tp:
                    rec["summary_path"] = tp
                    rec["note_md_path"] = tp
                    repaired += 1
        if not args.dry_run:
            save_state(config, state)
        print(f"REPAIRED state records: {repaired}{' (dry-run)' if args.dry_run else ''}")
        return 0
    by_name: dict[str, dict] = {}
    for rec in state.get("links", {}).values():
        tp = rec.get("transcript_path")
        if tp:
            by_name[Path(tp).name] = rec

    reformatted, adopted, skipped = [], [], []

    # 1. Reformat legacy raw transcripts (top level only).
    for path in sorted(EXTERNAL_DIR.glob("*.md")):
        if path.name.lower() in SKIP_NAMES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "type: external-resource" in text[:200]:
            skipped.append(path.name)
            continue
        rec = record_for(path, by_name)
        if not args.dry_run:
            path.write_text(build_auto_note(rec, text), encoding="utf-8")
        reformatted.append(f"{path.name}  [{rec.get('kind')}, url={'yes' if rec.get('url') else 'NO'}]")

    # 2. Adopt analyzer-format notes into the canonical folder.
    if ANALYZER_DIR.exists():
        for path in sorted(ANALYZER_DIR.glob("*.md")):
            target = EXTERNAL_DIR / f"{{link}} {path.stem} – analyzer.md"
            if not args.dry_run:
                target.write_text(adapt_analyzer_note(path.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
            adopted.append(f"{path.name} -> {target.name}")

    print(f"REFORMATTED ({len(reformatted)}):")
    for line in reformatted:
        print(f"  - {line}")
    print(f"\nADOPTED ({len(adopted)}):")
    for line in adopted:
        print(f"  - {line}")
    print(f"\nSKIPPED already-unified ({len(skipped)}):")
    for line in skipped:
        print(f"  - {line}")
    print(f"\n{'DRY-RUN — nothing written.' if args.dry_run else 'DONE.'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
