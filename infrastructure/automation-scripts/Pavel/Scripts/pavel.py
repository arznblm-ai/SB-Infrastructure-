#!/usr/bin/env python3
"""Pavel — read-only наблюдатель Telegram-нетворка (CLI ядра, T1).

Команды: login, resolve, scan-meta, backfill, export-md.
Команды слоя знаний (profile / push-sheets / report / run) подключаются
модулем `pavel.cli_knowledge`, если он присутствует.

Запускать только на VPS (единственный писатель состояния).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pavel import collectors, db as dbmod  # noqa: E402
from pavel.config import ConfigError, load_config  # noqa: E402
from pavel.exporter import export_md  # noqa: E402
from pavel.readonly_client import ReadOnlyViolation  # noqa: E402

log = logging.getLogger("pavel")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _require_exclude(config, args) -> None:
    """Команды, читающие текст, не работают с пустым blacklist без явного флага."""
    if config.exclude_chat_ids or getattr(args, "allow_empty_exclude", False):
        return
    raise SystemExit(
        "PAVEL_EXCLUDE_CHAT_IDS пуст — отказываюсь читать текст чатов.\n"
        "Найди чат(ы) для исключения: python3 Scripts/pavel.py resolve --query \"<имя>\"\n"
        "и впиши id в PAVEL_EXCLUDE_CHAT_IDS в env-файле.\n"
        "Если исключений действительно нет — передай --allow-empty-exclude осознанно."
    )


# ------------------------------------------------------------------ commands


def cmd_login(args) -> int:
    config = load_config(args.env, args.settings)
    client = collectors.make_client(config)

    async def _run() -> None:
        await client.start(phone=config.phone)  # type: ignore[union-attr]
        me = await client.get_me()
        print(f"Авторизован: {getattr(me, 'first_name', '')} @{getattr(me, 'username', '')} (id {me.id})")
        print(f"Сессия: {config.session}")

    with client:
        client.loop.run_until_complete(_run())
    return 0


def cmd_resolve(args) -> int:
    config = load_config(args.env, args.settings)
    client = collectors.make_client(config)

    async def _run() -> None:
        await client.connect()
        if not await client.is_user_authorized():
            raise SystemExit("Сессия не авторизована — сначала `login`.")
        found = await collectors.resolve_candidates(client, args.query)
        if not found:
            print("Ничего не найдено.")
            return
        print(f"{'id':>14}  {'тип':<11} {'@username':<20} title")
        for item in found:
            print(
                f"{item['id']:>14}  {item['type']:<11} "
                f"{('@' + item['username']) if item['username'] else '—':<20} {item['title']}"
            )
        print("\nId нужного чата впиши в PAVEL_EXCLUDE_CHAT_IDS (через запятую).")

    with client:
        client.loop.run_until_complete(_run())
    return 0


def cmd_scan_meta(args) -> int:
    config = load_config(args.env, args.settings)
    conn = dbmod.connect(config.db_path)
    client = collectors.make_client(config)

    async def _run() -> None:
        await client.connect()
        if not await client.is_user_authorized():
            raise SystemExit("Сессия не авторизована — сначала `login`.")
        stats = await collectors.scan_meta(client, conn, config.settings, config.exclude_chat_ids)
        print(json.dumps(stats, ensure_ascii=False, indent=2))

    with client:
        client.loop.run_until_complete(_run())
    conn.close()
    return 0


def cmd_backfill(args) -> int:
    config = load_config(args.env, args.settings)
    _require_exclude(config, args)
    conn = dbmod.connect(config.db_path)
    client = collectors.make_client(config)

    async def _run() -> None:
        await client.connect()
        if not await client.is_user_authorized():
            raise SystemExit("Сессия не авторизована — сначала `login`.")
        stats = await collectors.backfill(
            client,
            conn,
            config.settings,
            config.exclude_chat_ids,
            months=args.months,
            full=args.full,
        )
        print(json.dumps(stats, ensure_ascii=False, indent=2))

    with client:
        client.loop.run_until_complete(_run())
    conn.close()
    return 0


def cmd_export_md(args) -> int:
    config = load_config(args.env, args.settings)
    conn = dbmod.connect(config.db_path)
    result = export_md(conn, config.settings, target_dir=args.out)
    print(
        json.dumps(
            {
                "files": len(result["written"]),
                "appended": sum(item["appended"] for item in result["written"]),
                "skipped_below_threshold": len(result["skipped"]),
                "index": result["index"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    conn.close()
    return 0


# --------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pavel",
        description="Pavel — read-only наблюдатель Telegram-нетворка.",
    )
    parser.add_argument("--env", help="Путь к env-файлу (по умолчанию $PAVEL_ENV)")
    parser.add_argument("--settings", help="Путь к config/settings.json")
    parser.add_argument("-v", "--verbose", action="store_true")

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_login = subparsers.add_parser("login", help="Интерактивная авторизация user-сессии")
    p_login.set_defaults(func=cmd_login)

    p_resolve = subparsers.add_parser("resolve", help="Найти чат для PAVEL_EXCLUDE_CHAT_IDS")
    p_resolve.add_argument("--query", required=True, help="Часть имени или @username")
    p_resolve.set_defaults(func=cmd_resolve)

    p_scan = subparsers.add_parser("scan-meta", help="Метаданные нетворка (без текста, без LLM)")
    p_scan.set_defaults(func=cmd_scan_meta)

    p_backfill = subparsers.add_parser("backfill", help="Текст сообщений в sqlite (инкрементально)")
    p_backfill.add_argument("--months", type=int, default=12)
    p_backfill.add_argument("--full", action="store_true", help="Вся история (второй этап)")
    p_backfill.add_argument(
        "--allow-empty-exclude",
        action="store_true",
        help="Осознанно читать текст при пустом PAVEL_EXCLUDE_CHAT_IDS",
    )
    p_backfill.set_defaults(func=cmd_backfill)

    p_export = subparsers.add_parser("export-md", help="sqlite → transcripts/telegram/")
    p_export.add_argument("--out", help="Каталог назначения (по умолчанию из settings)")
    p_export.set_defaults(func=cmd_export_md)

    # Слой знаний (T2) — подключается, если модуль существует.
    try:
        from pavel.cli_knowledge import register  # type: ignore

        register(subparsers)
    except ImportError:
        pass

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    try:
        return int(args.func(args) or 0)
    except ReadOnlyViolation as exc:
        log.error("READ-ONLY: %s", exc)
        return 3
    except ConfigError as exc:
        log.error("Конфигурация: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
