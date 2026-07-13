"""task_owner.py — классификация исполнителя задачи из встречи.

Общий модуль для meeting_notes.py и telegram_codex_bot.py (оба лежат в этом
каталоге и запускаются по абсолютному пути, поэтому обычный import работает).

classify_owner(who) -> "me" | "other" | "unknown"
"me" — задача Антона (владельца vault), только такие попадают в /todo автоматически.
"""

import re

# Нормализованные формы имени Антона. Сравнение — по полной нормализованной
# строке, без substring-match: «Андрей Поляков» или «Антон Иванов» не заденет.
ME_ALIASES = {
    "антон",
    "anton",
    "антон розенблюм",
    "антон розенблум",
    "anton rozenblum",
    "anton rosenblum",
    "розенблюм",
    "розенблум",
    "rozenblum",
    "rosenblum",
}

_UNKNOWN_VALUES = {"", "unknown", "не назван", "не названо", "неизвестно", "участник"}
_SPEAKER_RE = re.compile(r"^(speaker|спикер)\s*\d*$")


def normalize_who(value: str) -> str:
    lowered = (value or "").lower().replace("ё", "е")
    lowered = re.sub(r"[^a-zа-я0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def classify_owner(who: str) -> str:
    normalized = normalize_who(who)
    if normalized in _UNKNOWN_VALUES or _SPEAKER_RE.match(normalized):
        return "unknown"
    if normalized in ME_ALIASES:
        return "me"
    return "other"
