#!/usr/bin/env python3
"""Оркестратор ежедневного финансового синка (запускается systemd-таймером на VPS).

Порядок: pull_planfact -> pull_zenmoney -> pull_crm_topic -> render_dashboard.
Падение одного шага не останавливает остальные; итоговый код возврата
ненулевой, если хоть один шаг завершился ошибкой. Шаг, помеченный как
пропущенный (код 3), фейлом не считается — это штатные состояния:
нет токена, каркас без эндпоинтов, а для render — ещё не создан
data/model.json (тогда рендерить нечего, но таймер на VPS не должен
рапортовать ошибку). Битый или невалидный model.json — уже FAIL.

Примеры:
    python3 sync_daily.py                 # dry-run: без сети и без записи
    python3 sync_daily.py --live          # боевой прогон
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import common
from common import EXIT_ERROR, EXIT_OK, EXIT_SKIPPED

#: шаги в порядке исполнения: (имя, файл скрипта, доп. аргументы)
STEPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("planfact", "pull_planfact.py", ()),
    ("zenmoney", "pull_zenmoney.py", ()),
    ("crm", "pull_crm_topic.py", ()),
    ("render", "render_dashboard.py", ()),
)

STATUS = {EXIT_OK: "OK", EXIT_SKIPPED: "SKIP", EXIT_ERROR: "FAIL"}

log = common.get_logger("sync")


def run_step(name: str, script: str, extra: tuple[str, ...], passthrough: list[str]) -> int:
    """Запускает шаг отдельным процессом, чтобы его падение не унесло оркестратор."""
    path = common.SCRIPTS_DIR / script
    if not path.is_file():
        log.error("[%s] скрипт не найден: %s", name, path)
        return EXIT_ERROR
    cmd = [sys.executable, str(path), *extra, *passthrough]
    log.info("[%s] старт: %s", name, " ".join(cmd[1:]))
    try:
        proc = subprocess.run(cmd, cwd=str(common.SCRIPTS_DIR), check=False)
    except Exception as exc:
        log.error("[%s] не удалось запустить: %s", name, exc)
        return EXIT_ERROR
    code = proc.returncode
    log.info("[%s] финиш: %s (код %s)", name, STATUS.get(code, "FAIL"), code)
    return code if code in STATUS else EXIT_ERROR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ежедневный финансовый синк (оркестратор)")
    common.add_common_args(parser)
    parser.add_argument("--only", action="append", choices=[s[0] for s in STEPS],
                        help="запустить только указанные шаги (можно повторять)")
    parser.add_argument("--skip", action="append", choices=[s[0] for s in STEPS],
                        help="пропустить указанные шаги (можно повторять)")
    args = parser.parse_args(argv)
    common.apply_common_args(args, log)

    # флаги, которые пробрасываем каждому шагу как есть
    passthrough: list[str] = []
    if args.live:
        passthrough.append("--live")
    if args.date:
        passthrough += ["--date", args.date]
    if args.data_dir:
        passthrough += ["--data-dir", str(Path(args.data_dir))]
    if args.verbose:
        passthrough.append("--verbose")

    results: dict[str, int] = {}
    for name, script, extra in STEPS:
        if args.only and name not in args.only:
            continue
        if args.skip and name in args.skip:
            log.info("[%s] пропущен по --skip", name)
            continue
        results[name] = run_step(name, script, extra, passthrough)

    failed = [n for n, c in results.items() if c == EXIT_ERROR]
    summary = " · ".join(f"{n}={STATUS.get(c, 'FAIL')}" for n, c in results.items())
    log.info("Итог: %s", summary or "шагов не было")
    if failed:
        log.error("Шаги с ошибкой: %s", ", ".join(failed))
        return EXIT_ERROR
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
