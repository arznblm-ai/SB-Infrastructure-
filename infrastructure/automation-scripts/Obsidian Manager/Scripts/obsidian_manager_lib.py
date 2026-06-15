#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

try:
    import yaml
except ImportError:  # pragma: no cover - optional dependency
    yaml = None

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "obsidian-manager" / "config.json"
DATE_RE = re.compile(r"(?:\s[–-]\s)(\d{4}-\d{2}-\d{2})(?=\.md$|$)")
THESIS_RE = re.compile(r"^>\s+\*\*(?:Thesis|TL;DR):\*\*\s*(.+)$", re.MULTILINE)
WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+)")
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
ROOT_CODE_RE = re.compile(r"^\{[^{}]+\}")
CODE_RE = re.compile(r"\{[^{}]+\}")
ABS_PATH_RE = re.compile(r"`(/[^`]+)`")


@dataclass
class NoteMetadata:
    path: Path
    title: str
    date: str
    thesis: str
    tags: list[str]
    frontmatter: dict
    content: str


@dataclass
class ProjectEntry:
    name: str
    description: str
    has_claude: bool


@dataclass
class SkillEntry:
    name: str
    description: str


def load_config(config_path: Path | None = None) -> dict:
    path = (config_path or DEFAULT_CONFIG_PATH).expanduser()
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def setup_logging(config: dict) -> Path:
    log_file = Path(config["log_file"]).expanduser()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )
    return log_file


def now_string() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def is_skipped_path(path: Path, config: dict, vault_path: Path) -> bool:
    if is_hidden(path):
        return True
    relative = path.relative_to(vault_path)
    skip_folders = set(config.get("skip_folders", []))
    skip_files = set(config.get("skip_files", []))
    folder_skip_files = config.get("folder_skip_files", {})
    if any(part in skip_folders for part in relative.parts):
        return True
    if path.name in skip_files:
        return True
    for folder_name, names in folder_skip_files.items():
        if relative.parts and relative.parts[0] == folder_name and path.name in set(names):
            return True
    return False


def is_indexable_markdown(path: Path, config: dict, vault_path: Path) -> bool:
    if path.suffix.lower() != ".md":
        return False
    if is_skipped_path(path, config, vault_path):
        return False
    if path.name in {"index.md", "README.md"}:
        return False
    return True


def collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def slug_tokens(name: str) -> list[str]:
    cleaned = CODE_RE.sub(" ", name)
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", " ", cleaned)
    return [token.lower() for token in cleaned.split() if len(token) > 1]


def topic_signature(name: str) -> str:
    return " ".join(slug_tokens(name))


def fuzzy_topic_match(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def parse_frontmatter(text: str) -> tuple[dict, str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    raw = match.group(1)
    body = text[match.end() :]
    if yaml is not None:
        try:
            loaded = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            logging.warning("PyYAML failed to parse frontmatter, falling back to simple parser: %s", exc)
        else:
            if isinstance(loaded, dict):
                return loaded, body
            return {}, body

    data: dict[str, object] = {}
    current_key: str | None = None

    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") or line.startswith("\t- "):
            if current_key is None:
                continue
            data.setdefault(current_key, [])
            assert isinstance(data[current_key], list)
            data[current_key].append(_parse_yaml_scalar(line.split("-", 1)[1].strip()))
            continue
        if re.match(r"^\s*-\s+", line):
            if current_key is None:
                continue
            data.setdefault(current_key, [])
            assert isinstance(data[current_key], list)
            data[current_key].append(_parse_yaml_scalar(re.sub(r"^\s*-\s+", "", line)))
            continue
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        current_key = key
        if not value:
            data[key] = []
            continue
        data[key] = _parse_yaml_scalar(value)
    return data, body


def _parse_yaml_scalar(value: str):
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("\"'") for item in inner.split(",")]
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return value


def current_folder_target(vault_path: Path, config: dict, cwd: Path) -> str | None:
    try:
        relative = cwd.resolve().relative_to(vault_path)
    except ValueError:
        return None

    special_roots = sorted(config.get("index_special", {}).keys(), key=len, reverse=True)
    relative_str = str(relative)
    for root in special_roots:
        if relative_str == root or relative_str.startswith(f"{root}/"):
            return root

    indexed_roots = set(config.get("index_folders", []))
    if relative.parts and relative.parts[0] in indexed_roots:
        return relative.parts[0]
    return None


def extract_date(path: Path, frontmatter: dict) -> str:
    value = frontmatter.get("date")
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip('"')):
        return value.strip('"')
    match = DATE_RE.search(path.name)
    if match:
        return match.group(1)
    return ""


def extract_first_heading(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def extract_thesis(body: str) -> str:
    match = THESIS_RE.search(body)
    if match:
        return collapse_whitespace(match.group(1))

    lines = body.splitlines()
    heading_seen = False
    paragraph: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not heading_seen:
            if stripped.startswith("# "):
                heading_seen = True
            continue
        if not stripped:
            if paragraph:
                break
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith(">"):
            text = stripped.lstrip("> ").strip()
            if text:
                paragraph.append(text)
            continue
        paragraph.append(stripped)
    if paragraph:
        return collapse_whitespace(" ".join(paragraph))

    cleaned = collapse_whitespace(body)
    return cleaned[:100]


def read_note_metadata(path: Path) -> NoteMetadata | None:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logging.warning("Failed reading %s: %s", path, exc)
        return None

    frontmatter, body = parse_frontmatter(text)
    title = path.stem
    heading = extract_first_heading(body)
    if heading:
        title = heading
    tags = frontmatter.get("tags", [])
    if not isinstance(tags, list):
        tags = [str(tags)] if tags else []
    return NoteMetadata(
        path=path,
        title=title,
        date=extract_date(path, frontmatter),
        thesis=extract_thesis(body),
        tags=[str(tag) for tag in tags],
        frontmatter=frontmatter,
        content=text,
    )


def iter_markdown_files(folder: Path, config: dict, vault_path: Path) -> Iterable[Path]:
    for path in sorted(folder.rglob("*.md")):
        if path.parent != folder and "index.md" == path.name:
            continue
        if is_indexable_markdown(path, config, vault_path):
            yield path


def collect_folder_notes(folder: Path, config: dict, vault_path: Path) -> list[NoteMetadata]:
    notes: list[NoteMetadata] = []
    for path in iter_markdown_files(folder, config, vault_path):
        note = read_note_metadata(path)
        if note is not None:
            notes.append(note)
    notes.sort(key=lambda item: (item.date or "0000-00-00", item.path.stem.lower()), reverse=True)
    return notes


def build_standard_index(folder: Path, notes: list[NoteMetadata]) -> str:
    folder_name = folder.name
    count = len(notes)
    latest = next((note.date for note in notes if note.date), "n/a")
    lines = [
        f"# Index: {folder_name}",
        "",
        "> Автоматически сгенерировано Obsidian Manager.",
        f"> Последнее обновление: {now_string()}.",
        "> Не редактировать вручную — будет перезаписан при следующей индексации.",
        "",
        "| Файл | Дата | Thesis |",
        "|------|------|--------|",
    ]
    for note in notes:
        thesis = note.thesis.replace("|", "\\|")
        lines.append(f"| [[{note.path.stem}]] | {note.date or '—'} | {thesis} |")
    lines.extend(
        [
            "",
            f"**Статистика:** {count} файлов. Последний добавлен: {latest}.",
            "",
        ]
    )
    return "\n".join(lines)


def project_description(project_dir: Path) -> tuple[str, bool]:
    claude_path = project_dir / "CLAUDE.md"
    if not claude_path.exists():
        return project_dir.name, False
    try:
        text = claude_path.read_text(encoding="utf-8")
    except OSError as exc:
        logging.warning("Failed reading %s: %s", claude_path, exc)
        return project_dir.name, True
    _, body = parse_frontmatter(text)
    lines = [collapse_whitespace(line) for line in body.splitlines()]
    for line in lines:
        if not line or line.startswith("#"):
            continue
        return line.lstrip("> ").strip(), True
    return project_dir.name, True


def collect_projects(folder: Path, config: dict, vault_path: Path) -> list[ProjectEntry]:
    entries: list[ProjectEntry] = []
    for child in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue
        if child.is_symlink():
            continue
        if child.name == "archive":
            continue
        if is_skipped_path(child, config, vault_path):
            continue
        description, has_claude = project_description(child)
        entries.append(ProjectEntry(name=child.name, description=description, has_claude=has_claude))
    return entries


def build_projects_index(folder: Path, entries: list[ProjectEntry]) -> str:
    lines = [
        f"# Index: {folder.name}",
        "",
        "> Автоматически сгенерировано Obsidian Manager.",
        f"> Последнее обновление: {now_string()}.",
        "> Не редактировать вручную — будет перезаписан при следующей индексации.",
        "",
        "| Проект | Описание | CLAUDE.md |",
        "|--------|----------|-----------|",
    ]
    for entry in entries:
        description = entry.description.replace("|", "\\|")
        lines.append(f"| [[{entry.name}]] | {description} | {'Есть' if entry.has_claude else 'Нет'} |")
    lines.extend(["", f"**Статистика:** {len(entries)} проектов.", ""])
    return "\n".join(lines)


def collect_skills(folder: Path, config: dict, vault_path: Path) -> list[SkillEntry]:
    entries: list[SkillEntry] = []
    for child in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue
        if is_skipped_path(child, config, vault_path):
            continue
        skill_path = child / "SKILL.md"
        description = child.name
        if skill_path.exists():
            note = read_note_metadata(skill_path)
            if note is not None:
                description = str(note.frontmatter.get("description") or note.thesis or child.name)
        entries.append(SkillEntry(name=child.name, description=collapse_whitespace(description)))
    return entries


def build_skills_index(folder: Path, entries: list[SkillEntry]) -> str:
    lines = [
        "# Index: skills",
        "",
        "> Автоматически сгенерировано Obsidian Manager.",
        f"> Последнее обновление: {now_string()}.",
        "> Не редактировать вручную — будет перезаписан при следующей индексации.",
        "",
        "| Скил | Описание |",
        "|------|----------|",
    ]
    for entry in entries:
        description = entry.description.replace("|", "\\|")
        lines.append(f"| [[{entry.name}]] | {description} |")
    lines.extend(["", f"**Статистика:** {len(entries)} скилов.", ""])
    return "\n".join(lines)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def target_mode(relative_folder: str, config: dict) -> str:
    special = config.get("index_special", {})
    return special.get(relative_folder, "standard")


def generate_index_for_folder(relative_folder: str, config: dict) -> tuple[Path, int]:
    vault_path = Path(config["vault_path"]).expanduser()
    folder = vault_path / relative_folder
    mode = target_mode(relative_folder, config)
    index_path = folder / "index.md"

    if mode == "project_list":
        entries = collect_projects(folder, config, vault_path)
        write_text(index_path, build_projects_index(folder, entries))
        return index_path, len(entries)

    if mode == "skill_list":
        entries = collect_skills(folder, config, vault_path)
        write_text(index_path, build_skills_index(folder, entries))
        return index_path, len(entries)

    notes = collect_folder_notes(folder, config, vault_path)
    write_text(index_path, build_standard_index(folder, notes))
    return index_path, len(notes)


def collect_all_note_files(config: dict) -> list[Path]:
    vault_path = Path(config["vault_path"]).expanduser()
    files: list[Path] = []
    for path in vault_path.rglob("*"):
        if path.is_dir():
            continue
        if is_skipped_path(path, config, vault_path):
            continue
        files.append(path)
    return sorted(files)


def extract_links_from_text(text: str) -> set[str]:
    normalized = set()
    for match in WIKI_LINK_RE.findall(text):
        target = match.strip()
        if target.lower().endswith(".md"):
            target = target[:-3]
        normalized.add(target)
    return normalized


def load_index_mentions(index_path: Path) -> set[str]:
    if not index_path.exists():
        return set()
    try:
        text = index_path.read_text(encoding="utf-8")
    except OSError:
        return set()
    return extract_links_from_text(text)


def find_source_targets(note: NoteMetadata) -> set[str]:
    targets = set(extract_links_from_text(note.content))
    source = note.frontmatter.get("source")
    if isinstance(source, str):
        targets.update(extract_links_from_text(source))
    elif isinstance(source, list):
        for item in source:
            targets.update(extract_links_from_text(str(item)))
    return targets


def naming_violations(path: Path) -> list[str]:
    violations: list[str] = []
    stem = path.stem
    codes = CODE_RE.findall(stem)
    if not ROOT_CODE_RE.match(stem):
        violations.append("нет кода в фигурных скобках в начале имени")
    if not re.search(r"\s–\s\d{4}-\d{2}-\d{2}$", stem):
        if re.search(r"\s-\s\d{4}-\d{2}-\d{2}$", stem):
            violations.append("используется дефис '-' вместо EN DASH '–' перед датой")
        else:
            violations.append("нет даты в формате '– YYYY-MM-DD' в конце имени")
    if len(path.name) > 80:
        violations.append("имя длиннее 80 символов")
    if len(codes) > 2:
        violations.append("больше 2 кодов в фигурных скобках")
    return violations


def parse_claude_sections(claude_text: str) -> tuple[set[str], set[str], list[str]]:
    project_names = set()
    skill_names = set()

    for match in re.finditer(r"│\s+[├└]──\s+(.+?)/", claude_text):
        name = collapse_whitespace(match.group(1)).strip()
        if name and name not in {"context", "system", "rules", "skills"}:
            project_names.add(name)

    for skill_path_match in re.finditer(r"/system/skills/([^/`]+)/SKILL\.md", claude_text):
        skill_names.add(skill_path_match.group(1))

    truth_paths = ABS_PATH_RE.findall(claude_text)
    return project_names, skill_names, truth_paths
