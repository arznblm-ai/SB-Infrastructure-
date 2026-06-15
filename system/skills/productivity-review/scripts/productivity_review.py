#!/usr/bin/env python3
import argparse
import calendar
import datetime as dt
import json
import os
import re
from pathlib import Path

VAULT = Path("/Users/anton/AI AGENT FOLDER/Second Brain")
OUTPUT_DIR = VAULT / "infrastructure" / "Productivity Review"
STATS_FILE = OUTPUT_DIR / "stats" / "productivity-stats.jsonl"
GOOGLE_TOKEN_FILE = Path("/Users/anton/.config/second-brain/google-calendar-token.json")
GOOGLE_CREDENTIALS_FILE = Path("/Users/anton/.config/second-brain/google-calendar-credentials.json")
COMPLETED_TASKS_FILE = Path("/Users/anton/.config/second-brain/telegram-codex-bot.completed-tasks.json")
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
CALENDAR_IDS = ["primary", "tony@portalcg.us"]


def google_calendar_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if GOOGLE_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_FILE), GOOGLE_SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not GOOGLE_CREDENTIALS_FILE.exists():
            raise RuntimeError(f"Google Calendar credentials not found: {GOOGLE_CREDENTIALS_FILE}")
        flow = InstalledAppFlow.from_client_secrets_file(str(GOOGLE_CREDENTIALS_FILE), GOOGLE_SCOPES)
        creds = flow.run_local_server(port=0)
        GOOGLE_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        os.chmod(GOOGLE_TOKEN_FILE, 0o600)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def parse_date(value: str | None) -> dt.date:
    return dt.date.fromisoformat(value) if value else dt.date.today()


def period_range(period: str, anchor: dt.date) -> tuple[dt.date, dt.date, str]:
    if period == "week":
        start = anchor - dt.timedelta(days=anchor.weekday())
        end = start + dt.timedelta(days=7)
        label = f"{start.isoformat()} to {(end - dt.timedelta(days=1)).isoformat()}"
        return start, end, label
    if period == "month":
        start = anchor.replace(day=1)
        if start.month == 12:
            end = dt.date(start.year + 1, 1, 1)
        else:
            end = dt.date(start.year, start.month + 1, 1)
        label = f"{calendar.month_name[start.month]} {start.year}"
        return start, end, label
    start = dt.date(anchor.year, 1, 1)
    end = dt.date(anchor.year + 1, 1, 1)
    return start, end, str(anchor.year)


def read_events(start: dt.date, end: dt.date) -> list[dict]:
    service = google_calendar_service()
    start_dt = dt.datetime.combine(start, dt.time.min).astimezone()
    end_dt = dt.datetime.combine(end, dt.time.min).astimezone()
    events: list[dict] = []
    for calendar_id in CALENDAR_IDS:
        try:
            response = service.events().list(
                calendarId=calendar_id,
                timeMin=start_dt.isoformat(timespec="seconds"),
                timeMax=end_dt.isoformat(timespec="seconds"),
                singleEvents=True,
                orderBy="startTime",
                maxResults=2500,
            ).execute()
        except Exception as exc:
            events.append({"calendar": calendar_id, "error": str(exc)})
            continue
        for item in response.get("items", []):
            start_raw = item.get("start", {}).get("dateTime")
            end_raw = item.get("end", {}).get("dateTime")
            if not start_raw or not end_raw:
                continue
            start_value = dt.datetime.fromisoformat(start_raw).replace(tzinfo=None)
            end_value = dt.datetime.fromisoformat(end_raw).replace(tzinfo=None)
            if end_value <= start_value:
                continue
            events.append({
                "calendar": calendar_id,
                "id": item.get("id", ""),
                "summary": item.get("summary", "(untitled)"),
                "start": start_value.isoformat(timespec="minutes"),
                "end": end_value.isoformat(timespec="minutes"),
                "hours": round((end_value - start_value).total_seconds() / 3600, 2),
            })
    return events


def task_text_from_focus(summary: str) -> str:
    return summary[len("Focus: "):].strip() if summary.startswith("Focus: ") else summary


def category_for_event(event: dict) -> str:
    summary = event.get("summary", "").lower()
    calendar_id = event.get("calendar", "")
    text = task_text_from_focus(event.get("summary", "")).lower()
    if event.get("summary", "").startswith("Focus: "):
        if any(word in text for word in ["зал", "спорт", "health", "gym"]):
            return "fitness"
        if any(word in text for word in ["лекци", "ai mindset", "обуч", "курс", "study"]):
            return "learning"
        if any(word in text for word in ["агентств", "outreach", "партнер", "партнёр"]):
            return "outreach"
        if any(word in text for word in ["счета", "медицин", "документ", "admin"]):
            return "admin"
        if any(word in text for word in ["тг", "telegram", "бот", "agent", "репозитор", "скил", "инфраструкт"]):
            return "infrastructure"
        return "deep_work"
    if calendar_id == "tony@portalcg.us":
        return "meetings_work"
    if any(word in summary for word in ["meeting", "zoom", "созвон", "встреч", "синк", "call"]):
        return "meetings_work"
    if any(word in summary for word in ["зал", "спорт", "gym"]):
        return "fitness"
    if any(word in summary for word in ["лекци", "курс", "study", "обуч"]):
        return "learning"
    return "other_calendar"


def read_completed_tasks(start: dt.date, end: dt.date) -> list[dict]:
    if not COMPLETED_TASKS_FILE.exists():
        return []
    try:
        entries = json.loads(COMPLETED_TASKS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    result = []
    for entry in entries:
        raw = entry.get("completed_at", "")
        try:
            completed_at = dt.datetime.fromisoformat(raw)
        except Exception:
            continue
        if start <= completed_at.date() < end:
            result.append(entry)
    return result


def event_datetimes(event: dict) -> tuple[dt.datetime, dt.datetime] | None:
    try:
        return dt.datetime.fromisoformat(event["start"]), dt.datetime.fromisoformat(event["end"])
    except Exception:
        return None


def elapsed_event_hours(event: dict, now: dt.datetime) -> float:
    parsed = event_datetimes(event)
    if not parsed:
        return 0.0
    start, end = parsed
    if start >= now:
        return 0.0
    elapsed_end = min(end, now)
    if elapsed_end <= start:
        return 0.0
    return round((elapsed_end - start).total_seconds() / 3600, 2)


def focus_task_key(task: str) -> str:
    lowered = task.lower().replace("ё", "е")
    lowered = re.sub(r"[()]", "", lowered)
    lowered = re.sub(r"[^a-zа-я0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def focus_completion_from_calendar(events: list[dict]) -> dict:
    now = dt.datetime.now()
    past: dict[str, str] = {}
    future: dict[str, str] = {}
    completed_blocks: list[str] = []
    for event in events:
        summary = event.get("summary", "")
        if not summary.startswith("Focus: "):
            continue
        task = task_text_from_focus(summary)
        parsed = event_datetimes(event)
        if not parsed:
            continue
        _, end = parsed
        key = focus_task_key(task)
        if end <= now:
            past[key] = task
            completed_blocks.append(f"{end.isoformat(timespec='minutes')} | {task}")
        else:
            future[key] = task
    fully_completed = sorted(task for key, task in past.items() if key not in future)
    in_progress = sorted(task for key, task in past.items() if key in future)
    return {
        "completed_blocks": sorted(completed_blocks),
        "fully_completed_tasks": fully_completed,
        "in_progress_tasks": in_progress,
        "future_tasks": sorted(future.values()),
    }


def summarize(events: list[dict], manual_completed: list[dict], period: str, start: dt.date, end: dt.date, label: str) -> dict:
    hours: dict[str, float] = {}
    elapsed_hours: dict[str, float] = {}
    counted_events = [event for event in events if "error" not in event]
    now = dt.datetime.now()
    for event in counted_events:
        category = category_for_event(event)
        hours[category] = round(hours.get(category, 0.0) + float(event.get("hours", 0)), 2)
        elapsed = elapsed_event_hours(event, now)
        if elapsed:
            elapsed_hours[category] = round(elapsed_hours.get(category, 0.0) + elapsed, 2)
    focus_completion = focus_completion_from_calendar(counted_events)
    completed_focus_tasks = focus_completion["fully_completed_tasks"]
    manual_completed_tasks = [entry.get("task", "") for entry in manual_completed if entry.get("task")]
    completed_tasks = sorted(set(completed_focus_tasks + manual_completed_tasks))
    work_categories = ["deep_work", "meetings_work", "outreach", "admin", "infrastructure"]
    work_hours = round(sum(elapsed_hours.get(name, 0.0) for name in work_categories), 2)
    total_hours = round(sum(hours.values()), 2)
    elapsed_total_hours = round(sum(elapsed_hours.values()), 2)
    score = productivity_score(elapsed_hours, completed_tasks)
    return {
        "period": period,
        "period_start": start.isoformat(),
        "period_end": (end - dt.timedelta(days=1)).isoformat(),
        "label": label,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "hours_by_category": dict(sorted(hours.items())),
        "elapsed_hours_by_category": dict(sorted(elapsed_hours.items())),
        "total_calendar_hours": total_hours,
        "elapsed_calendar_hours": elapsed_total_hours,
        "future_scheduled_hours": round(max(total_hours - elapsed_total_hours, 0.0), 2),
        "work_hours": work_hours,
        "learning_hours": elapsed_hours.get("learning", 0.0),
        "fitness_hours": elapsed_hours.get("fitness", 0.0),
        "deep_work_hours": elapsed_hours.get("deep_work", 0.0),
        "meeting_hours": elapsed_hours.get("meetings_work", 0.0),
        "completed_tasks_count": len(completed_tasks),
        "completed_tasks": completed_tasks,
        "completed_focus_blocks_count": len(focus_completion["completed_blocks"]),
        "completed_focus_blocks": focus_completion["completed_blocks"],
        "in_progress_focus_tasks": focus_completion["in_progress_tasks"],
        "future_focus_tasks": focus_completion["future_tasks"],
        "manual_completed_tasks_count": len(manual_completed_tasks),
        "events_count": len(counted_events),
        "calendar_errors": [event for event in events if "error" in event],
        "productivity_score": score,
    }


def productivity_score(hours: dict[str, float], completed_tasks: list[str]) -> int:
    work = sum(hours.get(name, 0.0) for name in ["deep_work", "meetings_work", "outreach", "admin", "infrastructure"])
    deep = hours.get("deep_work", 0.0)
    learning = hours.get("learning", 0.0)
    fitness = hours.get("fitness", 0.0)
    score = (
        min(work / 35, 1) * 45
        + min(deep / 12, 1) * 20
        + min(learning / 6, 1) * 12
        + min(fitness / 5, 1) * 13
        + min(len(completed_tasks) / 5, 1) * 10
    )
    return int(score)


def read_stats() -> list[dict]:
    if not STATS_FILE.exists():
        return []
    rows = []
    for line in STATS_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def write_stat(record: dict) -> None:
    STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        row for row in read_stats()
        if not (row.get("period") == record.get("period") and row.get("period_start") == record.get("period_start"))
    ]
    rows.append(record)
    rows = sorted(rows, key=lambda row: (row.get("period_start", ""), row.get("period", "")))
    STATS_FILE.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def report_path(period: str, start: dt.date) -> Path:
    folder = {"week": "weekly", "month": "monthly", "year": "yearly"}[period]
    label = {"week": "weekly", "month": "monthly", "year": "yearly"}[period]
    return OUTPUT_DIR / "reviews" / folder / f"{{productivity}} {label} review – {start.isoformat()}.md"


def format_hours_table(hours: dict[str, float]) -> str:
    if not hours:
        return "| Category | Hours |\n|---|---:|\n| none | 0 |"
    lines = ["| Category | Hours |", "|---|---:|"]
    for category, value in sorted(hours.items(), key=lambda item: item[1], reverse=True):
        lines.append(f"| {category} | {value:.2f} |")
    return "\n".join(lines)


def markdown_report(record: dict, events: list[dict]) -> str:
    title = record["period"].capitalize()
    lines = [
        f"# Productivity {title} Review — {record['label']}",
        "",
        "## Short read",
        f"- Elapsed calendar-based estimate: {record['elapsed_calendar_hours']:.2f}h.",
        f"- Future scheduled calendar time in this period: {record['future_scheduled_hours']:.2f}h.",
        f"- Work elapsed estimate: {record['work_hours']:.2f}h; deep work: {record['deep_work_hours']:.2f}h; meetings: {record['meeting_hours']:.2f}h.",
        f"- Learning elapsed: {record['learning_hours']:.2f}h; fitness elapsed: {record['fitness_hours']:.2f}h.",
        f"- Completed Focus blocks: {record['completed_focus_blocks_count']}.",
        f"- Fully completed tasks: {record['completed_tasks_count']}.",
        f"- Heuristic productivity score: {record['productivity_score']}/100.",
        "",
        "## Elapsed hours by category",
        format_hours_table(record["elapsed_hours_by_category"]),
        "",
        "## Full-period scheduled hours by category",
        format_hours_table(record["hours_by_category"]),
        "",
        "## Completed Focus blocks",
    ]
    if record.get("completed_focus_blocks"):
        lines.extend(f"- {block}" for block in record["completed_focus_blocks"])
    else:
        lines.append("- none found in past Focus slots")

    lines.extend([
        "",
        "## Fully completed tasks",
    ])
    if record["completed_tasks"]:
        lines.extend(f"- {task}" for task in record["completed_tasks"])
    else:
        lines.append("- none fully completed")
    if record.get("in_progress_focus_tasks"):
        lines.extend(["", "## In-progress Focus tasks"])
        lines.extend(f"- {task}" for task in record["in_progress_focus_tasks"])
    if record.get("future_focus_tasks"):
        lines.extend(["", "## Future scheduled Focus tasks"])
        lines.extend(f"- {task}" for task in record["future_focus_tasks"])

    lines.extend(["", "## Calendar event sample"])
    for event in [item for item in events if "error" not in item][:40]:
        start = event["start"].replace("T", " ")
        end = event["end"].split("T")[-1]
        lines.append(f"- {start}-{end} | {category_for_event(event)} | {event.get('summary', '')}")

    if record["calendar_errors"]:
        lines.extend(["", "## Calendar read warnings"])
        for error in record["calendar_errors"]:
            lines.append(f"- {error.get('calendar')}: {error.get('error')}")

    lines.extend([
        "",
        "## Notes",
        "- Calendar time is an estimate, not perfect ground truth.",
        "- A Focus slot in the past is treated as a completed block by default.",
        "- A task is fully completed only when it has past Focus work and no known future Focus slots.",
        "- `/done` is only a legacy/manual override.",
        "- Monthly and yearly reviews should compare against `stats/productivity-stats.jsonl`.",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Productivity Review from calendar Focus slots and completed-task overrides.")
    parser.add_argument("--period", choices=["week", "month", "year"], default="week")
    parser.add_argument("--date", help="Anchor date YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--dry-run", action="store_true", help="Print summary without saving report/stats.")
    args = parser.parse_args()

    anchor = parse_date(args.date)
    start, end, label = period_range(args.period, anchor)
    events = read_events(start, end)
    manual_completed = read_completed_tasks(start, end)
    record = summarize(events, manual_completed, args.period, start, end, label)
    path = report_path(args.period, start)
    report = markdown_report(record, events)

    print(f"Productivity Review {args.period}: {label}")
    print(f"Elapsed calendar estimate: {record['elapsed_calendar_hours']:.2f}h")
    print(f"Future scheduled in period: {record['future_scheduled_hours']:.2f}h")
    print(f"Elapsed work: {record['work_hours']:.2f}h | Learning: {record['learning_hours']:.2f}h | Fitness: {record['fitness_hours']:.2f}h")
    print(f"Completed Focus blocks: {record['completed_focus_blocks_count']}")
    print(f"Fully completed tasks: {record['completed_tasks_count']}")
    print(f"Score: {record['productivity_score']}/100")
    if args.dry_run:
        print("Dry run: not saved")
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    write_stat(record)
    print(f"Saved report: {path}")
    print(f"Updated stats: {STATS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
