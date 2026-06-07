#!/usr/bin/env python3
"""Initial setup for the Research Department project."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Any

from research_common import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_LOG_FILE,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_TELEGRAM_CONFIG_FILE,
    DEFAULT_WORK_DIR,
    DEFAULT_VAULT_PATH,
    default_config,
    ensure_directory,
    load_json_file,
    save_json_file,
)

LAUNCH_AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / "com.user.research-department.plist"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Setup Research Department locally.")
    parser.add_argument("--config-only", action="store_true", help="Do not create venv or install packages.")
    parser.add_argument("--skip-telegram-auth", action="store_true", help="Do not authorize Telegram now.")
    parser.add_argument("--install-launchagent", action="store_true", help="Install LaunchAgent at 09:00.")
    return parser.parse_args()


def detect_project_root() -> Path:
    script_dir = Path(__file__).resolve().parent
    if (script_dir.parent / "requirements.txt").exists():
        return script_dir.parent
    return script_dir


def detect_scripts_dir(project_root: Path) -> Path:
    direct = Path(__file__).resolve().parent
    if (direct / "telegram_scout.py").exists():
        return direct
    return project_root / "Scripts"


def prompt(message: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{message}{suffix}: ").strip()
    return value or default


def parse_csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def build_config_payload(existing: dict[str, Any] | None = None) -> dict[str, Any]:
    config = existing or default_config()

    vault_path = prompt("Vault path", str(config.get("vault_path", DEFAULT_VAULT_PATH)))
    output_dir = prompt("Output directory inside vault", str(config.get("output_dir", DEFAULT_OUTPUT_DIR)))

    channel_default = ", ".join(config["telegram"].get("channels", []))
    channels = parse_csv_list(prompt("Telegram channels (comma-separated)", channel_default))

    existing_queries = config.get("web", {}).get("queries", [])
    query_default = ", ".join(
        item["query"] if isinstance(item, dict) else str(item) for item in existing_queries
    )
    queries = parse_csv_list(prompt("Web search queries (comma-separated)", query_default))

    config["vault_path"] = vault_path
    config["output_dir"] = output_dir
    config["log_file"] = str(DEFAULT_LOG_FILE)
    config["last_run_file"] = str(DEFAULT_CONFIG_DIR / "last_run.json")
    config["telegram"]["session_file"] = str(DEFAULT_CONFIG_DIR / "telegram.session")
    config["telegram"]["channels"] = channels or config["telegram"]["channels"]
    config["web"]["queries"] = [{"query": query} for query in queries] or config["web"]["queries"]

    return config


def build_telegram_credentials(existing: dict[str, Any] | None = None) -> dict[str, Any]:
    current = existing or {}
    api_id = prompt("Telegram api_id", str(current.get("api_id", "")))
    api_hash = prompt("Telegram api_hash", str(current.get("api_hash", "")))
    return {"api_id": api_id, "api_hash": api_hash}


def copy_project_files(project_root: Path, scripts_dir: Path) -> None:
    ensure_directory(DEFAULT_WORK_DIR)

    for source in scripts_dir.glob("*.py"):
        target = DEFAULT_WORK_DIR / source.name
        if source.resolve() == target.resolve():
            continue
        shutil.copy2(source, target)

    requirements_file = project_root / "requirements.txt"
    if requirements_file.exists():
        target = DEFAULT_WORK_DIR / "requirements.txt"
        if requirements_file.resolve() != target.resolve():
            shutil.copy2(requirements_file, target)


def create_venv(project_root: Path) -> Path:
    venv_dir = DEFAULT_WORK_DIR / "venv"
    if not venv_dir.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    pip = venv_dir / "bin" / "pip"
    requirements = project_root / "requirements.txt"
    subprocess.run([str(pip), "install", "--upgrade", "pip"], check=True)
    subprocess.run([str(pip), "install", "-r", str(requirements)], check=True)
    return venv_dir


def authorize_telegram(venv_dir: Path) -> None:
    python_bin = venv_dir / "bin" / "python"
    script_path = DEFAULT_WORK_DIR / "telegram_scout.py"
    subprocess.run([str(python_bin), str(script_path), "--authorize-only"], check=True)


def install_launchagent() -> None:
    ensure_directory(LAUNCH_AGENT_PATH.parent)
    stdout_log = Path.home() / "Library" / "Logs" / "research-department.stdout.log"
    stderr_log = Path.home() / "Library" / "Logs" / "research-department.stderr.log"

    plist = dedent(
        f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
          <key>Label</key>
          <string>com.user.research-department</string>
          <key>ProgramArguments</key>
          <array>
            <string>{DEFAULT_WORK_DIR / "venv" / "bin" / "python"}</string>
            <string>{DEFAULT_WORK_DIR / "scout_runner.py"}</string>
          </array>
          <key>RunAtLoad</key>
          <true/>
          <key>StartCalendarInterval</key>
          <dict>
            <key>Hour</key>
            <integer>9</integer>
            <key>Minute</key>
            <integer>0</integer>
          </dict>
          <key>WorkingDirectory</key>
          <string>{DEFAULT_WORK_DIR}</string>
          <key>StandardOutPath</key>
          <string>{stdout_log}</string>
          <key>StandardErrorPath</key>
          <string>{stderr_log}</string>
        </dict>
        </plist>
        """
    )
    LAUNCH_AGENT_PATH.write_text(plist, encoding="utf-8")
    subprocess.run(["launchctl", "unload", str(LAUNCH_AGENT_PATH)], check=False)
    subprocess.run(["launchctl", "load", str(LAUNCH_AGENT_PATH)], check=True)


def main() -> int:
    args = parse_args()
    project_root = detect_project_root()
    scripts_dir = detect_scripts_dir(project_root)

    ensure_directory(DEFAULT_CONFIG_DIR)
    ensure_directory(DEFAULT_WORK_DIR)
    ensure_directory(DEFAULT_LOG_FILE.parent)

    existing_config = load_json_file(DEFAULT_CONFIG_DIR / "config.json", default_config())
    existing_credentials = load_json_file(DEFAULT_TELEGRAM_CONFIG_FILE, {})

    config = build_config_payload(existing_config)
    telegram_credentials = build_telegram_credentials(existing_credentials)

    save_json_file(DEFAULT_CONFIG_DIR / "config.json", config)
    save_json_file(DEFAULT_TELEGRAM_CONFIG_FILE, telegram_credentials)
    save_json_file(DEFAULT_CONFIG_DIR / "last_run.json", load_json_file(DEFAULT_CONFIG_DIR / "last_run.json", {}))

    copy_project_files(project_root, scripts_dir)

    print(f"Config saved to: {DEFAULT_CONFIG_DIR / 'config.json'}")
    print(f"Telegram credentials saved to: {DEFAULT_TELEGRAM_CONFIG_FILE}")
    print(f"Working copy synced to: {DEFAULT_WORK_DIR}")

    venv_dir: Path | None = None
    if not args.config_only:
        venv_dir = create_venv(project_root)
        print(f"Virtual environment ready: {venv_dir}")

    if venv_dir is not None and not args.skip_telegram_auth:
        print("Starting Telegram authorization...")
        authorize_telegram(venv_dir)

    if args.install_launchagent:
        install_launchagent()
        print(f"LaunchAgent installed: {LAUNCH_AGENT_PATH}")

    print("\nSetup complete.")
    print("Next steps:")
    print(f"  1. Review {DEFAULT_CONFIG_DIR / 'config.json'}")
    print(f"  2. Run {DEFAULT_WORK_DIR / 'venv' / 'bin' / 'python'} {DEFAULT_WORK_DIR / 'scout_runner.py'}")
    print(f"  3. Tail logs with: tail -f {DEFAULT_LOG_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
