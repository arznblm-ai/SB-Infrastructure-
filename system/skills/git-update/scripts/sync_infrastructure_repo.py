#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


VAULT = Path("/Users/anton/AI AGENT FOLDER/Second Brain")
REPO = Path("/Users/anton/AI AGENT FOLDER/SB-Infrastructure-")

FORBIDDEN_DIR_NAMES = {
    ".next",
    ".tools",
    "data",
    "exports",
    "meetings",
    "node_modules",
    "output",
    "runs",
    "sessions",
    "transcripts",
    "workspace",
}

FORBIDDEN_FILE_PATTERNS = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.crt",
    "*credential*",
    "*secret*",
    "import_finance_summary_*.ts",
    "seed.ts",
]

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(api[_-]?key|client[_-]?secret|secret|password)\s*[:=]\s*['\"][^'\"\n]{8,}['\"]"),
]

EXCLUDES = [
    ".DS_Store",
    ".env",
    ".env.*",
    "*.log",
    "*.tmp",
    "*.pem",
    "*.key",
    "*.crt",
    "__pycache__",
    "*.pyc",
    "node_modules",
    ".next",
    ".tools",
    "data",
    "output",
    "runs",
    "workspace",
    "exports",
]

PERSONAL_OS_EXCLUDES = EXCLUDES + [
    "scripts/import_finance_summary_*.ts",
    "scripts/seed.ts",
    "*.png",
    "*.html",
]


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd or REPO), check=check, text=True, capture_output=True)


def print_cmd_result(label: str, result: subprocess.CompletedProcess[str]) -> None:
    if result.stdout.strip():
        print(f"\n## {label}\n{result.stdout.strip()}")
    if result.stderr.strip():
        print(f"\n## {label} stderr\n{result.stderr.strip()}", file=sys.stderr)


def ensure_repo() -> None:
    if not VAULT.exists():
        raise SystemExit(f"Vault not found: {VAULT}")
    if not (REPO / ".git").exists():
        raise SystemExit(f"Git repo not found: {REPO}")


def rsync_args(excludes: list[str]) -> list[str]:
    args = ["rsync", "-a", "--delete"]
    for pattern in excludes:
        args.extend(["--exclude", pattern])
    return args


def sync_dir(src: Path, dest: Path, excludes: list[str], dry_run: bool) -> None:
    if not src.exists():
        print(f"skip missing dir: {src}")
        return
    dest.mkdir(parents=True, exist_ok=True)
    cmd = rsync_args(excludes)
    if dry_run:
        cmd.append("--dry-run")
    cmd.extend([str(src) + "/", str(dest) + "/"])
    result = run(cmd, cwd=REPO, check=True)
    print_cmd_result(f"sync {src} -> {dest}", result)


def sync_file(src: Path, dest: Path, dry_run: bool) -> None:
    if not src.exists():
        print(f"skip missing file: {src}")
        return
    if dry_run:
        print(f"would copy file: {src} -> {dest}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def remove_dir(path: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"would reset dir: {path}")
        return
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def sync_whitelist(dry_run: bool) -> None:
    sync_dir(VAULT / "system/skills", REPO / "system/skills", EXCLUDES, dry_run)

    research_src = VAULT / "infrastructure/Research Dept"
    research_dest = REPO / "infrastructure/Research Dept"
    sync_file(research_src / "SKILL.md", research_dest / "SKILL.md", dry_run)
    for subdir in ["references", "templates", "scripts", "skills", "agents"]:
        sync_dir(research_src / subdir, research_dest / subdir, EXCLUDES, dry_run)

    sync_dir(
        VAULT / "infrastructure/Personal OS",
        REPO / "infrastructure/Personal OS",
        PERSONAL_OS_EXCLUDES,
        dry_run,
    )

    link_inbox_src = VAULT / "infrastructure/Link Inbox"
    link_inbox_dest = REPO / "infrastructure/Link Inbox"
    sync_file(link_inbox_src / "CLAUDE.md", link_inbox_dest / "CLAUDE.md", dry_run)
    sync_file(link_inbox_src / "requirements.txt", link_inbox_dest / "requirements.txt", dry_run)
    sync_dir(link_inbox_src / "config", link_inbox_dest / "config", EXCLUDES, dry_run)

    git_update_src = VAULT / "infrastructure/Git Update"
    git_update_dest = REPO / "infrastructure/Git Update"
    sync_file(git_update_src / "CLAUDE.md", git_update_dest / "CLAUDE.md", dry_run)

    automation_dest = REPO / "infrastructure/automation-scripts"
    remove_dir(automation_dest, dry_run)
    for scripts_dir in sorted((VAULT / "infrastructure").glob("*/Scripts")):
        rel = scripts_dir.relative_to(VAULT / "infrastructure")
        sync_dir(scripts_dir, automation_dest / rel, EXCLUDES, dry_run)

    sync_dir(
        VAULT / "sessions/codex/Scripts",
        automation_dest / "Codex Session Export/Scripts",
        EXCLUDES,
        dry_run,
    )


def is_forbidden_file(path: Path) -> bool:
    name = path.name
    rel = str(path.relative_to(REPO))
    return any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel, p) for p in FORBIDDEN_FILE_PATTERNS)


def cleanup_forbidden_snapshot_paths(dry_run: bool) -> None:
    """Remove known private/runtime artifacts from the repo snapshot copy only."""
    removed: list[str] = []
    for root, dirs, files in os.walk(REPO):
        root_path = Path(root)
        if ".git" in root_path.parts:
            continue
        for dirname in list(dirs):
            path = root_path / dirname
            if dirname in FORBIDDEN_DIR_NAMES:
                removed.append(str(path))
                if not dry_run:
                    shutil.rmtree(path, ignore_errors=True)
                dirs.remove(dirname)
        for filename in files:
            path = root_path / filename
            if is_forbidden_file(path):
                removed.append(str(path))
                if not dry_run:
                    path.unlink(missing_ok=True)
    if removed:
        print("\nCleaned forbidden snapshot paths:" if not dry_run else "\nWould clean forbidden snapshot paths:")
        for path in removed:
            print(f"- {path}")


def safety_scan() -> list[str]:
    findings: list[str] = []
    for root, dirs, files in os.walk(REPO):
        root_path = Path(root)
        if ".git" in root_path.parts:
            continue
        for dirname in list(dirs):
            if dirname in FORBIDDEN_DIR_NAMES:
                findings.append(f"forbidden directory: {root_path / dirname}")
        for filename in files:
            path = root_path / filename
            if is_forbidden_file(path):
                findings.append(f"forbidden file: {path}")
                continue
            try:
                if path.stat().st_size > 2_000_000:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line in text.splitlines():
                if "os.environ" in line or "getenv(" in line:
                    continue
                for pattern in SECRET_PATTERNS:
                    if pattern.search(line):
                        findings.append(f"possible secret pattern in: {path}")
                        break
                else:
                    continue
                break
    return findings


def git_changed_files() -> list[str]:
    result = run(["git", "status", "--porcelain"], cwd=REPO)
    return [line for line in result.stdout.splitlines() if line.strip()]


def commit_changes(message: str) -> str:
    run(["git", "add", "-A"], cwd=REPO)
    run(["git", "commit", "-m", message], cwd=REPO)
    result = run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO)
    return result.stdout.strip()


def push_changes() -> bool:
    result = run(["git", "push", "-u", "origin", "main"], cwd=REPO, check=False)
    print_cmd_result("git push", result)
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Anton's Second Brain infrastructure into SB-Infrastructure repo.")
    parser.add_argument("--dry-run", action="store_true", help="Preview rsync operations without writing.")
    parser.add_argument("--no-commit", action="store_true", help="Sync and scan, but do not create a git commit.")
    parser.add_argument("--push", action="store_true", help="Push after committing. Requires terminal GitHub auth.")
    parser.add_argument("--message", default="Update Second Brain infrastructure snapshot", help="Commit message.")
    args = parser.parse_args()

    ensure_repo()
    sync_whitelist(args.dry_run)
    cleanup_forbidden_snapshot_paths(args.dry_run)

    if args.dry_run:
        print("\ndry-run complete; no files changed.")
        return 0

    findings = safety_scan()
    if findings:
        print("\nSAFETY SCAN FAILED")
        for finding in findings:
            print(f"- {finding}")
        print("\nNo commit created. Remove or ignore these files before retrying.")
        return 2

    changes = git_changed_files()
    if not changes:
        print("\nNo infrastructure changes to commit.")
        return 0

    print("\nChanged files:")
    for line in changes:
        print(line)

    if args.no_commit:
        print("\n--no-commit set; leaving changes uncommitted.")
        return 0

    commit_hash = commit_changes(args.message)
    print(f"\nCreated commit {commit_hash}: {args.message}")

    if args.push:
        if push_changes():
            print("Push completed.")
        else:
            print("Push failed. Use GitHub Desktop to push this commit.")
            return 3
    else:
        print("Push not attempted. Use GitHub Desktop, or rerun with --push when terminal auth works.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
