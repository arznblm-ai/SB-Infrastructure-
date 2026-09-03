"""Конфигурация Pavel: секреты из env-файла, пороги из config/settings.json.

Секреты живут ТОЛЬКО вне vault: `$PAVEL_ENV` или
`~/.config/second-brain/pavel.env` (на VPS — `/root/.config/...`).
Несекретные пороги — в `config/settings.json` рядом с кодом.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# Корень проекта: .../infrastructure/Pavel/
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.json"
DEFAULT_ENV_PATH = Path.home() / ".config" / "second-brain" / "pavel.env"

SETTINGS_DEFAULTS: dict[str, object] = {
    "max_participants_fetch": 3000,
    "min_messages_for_md": 10,
    "md_tail_limit": 2000,
    "chat_pause_seconds": 1.5,
    "flood_sleep_threshold": 300,
    "vault_root": "/root/second-brain",
    "profile_min_msgs": 20,
    "profile_months": 12,
}


class ConfigError(RuntimeError):
    """Конфигурация неполна или противоречива."""


def _parse_env_file(path: Path) -> dict[str, str]:
    """Минимальный парсер `KEY=value` (поддержка `export`, кавычек, #-комментариев)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_env(env_path: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """Env-файл + переменные процесса (процесс имеет приоритет)."""
    path = Path(env_path) if env_path else Path(os.environ.get("PAVEL_ENV", DEFAULT_ENV_PATH))
    values = _parse_env_file(path)
    for key, value in os.environ.items():
        if key.startswith("PAVEL_"):
            values[key] = value
    return values


def _parse_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    ids: list[int] = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.append(int(chunk))
        except ValueError as exc:  # явная ошибка лучше молчаливого пропуска
            raise ConfigError(f"PAVEL_EXCLUDE_CHAT_IDS: не число — {chunk!r}") from exc
    return ids


@dataclass
class Settings:
    """Несекретные пороги прогона + производные пути vault."""

    max_participants_fetch: int = 3000
    min_messages_for_md: int = 10
    md_tail_limit: int = 2000
    chat_pause_seconds: float = 1.5
    flood_sleep_threshold: int = 300
    vault_root: str = "/root/second-brain"
    profile_min_msgs: int = 20
    profile_months: int = 12

    @property
    def telegram_dir(self) -> Path:
        return Path(self.vault_root) / "transcripts" / "telegram"

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> "Settings":
        settings_path = Path(path) if path else DEFAULT_SETTINGS_PATH
        data = dict(SETTINGS_DEFAULTS)
        if settings_path.is_file():
            loaded = json.loads(settings_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ConfigError(f"{settings_path}: ожидался JSON-объект")
            data.update({k: v for k, v in loaded.items() if k in SETTINGS_DEFAULTS})
        return cls(
            max_participants_fetch=int(data["max_participants_fetch"]),
            min_messages_for_md=int(data["min_messages_for_md"]),
            md_tail_limit=int(data["md_tail_limit"]),
            chat_pause_seconds=float(data["chat_pause_seconds"]),
            flood_sleep_threshold=int(data["flood_sleep_threshold"]),
            vault_root=str(data["vault_root"]),
            profile_min_msgs=int(data["profile_min_msgs"]),
            profile_months=int(data["profile_months"]),
        )


@dataclass
class Config:
    """Полная конфигурация прогона: секреты + пороги."""

    api_id: int | None = None
    api_hash: str | None = None
    phone: str | None = None
    session: str = ""
    exclude_chat_ids: list[int] = field(default_factory=list)
    sheets_webhook_url: str | None = None
    sheets_webhook_secret: str | None = None
    state_dir: Path = Path.home() / ".local" / "state" / "pavel"
    settings: Settings = field(default_factory=Settings)

    @property
    def db_path(self) -> Path:
        return self.state_dir / "pavel.db"

    def require_api(self) -> tuple[int, str]:
        """api_id/api_hash обязательны для любой сетевой команды."""
        if not self.api_id or not self.api_hash:
            raise ConfigError(
                "Нет PAVEL_API_ID / PAVEL_API_HASH. "
                f"Заполни env-файл ({os.environ.get('PAVEL_ENV', DEFAULT_ENV_PATH)})."
            )
        return self.api_id, self.api_hash


def load_config(
    env_path: str | os.PathLike[str] | None = None,
    settings_path: str | os.PathLike[str] | None = None,
) -> Config:
    env = load_env(env_path)
    settings = Settings.load(settings_path)

    api_id_raw = env.get("PAVEL_API_ID")
    try:
        api_id = int(api_id_raw) if api_id_raw else None
    except ValueError as exc:
        raise ConfigError(f"PAVEL_API_ID: не число — {api_id_raw!r}") from exc

    state_dir = Path(env.get("PAVEL_STATE_DIR") or (Path.home() / ".local" / "state" / "pavel"))
    session = env.get("PAVEL_SESSION") or str(state_dir / "pavel.session")

    return Config(
        api_id=api_id,
        api_hash=env.get("PAVEL_API_HASH") or None,
        phone=env.get("PAVEL_PHONE") or None,
        session=session,
        exclude_chat_ids=_parse_ids(env.get("PAVEL_EXCLUDE_CHAT_IDS")),
        sheets_webhook_url=env.get("PAVEL_SHEETS_WEBHOOK_URL") or None,
        sheets_webhook_secret=env.get("PAVEL_SHEETS_WEBHOOK_SECRET") or None,
        state_dir=state_dir,
        settings=settings,
    )
