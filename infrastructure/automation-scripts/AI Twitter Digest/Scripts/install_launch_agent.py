#!/usr/bin/env python3
"""
install_launch_agent.py — установка/снятие LaunchAgent для AI Twitter Digest.

Два запуска в сутки (по умолчанию 09:00 и 21:00 локального времени Мака).

    python3 Scripts/install_launch_agent.py
    python3 Scripts/install_launch_agent.py --hour 8 --second-hour 20
    python3 Scripts/install_launch_agent.py --uninstall
"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
from pathlib import Path

LABEL = "com.user.ai-twitter-digest"
PROJECT_DIR = Path("/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/AI Twitter Digest")
RUNTIME_DIR = Path.home() / ".config" / "ai-twitter-digest"
ENV_FILE = RUNTIME_DIR / "env"
VENV_PY = RUNTIME_DIR / "venv" / "bin" / "python3"
PLIST_FILE = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_FILE = Path.home() / "Library" / "Logs" / "ai-twitter-digest.log"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install or uninstall the AI Twitter Digest LaunchAgent.")
    parser.add_argument("--hour", type=int, default=9, help="Час утреннего выпуска.")
    parser.add_argument("--minute", type=int, default=0, help="Минута утреннего выпуска.")
    parser.add_argument("--second-hour", type=int, default=21, help="Час вечернего выпуска.")
    parser.add_argument("--second-minute", type=int, default=0, help="Минута вечернего выпуска.")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--skip-validation", action="store_true")
    return parser.parse_args()


def validate_runtime() -> None:
    if not VENV_PY.exists():
        raise RuntimeError(f"Нет venv: {VENV_PY}. Сначала запусти Scripts/setup.sh")
    if not ENV_FILE.exists():
        raise RuntimeError(f"Нет env-файла: {ENV_FILE}. Сначала запусти Scripts/setup.sh")
    content = ENV_FILE.read_text(encoding="utf-8")
    for key in ("AI_DIGEST_BOT_TOKEN", "AI_DIGEST_CHAT_ID"):
        if key not in content or f'{key}="PASTE' in content:
            raise RuntimeError(f"Заполни {key} в {ENV_FILE} перед установкой автозапуска.")
    if not (RUNTIME_DIR / "accounts.db").exists():
        raise RuntimeError(
            f"Нет базы аккаунтов X: {RUNTIME_DIR / 'accounts.db'}. "
            "Сначала twscrape add_accounts / login_accounts."
        )


def launchctl(*args: str) -> None:
    subprocess.run(["launchctl", *args], check=False)


def uninstall() -> None:
    launchctl("bootout", f"gui/{os.getuid()}", str(PLIST_FILE))
    if PLIST_FILE.exists():
        PLIST_FILE.unlink()
    print(f"Uninstalled: {PLIST_FILE}")


def install(slots: list[dict[str, int]]) -> None:
    PLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [
            "/bin/bash",
            str(PROJECT_DIR / "Scripts" / "run_digest.sh"),
        ],
        "WorkingDirectory": str(PROJECT_DIR),
        "StartCalendarInterval": slots,
        "StandardOutPath": str(LOG_FILE),
        "StandardErrorPath": str(LOG_FILE),
        "RunAtLoad": False,
    }
    PLIST_FILE.write_bytes(plistlib.dumps(plist, sort_keys=False))
    launchctl("bootout", f"gui/{os.getuid()}", str(PLIST_FILE))
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST_FILE)], check=True)
    print(f"Installed: {PLIST_FILE}")
    times = ", ".join(f"{slot['Hour']:02d}:{slot['Minute']:02d}" for slot in slots)
    print(f"Выпуски: {times} (локальное время Мака)")


def main() -> int:
    args = parse_args()
    if args.uninstall:
        uninstall()
        return 0
    if not args.skip_validation:
        validate_runtime()
    install([
        {"Hour": args.hour, "Minute": args.minute},
        {"Hour": args.second_hour, "Minute": args.second_minute},
    ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
