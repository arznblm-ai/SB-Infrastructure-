#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
from pathlib import Path

LABEL = "com.anton.link-inbox"
PROJECT_DIR = Path("/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Link Inbox")
CONFIG_FILE = Path.home() / ".config" / "link-inbox" / "config.json"
ENV_FILE = Path.home() / ".config" / "link-inbox" / "env"
PLIST_FILE = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
OUT_LOG = Path.home() / "Library" / "Logs" / "link-inbox.launchd.out.log"
ERR_LOG = Path.home() / "Library" / "Logs" / "link-inbox.launchd.err.log"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install or uninstall the Link Inbox macOS LaunchAgent.")
    parser.add_argument("--hour", type=int, default=21, help="Local hour for daily run.")
    parser.add_argument("--minute", type=int, default=30, help="Local minute for daily run.")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--skip-validation", action="store_true")
    return parser.parse_args()


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        raise RuntimeError(f"Config is missing: {CONFIG_FILE}")
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def validate_runtime() -> None:
    config = load_config()
    tg = config.get("telegram", {})
    missing = [
        name
        for name in ("review_channel",)
        if not str(tg.get(name, "")).strip()
    ]
    if missing:
        raise RuntimeError(f"Fill Telegram config first: {', '.join(missing)} in {CONFIG_FILE}")
    if not ENV_FILE.exists():
        raise RuntimeError(f"Create {ENV_FILE} with LINK_INBOX_BOT_TOKEN before installing auto-send.")


def launchctl(*args: str) -> None:
    subprocess.run(["launchctl", *args], check=False)


def uninstall() -> None:
    launchctl("bootout", f"gui/{os.getuid()}", str(PLIST_FILE))
    if PLIST_FILE.exists():
        PLIST_FILE.unlink()
    print(f"Uninstalled: {PLIST_FILE}")


def install(hour: int, minute: int) -> None:
    PLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_LOG.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [
            "/bin/zsh",
            str(PROJECT_DIR / "Scripts" / "run_link_inbox.sh"),
            "--send-review",
            "--limit",
            "8",
        ],
        "WorkingDirectory": str(PROJECT_DIR),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(OUT_LOG),
        "StandardErrorPath": str(ERR_LOG),
        "RunAtLoad": False,
    }
    PLIST_FILE.write_bytes(plistlib.dumps(plist, sort_keys=False))
    launchctl("bootout", f"gui/{os.getuid()}", str(PLIST_FILE))
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST_FILE)], check=True)
    print(f"Installed: {PLIST_FILE}")
    print(f"Daily run: {hour:02d}:{minute:02d}")


def main() -> int:
    args = parse_args()
    if args.uninstall:
        uninstall()
        return 0
    if not args.skip_validation:
        validate_runtime()
    install(args.hour, args.minute)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
