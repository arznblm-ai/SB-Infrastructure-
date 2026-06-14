#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
from pathlib import Path


LABEL = "com.anton.link-inbox-quality-audit"
PROJECT_DIR = Path("/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Link Inbox")
PLIST_FILE = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_FILE = Path.home() / "Library" / "Logs" / "link-inbox-quality-audit.log"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install weekly Link Inbox quality audit LaunchAgent.")
    parser.add_argument("--weekday", type=int, default=1, help="launchd weekday. Default: 1 = Monday.")
    parser.add_argument("--hour", type=int, default=10)
    parser.add_argument("--minute", type=int, default=15)
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--run-now", action="store_true")
    return parser.parse_args()


def launchctl(*args: str, check: bool = False) -> None:
    subprocess.run(["launchctl", *args], check=check)


def uninstall() -> None:
    launchctl("bootout", f"gui/{os.getuid()}", str(PLIST_FILE))
    if PLIST_FILE.exists():
        PLIST_FILE.unlink()
    print(f"Uninstalled: {PLIST_FILE}")


def install(weekday: int, hour: int, minute: int) -> None:
    PLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [
            "/bin/zsh",
            str(PROJECT_DIR / "Scripts" / "run_quality_audit.sh"),
        ],
        "WorkingDirectory": str(PROJECT_DIR),
        "StartCalendarInterval": {
            "Weekday": weekday,
            "Hour": hour,
            "Minute": minute,
        },
        "RunAtLoad": False,
        "StandardOutPath": str(LOG_FILE),
        "StandardErrorPath": str(LOG_FILE),
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
    }
    PLIST_FILE.write_bytes(plistlib.dumps(plist, sort_keys=False))
    launchctl("bootout", f"gui/{os.getuid()}", str(PLIST_FILE))
    launchctl("bootstrap", f"gui/{os.getuid()}", str(PLIST_FILE), check=True)
    print(f"Installed: {PLIST_FILE}")
    print(f"Weekly run: weekday={weekday} {hour:02d}:{minute:02d}")


def main() -> int:
    args = parse_args()
    if args.uninstall:
        uninstall()
        return 0
    install(args.weekday, args.hour, args.minute)
    if args.run_now:
        launchctl("kickstart", "-k", f"gui/{os.getuid()}/{LABEL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
