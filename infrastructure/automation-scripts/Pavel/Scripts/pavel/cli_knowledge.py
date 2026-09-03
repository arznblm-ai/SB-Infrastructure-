"""Подкоманды слоя знаний: `profile`, `push-sheets`, `report`, `run`.

Подключается из `Scripts/pavel.py` (T1) хуком `register(subparsers)`; каждая
подкоманда получает `func(args)` и сама открывает конфиг и соединение —
как остальные команды CLI.

Ядро T1 (`pavel.collectors`, `pavel.exporter`) импортируется **лениво** внутри
`run`: без него `profile`, `push-sheets` и `report` продолжают работать по
готовой базе, а `run` завершается понятной ошибкой, а не ImportError'ом.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import profiler, report as report_mod, sheets as sheets_mod
from .config import load_config

T1_MISSING = (
    "Ядро Pavel (T1) недоступно: {what}. "
    "Команда `run` вызывает scan-meta/backfill/export-md из pavel.collectors и "
    "pavel.exporter; без них доступны только profile, push-sheets и report."
)


# ── общее ───────────────────────────────────────────────────────────────────

def _config(args):
    return load_config(getattr(args, "env", None), getattr(args, "settings", None))


def _connect(config):
    from . import db as db_mod  # noqa: PLC0415 — ядро T1, импорт по месту

    return db_mod.connect(config.db_path)


def _change_recorder(conn):
    """Адаптер под сигнатуру profiler.run_profile → db.record_change(conn, ...)."""
    from . import db as db_mod  # noqa: PLC0415

    def record(run_id=None, kind="", user_id=None, chat_id=None, detail=None):
        if run_id is None:  # без run_id запись в changes бессмысленна
            return None
        return db_mod.record_change(conn, run_id, kind, user_id=user_id, chat_id=chat_id, detail=detail)

    return record


def _outputs_dir(config, args) -> Path:
    explicit = getattr(args, "outputs_dir", None)
    if explicit:
        return Path(explicit)
    vault_root = getattr(config.settings, "vault_root", None)
    if vault_root and Path(vault_root).is_dir():
        return Path(vault_root) / "infrastructure" / "Pavel" / "outputs"
    return report_mod.DEFAULT_OUTPUTS_DIR


# ── команды ─────────────────────────────────────────────────────────────────

def _do_profile(conn, config, args) -> dict:
    return profiler.run_profile(
        conn,
        settings=config.settings,
        run_id=getattr(args, "run_id", None),
        min_msgs=getattr(args, "min_msgs", None),
        only_new=bool(getattr(args, "only_new", False)),
        limit=getattr(args, "limit", None),
        months=getattr(args, "months", None),
        record_change=_change_recorder(conn),
    )


def cmd_profile(args) -> int:
    config = _config(args)
    conn = _connect(config)
    try:
        stats = _do_profile(conn, config, args)
    finally:
        conn.close()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def _do_push_sheets(conn, config, args) -> dict:
    return sheets_mod.push_sheets(
        conn,
        config.settings,
        config,
        run_id=getattr(args, "run_id", None),
        dry_run=bool(getattr(args, "dry_run", False)),
    )


def cmd_push_sheets(args) -> int:
    config = _config(args)
    conn = _connect(config)
    try:
        stats = _do_push_sheets(conn, config, args)
    finally:
        conn.close()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def _do_report(conn, config, args):
    return report_mod.write_report(
        conn,
        run_id=getattr(args, "run_id", None),
        outputs_dir=_outputs_dir(config, args),
    )


def cmd_report(args) -> int:
    config = _config(args)
    conn = _connect(config)
    try:
        md_path, json_path = _do_report(conn, config, args)
    finally:
        conn.close()
    print(json.dumps({"report": str(md_path), "network": str(json_path)}, ensure_ascii=False, indent=2))
    return 0


def cmd_run(args) -> int:
    """scan-meta → backfill (инкремент) → export-md → profile --only-new → push-sheets → report."""
    try:
        from . import collectors, exporter  # noqa: PLC0415
    except ImportError as exc:
        print(T1_MISSING.format(what=exc), file=sys.stderr)
        return 2
    missing = [
        name
        for name, module in (("scan_meta", collectors), ("backfill", collectors), ("export_md", exporter))
        if not hasattr(module, name)
    ]
    if missing:
        print(T1_MISSING.format(what="нет функций " + ", ".join(missing)), file=sys.stderr)
        return 2

    from . import db as db_mod  # noqa: PLC0415

    config = _config(args)
    if not config.exclude_chat_ids and not getattr(args, "allow_empty_exclude", False):
        print(
            "PAVEL_EXCLUDE_CHAT_IDS пуст: `run` читает текст чатов. "
            "Определи чаты Ралины (`resolve --query`) или передай --allow-empty-exclude.",
            file=sys.stderr,
        )
        return 2

    conn = _connect(config)
    stats: dict[str, Any] = {}
    try:
        run_id = db_mod.start_run(conn, "run")
        args.run_id = run_id
        args.only_new = True

        client = collectors.make_client(config)

        async def _collect() -> None:
            await client.connect()
            if not await client.is_user_authorized():
                raise SystemExit("Сессия не авторизована — сначала `login`.")
            stats["scan_meta"] = await collectors.scan_meta(
                client, conn, config.settings, config.exclude_chat_ids
            )
            stats["backfill"] = await collectors.backfill(
                client,
                conn,
                config.settings,
                config.exclude_chat_ids,
                months=getattr(args, "months", None) or 12,
                full=False,
            )

        with client:
            client.loop.run_until_complete(_collect())

        export = exporter.export_md(conn, config.settings)
        stats["export_md"] = {
            "files": len(export["written"]),
            "appended": sum(item["appended"] for item in export["written"]),
        }
        stats["profile"] = _do_profile(conn, config, args)
        if getattr(args, "skip_sheets", False):
            stats["push_sheets"] = {"skipped": True}
        else:
            stats["push_sheets"] = _do_push_sheets(conn, config, args)

        db_mod.finish_run(conn, run_id, stats)
        md_path, json_path = _do_report(conn, config, args)
        stats["report"] = str(md_path)
        stats["network"] = str(json_path)
    finally:
        conn.close()

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


# ── регистрация ─────────────────────────────────────────────────────────────

def register(subparsers) -> None:
    """Добавляет подкоманды слоя знаний в общий CLI Pavel."""
    p_profile = subparsers.add_parser(
        "profile", help="LLM-профилирование активных контактов (claude -p, без инструментов)"
    )
    profiler.add_arguments(p_profile)
    p_profile.add_argument("--run-id", type=int, default=None, dest="run_id")
    p_profile.set_defaults(func=cmd_profile)

    p_sheets = subparsers.add_parser(
        "push-sheets", help="выгрузка в Google-таблицу «Anton Telegram» через Apps Script веб-хук"
    )
    p_sheets.add_argument("--dry-run", action="store_true", help="только посчитать строки, не отправлять")
    p_sheets.add_argument("--run-id", type=int, default=None, dest="run_id")
    p_sheets.set_defaults(func=cmd_push_sheets)

    p_report = subparsers.add_parser("report", help="отчёт о прогоне + outputs/network.json")
    p_report.add_argument("--run-id", type=int, default=None, dest="run_id")
    p_report.add_argument("--outputs-dir", default=None, dest="outputs_dir")
    p_report.set_defaults(func=cmd_report)

    p_run = subparsers.add_parser(
        "run", help="полный прогон: scan-meta → backfill → export-md → profile → push-sheets → report"
    )
    p_run.add_argument("--months", type=int, default=12)
    p_run.add_argument("--limit", type=int, default=None)
    p_run.add_argument("--min-msgs", type=int, default=None)
    p_run.add_argument("--skip-sheets", action="store_true", help="не трогать таблицу (нет веб-хука)")
    p_run.add_argument(
        "--allow-empty-exclude",
        action="store_true",
        help="осознанно читать текст при пустом PAVEL_EXCLUDE_CHAT_IDS",
    )
    p_run.set_defaults(func=cmd_run, run_id=None, only_new=True, dry_run=False, outputs_dir=None)
