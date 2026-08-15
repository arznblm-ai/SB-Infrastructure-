"""Общая инфраструктура скриптов департамента Financial Coach.

Здесь живёт всё, что нужно каждому pull_*/render-скрипту:
пути департамента, чтение env-файла с секретами, датированные снапшоты,
логирование в stderr и минимальный HTTP-клиент.

Секреты в код не зашиваются: только чтение из ~/.config/second-brain/finance.env
(или из os.environ — так работает systemd EnvironmentFile на VPS).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# --- пути -------------------------------------------------------------------

# Считаем от расположения файла, а не от cwd: одинаково работает
# на маке (~/AI AGENT FOLDER/Second Brain/...) и на VPS (/root/second-brain/...).
SCRIPTS_DIR = Path(__file__).resolve().parent
DEPT_ROOT = SCRIPTS_DIR.parent
DATA_DIR = DEPT_ROOT / "data"
DASHBOARD_DIR = DEPT_ROOT / "dashboard"
MEMORY_DIR = DEPT_ROOT / "memory"
REFERENCES_DIR = DEPT_ROOT / "references"

#: единый вход детерминированного рендера (см. render_dashboard.py)
MODEL_PATH = DATA_DIR / "model.json"

#: постоянное правило Антона: копия деливерабла на десктоп
VIBECODE_OUT = Path.home() / "Desktop" / "Vibecode OUT"

#: файл с токенами; путь переопределяется переменной FINANCE_ENV
DEFAULT_ENV_PATH = Path.home() / ".config" / "second-brain" / "finance.env"

#: известные источники снапшотов -> подкаталог в data/
SOURCES = ("planfact", "zenmoney", "crm")

# --- коды возврата (общие для всех шагов) -----------------------------------

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_SKIPPED = 3  # шаг не настроен (нет токена / каркас без эндпоинтов) — не фейл


# --- логирование ------------------------------------------------------------


def get_logger(name: str) -> logging.Logger:
    """Логгер в stderr (stdout оставляем под машиночитаемый вывод)."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
                              datefmt="%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(handler)
    logger.setLevel(os.environ.get("FINANCE_LOG_LEVEL", "INFO").upper())
    logger.propagate = False
    return logger


log = get_logger("common")


# --- env / секреты ----------------------------------------------------------


def load_env(path: Path | str | None = None) -> dict[str, str]:
    """Читает env-файл вида KEY=VALUE.

    Комментарии (#), пустые строки и префикс `export ` игнорируются,
    обрамляющие кавычки снимаются. Значения из os.environ имеют приоритет
    (на VPS их подставляет systemd EnvironmentFile).
    """
    env_path = Path(path or os.environ.get("FINANCE_ENV") or DEFAULT_ENV_PATH).expanduser()
    values: dict[str, str] = {}
    if env_path.is_file():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                values[key] = value
    else:
        log.debug("env-файл не найден: %s (работаем только на os.environ)", env_path)

    for key in list(values) + [k for k in os.environ if k.startswith(("ZENMONEY_", "PLANFACT_", "TG_"))]:
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def get_secret(name: str, env: dict[str, str] | None = None, required: bool = True) -> str | None:
    """Достаёт токен по имени. При required=False возвращает None вместо ошибки."""
    env = env if env is not None else load_env()
    value = env.get(name) or os.environ.get(name)
    if value:
        return value
    if required:
        raise RuntimeError(
            f"Не найден секрет {name}. Добавь строку {name}=... в {DEFAULT_ENV_PATH} "
            f"(или пробрось через окружение)."
        )
    return None


def mask(secret: str | None) -> str:
    """Маска токена для логов: никогда не печатаем значение целиком."""
    if not secret:
        return "<нет>"
    return f"{secret[:4]}…{secret[-2:]} (len={len(secret)})"


# --- json / снапшоты --------------------------------------------------------


def today_str(value: str | None = None) -> str:
    """YYYY-MM-DD: переданная дата (с валидацией) либо сегодня."""
    if value:
        _date.fromisoformat(value)  # бросит ValueError на кривом формате
        return value
    return _date.today().isoformat()


def shift_days(date: str, days: int) -> str:
    """Сдвиг даты YYYY-MM-DD на N дней (минус — назад)."""
    return (_date.fromisoformat(date) + timedelta(days=days)).isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path | str, default: Any = None) -> Any:
    """Мягкое чтение JSON: нет файла или битый — возвращаем default."""
    p = Path(path)
    if not p.is_file():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.warning("Битый JSON %s: %s", p, exc)
        return default


def write_json(path: Path | str, payload: Any) -> Path:
    """Атомарная запись JSON (tmp в том же каталоге + os.replace)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
            fh.write("\n")
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return p


def snapshot_path(source: str, date: str | None = None, data_dir: Path | str | None = None) -> Path:
    """Путь датированного снапшота: <data>/<source>/YYYY-MM-DD.json."""
    if source not in SOURCES:
        log.warning("Неизвестный источник снапшота: %s", source)
    base = Path(data_dir) if data_dir else DATA_DIR
    return base / source / f"{today_str(date)}.json"


def state_path(source: str, data_dir: Path | str | None = None) -> Path:
    """Путь файла инкрементального состояния источника."""
    base = Path(data_dir) if data_dir else DATA_DIR
    return base / source / "state.json"


def write_snapshot(
    source: str,
    payload: Any,
    date: str | None = None,
    data_dir: Path | str | None = None,
    meta: dict[str, Any] | None = None,
) -> Path:
    """Пишет снапшот источника за дату, оборачивая данные в конверт с мета-инфой."""
    path = snapshot_path(source, date, data_dir)
    envelope = {
        "source": source,
        "date": today_str(date),
        "fetched_at": now_iso(),
        "meta": meta or {},
        "data": payload,
    }
    write_json(path, envelope)
    log.info("Снапшот записан: %s", path)
    return path


def latest_snapshot(source: str, data_dir: Path | str | None = None) -> Path | None:
    """Самый свежий датированный снапшот источника (по имени файла)."""
    base = (Path(data_dir) if data_dir else DATA_DIR) / source
    if not base.is_dir():
        return None
    files = sorted(p for p in base.glob("*.json") if p.name != "state.json")
    return files[-1] if files else None


# --- HTTP -------------------------------------------------------------------


def http_get_json(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
) -> Any:
    """GET -> распарсенный JSON-ответ.

    Как и http_post_json: requests, если он есть, иначе stdlib urllib.
    Внимание: на маке Антона стандартный urllib падает на проверке TLS
    (в системном хранилище есть self-signed корень), поэтому requests —
    основной путь, а urllib именно запасной для чистого VPS.
    """
    hdrs = {"Accept": "application/json"}
    hdrs.update(headers or {})

    try:
        import requests  # noqa: PLC0415 — опциональная зависимость
    except ImportError:
        requests = None  # type: ignore[assignment]

    if requests is not None:
        resp = requests.get(url, params=params, headers=hdrs, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    from urllib import parse as _parse  # noqa: PLC0415
    from urllib import request as _request  # noqa: PLC0415

    full = url + ("?" + _parse.urlencode(params) if params else "")
    req = _request.Request(full, headers=hdrs, method="GET")
    with _request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — url задан в коде
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: int = 60,
) -> Any:
    """POST JSON -> распарсенный JSON-ответ.

    Использует requests, если он есть; иначе падает на stdlib urllib —
    чтобы скрипт не зависел от наличия пакета на VPS.
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
    hdrs.update(headers or {})

    try:
        import requests  # noqa: PLC0415 — опциональная зависимость
    except ImportError:
        requests = None  # type: ignore[assignment]

    if requests is not None:
        resp = requests.post(url, data=body, headers=hdrs, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    from urllib import request as _request  # noqa: PLC0415

    req = _request.Request(url, data=body, headers=hdrs, method="POST")
    with _request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — url задан в коде
        return json.loads(resp.read().decode("utf-8"))


# --- argparse ---------------------------------------------------------------


def add_common_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Общие флаги всех шагов синка.

    По умолчанию режим сухой: без сети и без записи в data/.
    Реальные вызовы и запись включает только --live.
    """
    parser.add_argument("--live", action="store_true",
                        help="реальные API-вызовы и запись снапшота (по умолчанию dry-run)")
    parser.add_argument("--dry-run", action="store_true",
                        help="явный dry-run (режим по умолчанию, флаг для читаемости команд)")
    parser.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                        help="дата снапшота (по умолчанию сегодня)")
    parser.add_argument("--data-dir", default=None, metavar="PATH",
                        help=f"корень снапшотов (по умолчанию {DATA_DIR})")
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG-логи")
    return parser


def apply_common_args(args: argparse.Namespace, logger: logging.Logger) -> None:
    """Разруливает конфликт --live/--dry-run и уровень логов."""
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    if getattr(args, "dry_run", False) and args.live:
        raise SystemExit("Нельзя одновременно --live и --dry-run")
    logger.info("Режим: %s", "LIVE" if args.live else "DRY-RUN (без сети и без записи)")
