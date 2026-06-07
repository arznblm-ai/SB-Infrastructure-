#!/usr/bin/env python3
"""Unified runner for the Research Department scouts."""

from __future__ import annotations

import argparse
import json
import time
from typing import Any, Callable

from research_common import configure_logging, load_runtime_config
from telegram_scout import run as run_telegram_scout
from web_scout import run as run_web_scout


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Research Department scouts.")
    parser.add_argument("--config", help="Path to config.json", default=None)
    parser.add_argument(
        "--component",
        choices=("all", "telegram", "web"),
        default="all",
        help="Run all scouts or only one component.",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def execute_component(
    name: str,
    runner: Callable[..., dict[str, Any]],
    *,
    config_path: str | None,
    verbose: bool,
    logger: Any,
) -> dict[str, Any]:
    start = time.monotonic()
    try:
        result = runner(config_path=config_path, verbose=verbose)
    except Exception as exc:  # pragma: no cover - runtime guard
        logger.exception(f"{name} scout failed: {exc}")
        return {
            "component": name,
            "status": "failed",
            "error": str(exc),
            "duration_seconds": round(time.monotonic() - start, 2),
        }

    result["status"] = "ok"
    result["duration_seconds"] = round(time.monotonic() - start, 2)
    return result


def main() -> int:
    args = parse_args()
    config = load_runtime_config(args.config)
    logger = configure_logging(config["log_file"], verbose=args.verbose).getChild("runner")

    logger.info("Research Department run started.")

    results: list[dict[str, Any]] = []
    components: list[tuple[str, Callable[..., dict[str, Any]]]] = []
    if args.component in ("all", "telegram"):
        components.append(("telegram", run_telegram_scout))
    if args.component in ("all", "web"):
        components.append(("web", run_web_scout))

    for name, runner in components:
        results.append(
            execute_component(
                name,
                runner,
                config_path=args.config,
                verbose=args.verbose,
                logger=logger.getChild(name),
            )
        )

    failures = [result for result in results if result.get("status") != "ok"]
    logger.info("Run summary:")
    logger.info(json.dumps(results, indent=2, ensure_ascii=False))

    return 1 if failures and len(failures) == len(results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
