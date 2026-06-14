#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
from pathlib import Path

LABEL = "com.anton.link-inbox-bot"
PROJECT_DIR = Path("/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Link Inbox")
ENV_FILE = Path.home() / ".config" / "link-inbox" / "env"
PLIST_FILE = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_FILE = Path.home() / "Library" / "Logs" / "link-inbox-bot.log"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install or uninstall the Saved Links Telegram bot LaunchAgent.")
    parser.add_argument("--uninstall", action="store_true")
    return parser.parse_args()


def launchctl(*args: str, check: bool = False) -> None:
    subprocess.run(["launchctl", *args], check=check)


def uninstall() -> None:
    launchctl("bootout", f"gui/{os.getuid()}", str(PLIST_FILE))
    if PLIST_FILE.exists():
        PLIST_FILE.unlink()
    print(f"Uninstalled: {PLIST_FILE}")


def install() -> None:
    if not ENV_FILE.exists():
        raise RuntimeError(f"Create token env first: {ENV_FILE}")
    PLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [
            "/bin/zsh",
            str(PROJECT_DIR / "Scripts" / "run_telegram_link_bot.sh"),
        ],
        "WorkingDirectory": str(PROJECT_DIR),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(LOG_FILE),
        "StandardErrorPath": str(LOG_FILE),
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
    }
    PLIST_FILE.write_bytes(plistlib.dumps(plist, sort_keys=False))
    launchctl("bootout", f"gui/{os.getuid()}", str(PLIST_FILE))
    launchctl("bootstrap", f"gui/{os.getuid()}", str(PLIST_FILE), check=True)
    launchctl("kickstart", "-k", f"gui/{os.getuid()}/{LABEL}")
    print(f"Installed: {PLIST_FILE}")


def main() -> int:
    args = parse_args()
    if args.uninstall:
        uninstall()
    else:
        install()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
