#!/usr/bin/env python3
"""Забор личных финансов из ZenMoney (api.zenmoney.ru/v8/diff/).

Рабочий код: инкрементальная синхронизация через serverTimestamp.
Первый прогон (нет state.json) тянет serverTimestamp=0 — максимальную
доступную историю (решение Антона, план-файл 2026-08-14).

Сеть вызывается ТОЛЬКО с флагом --live. По умолчанию dry-run:
печатает план запроса (с замаскированным токеном) и выходит.

Примеры:
    python3 pull_zenmoney.py                     # dry-run, ничего не трогает
    python3 pull_zenmoney.py --live              # инкремент от state.json
    python3 pull_zenmoney.py --live --full       # переимпорт всей истории
"""

from __future__ import annotations

import argparse
import time

import common
from common import EXIT_ERROR, EXIT_OK, EXIT_SKIPPED

API_URL = "https://api.zenmoney.ru/v8/diff/"
SOURCE = "zenmoney"

#: ключи ответа, которые нам интересны в модели (остальное сохраняем как есть)
PAYLOAD_KEYS = ("transaction", "account", "tag", "instrument", "merchant", "budget")

log = common.get_logger("zenmoney")


def read_state(data_dir=None) -> dict:
    """Состояние инкремента: {"serverTimestamp": N, "updated_at": ...}."""
    return common.read_json(common.state_path(SOURCE, data_dir), default={}) or {}


def build_request(server_timestamp: int) -> dict:
    """Тело diff-запроса. currentClientTimestamp — unix-время клиента."""
    return {
        "currentClientTimestamp": int(time.time()),
        "serverTimestamp": int(server_timestamp),
    }


def fetch_diff(token: str, server_timestamp: int, timeout: int = 120) -> dict:
    """Живой POST к diff-эндпоинту. Вызывается только в --live."""
    payload = build_request(server_timestamp)
    log.info("POST %s (serverTimestamp=%s)", API_URL, server_timestamp)
    data = common.http_post_json(
        API_URL,
        payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    if not isinstance(data, dict):
        raise RuntimeError(f"Неожиданный ответ ZenMoney: {type(data).__name__}")
    return data


def summarize(data: dict) -> dict:
    """Короткая сводка ответа для логов и meta снапшота."""
    return {
        "serverTimestamp": data.get("serverTimestamp"),
        "counts": {k: len(data.get(k) or []) for k in PAYLOAD_KEYS if k in data},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Синк ZenMoney -> data/zenmoney/YYYY-MM-DD.json")
    common.add_common_args(parser)
    parser.add_argument("--full", action="store_true",
                        help="игнорировать state.json и тянуть всю историю (serverTimestamp=0)")
    args = parser.parse_args(argv)
    common.apply_common_args(args, log)

    env = common.load_env()
    token = common.get_secret("ZENMONEY_TOKEN", env, required=False)
    if not token:
        log.warning("ZENMONEY_TOKEN не найден — шаг пропущен")
        return EXIT_SKIPPED

    state = read_state(args.data_dir)
    last_ts = 0 if args.full else int(state.get("serverTimestamp") or 0)
    mode = "полная история" if last_ts == 0 else f"инкремент от {last_ts}"
    log.info("Токен: %s · режим: %s", common.mask(token), mode)

    if not args.live:
        log.info("DRY-RUN: запрос не отправлен. Тело: %s", build_request(last_ts))
        log.info("DRY-RUN: записал бы %s", common.snapshot_path(SOURCE, args.date, args.data_dir))
        return EXIT_OK

    try:
        data = fetch_diff(token, last_ts)
    except Exception as exc:  # сеть/401/5xx — не роняем весь синк, отдаём код ошибки
        log.error("Запрос к ZenMoney не удался: %s", exc)
        return EXIT_ERROR

    meta = summarize(data)
    log.info("Получено: %s", meta["counts"])
    common.write_snapshot(SOURCE, data, date=args.date, data_dir=args.data_dir, meta=meta)

    new_ts = data.get("serverTimestamp")
    if isinstance(new_ts, int) and new_ts > 0:
        common.write_json(
            common.state_path(SOURCE, args.data_dir),
            {"serverTimestamp": new_ts, "updated_at": common.now_iso(),
             "last_mode": "full" if last_ts == 0 else "incremental"},
        )
        log.info("state.json обновлён: serverTimestamp=%s", new_ts)
    else:
        log.warning("В ответе нет валидного serverTimestamp — state.json не тронут")

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
