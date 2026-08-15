#!/usr/bin/env python3
"""Забор новых сообщений топика «CRM» -> data/crm/YYYY-MM-DD.json.

КАРКАС. Механика чтения НЕ решена (нерешённый вопрос №3 план-файла):
инфраструктура Гермеса или отдельный бот-токен. Пока фиксируем контракт
и место вставки.

Что делает шаг в готовом виде: забирает из топика «CRM» чата Гермеса
сообщения, появившиеся с прошлого прогона (offset в data/crm/state.json),
и складывает их сырьём в датированный снапшот. Разбор голосовых наговоров
в план-слой модели (`claude -p`) — отдельный шаг, и только при наличии
новых сообщений (cost gate).

По умолчанию dry-run: без сети и без записи.
"""

from __future__ import annotations

import argparse

import common
from common import EXIT_ERROR, EXIT_OK, EXIT_SKIPPED

SOURCE = "crm"

# Кандидаты имён секретов; какой набор реально используем — решится вместе с механикой.
SECRET_CANDIDATES = ("TG_BOT_TOKEN", "TG_CRM_CHAT_ID", "TG_CRM_TOPIC_ID")

log = common.get_logger("crm")


def read_state(data_dir=None) -> dict:
    """Состояние: {"last_message_id": N, "offset": N, "updated_at": ...}."""
    return common.read_json(common.state_path(SOURCE, data_dir), default={}) or {}


def fetch_new_messages(env: dict, state: dict) -> list[dict]:
    """Читает новые сообщения топика.

    TODO(crm): выбрать механику и реализовать один из вариантов —
      A. Bot API getUpdates отдельным ботом: нужен свой токен + свой offset;
         конфликтует с любым другим полером того же бота (грабли из памяти).
      B. Чтение из хранилища Гермеса (его лог привязанных чатов/топиков):
         без второго полера, но нужен доступ к его состоянию на VPS.
      C. Экспорт через уже работающий inbox-бот с фильтром по message_thread_id.
    Решение — вопрос к /matt; до него шаг возвращает SKIPPED.

    Возврат: список сообщений в сыром виде (dict как отдаёт источник),
    отсортированный по возрастанию id; фильтр по message_thread_id топика «CRM».
    """
    raise NotImplementedError("Механика чтения CRM-топика не выбрана (см. TODO выше)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Синк CRM-топика -> data/crm/YYYY-MM-DD.json")
    common.add_common_args(parser)
    args = parser.parse_args(argv)
    common.apply_common_args(args, log)

    env = common.load_env()
    missing = [name for name in SECRET_CANDIDATES if not common.get_secret(name, env, required=False)]
    if missing:
        log.warning("Нет кредов %s — шаг пропущен (каркас, механика не выбрана)", missing)
        return EXIT_SKIPPED

    state = read_state(args.data_dir)
    log.info("Последний обработанный message_id: %s", state.get("last_message_id", "—"))

    if not args.live:
        log.info("DRY-RUN: сообщения не запрашивались")
        log.info("DRY-RUN: записал бы %s", common.snapshot_path(SOURCE, args.date, args.data_dir))
        return EXIT_OK

    try:
        messages = fetch_new_messages(env, state)
    except NotImplementedError as exc:
        log.warning("Каркас не дореализован: %s", exc)
        return EXIT_SKIPPED
    except Exception as exc:
        log.error("Чтение CRM-топика не удалось: %s", exc)
        return EXIT_ERROR

    if not messages:
        log.info("Новых сообщений нет — снапшот не пишем (cost gate: парсер не запускается)")
        return EXIT_OK

    common.write_snapshot(SOURCE, messages, date=args.date, data_dir=args.data_dir,
                          meta={"count": len(messages)})
    common.write_json(
        common.state_path(SOURCE, args.data_dir),
        {"last_message_id": messages[-1].get("message_id"), "updated_at": common.now_iso()},
    )
    # TODO(crm): здесь же (или отдельным шагом) — разбор в план-слой data/model.json
    # через `claude -p`, строго при непустом messages.
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
