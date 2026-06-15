#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


CODEX_SKILLS = Path("/Users/anton/.codex/skills")
VAULT_SKILLS = Path("/Users/anton/AI AGENT FOLDER/Second Brain/system/skills")
REPORT_PATH = Path("/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Agent Operating Standard/skill-registry.md")

LOCAL_ONLY_PREFIXES = (
    ".system",
    "figma",
    "figma-",
)

LOCAL_ONLY_SKILLS = {
    "docx-form-filler",
    "file-organizer",
    "hatch-pet",
}

VAULT_CANONICAL_ALIASES = {
    "design-department": "design department",
    "design-orchestrator": "design department/design-orchestrator",
    "research-department": "research department",
    "portal-designer": "design department/portal-designer",
    "presentation-art-director": "design department/presentation-art-director",
    "presentation-designer": "design department/presentation-designer",
    "presentation-generator-critic": "design department/presentation-generator-critic",
}


@dataclass(frozen=True)
class SkillRecord:
    name: str
    path: Path
    description: str


def skill_name(path: Path, root: Path) -> str:
    rel = path.parent.relative_to(root)
    return str(rel)


def parse_description(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    match = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
    if not match:
        return ""
    return match.group(1).strip().strip('"')


def collect(root: Path, max_depth: int) -> dict[str, SkillRecord]:
    records: dict[str, SkillRecord] = {}
    if not root.exists():
        return records
    paths = set(root.rglob("SKILL.md"))
    for entry in root.iterdir():
        skill_path = entry / "SKILL.md"
        if skill_path.exists():
            paths.add(skill_path)
    for path in sorted(paths):
        try:
            depth = len(path.relative_to(root).parts)
        except ValueError:
            continue
        if depth > max_depth:
            continue
        name = skill_name(path, root)
        records[name] = SkillRecord(name=name, path=path, description=parse_description(path))
    return records


def normalize(name: str) -> str:
    return name.replace(" ", "-")


def is_local_only(name: str) -> bool:
    return name in LOCAL_ONLY_SKILLS or any(name.startswith(prefix) for prefix in LOCAL_ONLY_PREFIXES)


def compare() -> dict[str, list[tuple[str, str, str]]]:
    codex = collect(CODEX_SKILLS, max_depth=3)
    vault = collect(VAULT_SKILLS, max_depth=4)
    vault_by_normalized = {normalize(name): name for name in vault}

    in_sync: list[tuple[str, str, str]] = []
    codex_only: list[tuple[str, str, str]] = []
    vault_only: list[tuple[str, str, str]] = []
    local_only: list[tuple[str, str, str]] = []
    mapped: list[tuple[str, str, str]] = []

    matched_vault: set[str] = set()

    for name, record in codex.items():
        if is_local_only(name):
            local_only.append((name, "local-only", str(record.path)))
            continue
        mapped_target = VAULT_CANONICAL_ALIASES.get(name)
        if mapped_target and mapped_target in vault:
            mapped.append((name, mapped_target, str(record.path)))
            matched_vault.add(mapped_target)
            continue
        if name in vault:
            in_sync.append((name, name, str(record.path)))
            matched_vault.add(name)
            continue
        normalized = normalize(name)
        if normalized in vault_by_normalized:
            target = vault_by_normalized[normalized]
            mapped.append((name, target, str(record.path)))
            matched_vault.add(target)
            continue
        codex_only.append((name, "missing-in-vault", str(record.path)))

    for name, record in vault.items():
        if name not in matched_vault and name not in codex and normalize(name) not in codex:
            vault_only.append((name, "not-installed-in-codex", str(record.path)))

    return {
        "in_sync": sorted(in_sync),
        "mapped": sorted(mapped),
        "codex_only": sorted(codex_only),
        "vault_only": sorted(vault_only),
        "local_only": sorted(local_only),
    }


def table(rows: list[tuple[str, str, str]], headers: tuple[str, str, str]) -> list[str]:
    lines = [f"| {headers[0]} | {headers[1]} | {headers[2]} |", "|---|---|---|"]
    if not rows:
        lines.append("| - | - | - |")
        return lines
    for left, right, path in rows:
        safe_path = path.replace("|", "\\|")
        lines.append(f"| `{left}` | `{right}` | `{safe_path}` |")
    return lines


def render() -> str:
    data = compare()
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = [
        "# Skill Registry Sync",
        "",
        "> Purpose: keep Codex-local skills and vault-backed Second Brain skills understandable without pretending every local/plugin skill must be mirrored into the vault.",
        "",
        f"Generated: `{generated}`",
        "",
        "## Policy",
        "",
        "- `~/.codex/skills` is the active local Codex runtime skill layer.",
        "- `/Users/anton/AI AGENT FOLDER/Second Brain/system/skills` is the vault-backed canonical layer for reusable Second Brain skills and Git snapshots.",
        "- Plugin/vendor/system skills can stay local-only.",
        "- Personal reusable skills that affect Second Brain infrastructure should either be mirrored into the vault or explicitly listed as local-only.",
        "- Mapped skills are considered synced when their canonical vault equivalent exists under a different folder name.",
        "",
        "## Summary",
        "",
        f"- In sync: {len(data['in_sync'])}",
        f"- Mapped equivalents: {len(data['mapped'])}",
        f"- Codex-only review needed: {len(data['codex_only'])}",
        f"- Vault-only not installed in Codex: {len(data['vault_only'])}",
        f"- Local/plugin-only: {len(data['local_only'])}",
        "",
        "## In Sync",
        "",
        *table(data["in_sync"], ("Codex skill", "Vault skill", "Codex path")),
        "",
        "## Mapped Equivalents",
        "",
        *table(data["mapped"], ("Codex skill", "Vault canonical skill", "Codex path")),
        "",
        "## Codex-Only Review Needed",
        "",
        *table(data["codex_only"], ("Codex skill", "Status", "Codex path")),
        "",
        "## Vault-Only Not Installed In Codex",
        "",
        *table(data["vault_only"], ("Vault skill", "Status", "Vault path")),
        "",
        "## Local / Plugin Only",
        "",
        *table(data["local_only"], ("Codex skill", "Status", "Codex path")),
        "",
        "## Recommended Next Review",
        "",
        "- Keep Figma/plugin/system skills local-only unless the vault needs a portable copy of their policy.",
        "- Treat symlinked vault skills in `~/.codex/skills` as synced local runtime skills.",
        "- Re-run this audit after creating or installing any new skill.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare local Codex skills with vault-backed Second Brain skills.")
    parser.add_argument("--write", action="store_true", help=f"Write report to {REPORT_PATH}")
    args = parser.parse_args()

    report = render()
    print(report)
    if args.write:
        REPORT_PATH.write_text(report, encoding="utf-8")
        print(f"\nSKILL_REGISTRY_REPORT={REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
