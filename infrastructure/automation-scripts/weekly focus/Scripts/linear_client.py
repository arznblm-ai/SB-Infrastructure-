"""linear_client.py — best-effort клиент Linear GraphQL API (команда ROZ).

Общий модуль для meeting_notes.py и telegram_codex_bot.py (оба лежат в этом
каталоге и запускаются по абсолютному пути, поэтому обычный import работает).

Контракт: **ни одна публичная функция не бросает исключение наружу**. Любая
проблема (нет ключа, сеть, GraphQL error, битый JSON) → лог через `log_fn`
с префиксом `linear:` и `None`/`False`/`[]` в ответе. Linear-слой — надстройка:
если он недоступен, vault-запись и Telegram-флоу работают как раньше.

HTTP через `curl` subprocess — тем же способом, что Telegram API в этом проекте
(Python.framework 3.11 на этой машине без CA-сертификатов).
"""

import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

LINEAR_ENV_FILE = Path("/Users/anton/.config/second-brain/linear.env")
LINEAR_API_URL = "https://api.linear.app/graphql"
PROJECT_MAP_FILE = Path("/Users/anton/.config/second-brain/linear-project-map.json")
TODO_ISSUE_MAP_FILE = Path("/Users/anton/.config/second-brain/linear-todo-map.json")

# Константы workspace (получены read-only запросом к API 2026-07-30)
TEAM_ID = "0044c91c-7a5b-44c3-a9b1-7ade88e8e54b"
TEAM_KEY = "ROZ"
ANTON_ASSIGNEE_ID = "b4717c07-9e53-4663-9417-5679ebab1732"

STATE_IDS = {
    "Backlog": "7a8c1bf3-a52b-4106-a1ea-3e169b9be301",
    "Todo": "416b91ba-f9b0-41d2-ba11-f90f5b0a4d73",
    "In Progress": "897c453e-1d66-47bd-b5a4-17859cd2c28f",
    "Done": "35e311bc-56b0-441b-8108-a9155d07e208",
    "Canceled": "3114fe5e-a5c1-4bf6-b35e-0d352c08145f",
    "Duplicate": "0a459d46-ca78-458f-bf69-6d0998f3181e",
}

LABEL_OTHER_ID = "5cc2e8e9-f6f0-4432-8a09-f6020ebe583f"      # «чужая»
LABEL_UNOWNED_ID = "efec8fdc-e39e-4a76-be7d-c6252ae6b06a"    # «без владельца»

PROJECT_IDS = {
    "AI-видеопродакшн": "fbdc5990-fb1d-45d3-bb58-6077e97ed381",
    "Кино и экранизации": "5b3741a3-fea8-47f5-9821-333bccedeaff",
    "UGC/SMM контент-завод": "c9be66a9-61f7-4ab8-9b9b-955db536189e",
    "Консалтинг Creative Industry": "d5941594-200d-4e37-8074-7f01cac97dce",
    "Карьера": "9445b9fd-be91-4698-9ac8-415c32e0bace",
    "Перекрёсток": "8b8e5a26-1108-4936-85cd-c2e907764f69",
    "Yandex.Scale CGI Promo": "92890867-b356-465c-876b-5b44fc9f6962",
}

# WorkflowStateType значения Linear для «открытых» задач:
# backlog = Backlog, unstarted = Todo, started = In Progress
OPEN_STATE_TYPES = ["backlog", "unstarted", "started"]

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


def load_linear_api_key(log_fn: LogFn = print) -> Optional[str]:
    key = load_env_file(LINEAR_ENV_FILE).get("LINEAR_API_KEY", "").strip()
    if not key:
        log_fn(f"linear: API key not found in {LINEAR_ENV_FILE}, Linear sync skipped")
        return None
    return key


# ── GraphQL ───────────────────────────────────────────────────────────────

def linear_request(
    query: str,
    variables: Optional[dict] = None,
    timeout: int = 20,
    log_fn: LogFn = print,
) -> Optional[dict]:
    """POST на Linear GraphQL. Возвращает содержимое `data` или None."""
    key = load_linear_api_key(log_fn=log_fn)
    if not key:
        return None
    payload: dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables
    command = [
        "curl", "-sS", "--max-time", str(timeout),
        "-X", "POST",
        "-H", "Content-Type: application/json",
        "-H", f"Authorization: {key}",
        "--data-binary", "@-",
        LINEAR_API_URL,
    ]
    try:
        completed = subprocess.run(
            command,
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=timeout + 10,
        )
    except Exception as exc:
        log_fn(f"linear: request failed: {type(exc).__name__}: {exc}")
        return None
    if completed.returncode != 0:
        log_fn(f"linear: curl exit {completed.returncode}: {(completed.stderr or '').strip()[:300]}")
        return None
    try:
        body = json.loads(completed.stdout)
    except Exception as exc:
        log_fn(f"linear: bad JSON response: {exc}; body={(completed.stdout or '')[:200]}")
        return None
    if not isinstance(body, dict):
        log_fn(f"linear: unexpected response type: {type(body).__name__}")
        return None
    if body.get("errors"):
        log_fn(f"linear: GraphQL errors: {json.dumps(body['errors'], ensure_ascii=False)[:400]}")
        return None
    data = body.get("data")
    if not isinstance(data, dict):
        log_fn("linear: response without data")
        return None
    return data


# ── Маппинг встреча → проект ──────────────────────────────────────────────

_DATE_SUFFIX_RE = re.compile(r"\s*[–—-]\s*\d{4}-\d{2}-\d{2}\s*$")


def normalize_meeting_title(source_meeting: str) -> str:
    """«Название – 2026-07-28» → «название» (без даты, lowercase)."""
    title = _DATE_SUFFIX_RE.sub("", source_meeting or "")
    return re.sub(r"\s+", " ", title).strip().lower()


def linear_project_id_for_meeting(
    source_meeting: str,
    config_path: Path = PROJECT_MAP_FILE,
    log_fn: LogFn = print,
) -> Optional[str]:
    """Точный матч по нормализованному заголовку встречи. Не keyword-поиск."""
    key = normalize_meeting_title(source_meeting)
    if not key:
        return None
    try:
        if not config_path.exists():
            log_fn(f"linear: project map not found ({config_path}), issue goes to team backlog")
            return None
        mapping = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log_fn(f"linear: project map unreadable ({config_path}): {exc}")
        return None
    if not isinstance(mapping, dict):
        log_fn(f"linear: project map is not an object ({config_path})")
        return None
    project_id = mapping.get(key)
    if isinstance(project_id, str) and project_id.strip():
        return project_id.strip()
    log_fn(f"linear: no project mapping for meeting '{key}', issue goes to team backlog")
    return None


# ── Issues ────────────────────────────────────────────────────────────────

def build_issue_description(vault_id: str, source_meeting: str, who: str, created: str) -> str:
    """Формат, совпадающий с уже мигрированными issues (ROZ-5…ROZ-86)."""
    return (
        f"Vault ID: {vault_id}\n"
        f"Встреча: {source_meeting}\n"
        f"Исполнитель (из встречи): {who or 'unknown'}\n"
        f"Создано: {created}"
    )


_CREATE_ISSUE_MUTATION = """
mutation CreateIssue($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue { id identifier }
  }
}
""".strip()


def create_linear_issue(
    title: str,
    vault_id: str,
    source_meeting: str,
    who: str,
    created: str,
    owner: str,
    project_id: Optional[str] = None,
    log_fn: LogFn = print,
) -> Optional[str]:
    """Создаёт issue в команде ROZ. Возвращает internal id issue или None."""
    clean_title = (title or "").strip()
    if not clean_title:
        log_fn(f"linear: empty title for {vault_id}, skip")
        return None
    issue_input: dict[str, Any] = {
        "teamId": TEAM_ID,
        "title": clean_title,
        "description": build_issue_description(vault_id, source_meeting, who, created),
    }
    # Пустые поля не передаём вовсе — Linear строже к null, чем к отсутствию ключа.
    if project_id:
        issue_input["projectId"] = project_id
    if owner == "me":
        issue_input["assigneeId"] = ANTON_ASSIGNEE_ID
    elif owner == "other":
        issue_input["labelIds"] = [LABEL_OTHER_ID]
    else:
        issue_input["labelIds"] = [LABEL_UNOWNED_ID]
    data = linear_request(_CREATE_ISSUE_MUTATION, {"input": issue_input}, log_fn=log_fn)
    if not data:
        log_fn(f"linear: issueCreate failed for {vault_id}")
        return None
    result = data.get("issueCreate") or {}
    issue = result.get("issue") or {}
    issue_id = issue.get("id")
    if not result.get("success") or not issue_id:
        log_fn(f"linear: issueCreate returned no issue for {vault_id}")
        return None
    log_fn(f"linear: created {issue.get('identifier', issue_id)} for {vault_id} (project={project_id or 'backlog'})")
    return issue_id


_CLOSE_ISSUE_MUTATION = """
mutation CloseIssue($id: String!, $stateId: String!) {
  issueUpdate(id: $id, input: { stateId: $stateId }) {
    success
    issue { id identifier }
  }
}
""".strip()


def close_linear_issue(issue_id: str, log_fn: LogFn = print) -> bool:
    if not issue_id:
        return False
    data = linear_request(
        _CLOSE_ISSUE_MUTATION,
        {"id": issue_id, "stateId": STATE_IDS["Done"]},
        log_fn=log_fn,
    )
    if not data:
        log_fn(f"linear: issueUpdate(Done) failed for {issue_id}")
        return False
    result = data.get("issueUpdate") or {}
    if not result.get("success"):
        log_fn(f"linear: issueUpdate(Done) not successful for {issue_id}")
        return False
    issue = result.get("issue") or {}
    log_fn(f"linear: closed {issue.get('identifier', issue_id)}")
    return True


_FIND_BY_VAULT_ID_QUERY = """
query FindByVaultId($filter: IssueFilter!) {
  issues(filter: $filter, first: 10) {
    nodes { id identifier description }
  }
}
""".strip()


def find_linear_issue_id_by_vault_id(vault_id: str, log_fn: LogFn = print) -> Optional[str]:
    """Fallback-поиск issue по `Vault ID: T<n>` в description (точный матч строки)."""
    if not vault_id:
        return None
    needle = f"Vault ID: {vault_id}"
    data = linear_request(
        _FIND_BY_VAULT_ID_QUERY,
        {"filter": {
            "team": {"key": {"eq": TEAM_KEY}},
            "description": {"contains": needle},
        }},
        log_fn=log_fn,
    )
    if not data:
        return None
    nodes = ((data.get("issues") or {}).get("nodes")) or []
    # `contains` матчит и T5 внутри T57 — проверяем границу строки вручную.
    exact_re = re.compile(rf"Vault ID: {re.escape(vault_id)}(?:\s|$)")
    for node in nodes:
        description = node.get("description") or ""
        if exact_re.search(description):
            return node.get("id")
    log_fn(f"linear: no issue found for {vault_id}")
    return None


# ── Локальный mapping-state T<n> → issue id ───────────────────────────────

def load_todo_issue_map(log_fn: LogFn = print) -> dict[str, str]:
    try:
        if not TODO_ISSUE_MAP_FILE.exists():
            return {}
        data = json.loads(TODO_ISSUE_MAP_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        log_fn(f"linear: todo map unreadable, starting fresh: {exc}")
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if v}


def save_todo_issue_map(mapping: dict[str, str], log_fn: LogFn = print) -> None:
    try:
        TODO_ISSUE_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = TODO_ISSUE_MAP_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(TODO_ISSUE_MAP_FILE)
    except Exception as exc:
        log_fn(f"linear: cannot save todo map: {exc}")


def remember_issue_id(vault_id: str, issue_id: str, log_fn: LogFn = print) -> None:
    if not vault_id or not issue_id:
        return
    mapping = load_todo_issue_map(log_fn=log_fn)
    if mapping.get(vault_id) == issue_id:
        return
    mapping[vault_id] = issue_id
    save_todo_issue_map(mapping, log_fn=log_fn)


def resolve_issue_id(vault_id: str, log_fn: LogFn = print) -> Optional[str]:
    """Сначала локальный mapping, затем поиск по API (с запоминанием результата)."""
    if not vault_id:
        return None
    mapping = load_todo_issue_map(log_fn=log_fn)
    issue_id = mapping.get(vault_id)
    if issue_id:
        return issue_id
    issue_id = find_linear_issue_id_by_vault_id(vault_id, log_fn=log_fn)
    if issue_id:
        mapping[vault_id] = issue_id
        save_todo_issue_map(mapping, log_fn=log_fn)
    return issue_id
