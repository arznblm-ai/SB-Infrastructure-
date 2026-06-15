#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


INFRA = Path("/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure")

REQUIRED_SIGNALS = {
    "purpose": ("purpose", "контекст", "роль", "цель"),
    "source_map": ("source map", "ключевые пути", "папки", "paths"),
    "inputs": ("inputs", "источники", "trigger", "вход"),
    "workflow": ("workflow", "как работает", "протокол", "runtime"),
    "outputs": ("outputs", "сохран", "артефакт", "результат"),
    "verification": ("verification", "verify", "провер", "quality", "audit", "eval"),
    "confirmation": ("confirmation", "подтверж", "human", "антон", "gates"),
    "commands": ("commands", "команды", "manual", "run", "logs", "launchctl"),
    "safety": ("safety", "безопас", "secret", "token", "secrets", "env"),
}

SKIP_DIRS = {
    ".next",
    ".tools",
    "node_modules",
    "archive",
    "outputs",
    "output",
    "runs",
    "templates",
    "Scripts",
    "config",
    "decisions",
    "workspace",
    "references",
    "agents",
    "skills",
    "reviews",
    "stats",
}


@dataclass
class ProjectAudit:
    name: str
    path: Path
    has_claude: bool
    missing: list[str]

    @property
    def score(self) -> int:
        if not self.has_claude:
            return 0
        total = len(REQUIRED_SIGNALS)
        present = total - len(self.missing)
        return round((present / total) * 100)


def project_dirs() -> list[Path]:
    projects: list[Path] = []
    for path in sorted(INFRA.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_dir() or path.name.startswith(".") or path.name in SKIP_DIRS:
            continue
        if path.is_symlink():
            continue
        projects.append(path)
    return projects


def read_claude(path: Path) -> str:
    claude = path / "CLAUDE.md"
    if claude.exists():
        return claude.read_text(encoding="utf-8", errors="ignore").lower()
    agents = path / "AGENTS.md"
    if agents.exists():
        return agents.read_text(encoding="utf-8", errors="ignore").lower()
    skill = path / "SKILL.md"
    if skill.exists():
        return skill.read_text(encoding="utf-8", errors="ignore").lower()
    return ""


def audit_project(path: Path) -> ProjectAudit:
    text = read_claude(path)
    has_contract = bool(text)
    missing: list[str] = []
    if has_contract:
        for section, signals in REQUIRED_SIGNALS.items():
            if not any(signal in text for signal in signals):
                missing.append(section)
    return ProjectAudit(path.name, path, has_contract, missing)


def status(audit: ProjectAudit) -> str:
    if not audit.has_claude:
        return "MISSING"
    if not audit.missing:
        return "OK"
    if audit.score >= 70:
        return "PARTIAL"
    return "WEAK"


def main() -> int:
    audits = [audit_project(path) for path in project_dirs()]
    print("# Agent Operating Standard Audit")
    print()
    print(f"- infrastructure: {INFRA}")
    print(f"- projects: {len(audits)}")
    print()
    print("| Status | Score | Project | Missing |")
    print("|---|---:|---|---|")
    for audit in audits:
        missing = ", ".join(audit.missing) if audit.missing else "-"
        print(f"| {status(audit)} | {audit.score}% | {audit.name} | {missing} |")

    blockers = [audit for audit in audits if not audit.has_claude]
    weak = [audit for audit in audits if audit.has_claude and audit.missing]
    print()
    print("## Summary")
    print()
    print(f"- Missing operating contract: {len(blockers)}")
    print(f"- Partial contracts: {len(weak)}")
    print(f"- Fully aligned: {len([audit for audit in audits if status(audit) == 'OK'])}")
    return 1 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
