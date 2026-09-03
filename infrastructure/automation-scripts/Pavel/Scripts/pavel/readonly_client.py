"""Read-only инвариант Pavel на уровне транспорта.

`ReadOnlyTelegramClient` перехватывает `TelegramClient.__call__` — единственную
точку, через которую Telethon отправляет TL-запросы, — и пропускает дальше
только запросы из allowlist. Всё остальное (`messages.SendMessage`,
`messages.ReadHistory`, реакции, вступления в чаты, удаления, «печатает…»)
поднимает `ReadOnlyViolation` ДО отправки в сеть.

Проверяется не только сам запрос, но и всё, во что он может быть вложен:
`InvokeWithLayer`/`InitConnection`/`InvokeWithoutUpdates` (поле `query`),
контейнер сообщений (`MessageContainer.messages[*].obj`) и списки запросов
(`client([req1, req2])`). Обёртки-контейнеры сами по себе безвредны —
решает содержимое.
"""

from __future__ import annotations

from typing import Any, Iterator

try:  # телетон нужен в рантайме, но не в юнит-тестах чистой логики
    from telethon import TelegramClient
    from telethon.tl.tlobject import TLRequest
except ImportError:  # pragma: no cover - защитный путь
    TelegramClient = object  # type: ignore[assignment,misc]
    TLRequest = ()  # type: ignore[assignment]


class ReadOnlyViolation(Exception):
    """Попытка отправить запрос вне allowlist. Ничего не ушло в сеть."""


# Полные имена классов запросов: `telethon.tl.functions.<ns>.<Name>Request`.
ALLOWED_REQUESTS: frozenset[str] = frozenset(
    {
        # служебное
        "telethon.tl.functions.help.GetConfigRequest",
        "telethon.tl.functions.help.GetNearestDcRequest",
        "telethon.tl.functions.updates.GetStateRequest",
        # диалоги и история
        "telethon.tl.functions.messages.GetDialogsRequest",
        "telethon.tl.functions.messages.GetPeerDialogsRequest",
        "telethon.tl.functions.messages.GetHistoryRequest",
        "telethon.tl.functions.messages.GetFullChatRequest",
        "telethon.tl.functions.messages.GetChatsRequest",
        "telethon.tl.functions.messages.GetCommonChatsRequest",
        "telethon.tl.functions.messages.SearchRequest",
        # каналы и группы
        "telethon.tl.functions.channels.GetFullChannelRequest",
        "telethon.tl.functions.channels.GetChannelsRequest",
        "telethon.tl.functions.channels.GetParticipantsRequest",
        # люди
        "telethon.tl.functions.users.GetFullUserRequest",
        "telethon.tl.functions.users.GetUsersRequest",
        "telethon.tl.functions.contacts.GetContactsRequest",
        "telethon.tl.functions.contacts.ResolveUsernameRequest",
    }
)

# Логин: весь namespace auth.* (SendCode/SignIn/CheckPassword/ExportAuthorization…).
ALLOWED_NAMESPACE_PREFIXES: tuple[str, ...] = ("telethon.tl.functions.auth.",)

# Обёртки/контейнеры: сами проходят, содержимое проверяется рекурсивно.
WRAPPER_REQUESTS: frozenset[str] = frozenset(
    {
        "telethon.tl.functions.InvokeWithLayerRequest",
        "telethon.tl.functions.InitConnectionRequest",
        "telethon.tl.functions.InvokeWithoutUpdatesRequest",
        "telethon.tl.functions.InvokeAfterMsgRequest",
        "telethon.tl.functions.InvokeAfterMsgsRequest",
    }
)


def request_name(request: Any) -> str:
    """Полное имя класса запроса — ключ allowlist."""
    cls = request if isinstance(request, type) else type(request)
    return f"{cls.__module__}.{cls.__name__}"


def _is_tl_object(obj: Any) -> bool:
    return hasattr(obj, "CONSTRUCTOR_ID") or hasattr(obj, "SUBCLASS_OF_ID")


def iter_nested(request: Any) -> Iterator[Any]:
    """Разворачивает списки, обёртки (`query`/`request`) и MessageContainer."""
    if isinstance(request, (list, tuple, set)):
        for item in request:
            yield from iter_nested(item)
        return

    if not _is_tl_object(request):
        return

    yield request

    for attr in ("query", "request"):
        inner = getattr(request, attr, None)
        if inner is not None and (_is_tl_object(inner) or isinstance(inner, (list, tuple))):
            yield from iter_nested(inner)

    # telethon.tl.core.MessageContainer: .messages -> TLMessage(.obj)
    messages = getattr(request, "messages", None)
    if isinstance(messages, (list, tuple)):
        for item in messages:
            inner = getattr(item, "obj", None)
            if inner is not None:
                yield from iter_nested(inner)
            elif _is_tl_object(item):
                yield from iter_nested(item)


def is_allowed(request: Any) -> bool:
    name = request_name(request)
    if name in ALLOWED_REQUESTS or name in WRAPPER_REQUESTS:
        return True
    return any(name.startswith(prefix) for prefix in ALLOWED_NAMESPACE_PREFIXES)


def assert_readonly(request: Any) -> None:
    """Бросает `ReadOnlyViolation`, если хоть один вложенный запрос вне allowlist."""
    for candidate in iter_nested(request):
        # Проверяем только запросы (TLRequest); ответы/вложенные структуры пропускаем.
        if TLRequest and isinstance(TLRequest, type) and not isinstance(candidate, TLRequest):
            continue
        if not is_allowed(candidate):
            raise ReadOnlyViolation(
                f"Pavel — наблюдатель: запрос {request_name(candidate)} запрещён "
                f"(разрешён только read-only allowlist). В сеть ничего не отправлено."
            )


class ReadOnlyTelegramClient(TelegramClient):  # type: ignore[misc,valid-type]
    """TelegramClient, физически неспособный писать в Telegram."""

    def __call__(self, request, ordered: bool = False, flood_sleep_threshold=None):  # type: ignore[override]
        assert_readonly(request)
        return super().__call__(
            request, ordered=ordered, flood_sleep_threshold=flood_sleep_threshold
        )

    # Явные заглушки на удобные хелперы Telethon: если их когда-нибудь вызовут,
    # ошибка будет читаемой, а не «странным» ReadOnlyViolation из глубины.
    async def send_message(self, *args, **kwargs):  # type: ignore[override]
        raise ReadOnlyViolation("send_message запрещён: Pavel только наблюдает")

    async def send_file(self, *args, **kwargs):  # type: ignore[override]
        raise ReadOnlyViolation("send_file запрещён: Pavel только наблюдает")

    async def send_read_acknowledge(self, *args, **kwargs):  # type: ignore[override]
        raise ReadOnlyViolation("отметка о прочтении запрещена: непрочитанное остаётся непрочитанным")

    async def delete_messages(self, *args, **kwargs):  # type: ignore[override]
        raise ReadOnlyViolation("delete_messages запрещён: Pavel только наблюдает")

    async def download_media(self, *args, **kwargs):  # type: ignore[override]
        raise ReadOnlyViolation("медиа не скачиваются: upload.GetFile вне allowlist")
