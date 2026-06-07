#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from obsidian_manager_lib import (
    DEFAULT_CONFIG_PATH,
    current_folder_target,
    generate_index_for_folder,
    load_config,
    load_index_mentions,
    setup_logging,
)


def resolve_relative_folder(target: Path, vault_path: Path, config: dict) -> str:
    relative = target.expanduser().resolve().relative_to(vault_path)
    special_roots = sorted(config.get("index_special", {}).keys(), key=len, reverse=True)
    relative_folder = str(relative.parent if target.is_file() else relative)
    for root in special_roots:
        if relative_folder == root or relative_folder.startswith(f"{root}/"):
            return root
    first_part = relative.parts[0] if relative.parts else relative_folder
    if first_part in set(config.get("index_folders", [])):
        return first_part
    return relative_folder


def scan_missing_entries(relative_folder: str, config: dict) -> list[str]:
    vault_path = Path(config["vault_path"]).expanduser()
    folder = vault_path / relative_folder
    existing_mentions = load_index_mentions(folder / "index.md")
    mode = config.get("index_special", {}).get(relative_folder, "standard")
    missing: list[str] = []

    if mode in {"project_list", "skill_list"}:
        for path in sorted(folder.iterdir()):
            if not path.is_dir() or path.name.startswith("."):
                continue
            if path.name not in existing_mentions:
                missing.append(path.name)
        return missing

    for path in sorted(folder.rglob("*.md")):
        if path.name in {"index.md", "README.md"}:
            continue
        if path.stem not in existing_mentions:
            missing.append(path.stem)
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description="Update one Obsidian folder index.")
    parser.add_argument("target", nargs="?", type=Path, help="File or folder inside the vault")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to config.json")
    args = parser.parse_args()

    config = load_config(args.config)
    log_file = setup_logging(config)
    vault_path = Path(config["vault_path"]).expanduser().resolve()

    if args.target is None:
        relative_folder = current_folder_target(vault_path, config, Path.cwd())
        if relative_folder is None:
            print(
                "Obsidian Manager: no target provided.\n"
                "Run this command from an indexed vault folder or pass a file/folder path explicitly.",
                file=sys.stderr,
            )
            return 2

        missing = scan_missing_entries(relative_folder, config)
        if missing:
            logging.info("Missing entries in %s: %s", relative_folder, ", ".join(missing))
        index_path, count = generate_index_for_folder(relative_folder, config)

        print(f"Obsidian Manager: refreshed index for {relative_folder}")
        suffix = f", {len(missing)} missing entries detected" if missing else ""
        print(f"- entries: {count}{suffix}")
        print(f"- index: {index_path}")
        print(f"Log file: {log_file}")
        return 0

    target = args.target.expanduser().resolve()
    relative_folder = resolve_relative_folder(target, vault_path, config)
    folder = vault_path / relative_folder
    existing_mentions = load_index_mentions(folder / "index.md")
    target_name = target.stem if target.is_file() else target.name
    action = "updated"
    if target_name not in existing_mentions:
        action = "added"

    index_path, count = generate_index_for_folder(relative_folder, config)
    logging.info("%s entry for %s in %s", action.capitalize(), target, index_path)

    print(f"Obsidian Manager: {action} index for {relative_folder}")
    print(f"- entries: {count}")
    print(f"- index: {index_path}")
    print(f"Log file: {log_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
