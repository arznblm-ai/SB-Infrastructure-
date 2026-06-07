#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from obsidian_manager_lib import DEFAULT_CONFIG_PATH, generate_index_for_folder, load_config, setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Obsidian folder indexes.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to config.json")
    args = parser.parse_args()

    config = load_config(args.config)
    log_file = setup_logging(config)

    targets = list(config.get("index_folders", [])) + list(config.get("index_special", {}).keys())
    logging.info("Starting full index build for %s folders", len(targets))

    processed = []
    for relative_folder in targets:
        index_path, count = generate_index_for_folder(relative_folder, config)
        processed.append((relative_folder, count, index_path))
        logging.info("Indexed %s entries in %s -> %s", count, relative_folder, index_path)

    print("Obsidian Manager: index build complete")
    for relative_folder, count, index_path in processed:
        print(f"- {relative_folder}: {count} entries -> {index_path}")
    print(f"Log file: {log_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
