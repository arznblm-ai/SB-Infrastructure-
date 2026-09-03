"""Pavel — read-only наблюдатель Telegram-нетворка.

Пакет ядра (T1): конфигурация, read-only клиент, sqlite-состояние,
коллекторы метаданных/сообщений и экспорт в markdown vault.

Инвариант проекта: клиент физически не может писать в Telegram —
см. `readonly_client.ReadOnlyTelegramClient`.
"""

__all__ = ["config", "readonly_client", "db", "collectors", "exporter"]
