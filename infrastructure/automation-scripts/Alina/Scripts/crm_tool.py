#!/usr/bin/env python3
"""CRM Алины: инструмент vault-стороны.

Архитектура: **`crm/crm.json` — истина, md — отрендеренный вид**.
json правится только этим скриптом (ставит `updated_at` по Москве и
`updated_by=vault`); параллельно тот же json на VPS правит демон бота
(`updated_by=bot`), поэтому запись:

  * сохраняет неизвестные поля и порядок ключей (мутация загруженного dict);
  * атомарна (tmp + os.replace);
  * не переформатирует файл (indent=1, ensure_ascii=False — как сейчас).

Подкоманды:
  list                    таблица воронки в stdout
  set --code <code> …     точечная правка полей проекта
  add --code … --name …   новый проект в воронку
  render                  пересобрать md-вид из json
  check                   валидация json (exit 0 — ок, 1 — есть ошибки)

Usage:
  python3 Scripts/crm_tool.py list
  python3 Scripts/crm_tool.py set --code timeframe --stage "в производстве" --next "кикофф 24.08"
  python3 Scripts/crm_tool.py set --code timeframe --stage сдан --close
  python3 Scripts/crm_tool.py add --code new-client --name "New" --client "X" --country RU --scope "ролик 15 сек"
  python3 Scripts/crm_tool.py render [--dry-run]
  python3 Scripts/crm_tool.py check

Только stdlib, python3 >= 3.9. Сети не трогает, наружу ничего не шлёт.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEPT = Path(__file__).resolve().parent.parent
DEFAULT_CRM = DEPT / "crm" / "crm.json"
MD_GLOB = "*CRM проектов*.md"
DEFAULT_MD_NAME = "{alina} {plan} CRM проектов – 2026-08-19.md"

SCHEMA = "alina-crm/1"
TERMINAL_STAGES = ("сдан", "проигран", "отложен")
CODE_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
COUNTRY_LABEL = {"RU": "РФ", "US": "США"}

# Порядок ключей нового проекта — как у существующих записей в crm.json
NEW_PROJECT_KEYS = (
    "code", "name", "client", "country", "scope", "stage", "price",
    "next_step", "due", "breakdown", "updated_at", "updated_by", "roster", "notes",
)

VORONKA_HEADER = (
    "| # | Проект | Клиент / агентство | Что делаем | Стадия | Цена | "
    "След. шаг | Когда | Брейкдаун | Обновлено |",
    "|---|---|---|---|---|---|---|---|---|---|",
)
CLOSED_HEADER = (
    "| Проект | Стадия | Когда | Источник |",
    "|---|---|---|---|",
)

MD_STUB_HEAD = """---
tags:
  - type/plan
  - agent/alina
date: {today}
status: active
---

# CRM проектов Алины

### [[{today}]]

> Воронка проектов Алины. Источник истины — `crm.json` рядом; этот файл — отрендеренный вид (`Scripts/crm_tool.py render`).

**Стадии:** {stages}.
"""


# ─────────────────────────── время и мелкие утилиты ───────────────────────────

def msk() -> timezone:
    """Europe/Moscow. Постоянный UTC+3 с 2014; zoneinfo — если доступна tzdata."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Europe/Moscow")  # type: ignore[return-value]
    except Exception:
        return timezone(timedelta(hours=3))


def now_iso() -> str:
    return datetime.now(msk()).replace(microsecond=0).isoformat()


def today_str() -> str:
    return datetime.now(msk()).date().isoformat()


def is_iso_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


def strip_up(path: str) -> str:
    """Пути в json хранятся относительно infrastructure/Alina/ — без ведущего `../`."""
    path = (path or "").strip()
    return path[3:] if path.startswith("../") else path


def die(msg: str, code: int = 2) -> None:
    print(f"ОШИБКА: {msg}", file=sys.stderr)
    sys.exit(code)


# ─────────────────────────────── чтение / запись ───────────────────────────────

def load_crm(path: Path) -> dict:
    if not path.exists():
        die(f"нет файла {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        die(f"{path}: битый json — {exc}")
    if not isinstance(data, dict):
        die(f"{path}: ожидался объект в корне")
    data.setdefault("projects", [])
    data.setdefault("closed", [])
    return data


def dumps_crm(data: dict) -> str:
    """Формат как в файле сейчас: indent=1, ensure_ascii=False, без хвостового \\n."""
    return json.dumps(data, ensure_ascii=False, indent=1)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def find_md(crm_path: Path, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    folder = crm_path.parent
    candidates = sorted(folder.glob(MD_GLOB))
    return candidates[-1] if candidates else folder / DEFAULT_MD_NAME


def unified(old: str, new: str, name: str) -> str:
    diff = difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"{name} (сейчас)", tofile=f"{name} (станет)", n=2,
    )
    return "".join(diff)


# ────────────────────────────────── доступ ────────────────────────────────────

def all_entries(data: dict):
    for entry in data.get("projects", []):
        yield entry, "projects"
    for entry in data.get("closed", []):
        yield entry, "closed"


def find_entry(data: dict, code: str):
    for entry, bucket in all_entries(data):
        if entry.get("code") == code:
            return entry, bucket
    return None, None


def stamp(entry: dict, data: dict) -> None:
    """Метка правки vault-стороной + корневой updated_at."""
    entry["updated_at"] = now_iso()
    entry["updated_by"] = "vault"
    data["updated_at"] = entry["updated_at"]
    if isinstance(data.get("updated"), str) and is_iso_date(data["updated"]):
        data["updated"] = today_str()


# ─────────────────────────────── рендер таблиц ────────────────────────────────

def esc(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("\n", " ").replace("|", r"\|").strip()


def cell(value) -> str:
    text = esc(value)
    return text if text else "—"


def client_cell(project: dict) -> str:
    """«ВТБ / Mosaic» + RU → «ВТБ / Mosaic (РФ)»; «Сбер / Mosaic (тендер)» → «… (тендер, РФ)»."""
    client = esc(project.get("client"))
    country = esc(project.get("country")).upper()
    label = COUNTRY_LABEL.get(country, country)
    if not label or label in client:
        return client or "—"
    if not client:
        return f"({label})"
    if client.endswith(")"):
        return f"{client[:-1]}, {label})"
    return f"{client} ({label})"


def rel_link(path: str) -> str:
    """Путь из json (относительно infrastructure/Alina/) → ссылка из папки crm/."""
    path = esc(path)
    if not path:
        return "—"
    if path.startswith("./"):
        path = path[2:]
    if not path.startswith(("../", "/")):
        path = "../" + path
    return f"`{path}`"


def breakdown_cell(project: dict) -> str:
    return rel_link(project.get("breakdown"))


def updated_cell(entry: dict) -> str:
    ts = esc(entry.get("updated_at"))
    date = ts[:10] if len(ts) >= 10 else ""
    suffix = " (бот)" if entry.get("updated_by") == "bot" else ""
    return (date or "—") + suffix


def voronka_table(data: dict) -> list[str]:
    rows = list(VORONKA_HEADER)
    for i, project in enumerate(data.get("projects", []), start=1):
        rows.append(
            "| {n} | **{name}** | {client} | {scope} | **{stage}** | {price} | "
            "{next} | {due} | {breakdown} | {upd} |".format(
                n=i,
                name=cell(project.get("name")),
                client=client_cell(project),
                scope=cell(project.get("scope")),
                stage=cell(project.get("stage")),
                price=cell(project.get("price")),
                next=cell(project.get("next_step")),
                due=cell(project.get("due")),
                breakdown=breakdown_cell(project),
                upd=updated_cell(project),
            )
        )
    if len(rows) == len(VORONKA_HEADER):
        rows.append("| — | _пусто_ |  |  |  |  |  |  |  |  |")
    return rows


def closed_table(data: dict) -> list[str]:
    rows = list(CLOSED_HEADER)
    for entry in data.get("closed", []):
        rows.append(
            "| {name} | {stage} | {when} | {src} |".format(
                name=cell(entry.get("name") or entry.get("code")),
                stage=cell(entry.get("stage")),
                when=cell(entry.get("closed")),
                src=rel_link(entry.get("source")),
            )
        )
    if len(rows) == len(CLOSED_HEADER):
        rows.append("| _пусто_ |  |  |  |")
    return rows


# ─────────────────────────── разбор и сборка md-вида ──────────────────────────

def split_blocks(text: str):
    """[(heading_line|None, [body lines])] — преамбула идёт с heading=None."""
    blocks: list[tuple[str | None, list[str]]] = []
    heading: str | None = None
    body: list[str] = []
    for line in text.split("\n"):
        if line.startswith("## "):
            blocks.append((heading, body))
            heading, body = line, []
        else:
            body.append(line)
    blocks.append((heading, body))
    return blocks


def replace_table(body: list[str], new_table: list[str]) -> list[str]:
    """Заменить первый непрерывный блок строк-таблицы; нет таблицы — дописать."""
    start = next((i for i, ln in enumerate(body) if ln.lstrip().startswith("|")), None)
    if start is None:
        out = list(body)
        while out and not out[-1].strip():
            out.pop()
        return out + [""] + new_table + [""]
    end = start
    while end < len(body) and body[end].lstrip().startswith("|"):
        end += 1
    return body[:start] + new_table + body[end:]


def extract_table(body: list[str]) -> list[str]:
    start = next((i for i, ln in enumerate(body) if ln.lstrip().startswith("|")), None)
    if start is None:
        return []
    end = start
    while end < len(body) and body[end].lstrip().startswith("|"):
        end += 1
    return body[start:end]


def append_journal(body: list[str], line: str) -> list[str]:
    out = list(body)
    while out and not out[-1].strip():
        out.pop()
    return out + [line, ""]


def stub_md(data: dict) -> str:
    stages = " · ".join(f"`{s}`" for s in data.get("stages", []))
    head = MD_STUB_HEAD.format(today=today_str(), stages=stages)
    return (
        head
        + "\n## Воронка\n\n"
        + "\n".join(voronka_table(data))
        + "\n\n## Закрытые\n\n"
        + "\n".join(closed_table(data))
        + "\n\n## Правила ведения\n\n- Новый бриф на просчёт → строка сразу, стадия `считаем`, цена `?`.\n"
        + "\n## Журнал\n\n"
    )


def render_md(data: dict, md_path: Path) -> tuple[str, bool]:
    """Вернуть (новый текст md, изменились ли таблицы)."""
    new_voronka = voronka_table(data)
    new_closed = closed_table(data)

    if not md_path.exists():
        return stub_md(data), True

    old_text = md_path.read_text(encoding="utf-8")
    blocks = split_blocks(old_text)

    tables_changed = False
    rebuilt: list[tuple[str | None, list[str]]] = []
    journal_idx: int | None = None
    for heading, body in blocks:
        title = (heading or "").lstrip("#").strip().lower()
        if title.startswith("воронка"):
            if extract_table(body) != new_voronka:
                tables_changed = True
            body = replace_table(body, new_voronka)
        elif title.startswith("закрыт"):
            if extract_table(body) != new_closed:
                tables_changed = True
            body = replace_table(body, new_closed)
        elif title.startswith("журнал"):
            journal_idx = len(rebuilt)
        rebuilt.append((heading, body))

    if tables_changed and journal_idx is not None:
        heading, body = rebuilt[journal_idx]
        rebuilt[journal_idx] = (
            heading,
            append_journal(body, f"- {today_str()} - таблицы пересобраны из `crm.json` (`crm_tool.py render`)."),
        )

    lines: list[str] = []
    for heading, body in rebuilt:
        if heading is not None:
            lines.append(heading)
        lines.extend(body)
    return "\n".join(lines), tables_changed


# ──────────────────────────────── подкоманды ──────────────────────────────────

def cmd_list(args, data: dict) -> int:
    cols = [
        ("code", "Код", 16), ("name", "Проект", 20), ("stage", "Стадия", 28),
        ("price", "Цена", 24), ("due", "Когда", 10), ("next_step", "След. шаг", 42),
        ("upd", "Обновлено", 17),
    ]

    def clip(text: str, width: int) -> str:
        text = ("" if text is None else str(text)).replace("\n", " ")
        return (text[: width - 1] + "…") if len(text) > width else text.ljust(width)

    print(" | ".join(title.ljust(width) for _, title, width in cols))
    print("-+-".join("-" * width for _, _, width in cols))
    for project in data.get("projects", []):
        values = []
        for key, _, width in cols:
            raw = updated_cell(project) if key == "upd" else project.get(key, "")
            values.append(clip(raw, width))
        print(" | ".join(values))

    closed = data.get("closed", [])
    if closed:
        print("\nЗакрытые:")
        for entry in closed:
            print(
                f"  {str(entry.get('code','')).ljust(16)} "
                f"{str(entry.get('name','')).ljust(20)} "
                f"{str(entry.get('stage','')).ljust(12)} {entry.get('closed','')}"
            )
    print(
        f"\nАктивных: {len(data.get('projects', []))} · закрытых: {len(closed)} · "
        f"json обновлён: {data.get('updated_at', '?')}"
    )
    return 0


def apply_fields(entry: dict, args, stages: list[str]) -> list[str]:
    """Применить только переданные поля. Вернуть список описаний изменений."""
    changes: list[str] = []

    def put(key: str, value) -> None:
        old = entry.get(key)
        if old != value:
            changes.append(f"  {key}: {old!r} → {value!r}")
        entry[key] = value

    if args.stage is not None:
        if args.stage not in stages:
            die("стадия «{}» не из списка. Допустимые: {}".format(args.stage, " · ".join(stages)))
        put("stage", args.stage)
    if args.name is not None:
        put("name", args.name)
    if args.client is not None:
        put("client", args.client)
    if args.country is not None:
        put("country", args.country)
    if args.scope is not None:
        put("scope", args.scope)
    if args.price is not None:
        put("price", args.price)
    if args.next is not None:
        put("next_step", args.next)
    if args.due is not None:
        if args.due and not is_iso_date(args.due):
            die(f"--due ждёт YYYY-MM-DD или пустую строку, получено «{args.due}»")
        put("due", args.due)
    if args.notes is not None:
        put("notes", args.notes)
    if args.roster is not None:
        put("roster", [n.strip() for n in args.roster.split(",") if n.strip()])
    if args.breakdown is not None:
        put("breakdown", strip_up(args.breakdown))
    if getattr(args, "source", None) is not None:
        put("source", strip_up(args.source))
    return changes


def cmd_set(args, data: dict) -> int:
    entry, bucket = find_entry(data, args.code)
    if entry is None:
        codes = ", ".join(e.get("code", "?") for e, _ in all_entries(data))
        die(f"проект «{args.code}» не найден. Есть: {codes}")

    before = dumps_crm(data)
    stages = list(data.get("stages", []))
    changes = apply_fields(entry, args, stages)

    moved = False
    stage_now = entry.get("stage")
    if args.close:
        if stage_now not in TERMINAL_STAGES:
            die(
                "--close только для терминальных стадий ({}); сейчас «{}»".format(
                    " / ".join(TERMINAL_STAGES), stage_now
                )
            )
        if bucket == "projects":
            entry.setdefault("closed", today_str())
            if not entry.get("closed"):
                entry["closed"] = today_str()
            data["projects"].remove(entry)
            data["closed"].append(entry)
            moved = True
            changes.append(f"  перенос: projects[] → closed[] (closed={entry['closed']})")
    elif stage_now in TERMINAL_STAGES and bucket == "projects" and args.stage is not None:
        changes.append(
            "  примечание: стадия терминальная, но проект остался в projects[] "
            "(перенос — только с --close, чтобы бот успел увидеть)"
        )

    if not changes:
        print("Нечего менять: переданные поля совпадают с текущими.")
        return 0

    stamp(entry, data)
    after = dumps_crm(data)
    print(f"Проект: {args.code} ({'closed[]' if moved or bucket == 'closed' else 'projects[]'})")
    print("\n".join(changes))
    if args.dry_run:
        print("\n--- dry-run, файл не тронут ---")
        print(unified(before, after, args.crm_path.name))
        return 0
    atomic_write(args.crm_path, after)
    print(f"\nЗаписано: {args.crm_path}")
    print("Дальше: python3 Scripts/crm_tool.py render")
    return 0


def cmd_add(args, data: dict) -> int:
    if not CODE_RE.match(args.code):
        die("код «{}» не подходит под ^[a-z][a-z0-9-]{{1,31}}$".format(args.code))
    existing, bucket = find_entry(data, args.code)
    if existing is not None:
        die(f"код «{args.code}» уже есть в {bucket}[]")
    stages = list(data.get("stages", []))
    stage = args.stage if args.stage is not None else (stages[0] if stages else "считаем")
    if stages and stage not in stages:
        die("стадия «{}» не из списка. Допустимые: {}".format(stage, " · ".join(stages)))
    if args.due and not is_iso_date(args.due):
        die(f"--due ждёт YYYY-MM-DD, получено «{args.due}»")

    breakdown = strip_up(args.breakdown or "")
    project = {
        "code": args.code,
        "name": args.name,
        "client": args.client,
        "country": args.country,
        "scope": args.scope,
        "stage": stage,
        "price": args.price if args.price is not None else "?",
        "next_step": args.next or "",
        "due": args.due or "",
        "breakdown": breakdown,
        "updated_at": now_iso(),
        "updated_by": "vault",
        "roster": [n.strip() for n in (args.roster or "").split(",") if n.strip()],
        "notes": args.notes or "",
    }
    project = {k: project[k] for k in NEW_PROJECT_KEYS if k in project}

    before = dumps_crm(data)
    data["projects"].append(project)
    data["updated_at"] = project["updated_at"]
    if isinstance(data.get("updated"), str) and is_iso_date(data["updated"]):
        data["updated"] = today_str()
    after = dumps_crm(data)

    print(f"Новый проект: {args.code} · стадия «{stage}» · цена {project['price']}")
    if args.dry_run:
        print("\n--- dry-run, файл не тронут ---")
        print(unified(before, after, args.crm_path.name))
        return 0
    atomic_write(args.crm_path, after)
    print(f"Записано: {args.crm_path}")
    print("Дальше: python3 Scripts/crm_tool.py render")
    return 0


def cmd_render(args, data: dict) -> int:
    md_path = args.md_path
    old = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    new, tables_changed = render_md(data, md_path)

    if old == new:
        print(f"md-вид уже актуален: {md_path}")
        return 0
    if args.dry_run:
        print(unified(old, new, md_path.name) or "(изменений нет)")
        print("--- dry-run, файл не тронут ---")
        return 0
    atomic_write(md_path, new)
    print(f"Отрендерено: {md_path}")
    print(
        "Таблицы: {} · строка в «Журнал»: {}".format(
            "изменились" if tables_changed else "без изменений",
            "добавлена" if tables_changed else "нет",
        )
    )
    return 0


def cmd_check(args, data: dict) -> int:
    errors: list[str] = []
    warnings: list[str] = []

    if data.get("schema") != SCHEMA:
        errors.append(f"schema = {data.get('schema')!r}, ожидается {SCHEMA!r}")
    stages = data.get("stages")
    if not isinstance(stages, list) or not stages or not all(isinstance(s, str) for s in stages):
        errors.append("stages: ожидается непустой список строк")
        stages = []
    root_ts = data.get("updated_at")
    if not isinstance(root_ts, str):
        errors.append("корневой updated_at отсутствует")
    else:
        try:
            datetime.fromisoformat(root_ts)
        except ValueError:
            errors.append(f"корневой updated_at не ISO: {root_ts!r}")

    for key in ("projects", "closed"):
        if not isinstance(data.get(key), list):
            errors.append(f"{key}: ожидается список")

    seen: dict[str, str] = {}
    for entry, bucket in all_entries(data):
        if not isinstance(entry, dict):
            errors.append(f"{bucket}[]: элемент не объект")
            continue
        code = entry.get("code", "")
        label = f"{bucket}[{code or '?'}]"
        if not isinstance(code, str) or not CODE_RE.match(code or ""):
            errors.append(f"{label}: код не подходит под ^[a-z][a-z0-9-]{{1,31}}$")
        if code in seen:
            errors.append(f"{label}: дубликат кода (уже в {seen[code]}[])")
        elif code:
            seen[code] = bucket
        if not entry.get("name"):
            warnings.append(f"{label}: пустое name")

        stage = entry.get("stage")
        if stages and stage not in stages:
            msg = f"{label}: стадия {stage!r} не из stages"
            (warnings if bucket == "closed" else errors).append(msg)
        if bucket == "projects" and stage in TERMINAL_STAGES:
            warnings.append(f"{label}: терминальная стадия, но проект ещё в projects[] (ждёт --close)")

        ts = entry.get("updated_at")
        if not isinstance(ts, str):
            errors.append(f"{label}: нет updated_at")
        else:
            try:
                datetime.fromisoformat(ts)
            except ValueError:
                errors.append(f"{label}: updated_at не ISO: {ts!r}")
        if entry.get("updated_by") not in ("vault", "bot"):
            warnings.append(f"{label}: updated_by = {entry.get('updated_by')!r} (ждём vault|bot)")

        due = entry.get("due", "")
        if due and not is_iso_date(due):
            errors.append(f"{label}: due не YYYY-MM-DD: {due!r}")
        if bucket == "closed":
            closed_at = entry.get("closed", "")
            if not closed_at:
                warnings.append(f"{label}: нет даты closed")
            elif not is_iso_date(closed_at):
                errors.append(f"{label}: closed не YYYY-MM-DD: {closed_at!r}")

        roster = entry.get("roster", [])
        if roster is not None and not isinstance(roster, list):
            errors.append(f"{label}: roster не список")

        for key, human in (("breakdown", "брейкдаун"), ("source", "источник")):
            path = entry.get(key, "")
            if path and not (DEPT / str(path).lstrip("/")).exists():
                warnings.append(f"{label}: {human} не найден на диске: {path}")

    md_path = args.md_path
    if not md_path.exists():
        warnings.append(f"нет md-вида: {md_path} (сделает render)")

    print(f"json: {args.crm_path}")
    print(f"проектов: {len(data.get('projects', []))} · закрытых: {len(data.get('closed', []))}")
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ОШИБКА {e}")
    if not errors:
        print("OK: ошибок нет" + (f", предупреждений: {len(warnings)}" if warnings else ""))
    return 1 if errors else 0


# ──────────────────────────────────── CLI ─────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crm_tool.py",
        description="CRM Алины: json — истина, md — вид. json правится только этим скриптом.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--crm", default=str(DEFAULT_CRM), help="путь к crm.json (default: crm/crm.json)")
    parser.add_argument("--md", default=None, help="путь к md-виду (default: crm/*CRM проектов*.md)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="показать воронку")

    p_set = sub.add_parser("set", help="изменить поля проекта")
    p_set.add_argument("--code", required=True)
    p_set.add_argument("--stage")
    p_set.add_argument("--price")
    p_set.add_argument("--next", dest="next", help="следующий шаг")
    p_set.add_argument("--due", help="YYYY-MM-DD или \"\" чтобы очистить")
    p_set.add_argument("--notes")
    p_set.add_argument("--roster", help='"Имя1, Имя2"; "" очищает')
    p_set.add_argument("--name")
    p_set.add_argument("--client")
    p_set.add_argument("--country")
    p_set.add_argument("--scope")
    p_set.add_argument("--breakdown", help="путь относительно infrastructure/Alina/")
    p_set.add_argument("--source", help="путь-источник (для записей в closed[])")
    p_set.add_argument(
        "--close", action="store_true",
        help="перенести в closed[] (только для стадий: " + " / ".join(TERMINAL_STAGES) + ")",
    )
    p_set.add_argument("--dry-run", action="store_true")

    p_add = sub.add_parser("add", help="новый проект в воронку")
    p_add.add_argument("--code", required=True)
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--client", required=True)
    p_add.add_argument("--country", required=True)
    p_add.add_argument("--scope", required=True)
    p_add.add_argument("--stage")
    p_add.add_argument("--price")
    p_add.add_argument("--next", dest="next")
    p_add.add_argument("--due")
    p_add.add_argument("--breakdown")
    p_add.add_argument("--roster")
    p_add.add_argument("--notes")
    p_add.add_argument("--dry-run", action="store_true")

    p_render = sub.add_parser("render", help="пересобрать md-вид из json")
    p_render.add_argument("--dry-run", action="store_true")

    sub.add_parser("check", help="валидация json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.crm_path = Path(args.crm).expanduser()
    args.md_path = find_md(args.crm_path, args.md)
    data = load_crm(args.crm_path)
    handlers = {"list": cmd_list, "set": cmd_set, "add": cmd_add, "render": cmd_render, "check": cmd_check}
    return handlers[args.cmd](args, data)


if __name__ == "__main__":
    sys.exit(main())
