"""todoist_client.py — best-effort клиент Todoist unified API v1.

Порт `linear_client.py` (сигнатуры и контракт ошибок 1:1). Общий модуль для
meeting_notes.py и telegram_codex_bot.py (оба лежат в этом каталоге и
запускаются по абсолютному пути, поэтому обычный import работает).

Контракт: **ни одна публичная функция не бросает исключение наружу**. Любая
проблема (нет токена, сеть, HTTP-ошибка, битый JSON) → лог через `log_fn`
с префиксом `todoist:` и `None`/`False`/`[]`/`{}` в ответе. Todoist-слой —
надстройка: если он недоступен, vault-запись и Telegram-флоу работают как раньше.

HTTP через `curl` subprocess — тем же способом, что Telegram API и Linear в этом
проекте (Python.framework 3.11 на этой машине без CA-сертификатов).

Отличия модели от Linear (см. план миграции 2026-07-31):
- статусов нет: задача либо открыта, либо закрыта (`POST /tasks/{id}/close`);
- owner-логика через labels `other` / `unowned` (у `owner: me` labels нет);
- **рабочих проектов нет**: тариф Todoist ограничивает число проектов (Free = 5,
  они заняты личными: Films, Концерты, Reading list, Not urgent). Поэтому рабочая
  область (бывший Linear-проект) — это **label**, а все рабочие задачи лежат в Inbox.
  Решение Антона 2026-07-31 после того, как create project вернул 403
  MAX_PROJECTS_LIMIT_REACHED на четвёртом проекте;
- area-label встречи читается из `todoist-label-map.json` (встреча → имя label),
  вспомогательные id — из `todoist-ids.json` (оба пишет migrate_linear_to_todoist.py).
"""

import difflib
import json
import re
import shlex
import subprocess
import unicodedata
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlencode

# Пути относительно домашней директории: на маке это ~anton, на VPS — /root
# (уборщик todoist_janitor.py крутится там под root).
CONFIG_DIR = Path.home() / ".config" / "second-brain"
TODOIST_ENV_FILE = CONFIG_DIR / "todoist.env"
TODOIST_API_URL = "https://api.todoist.com/api/v1"
LABEL_MAP_FILE = CONFIG_DIR / "todoist-label-map.json"
TODO_TASK_MAP_FILE = CONFIG_DIR / "todoist-todo-map.json"
IDS_CONFIG_FILE = CONFIG_DIR / "todoist-ids.json"

# Labels-эквиваленты Linear-меток «чужая» / «без владельца».
LABEL_OTHER = "other"
LABEL_UNOWNED = "unowned"

# Максимум объектов на страницу у списочных endpoint-ов v1.
PAGE_LIMIT = 200

LogFn = Callable[[str], None]


# ── Env ───────────────────────────────────────────────────────────────────

def load_env_file(path: Path) -> dict[str, str]:
    """Best-effort вариант общего парсера env: отсутствие файла — не ошибка."""
    env: dict[str, str] = {}
    try:
        if not path.exists():
            return env
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            env[key.strip()] = shlex.split(value)[0] if value else ""
    except Exception:
        return {}
    return env


def load_todoist_api_token(log_fn: LogFn = print) -> Optional[str]:
    token = load_env_file(TODOIST_ENV_FILE).get("TODOIST_API_TOKEN", "").strip()
    if not token:
        log_fn(f"todoist: API token not found in {TODOIST_ENV_FILE}, Todoist sync skipped")
        return None
    return token


# ── HTTP ──────────────────────────────────────────────────────────────────

def todoist_request(
    method: str,
    path: str,
    payload: Optional[dict] = None,
    params: Optional[dict] = None,
    timeout: int = 20,
    log_fn: LogFn = print,
) -> Optional[dict]:
    """Запрос к Todoist API v1. Возвращает разобранный JSON-объект, `{}` для
    пустого тела (204) или None при любой проблеме."""
    token = load_todoist_api_token(log_fn=log_fn)
    if not token:
        return None
    url = TODOIST_API_URL + path
    if params:
        clean = {k: v for k, v in params.items() if v not in (None, "")}
        if clean:
            url = f"{url}?{urlencode(clean, doseq=True)}"
    command = [
        "curl", "-sS", "--max-time", str(timeout),
        "-X", method.upper(),
        "-H", f"Authorization: Bearer {token}",
        "-w", "\n%{http_code}",
    ]
    if payload is not None:
        command += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
    command.append(url)
    try:
        completed = subprocess.run(
            command,
            input=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout + 10,
        )
    except Exception as exc:
        log_fn(f"todoist: request failed ({method} {path}): {type(exc).__name__}: {exc}")
        return None
    if completed.returncode != 0:
        log_fn(f"todoist: curl exit {completed.returncode}: {(completed.stderr or '').strip()[:300]}")
        return None
    raw = completed.stdout or ""
    body, _, status = raw.rpartition("\n")
    status = status.strip()
    if not status.isdigit():
        log_fn(f"todoist: no HTTP status in response ({method} {path}): {raw[:200]}")
        return None
    code = int(status)
    if code < 200 or code >= 300:
        log_fn(f"todoist: HTTP {code} on {method} {path}: {body.strip()[:300]}")
        return None
    if not body.strip():
        return {}
    try:
        parsed = json.loads(body)
    except Exception as exc:
        log_fn(f"todoist: bad JSON response ({method} {path}): {exc}; body={body[:200]}")
        return None
    if isinstance(parsed, list):
        return {"results": parsed, "next_cursor": None}
    if not isinstance(parsed, dict):
        log_fn(f"todoist: unexpected response type ({method} {path}): {type(parsed).__name__}")
        return None
    return parsed


def todoist_list(
    path: str,
    params: Optional[dict] = None,
    results_key: str = "results",
    max_pages: int = 50,
    log_fn: LogFn = print,
) -> list[dict]:
    """Cursor-пагинация списочного endpoint-а v1. Пустой список при любой проблеме."""
    items: list[dict] = []
    cursor: Optional[str] = None
    for _ in range(max_pages):
        page_params = dict(params or {})
        page_params.setdefault("limit", PAGE_LIMIT)
        if cursor:
            page_params["cursor"] = cursor
        data = todoist_request("GET", path, params=page_params, log_fn=log_fn)
        if not data:
            return items
        page = data.get(results_key)
        if not isinstance(page, list):
            log_fn(f"todoist: unexpected list payload on {path} (no '{results_key}')")
            return items
        items.extend(x for x in page if isinstance(x, dict))
        cursor = data.get("next_cursor")
        if not cursor:
            return items
    log_fn(f"todoist: pagination stopped at {max_pages} pages on {path}")
    return items


# ── Локальные конфиги id (проекты и labels появляются после миграции) ──────

def load_ids_config(log_fn: LogFn = print) -> dict[str, Any]:
    """`todoist-ids.json`: labels, inbox, служебные id. `{}` если файла нет."""
    try:
        if not IDS_CONFIG_FILE.exists():
            return {}
        data = json.loads(IDS_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        log_fn(f"todoist: ids config unreadable ({IDS_CONFIG_FILE}): {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def save_ids_config(config: dict[str, Any], log_fn: LogFn = print) -> None:
    try:
        IDS_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = IDS_CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(IDS_CONFIG_FILE)
    except Exception as exc:
        log_fn(f"todoist: cannot save ids config: {exc}")


def inbox_project_id(log_fn: LogFn = print) -> Optional[str]:
    """Inbox — единственный проект, в котором живут рабочие задачи."""
    value = load_ids_config(log_fn=log_fn).get("inbox_project_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    log_fn(f"todoist: inbox_project_id не задан в {IDS_CONFIG_FILE}")
    return None


def work_scope_project_ids(log_fn: LogFn = print) -> list[str]:
    """Проекты, задачи которых считаются рабочими (аналог scope команды ROZ).

    В label-схеме это всегда Inbox: личные проекты (Films, Reading list…) в
    планирование не попадают. Пустой список = ограничения нет, берём все проекты."""
    config = load_ids_config(log_fn=log_fn)
    ids = config.get("work_scope_project_ids")
    if not isinstance(ids, list) or not ids:
        inbox = config.get("inbox_project_id")
        if isinstance(inbox, str) and inbox.strip():
            return [inbox.strip()]
        log_fn(f"todoist: work_scope_project_ids не задан в {IDS_CONFIG_FILE}, "
               "берём задачи из всех проектов")
        return []
    return [str(x) for x in ids if x]


def area_label_names(log_fn: LogFn = print) -> set[str]:
    """Имена area-labels (бывшие Linear-проекты) из `todoist-ids.json`."""
    areas = load_ids_config(log_fn=log_fn).get("area_labels")
    if not isinstance(areas, dict):
        return set()
    return {str(name) for name in areas if name}


# Кэш секций: конфиг читается один раз за процесс (файл правится вручную,
# долгоживущих процессов с горячей перезагрузкой у нас нет).
_SECTION_IDS_CACHE: Optional[dict[str, str]] = None


def load_section_ids(log_fn: LogFn = print) -> dict[str, str]:
    """`section_ids` из `todoist-ids.json`: intake / week / waiting / later → id.

    `{}` если файла или ключа нет — вызывающий код обязан продолжить работу
    (задача просто создастся без секции, то есть в корне Inbox)."""
    global _SECTION_IDS_CACHE
    if _SECTION_IDS_CACHE is not None:
        return dict(_SECTION_IDS_CACHE)
    sections = load_ids_config(log_fn=log_fn).get("section_ids")
    if not isinstance(sections, dict) or not sections:
        log_fn(f"todoist: section_ids не заданы в {IDS_CONFIG_FILE}, задачи идут без секции")
        _SECTION_IDS_CACHE = {}
        return {}
    clean = {
        str(key): str(value).strip()
        for key, value in sections.items()
        if key and isinstance(value, (str, int)) and str(value).strip()
    }
    _SECTION_IDS_CACHE = clean
    return dict(clean)


def section_id(key: str, log_fn: LogFn = print) -> Optional[str]:
    """id секции по ключу (`intake` / `week` / `waiting` / `later`) или None."""
    value = load_section_ids(log_fn=log_fn).get((key or "").strip())
    if value:
        return value
    log_fn(f"todoist: нет секции '{key}' в {IDS_CONFIG_FILE}")
    return None


# ── Маппинг встреча → area-label ──────────────────────────────────────────

_DATE_SUFFIX_RE = re.compile(r"\s*[–—-]\s*\d{4}-\d{2}-\d{2}\s*$")


def normalize_meeting_title(source_meeting: str) -> str:
    """«Название – 2026-07-28» → «название» (без даты, lowercase)."""
    title = _DATE_SUFFIX_RE.sub("", source_meeting or "")
    return re.sub(r"\s+", " ", title).strip().lower()


def todoist_label_for_meeting(
    source_meeting: str,
    config_path: Path = LABEL_MAP_FILE,
    log_fn: LogFn = print,
) -> Optional[str]:
    """Точный матч по нормализованному заголовку встречи. Не keyword-поиск.

    Возвращает **имя area-label** (например «Перекрёсток»), а не id проекта:
    задача всё равно ляжет в Inbox, область помечается label-ом."""
    key = normalize_meeting_title(source_meeting)
    if not key:
        return None
    try:
        if not config_path.exists():
            log_fn(f"todoist: label map not found ({config_path}), task goes without area label")
            return None
        mapping = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log_fn(f"todoist: label map unreadable ({config_path}): {exc}")
        return None
    if not isinstance(mapping, dict):
        log_fn(f"todoist: label map is not an object ({config_path})")
        return None
    label = mapping.get(key)
    if isinstance(label, str) and label.strip():
        return label.strip()
    log_fn(f"todoist: no area label for meeting '{key}', task goes without area label")
    return None


# ── Проекты и labels ──────────────────────────────────────────────────────

def list_projects(log_fn: LogFn = print) -> list[dict]:
    return todoist_list("/projects", log_fn=log_fn)


def project_names(log_fn: LogFn = print) -> dict[str, str]:
    """id проекта → имя. `{}` при недоступном API."""
    return {
        str(p.get("id")): (p.get("name") or "").strip()
        for p in list_projects(log_fn=log_fn)
        if p.get("id")
    }


def find_project_id_by_name(name: str, log_fn: LogFn = print) -> Optional[str]:
    target = (name or "").strip().casefold()
    if not target:
        return None
    for project in list_projects(log_fn=log_fn):
        if (project.get("name") or "").strip().casefold() == target:
            return str(project.get("id"))
    return None


def create_project(name: str, log_fn: LogFn = print) -> Optional[str]:
    clean = (name or "").strip()
    if not clean:
        return None
    data = todoist_request("POST", "/projects", payload={"name": clean}, log_fn=log_fn)
    if not data or not data.get("id"):
        log_fn(f"todoist: project create failed for '{clean}'")
        return None
    log_fn(f"todoist: created project '{clean}' ({data['id']})")
    return str(data["id"])


def list_labels(log_fn: LogFn = print) -> list[dict]:
    return todoist_list("/labels", log_fn=log_fn)


def create_label(name: str, log_fn: LogFn = print) -> Optional[str]:
    clean = (name or "").strip()
    if not clean:
        return None
    data = todoist_request("POST", "/labels", payload={"name": clean}, log_fn=log_fn)
    if not data or not data.get("id"):
        log_fn(f"todoist: label create failed for '{clean}'")
        return None
    log_fn(f"todoist: created label '{clean}' ({data['id']})")
    return str(data["id"])


# ── Задачи ────────────────────────────────────────────────────────────────

def build_task_description(vault_id: str, source_meeting: str, who: str, created: str) -> str:
    """Формат, совпадающий с Linear-описаниями (чтобы поиск по Vault ID работал)."""
    return (
        f"Vault ID: {vault_id}\n"
        f"Встреча: {source_meeting}\n"
        f"Исполнитель (из встречи): {who or 'unknown'}\n"
        f"Создано: {created}"
    )


def labels_for_owner(owner: str) -> list[str]:
    """`me` → без labels, всё остальное → «без владельца».

    `other` формально возвращает `LABEL_OTHER`, но задачи с этим owner в Todoist
    не создаются вовсе (см. `create_task`), поэтому в живых задачах его нет."""
    if owner == "me":
        return []
    if owner == "other":
        return [LABEL_OTHER]
    return [LABEL_UNOWNED]


def task_labels(owner: str, area_label: Optional[str] = None) -> list[str]:
    """Полный набор labels задачи: owner-label + area-label рабочей области."""
    labels = labels_for_owner(owner)
    clean_area = (area_label or "").strip()
    if clean_area and clean_area not in labels:
        labels.append(clean_area)
    return labels


def create_task(
    title: str,
    vault_id: str,
    source_meeting: str,
    who: str,
    created: str,
    owner: str,
    area_label: Optional[str] = None,
    log_fn: LogFn = print,
    section_id: Optional[str] = None,
    due_date: Optional[str] = None,
) -> Optional[str]:
    """Создаёт задачу в Inbox с area-label рабочей области. id задачи или None.

    `section_id` (id секции Inbox) и `due_date` (`YYYY-MM-DD`) необязательны:
    если не заданы, поведение ровно как раньше — задача в корне Inbox без даты."""
    clean_title = (title or "").strip()
    if not clean_title:
        log_fn(f"todoist: empty title for {vault_id}, skip")
        return None
    if owner == "other":
        # Чужие задачи в Todoist не живут (решение Антона 2026-07-31): они остаются
        # в vault-сторе `meeting todo/` и попадают в shareable-сообщение встречи.
        log_fn(f"todoist: skip other-owned task {vault_id}")
        return None
    payload: dict[str, Any] = {
        "content": clean_title,
        "description": build_task_description(vault_id, source_meeting, who, created),
    }
    # Пустые поля не передаём вовсе — как и в Linear-версии.
    inbox = inbox_project_id(log_fn=log_fn)
    if inbox:
        payload["project_id"] = inbox
    labels = task_labels(owner, area_label)
    if labels:
        payload["labels"] = labels
    clean_section = (section_id or "").strip()
    if clean_section:
        payload["section_id"] = clean_section
    clean_due = (due_date or "").strip()
    if clean_due:
        payload["due_date"] = clean_due
    data = todoist_request("POST", "/tasks", payload=payload, log_fn=log_fn)
    if not data:
        log_fn(f"todoist: task create failed for {vault_id}")
        return None
    task_id = data.get("id")
    if not task_id:
        log_fn(f"todoist: task create returned no id for {vault_id}")
        return None
    log_fn(f"todoist: created {task_id} for {vault_id} (area={area_label or 'без области'})")
    return str(task_id)


def update_task_labels(task_id: str, labels: list[str], log_fn: LogFn = print) -> bool:
    """Полная замена набора labels у задачи (работает и для закрытых задач)."""
    if not task_id:
        return False
    data = todoist_request("POST", f"/tasks/{task_id}", payload={"labels": labels}, log_fn=log_fn)
    if data is None:
        log_fn(f"todoist: label update failed for {task_id}")
        return False
    return True


def move_task(task_id: str, project_id: str, log_fn: LogFn = print) -> bool:
    """Перенос задачи в другой проект. Закрытые задачи переносятся закрытыми
    (проверено эмпирически 2026-07-31)."""
    if not task_id or not project_id:
        return False
    data = todoist_request(
        "POST", f"/tasks/{task_id}/move", payload={"project_id": project_id}, log_fn=log_fn
    )
    if data is None:
        log_fn(f"todoist: move failed for {task_id}")
        return False
    landed = str(data.get("project_id") or "")
    if landed and landed != str(project_id):
        log_fn(f"todoist: move {task_id} landed in {landed}, ожидался {project_id}")
        return False
    return True


def move_task_to_section(task_id: str, section_id: str, log_fn: LogFn = print) -> bool:
    """Перенос задачи в секцию Inbox (`POST /tasks/{id}/move` с `section_id`).

    Отдельная функция, а не параметр `move_task`: у `move_task` второй позиционный
    аргумент — project_id (так его зовёт migrate_linear_to_todoist.py), а id секции
    и id проекта визуально неразличимы, автодетект был бы тихой ошибкой."""
    clean_section = (section_id or "").strip()
    if not task_id or not clean_section:
        return False
    data = todoist_request(
        "POST", f"/tasks/{task_id}/move", payload={"section_id": clean_section}, log_fn=log_fn
    )
    if data is None:
        log_fn(f"todoist: section move failed for {task_id}")
        return False
    landed = str(data.get("section_id") or "")
    if landed and landed != clean_section:
        log_fn(f"todoist: move {task_id} landed in section {landed}, ожидалась {clean_section}")
        return False
    return True


def set_due(task_id: str, due_date: str, log_fn: LogFn = print) -> bool:
    """Проставить дату задачи (`YYYY-MM-DD`). True при успехе."""
    clean_due = (due_date or "").strip()
    if not task_id or not clean_due:
        return False
    data = todoist_request(
        "POST", f"/tasks/{task_id}", payload={"due_date": clean_due}, log_fn=log_fn
    )
    if data is None:
        log_fn(f"todoist: due update failed for {task_id}")
        return False
    return True


def get_task(task_id: str, log_fn: LogFn = print) -> Optional[dict]:
    """Одна задача по id (в том числе закрытая). None при любой проблеме."""
    if not task_id:
        return None
    data = todoist_request("GET", f"/tasks/{task_id}", log_fn=log_fn)
    if not data or not data.get("id"):
        log_fn(f"todoist: task {task_id} not found")
        return None
    return data


def append_description_line(task_id: str, line: str, log_fn: LogFn = print) -> bool:
    """Дописать строку в конец description задачи (GET + POST).

    Нужна дедупу: когда задача уже есть, вызывающий дописывает «Также из встречи: …»."""
    clean_line = (line or "").strip()
    if not task_id or not clean_line:
        return False
    task = get_task(task_id, log_fn=log_fn)
    if task is None:
        log_fn(f"todoist: cannot append description, task {task_id} unreadable")
        return False
    current = task.get("description")
    current = current if isinstance(current, str) else ""
    if clean_line in current:
        # Идемпотентность: повторный прогон того же дедупа не плодит дубли строк.
        return True
    updated = f"{current.rstrip()}\n{clean_line}" if current.strip() else clean_line
    data = todoist_request(
        "POST", f"/tasks/{task_id}", payload={"description": updated}, log_fn=log_fn
    )
    if data is None:
        log_fn(f"todoist: description append failed for {task_id}")
        return False
    return True


def close_task(task_id: str, log_fn: LogFn = print) -> bool:
    if not task_id:
        return False
    data = todoist_request("POST", f"/tasks/{task_id}/close", payload={}, log_fn=log_fn)
    if data is None:
        log_fn(f"todoist: close failed for {task_id}")
        return False
    log_fn(f"todoist: closed {task_id}")
    return True


def list_open_tasks(
    project_ids: Optional[Iterable[str]] = None,
    log_fn: LogFn = print,
) -> list[dict]:
    """Все незакрытые задачи (опционально — только в указанных проектах)."""
    scope = [str(x) for x in project_ids if x] if project_ids else []
    if not scope:
        return todoist_list("/tasks", log_fn=log_fn)
    tasks: list[dict] = []
    for project_id in scope:
        tasks.extend(todoist_list("/tasks", params={"project_id": project_id}, log_fn=log_fn))
    return tasks


def find_task_id_by_vault_id(vault_id: str, log_fn: LogFn = print) -> Optional[str]:
    """Fallback-поиск задачи по `Vault ID: T<n>` в description (точный матч строки).

    У Todoist нет серверного поиска по description, поэтому сканируем открытые
    задачи локально: аккаунт личный и небольшой, это 1-2 запроса."""
    if not vault_id:
        return None
    # Подстрочный матч цеплял бы T5 внутри T57 — проверяем границу строки.
    exact_re = re.compile(rf"Vault ID: {re.escape(vault_id)}(?:\s|$)")
    for task in list_open_tasks(log_fn=log_fn):
        description = task.get("description") or ""
        if exact_re.search(description):
            return str(task.get("id"))
    log_fn(f"todoist: no task found for {vault_id}")
    return None


# ── Дедуп задач по заголовку ──────────────────────────────────────────────

# «Область · текст задачи» → «текст задачи». Разделитель префикса области бывает
# разный: `·` ставим мы при создании, `:` и `—` пишет Антон руками.
_AREA_PREFIX_RE = re.compile(r"^[^·:—\n]{1,60}[·:—]\s+")
# Хвостовая ссылка на vault-задачу: «… + подключить Митю (T155)».
_VAULT_REF_RE = re.compile(r"\(\s*[Tt]\d+\s*\)")
DUPLICATE_RATIO = 0.85


def normalize_task_title(title: str) -> str:
    """lowercase, без ведущего «Что-то · / : / — », без «(T<n>)», без пунктуации."""
    text = (title or "").strip().lower()
    text = _AREA_PREFIX_RE.sub("", text).strip()
    text = _VAULT_REF_RE.sub(" ", text)
    text = "".join(
        " " if unicodedata.category(ch).startswith("P") or ch == "·" else ch
        for ch in text
    )
    return re.sub(r"\s+", " ", text).strip()


def _vault_ref_in_title_re(vault_id: str) -> Optional[re.Pattern]:
    """Регулярка «токен T<n> отдельным словом» — чтобы T15 не матчил T155."""
    clean = (vault_id or "").strip()
    if not re.fullmatch(r"[Tt]\d+", clean):
        return None
    return re.compile(rf"(?<![0-9A-Za-zА-Яа-я]){re.escape(clean)}(?![0-9A-Za-zА-Яа-я])", re.I)


def find_duplicate(
    title: str,
    area_label: Optional[str] = None,
    vault_id: Optional[str] = None,
    log_fn: LogFn = print,
) -> Optional[str]:
    """id уже существующей задачи с тем же смыслом или None.

    Порядок: (1) точный матч по `Vault ID` в description; (2) точный матч по токену
    `T<n>` в ЗАГОЛОВКЕ открытой задачи — так ловится ручная задача Антона, которая
    ссылается на авто-задачу («… + подключить Митю (T155)») и по тексту слишком
    непохожа для нечёткого матча; (3) нечёткий матч по заголовку среди открытых
    задач той же области (без area_label — по всему рабочему скоупу), порог
    SequenceMatcher >= 0.85."""
    if vault_id:
        task_id = find_task_id_by_vault_id(vault_id, log_fn=log_fn)
        if task_id:
            return task_id
    target = normalize_task_title(title)
    ref_re = _vault_ref_in_title_re(vault_id or "")
    if not target and ref_re is None:
        return None
    clean_area = (area_label or "").strip()
    try:
        tasks = list_open_tasks(work_scope_project_ids(log_fn=log_fn), log_fn=log_fn)
    except Exception as exc:  # контракт модуля: наружу исключений не отдаём
        log_fn(f"todoist: duplicate scan failed: {type(exc).__name__}: {exc}")
        return None
    if ref_re is not None:
        for task in tasks:
            if ref_re.search(task.get("content") or ""):
                log_fn(f"todoist: duplicate {task.get('id')} by ref {vault_id} in title")
                return str(task.get("id"))
    if not target:
        return None
    for task in tasks:
        if clean_area:
            labels = task.get("labels")
            labels = labels if isinstance(labels, list) else []
            if clean_area not in {str(x).strip() for x in labels}:
                continue
        candidate = normalize_task_title(task.get("content") or "")
        if not candidate:
            continue
        try:
            ratio = difflib.SequenceMatcher(None, target, candidate).ratio()
        except Exception:
            continue
        if ratio >= DUPLICATE_RATIO:
            log_fn(f"todoist: duplicate {task.get('id')} for '{title}' (ratio {ratio:.2f})")
            return str(task.get("id"))
    return None


# ── Локальный mapping-state T<n> → task id ────────────────────────────────

def load_todo_task_map(log_fn: LogFn = print) -> dict[str, str]:
    try:
        if not TODO_TASK_MAP_FILE.exists():
            return {}
        data = json.loads(TODO_TASK_MAP_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        log_fn(f"todoist: todo map unreadable, starting fresh: {exc}")
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if v}


def save_todo_task_map(mapping: dict[str, str], log_fn: LogFn = print) -> None:
    try:
        TODO_TASK_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = TODO_TASK_MAP_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(TODO_TASK_MAP_FILE)
    except Exception as exc:
        log_fn(f"todoist: cannot save todo map: {exc}")


def remember_task_id(vault_id: str, task_id: str, log_fn: LogFn = print) -> None:
    if not vault_id or not task_id:
        return
    mapping = load_todo_task_map(log_fn=log_fn)
    if mapping.get(vault_id) == task_id:
        return
    mapping[vault_id] = task_id
    save_todo_task_map(mapping, log_fn=log_fn)


def resolve_task_id(vault_id: str, log_fn: LogFn = print) -> Optional[str]:
    """Сначала локальный mapping, затем поиск по API (с запоминанием результата)."""
    if not vault_id:
        return None
    mapping = load_todo_task_map(log_fn=log_fn)
    task_id = mapping.get(vault_id)
    if task_id:
        return task_id
    task_id = find_task_id_by_vault_id(vault_id, log_fn=log_fn)
    if task_id:
        mapping[vault_id] = task_id
        save_todo_task_map(mapping, log_fn=log_fn)
    return task_id
