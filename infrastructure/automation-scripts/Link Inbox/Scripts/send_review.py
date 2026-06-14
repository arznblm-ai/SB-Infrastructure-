#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
import subprocess

from link_inbox_common import configure_logging, load_config, load_state, paths, save_state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create and optionally send a Link Inbox review.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--send", action="store_true")
    parser.add_argument(
        "--mark-reviewed",
        action="store_true",
        help="Mark links as reviewed even when --send is not used.",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def build_review(config: dict, state: dict) -> tuple[str, list[str]]:
    max_items = int(config.get("review", {}).get("max_items", 12) or 12)
    candidates = []
    for uid, record in state.get("links", {}).items():
        if record.get("reviewed_at"):
            continue
        if record.get("status") in {"processed", "needs_manual_processing", "failed"}:
            candidates.append((uid, record))
    candidates.sort(key=lambda item: item[1].get("created_at", ""))
    selected = candidates[:max_items]
    if not selected:
        return "Link Inbox review: новых обработанных ссылок нет.", []

    lines = [
        f"Link Inbox review — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    reviewed_ids = []
    for idx, (uid, record) in enumerate(selected, start=1):
        status = record.get("status", "unknown")
        kind = record.get("kind", "web")
        title = record.get("title") or record.get("url")
        url = record.get("url")
        lines.append(f"{idx}. [{kind}/{status}] {title}")
        lines.append(f"   {url}")
        if record.get("excerpt"):
            excerpt = str(record["excerpt"])
            lines.append(f"   {excerpt[:240]}")
        if record.get("transcript_path"):
            lines.append(f"   transcript: {record['transcript_path']}")
        if record.get("brief_path"):
            lines.append(f"   brief: {record['brief_path']}")
        if record.get("video_path"):
            lines.append(f"   video: {record['video_path']}")
        if record.get("error"):
            lines.append(f"   error: {record['error']}")
        lines.append("")
        reviewed_ids.append(uid)
    return "\n".join(lines).strip(), reviewed_ids


def send_telegram(config: dict, text: str) -> None:
    tg = config["telegram"]
    token = os.environ.get(str(tg.get("bot_token_env", "LINK_INBOX_BOT_TOKEN")))
    chat_id = str(tg.get("review_channel", "")).strip()
    if not token:
        raise RuntimeError(f"Bot token env is missing: {tg.get('bot_token_env')}")
    if not chat_id:
        raise RuntimeError("telegram.review_channel is missing in config.")
    chunks = [text[i : i + 3500] for i in range(0, len(text), 3500)] or ["(empty)"]
    for chunk in chunks:
        command = ["curl", "-fsS", "--max-time", "30"]
        for key, value in {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": "true"}.items():
            command.extend(["--data-urlencode", f"{key}={value}"])
        command.append(f"https://api.telegram.org/bot{token}/sendMessage")
        subprocess.check_output(command, text=True, stderr=subprocess.STDOUT)


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    logger = configure_logging(config, verbose=args.verbose)
    state = load_state(config)
    text, reviewed_ids = build_review(config, state)

    review_path = paths(config)["reviews"] / f"{{review}} link inbox – {datetime.now().date().isoformat()}.md"
    review_path.write_text(text + "\n", encoding="utf-8")
    logger.info(f"Review saved: {review_path}")
    print(text)

    if args.send:
        send_telegram(config, text)
        logger.info("Review sent to Telegram.")

    if args.send or args.mark_reviewed:
        now = datetime.now(timezone.utc).isoformat()
        for uid in reviewed_ids:
            state["links"][uid]["reviewed_at"] = now
        state["reviews"]["last_reviewed_at"] = now
        save_state(config, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
