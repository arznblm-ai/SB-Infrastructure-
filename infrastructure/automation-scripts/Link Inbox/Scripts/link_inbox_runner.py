#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Link Inbox pipeline.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--send-review", action="store_true")
    parser.add_argument("--bot-only", action="store_true", help="Skip Telethon channel collection and process bot-captured links.")
    parser.add_argument("--skip-youtube", action="store_true")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def run_step(script: str, args: list[str]) -> int:
    command = ["python3", str(SCRIPT_DIR / script), *args]
    print("+ " + " ".join(command), flush=True)
    result = subprocess.run(command, check=False)
    return result.returncode


def main() -> int:
    args = parse_args()
    common = []
    if args.config:
        common.extend(["--config", args.config])
    if args.verbose:
        common.append("--verbose")

    if not args.bot_only:
        code = run_step("collect_links.py", common)
        if code != 0:
            return code

    process_args = [*common, "--limit", str(args.limit)]
    if args.skip_youtube:
        process_args.append("--skip-youtube")
    code = run_step("process_links.py", process_args)
    if code != 0:
        return code

    review_args = [*common]
    if args.send_review:
        review_args.append("--send")
    return run_step("send_review.py", review_args)


if __name__ == "__main__":
    raise SystemExit(main())
