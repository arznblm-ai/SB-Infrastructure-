#!/usr/bin/env python3
"""migrate_linear_to_todoist.py — одноразовая миграция таск-системы Linear → Todoist.

План: tasks/daily focus/{automation} {plan} миграция задач Linear на Todoist – 2026-07-31.md

Схема (решение Антона 2026-07-31, после провала проектной схемы):
рабочих проектов в Todoist **нет** — тариф ограничивает число проектов (Free = 5,
они заняты личными: Films, Концерты, Reading list, Not urgent). Рабочая область
(бывший Linear-проект) — это **label**, все рабочие задачи лежат в Inbox.
Задачи с `owner: other` в Todoist не переносятся вовсе (живут в Linear и в vault).

Что делает (всё идемпотентно, повторный прогон ничего не дублирует):
1. читает из Linear ВСЕ issues команды ROZ (открытые и закрытые, с пагинацией)
   и ВСЕ проекты команды — единственный источник резолва области и для dry-run,
   и для `--execute`;
2. создаёт недостающие area-labels (по именам Linear-проектов) и label `unowned`;
3. чинит состояние прошлых прогонов: удаляет мигрированные задачи с label `other`,
   проставляет area-label и переносит задачи в Inbox (`POST /tasks/{id}/move`),
   удаляет опустевшие проекты, созданные прошлой (отменённой) проектной схемой;
4. создаёт недостающие задачи: заголовок, description (с `Vault ID: T<n>`), labels;
   закрытые (completed / canceled / duplicate) создаются и сразу закрываются;
5. пишет `todoist-label-map.json` (встреча → area-label), `todoist-todo-map.json`,
   `todoist-ids.json` и ledger `todoist-migration-ledger.json`
   (linear issue id → todoist task id, нужен для идемпотентности).

Безопасность: по умолчанию **dry-run**. Ни один запрос на запись в Todoist не
уходит и ни один локальный файл не переписывается, пока не передан `--execute`.
Перед удалением задач их полный JSON сохраняется в
`~/.config/second-brain/todoist-deleted-backup-<дата>.json`.

    python3 migrate_linear_to_todoist.py             # dry-run, только сводка
    python3 migrate_linear_to_todoist.py --execute   # боевой прогон
"""

import argparse
import datetime as dt
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import linear_client
import todoist_client

CONFIG_DIR = Path("/Users/anton/.config/second-brain")
LEDGER_FILE = CONFIG_DIR / "todoist-migration-ledger.json"
OBSOLETE_PROJECT_MAP_FILE = CONFIG_DIR / "todoist-project-map.json"
LINEAR_PROJECT_MAP_FILE = linear_client.PROJECT_MAP_FILE

# Пауза между запросами на запись (Todoist REST: 1000 req / 15 мин, запас большой)
WRITE_SLEEP_SECONDS = 0.35

VAULT_ID_RE = re.compile(r"Vault ID:\s*(T\d+)(?:\s|$)", re.MULTILINE)

LINEAR_LABEL_OTHER = "чужая"
LINEAR_LABEL_UNOWNED = "без владельца"

# Демо-issues, которые Linear создаёт при онбординге команды — переносить нечего.
LINEAR_ONBOARDING_TITLES = {
    "get familiar with linear",
    "import your data",
    "set up your teams",
    "connect your tools",
}

ALL_ISSUES_QUERY = """
query AllTeamIssues($after: String) {
  issues(filter: {team: {key: {eq: "ROZ"}}}, first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      identifier
      title
      description
      createdAt
      state { name type }
      project { id name }
      assignee { id }
      labels { nodes { name } }
    }
  }
}
""".strip()

PROJECTS_QUERY = """
query TeamProjects($teamId: String!) {
  team(id: $teamId) {
    projects(first: 250) {
      nodes { id name }
    }
  }
}
""".strip()


def log(message: str) -> None:
    print(message, flush=True)


# ── Linear ────────────────────────────────────────────────────────────────

def fetch_linear_projects() -> dict[str, str]:
    """linear project id → имя: ВСЕ проекты команды ROZ, а не только хардкод.

    Единственный источник резолва областей и для dry-run, и для `--execute`.
    Раньше запрашивались только id из `linear_client.PROJECT_IDS`, поэтому любой
    новый проект Linear был бы «неизвестным» и его задачи молча падали в Inbox.
    `PROJECT_IDS` остаётся только fallback-ом на случай недоступного API.
    """
    fallback = {pid: name for name, pid in linear_client.PROJECT_IDS.items()}
    data = linear_client.linear_request(
        PROJECTS_QUERY, {"teamId": linear_client.TEAM_ID}, log_fn=log
    )
    if not data:
        log("linear: project names unavailable, using hardcoded PROJECT_IDS names")
        return fallback
    nodes = (((data.get("team") or {}).get("projects") or {}).get("nodes")) or []
    names = {str(n.get("id")): (n.get("name") or "").strip() for n in nodes if n.get("id")}
    for pid, name in fallback.items():
        names.setdefault(pid, name)
    return names


def fetch_linear_issues() -> list[dict]:
    """Все issues команды ROZ (открытые и закрытые), с пагинацией."""
    issues: list[dict] = []
    after: Optional[str] = None
    for _ in range(50):
        data = linear_client.linear_request(ALL_ISSUES_QUERY, {"after": after}, log_fn=log)
        if not data:
            log("linear: issues fetch failed, aborting")
            return []
        block = data.get("issues") or {}
        issues.extend(block.get("nodes") or [])
        page = block.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return issues
        after = page.get("endCursor")
    log("linear: pagination limit reached")
    return issues


# ── Разбор issue ──────────────────────────────────────────────────────────

def issue_labels(issue: dict) -> list[str]:
    return [
        (n.get("name") or "").strip().lower()
        for n in ((issue.get("labels") or {}).get("nodes") or [])
    ]


def issue_owner(issue: dict) -> str:
    """Linear labels/assignee → owner-модель (me / other / unowned)."""
    labels = issue_labels(issue)
    if LINEAR_LABEL_OTHER in labels:
        return "other"
    if LINEAR_LABEL_UNOWNED in labels:
        return "unowned"
    assignee_id = (issue.get("assignee") or {}).get("id")
    if assignee_id == linear_client.ANTON_ASSIGNEE_ID:
        return "me"
    if assignee_id:
        return "other"
    return "unowned"


def issue_is_closed(issue: dict) -> bool:
    state_type = ((issue.get("state") or {}).get("type") or "").strip()
    return state_type not in linear_client.OPEN_STATE_TYPES


def issue_vault_id(issue: dict) -> Optional[str]:
    match = VAULT_ID_RE.search(issue.get("description") or "")
    return match.group(1) if match else None


def title_key(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "")).strip().casefold()


# ── Существующее состояние Todoist ────────────────────────────────────────

def fetch_todoist_completed_tasks(days: int = 90) -> list[dict]:
    """Закрытые задачи за последние `days` дней (для идемпотентности)."""
    until = dt.datetime.now(dt.timezone.utc)
    since = until - dt.timedelta(days=days)
    return todoist_client.todoist_list(
        "/tasks/completed/by_completion_date",
        params={
            "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "until": until.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        results_key="items",
        log_fn=log,
    )


class TodoistIndex:
    """Снимок задач Todoist: индексы идемпотентности + фактический проект/labels.

    `by_title` строится только по мигрированным задачам (те, у кого в description
    есть `Vault ID`), поэтому не зависит от проекта и не цепляет личные задачи.
    """

    def __init__(self, tasks: list[dict]) -> None:
        self.raw: dict[str, dict] = {}
        self.by_vault: dict[str, str] = {}
        self.by_title: dict[str, str] = {}
        self.project_of: dict[str, str] = {}
        self.labels_of: dict[str, list[str]] = {}
        self.closed: set[str] = set()
        self.scanned = len(tasks)
        for task in tasks:
            task_id = str(task.get("id") or "")
            if not task_id:
                continue
            self.raw[task_id] = task
            self.project_of[task_id] = str(task.get("project_id") or "")
            self.labels_of[task_id] = [str(x) for x in (task.get("labels") or [])]
            if task.get("checked") or task.get("completed_at"):
                self.closed.add(task_id)
            match = VAULT_ID_RE.search(task.get("description") or "")
            if match:
                self.by_vault.setdefault(match.group(1), task_id)
                self.by_title.setdefault(title_key(task.get("content") or ""), task_id)

    def exists(self, task_id: str) -> bool:
        return task_id in self.raw

    def forget(self, task_id: str) -> None:
        """Задача удалена — вычищаем из снимка, чтобы её не нашли повторно."""
        task = self.raw.pop(task_id, None)
        self.project_of.pop(task_id, None)
        self.labels_of.pop(task_id, None)
        self.closed.discard(task_id)
        if not task:
            return
        match = VAULT_ID_RE.search(task.get("description") or "")
        if match and self.by_vault.get(match.group(1)) == task_id:
            self.by_vault.pop(match.group(1), None)
        key = title_key(task.get("content") or "")
        if self.by_title.get(key) == task_id:
            self.by_title.pop(key, None)


def build_existing_index() -> TodoistIndex:
    tasks = todoist_client.list_open_tasks(log_fn=log)
    completed = fetch_todoist_completed_tasks()
    for task in completed:
        task.setdefault("checked", True)
    return TodoistIndex(tasks + completed)


def load_ledger() -> dict[str, str]:
    try:
        if not LEDGER_FILE.exists():
            return {}
        data = json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"todoist: ledger unreadable, starting fresh: {exc}")
        return {}
    return {str(k): str(v) for k, v in data.items() if v} if isinstance(data, dict) else {}


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# ── Labels областей ───────────────────────────────────────────────────────

def ensure_area_labels(
    linear_project_names: dict[str, str],
    execute: bool,
    report: dict[str, list[str]],
) -> tuple[dict[str, str], dict[str, str]]:
    """(linear project id → имя area-label, имя area-label → todoist label id)."""
    existing = {
        (l.get("name") or "").strip().casefold(): str(l.get("id"))
        for l in todoist_client.list_labels(log_fn=log)
        if l.get("id")
    }
    by_project: dict[str, str] = {}
    label_ids: dict[str, str] = {}
    for linear_id, name in sorted(linear_project_names.items(), key=lambda kv: kv[1]):
        clean = name.strip()
        if not clean:
            continue
        by_project[linear_id] = clean
        found = existing.get(clean.casefold())
        if found:
            report["reused"].append(f"area {clean} → {found}")
            label_ids[clean] = found
            continue
        if not execute:
            report["created"].append(f"area {clean}")
            label_ids[clean] = f"<new:{clean}>"
            continue
        new_id = todoist_client.create_label(clean, log_fn=log)
        time.sleep(WRITE_SLEEP_SECONDS)
        if new_id:
            report["created"].append(f"area {clean} → {new_id}")
            label_ids[clean] = new_id
            existing[clean.casefold()] = new_id
        else:
            report["failed"].append(f"area {clean}")
    return by_project, label_ids


def ensure_owner_labels(execute: bool, report: dict[str, list[str]]) -> dict[str, str]:
    """Только `unowned`: задачи с owner=other в Todoist больше не создаются."""
    existing = {
        (l.get("name") or "").strip().casefold(): str(l.get("id"))
        for l in todoist_client.list_labels(log_fn=log)
        if l.get("id")
    }
    result: dict[str, str] = {}
    name = todoist_client.LABEL_UNOWNED
    found = existing.get(name.casefold())
    if found:
        report["reused"].append(f"label {name} → {found}")
        result[name] = found
        return result
    if not execute:
        report["created"].append(f"label {name}")
        result[name] = f"<new:{name}>"
        return result
    new_id = todoist_client.create_label(name, log_fn=log)
    time.sleep(WRITE_SLEEP_SECONDS)
    if new_id:
        report["created"].append(f"label {name} → {new_id}")
        result[name] = new_id
    else:
        report["failed"].append(f"label {name}")
    return result


# ── Починка состояния ─────────────────────────────────────────────────────

def purge_other_tasks(
    issues: list[dict],
    ledger: dict[str, str],
    todo_map: dict[str, str],
    index: TodoistIndex,
    execute: bool,
    problems: list[str],
) -> Counter:
    """Удаляет из Todoist задачи с owner=other, созданные прошлыми прогонами.

    Решение Антона 2026-07-31: чужие задачи живут только в Linear и в vault.
    Полный JSON удаляемых задач сохраняется в backup-файл рядом с конфигами.
    """
    stats = Counter()
    backup: list[dict] = []
    for issue in issues:
        if issue_owner(issue) != "other":
            continue
        task_id = ledger.get(str(issue.get("id")))
        if not task_id or not index.exists(task_id):
            continue
        closed = task_id in index.closed
        stats["to_delete_closed" if closed else "to_delete_open"] += 1
        if not execute:
            continue
        backup.append(index.raw[task_id])
        data = todoist_client.todoist_request("DELETE", f"/tasks/{task_id}", log_fn=log)
        time.sleep(WRITE_SLEEP_SECONDS)
        if data is None:
            problems.append(f"{issue.get('identifier')}: не удалось удалить задачу {task_id}")
            stats["delete_failed"] += 1
            backup.pop()
            continue
        stats["deleted_closed" if closed else "deleted_open"] += 1
        index.forget(task_id)
        ledger.pop(str(issue.get("id")), None)
        vault_id = issue_vault_id(issue)
        if vault_id and todo_map.get(vault_id) == task_id:
            todo_map.pop(vault_id, None)
    if execute and backup:
        stamp = dt.datetime.now().strftime("%Y-%m-%d")
        path = CONFIG_DIR / f"todoist-deleted-backup-{stamp}.json"
        try:
            existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except Exception:
            existing = []
        save_json(path, (existing if isinstance(existing, list) else []) + backup)
        log(f"todoist: JSON удалённых задач сохранён в {path.name} ({len(backup)} шт.)")
    return stats


def reconcile_tasks(
    issues: list[dict],
    ledger: dict[str, str],
    todo_map: dict[str, str],
    area_of_project: dict[str, str],
    index: TodoistIndex,
    inbox_id: str,
    execute: bool,
    problems: list[str],
) -> Counter:
    """Идемпотентная починка: правильные labels + проект Inbox у существующих задач.

    Источник истины — Linear (project и owner у issue) + ledger (issue id → task id).
    Новых задач не создаёт: только `POST /tasks/{id}` и `POST /tasks/{id}/move`.
    Если всё на местах — ноль запросов на запись.
    """
    stats = Counter()
    for issue in issues:
        task_id = ledger.get(str(issue.get("id")))
        if not task_id:
            continue
        if not index.exists(task_id):
            # Задача удалена в Todoist (нами или руками) — запись ledger протухла.
            # Чистим её, иначе следующий прогон снова будет считать задачу мигрированной.
            stats["missing"] += 1
            if execute:
                ledger.pop(str(issue.get("id")), None)
                vault_id = issue_vault_id(issue)
                if vault_id and todo_map.get(vault_id) == task_id:
                    todo_map.pop(vault_id, None)
                stats["stale_ledger_cleared"] += 1
            else:
                problems.append(
                    f"{issue.get('identifier')}: задача {task_id} есть в ledger, но её нет "
                    "в Todoist (запись ledger будет вычищена)"
                )
            continue
        linear_project_id = (issue.get("project") or {}).get("id")
        area = area_of_project.get(linear_project_id) if linear_project_id else None
        wanted = todoist_client.task_labels(issue_owner(issue), area)
        current = index.labels_of.get(task_id, [])
        closed = task_id in index.closed
        needs_labels = sorted(x.casefold() for x in current) != sorted(x.casefold() for x in wanted)
        needs_move = bool(inbox_id) and index.project_of.get(task_id) != inbox_id
        if not needs_labels and not needs_move:
            stats["already_ok"] += 1
            continue
        stats["to_fix_closed" if closed else "to_fix_open"] += 1
        if not execute:
            continue
        ok = True
        if needs_labels:
            if todoist_client.update_task_labels(task_id, wanted, log_fn=log):
                index.labels_of[task_id] = wanted
                stats["labels_set"] += 1
            else:
                ok = False
                problems.append(f"{issue.get('identifier')}: не удалось проставить labels {task_id}")
            time.sleep(WRITE_SLEEP_SECONDS)
        if needs_move:
            if todoist_client.move_task(task_id, inbox_id, log_fn=log):
                index.project_of[task_id] = inbox_id
                stats["moved"] += 1
            else:
                ok = False
                problems.append(f"{issue.get('identifier')}: не удалось перенести {task_id} в Inbox")
            time.sleep(WRITE_SLEEP_SECONDS)
        stats["fixed_closed" if closed else "fixed_open"] += 1 if ok else 0
    return stats


def cleanup_migration_projects(
    todoist_projects: list[dict],
    linear_project_names: dict[str, str],
    index: TodoistIndex,
    execute: bool,
    problems: list[str],
) -> Counter:
    """Удаляет пустые проекты Todoist, созданные отменённой проектной схемой.

    Кандидат — только проект, имя которого совпадает с именем Linear-проекта
    (личные Films / Концерты / Reading list / Not urgent так не совпадут ни при
    каких условиях) И в котором не осталось ни одной задачи — ни открытой, ни
    закрытой. Пустота проверяется дважды: по снимку (открытые + закрытые) и живым
    запросом открытых задач непосредственно перед удалением.
    """
    stats = Counter()
    artifact_names = {name.strip().casefold() for name in linear_project_names.values() if name}
    for project in todoist_projects:
        project_id = str(project.get("id") or "")
        name = (project.get("name") or "").strip()
        if not project_id or project.get("inbox_project") or project.get("is_inbox_project"):
            continue
        if name.casefold() not in artifact_names:
            continue
        stats["candidates"] += 1
        left = [tid for tid, pid in index.project_of.items() if pid == project_id]
        live = todoist_client.todoist_list(
            "/tasks", params={"project_id": project_id}, log_fn=log
        )
        if left or live:
            problems.append(
                f"проект '{name}' ({project_id}) не пуст "
                f"({len(left)} по снимку / {len(live)} открытых сейчас) — не удаляю"
            )
            stats["not_empty"] += 1
            continue
        stats["to_delete"] += 1
        if not execute:
            continue
        data = todoist_client.todoist_request("DELETE", f"/projects/{project_id}", log_fn=log)
        time.sleep(WRITE_SLEEP_SECONDS)
        if data is None:
            problems.append(f"проект '{name}' ({project_id}): удаление не удалось")
            stats["delete_failed"] += 1
            continue
        log(f"todoist: удалён пустой проект '{name}' ({project_id})")
        stats["deleted"] += 1
    return stats


def cleanup_other_label(index: TodoistIndex, execute: bool) -> Optional[str]:
    """Label `other` в новой схеме не используется: удаляем, если он ничей."""
    in_use = any(
        todoist_client.LABEL_OTHER.casefold() in {x.casefold() for x in labels}
        for labels in index.labels_of.values()
    )
    if in_use:
        return "используется задачами — оставлен"
    label = next(
        (l for l in todoist_client.list_labels(log_fn=log)
         if (l.get("name") or "").strip().casefold() == todoist_client.LABEL_OTHER),
        None,
    )
    if not label:
        return None
    if not execute:
        return f"будет удалён ({label.get('id')})"
    data = todoist_client.todoist_request("DELETE", f"/labels/{label['id']}", log_fn=log)
    time.sleep(WRITE_SLEEP_SECONDS)
    return "удалён" if data is not None else "удалить не удалось"


# ── Миграция ──────────────────────────────────────────────────────────────

def migrate(execute: bool, limit: Optional[int]) -> int:
    mode = "EXECUTE" if execute else "DRY-RUN"
    log(f"=== Linear → Todoist migration [{mode}] ===")

    linear_project_names = fetch_linear_projects()
    issues = fetch_linear_issues()
    if not issues:
        log("Нет issues из Linear — прекращаю (проверь LINEAR_API_KEY и сеть).")
        return 1
    log(f"linear: {len(issues)} issues получено")

    existing_projects = todoist_client.list_projects(log_fn=log)
    if not existing_projects:
        log("todoist: список проектов пуст или недоступен — прекращаю.")
        return 1
    inbox_id = next(
        (str(p["id"]) for p in existing_projects
         if p.get("inbox_project") or p.get("is_inbox_project")),
        "",
    )
    if not inbox_id:
        log("todoist: Inbox не найден — прекращаю (все рабочие задачи живут в Inbox).")
        return 1
    log(f"todoist: {len(existing_projects)} существующих проектов, Inbox={inbox_id}")

    label_report: dict[str, list[str]] = {"reused": [], "created": [], "failed": []}
    area_of_project, area_label_ids = ensure_area_labels(
        linear_project_names, execute, label_report
    )
    owner_label_ids = ensure_owner_labels(execute, label_report)
    # Fail-fast: без labels области задачи потеряли бы принадлежность и молча
    # смешались бы в Inbox (инцидент 2026-07-31 с проектами и лимитом тарифа).
    if label_report["failed"]:
        log("")
        log("ОСТАНОВКА: не удалось создать labels в Todoist:")
        for name in label_report["failed"]:
            log(f"  · {name}")
        log("Задачи не создавались и не менялись — состояние Todoist не изменено.")
        return 2

    unknown_projects = sorted(
        {pid for pid in ((i.get("project") or {}).get("id") for i in issues) if pid}
        - set(area_of_project)
    )
    if unknown_projects:
        log("")
        log("ОСТАНОВКА: у issues есть Linear-проекты, которых нет в маппинге областей:")
        for pid in unknown_projects:
            log(f"  · {pid}")
        log("Иначе эти задачи остались бы без области. Состояние Todoist не изменено.")
        return 2

    index = build_existing_index()
    log(f"todoist: проиндексировано {index.scanned} существующих задач "
        f"({len(index.by_vault)} с Vault ID)")
    ledger = load_ledger()
    todo_map = todoist_client.load_todo_task_map(log_fn=log)

    stats = Counter()
    per_area: Counter = Counter()
    no_area_titles: list[str] = []
    problems: list[str] = []

    ordered = sorted(issues, key=lambda i: i.get("createdAt") or "")
    if limit:
        ordered = ordered[:limit]

    # ── 1. зачистка чужих задач ───────────────────────────────────────────
    purge_stats = purge_other_tasks(ordered, ledger, todo_map, index, execute, problems)

    # ── 2. создание недостающих задач ─────────────────────────────────────
    for issue in ordered:
        identifier = issue.get("identifier") or issue.get("id") or "?"
        title = (issue.get("title") or "").strip()
        if not title:
            problems.append(f"{identifier}: пустой заголовок, пропуск")
            stats["skipped_empty"] += 1
            continue
        if title.casefold() in LINEAR_ONBOARDING_TITLES:
            stats["skipped_onboarding"] += 1
            continue
        owner = issue_owner(issue)
        closed = issue_is_closed(issue)
        linear_project_id = (issue.get("project") or {}).get("id")
        area = area_of_project.get(linear_project_id) if linear_project_id else None
        vault_id = issue_vault_id(issue)
        description = (issue.get("description") or "").strip() or f"Linear: {identifier}"

        stats["closed" if closed else "open"] += 1
        if owner == "other":
            # Чужие задачи в Todoist не живут (решение Антона 2026-07-31).
            stats["skipped_other"] += 1
            continue
        per_area[area or "без области"] += 1
        if not area:
            no_area_titles.append(f"{identifier} · {title[:60]}")

        # ── идемпотентность: ledger → Vault ID → заголовок мигрированной задачи ──
        known = ledger.get(str(issue.get("id")))
        if known and not index.exists(known):
            known = None  # задача удалена в Todoist — создаём заново
        if not known and vault_id:
            known = index.by_vault.get(vault_id)
        if not known:
            known = index.by_title.get(title_key(title))
        if known:
            ledger[str(issue.get("id"))] = str(known)
            stats["already_migrated"] += 1
            if vault_id:
                todo_map[vault_id] = str(known)
            continue

        stats["to_create"] += 1
        if not execute:
            continue

        # Перенос as-is: description Linear уже в нужном формате (Vault ID / встреча /
        # исполнитель), пересобирать его через create_task() значило бы потерять данные.
        task_id = _create_task_verbatim(title, description, owner, area, inbox_id)
        time.sleep(WRITE_SLEEP_SECONDS)
        if not task_id:
            problems.append(f"{identifier}: создание задачи не удалось")
            stats["create_failed"] += 1
            continue
        ledger[str(issue.get("id"))] = task_id
        index.raw[task_id] = {
            "id": task_id, "content": title, "description": description,
            "project_id": inbox_id, "labels": todoist_client.task_labels(owner, area),
        }
        index.project_of[task_id] = inbox_id
        index.labels_of[task_id] = todoist_client.task_labels(owner, area)
        if vault_id:
            todo_map[vault_id] = task_id
        stats["created"] += 1
        if closed:
            if todoist_client.close_task(task_id, log_fn=log):
                index.closed.add(task_id)
                stats["closed_after_create"] += 1
            else:
                problems.append(f"{identifier}: задача создана, но не закрыта ({task_id})")
            time.sleep(WRITE_SLEEP_SECONDS)

    # ── 3. починка labels/проекта у уже существующих задач ────────────────
    fix_stats = reconcile_tasks(
        ordered, ledger, todo_map, area_of_project, index, inbox_id, execute, problems
    )

    # ── 4. удаление артефактов отменённой проектной схемы ─────────────────
    project_stats = cleanup_migration_projects(
        existing_projects, linear_project_names, index, execute, problems,
    )
    other_label_state = cleanup_other_label(index, execute)

    # ── запись конфигов ───────────────────────────────────────────────────
    label_map_out = build_meeting_label_map(area_of_project)
    ids_config = {
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "migrate_linear_to_todoist.py",
        "scheme": "labels-in-inbox",
        "inbox_project_id": inbox_id,
        "labels": owner_label_ids,
        "area_labels": area_label_ids,
        "work_scope_project_ids": [inbox_id],
    }

    if execute:
        save_json(todoist_client.LABEL_MAP_FILE, label_map_out)
        save_json(todoist_client.TODO_TASK_MAP_FILE, todo_map)
        save_json(todoist_client.IDS_CONFIG_FILE, ids_config)
        save_json(LEDGER_FILE, ledger)
        if OBSOLETE_PROJECT_MAP_FILE.exists():
            OBSOLETE_PROJECT_MAP_FILE.replace(
                OBSOLETE_PROJECT_MAP_FILE.with_suffix(".json.obsolete")
            )
            log("todoist: todoist-project-map.json переименован в .json.obsolete "
                "(проектной схемы больше нет)")
        log("todoist: конфиги записаны "
            f"({todoist_client.LABEL_MAP_FILE.name}, {todoist_client.TODO_TASK_MAP_FILE.name}, "
            f"{todoist_client.IDS_CONFIG_FILE.name}, {LEDGER_FILE.name})")

    print_summary(
        mode, label_report, label_map_out, stats, per_area, no_area_titles, problems,
        ids_config, purge_stats, fix_stats, project_stats, other_label_state,
    )
    return 0


def _create_task_verbatim(
    title: str, description: str, owner: str, area: Optional[str], inbox_id: str
) -> Optional[str]:
    """Создание задачи с готовым description (без пересборки в build_task_description)."""
    payload: dict[str, Any] = {
        "content": title, "description": description, "project_id": inbox_id,
    }
    labels = todoist_client.task_labels(owner, area)
    if labels:
        payload["labels"] = labels
    data = todoist_client.todoist_request("POST", "/tasks", payload=payload, log_fn=log)
    if not data or not data.get("id"):
        return None
    return str(data["id"])


def build_meeting_label_map(area_of_project: dict[str, str]) -> dict[str, str]:
    """linear-project-map.json (встреча → linear project) → встреча → area-label."""
    try:
        source = json.loads(LINEAR_PROJECT_MAP_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"linear: project map unreadable ({LINEAR_PROJECT_MAP_FILE}): {exc}")
        return {}
    if not isinstance(source, dict):
        return {}
    out: dict[str, str] = {}
    for meeting_key, linear_project_id in source.items():
        target = area_of_project.get(str(linear_project_id))
        if target:
            out[str(meeting_key)] = target
        else:
            log(f"todoist: встреча '{meeting_key}' ссылается на неизвестный проект "
                f"{linear_project_id}")
    return out


def print_summary(
    mode: str,
    label_report: dict[str, list[str]],
    label_map_out: dict[str, str],
    stats: Counter,
    per_area: Counter,
    no_area_titles: list[str],
    problems: list[str],
    ids_config: dict,
    purge_stats: Counter,
    fix_stats: Counter,
    project_stats: Counter,
    other_label_state: Optional[str],
) -> None:
    dry = mode == "DRY-RUN"
    log("")
    log(f"───── СВОДКА [{mode}] ─────")
    log("")
    log("Labels (области + owner):")
    for line in label_report["reused"]:
        log(f"  переиспользуется: {line}")
    for line in label_report["created"]:
        log(f"  будет создан:     {line}" if dry else f"  создан: {line}")
    for line in label_report["failed"]:
        log(f"  ОШИБКА: {line}")
    if other_label_state:
        log(f"  label other: {other_label_state}")
    log("")
    log("Задачи:")
    log(f"  всего issues в Linear:      {stats['open'] + stats['closed']}")
    log(f"    открытых:                 {stats['open']}")
    log(f"    закрытых (Done/Canceled): {stats['closed']}")
    log(f"  пропущено (owner=other):    {stats['skipped_other']}")
    log(f"  уже есть в Todoist (skip):  {stats['already_migrated']}")
    log(f"  будет создано:              {stats['to_create']}"
        if dry else f"  создано:                    {stats['created']}")
    if not dry:
        log(f"  закрыто после создания:     {stats['closed_after_create']}")
        log(f"  ошибок создания:            {stats['create_failed']}")
    if stats["skipped_empty"]:
        log(f"  пропущено (пустой заголовок): {stats['skipped_empty']}")
    if stats["skipped_onboarding"]:
        log(f"  пропущено (демо-issues Linear): {stats['skipped_onboarding']}")
    log("")
    log("Зачистка чужих задач (owner=other) из Todoist:")
    if dry:
        log(f"  будет удалено открытых:     {purge_stats['to_delete_open']}")
        log(f"  будет удалено закрытых:     {purge_stats['to_delete_closed']}")
    else:
        log(f"  удалено открытых:           {purge_stats['deleted_open']}"
            f" из {purge_stats['to_delete_open']}")
        log(f"  удалено закрытых:           {purge_stats['deleted_closed']}"
            f" из {purge_stats['to_delete_closed']}")
        if purge_stats["delete_failed"]:
            log(f"  ОШИБОК удаления:            {purge_stats['delete_failed']}")
    log("")
    log("Починка существующих задач (labels + перенос в Inbox):")
    log(f"  уже корректны:              {fix_stats['already_ok']}")
    if dry:
        log(f"  будет починено открытых:    {fix_stats['to_fix_open']}")
        log(f"  будет починено закрытых:    {fix_stats['to_fix_closed']}")
    else:
        log(f"  починено открытых:          {fix_stats['fixed_open']}"
            f" из {fix_stats['to_fix_open']}")
        log(f"  починено закрытых:          {fix_stats['fixed_closed']}"
            f" из {fix_stats['to_fix_closed']}")
        log(f"    проставлено labels:       {fix_stats['labels_set']}")
        log(f"    перенесено в Inbox:       {fix_stats['moved']}")
    if fix_stats["missing"]:
        log(f"  в ledger есть, в Todoist нет: {fix_stats['missing']}"
            f" (вычищено записей ledger: {fix_stats['stale_ledger_cleared']})")
    log("")
    log("Проекты-артефакты отменённой схемы:")
    log(f"  кандидатов:                 {project_stats['candidates']}")
    log(f"  не пусты (пропущены):       {project_stats['not_empty']}")
    log(f"  будет удалено:              {project_stats['to_delete']}"
        if dry else f"  удалено:                    {project_stats['deleted']}")
    log("")
    log("По областям (area-labels):")
    for name, count in per_area.most_common():
        log(f"  {count:>4}  {name}")
    log("")
    log(f"Без области (issues без проекта в Linear): {len(no_area_titles)}")
    for line in no_area_titles[:15]:
        log(f"  · {line}")
    if len(no_area_titles) > 15:
        log(f"  … ещё {len(no_area_titles) - 15}")
    log("")
    log(f"todoist-label-map.json: {len(label_map_out)} записей встреча → area-label")
    log(f"todoist-ids.json: {len(ids_config['area_labels'])} area-labels, "
        f"scope={ids_config['work_scope_project_ids']}")
    log("")
    if problems:
        log(f"Проблемы ({len(problems)}):")
        for line in problems[:25]:
            log(f"  ! {line}")
        if len(problems) > 25:
            log(f"  … ещё {len(problems) - 25}")
    else:
        log("Проблем не обнаружено.")
    if dry:
        log("")
        log("Ничего не записано (ни в Todoist, ни в локальные конфиги).")
        log("Боевой прогон: python3 migrate_linear_to_todoist.py --execute")


def main() -> int:
    parser = argparse.ArgumentParser(description="Миграция задач Linear (ROZ) → Todoist")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="только сводка, ничего не пишется (поведение по умолчанию)")
    parser.add_argument("--execute", action="store_true",
                        help="боевой прогон: изменения в Todoist и запись конфигов")
    parser.add_argument("--limit", type=int, default=None,
                        help="обработать только первые N issues (отладка)")
    args = parser.parse_args()
    return migrate(execute=args.execute, limit=args.limit)


if __name__ == "__main__":
    sys.exit(main())
