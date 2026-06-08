#!/usr/bin/env bash
set -euo pipefail

VAULT="/Users/anton/AI AGENT FOLDER/Second Brain"
DAILY_DIR="$VAULT/infrastructure/daily focus"
ROOT_CONTEXT="$VAULT/claude.md"
AGENT_CONTEXT="$DAILY_DIR/CLAUDE.md"
OUTPUT_DIR="$DAILY_DIR/daily focus messages"
PLANNING_INBOX="$DAILY_DIR/planning inbox"
PLANNING_MEMORY="$DAILY_DIR/planning memory"
MEETING_ACTION_CANDIDATES="$DAILY_DIR/meeting action candidates"
LOG_FILE="/Users/anton/Library/Logs/daily-focus.log"
LAST_MESSAGE="/Users/anton/Library/Logs/daily-focus.last-message.md"
TELEGRAM_ENV="/Users/anton/.config/second-brain/daily-focus.env"
CODEX_BIN="${CODEX_BIN:-/Applications/Codex.app/Contents/Resources/codex}"

usage() {
  cat <<'USAGE'
Usage: run_daily_focus.sh [--dry-run]

Runs Daily Focus Assistant through Codex.

Options:
  --dry-run   Validate paths and Codex binary without launching a model run.
USAGE
}

send_telegram_delivery() {
  if [[ "${DAILY_FOCUS_ENABLE_TELEGRAM_DELIVERY:-0}" != "1" ]]; then
    echo "Telegram delivery skipped: DAILY_FOCUS_ENABLE_TELEGRAM_DELIVERY is not set to 1" >> "$LOG_FILE"
    return 0
  fi

  if [[ "${DAILY_FOCUS_SKIP_TELEGRAM_DELIVERY:-0}" == "1" ]]; then
    echo "Telegram delivery skipped: DAILY_FOCUS_SKIP_TELEGRAM_DELIVERY=1" >> "$LOG_FILE"
    return 0
  fi

  if [[ ! -f "$TELEGRAM_ENV" ]]; then
    echo "Telegram delivery skipped: env file not found at $TELEGRAM_ENV" >> "$LOG_FILE"
    return 0
  fi

  # Secrets live outside the vault. This file should define TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
  # shellcheck disable=SC1090
  source "$TELEGRAM_ENV"

  if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${TELEGRAM_CHAT_ID:-}" ]]; then
    echo "Telegram delivery skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is empty" >> "$LOG_FILE"
    return 0
  fi

  local latest_brief
  latest_brief="$(find "$OUTPUT_DIR" -maxdepth 1 -type f -name '*.md' -print0 | xargs -0 ls -t 2>/dev/null | head -1 || true)"
  if [[ -z "$latest_brief" || ! -s "$latest_brief" ]]; then
    echo "Telegram delivery skipped: latest brief is empty or missing in $OUTPUT_DIR" >> "$LOG_FILE"
    return 0
  fi

  local api="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}"
  local today
  today="$(date '+%Y-%m-%d')"
  local message
  message="$(python3 - "$latest_brief" "$today" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
today = sys.argv[2]
text = path.read_text(encoding="utf-8", errors="replace")

def section(title: str) -> str:
    pattern = rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, text, flags=re.M | re.S)
    return match.group(1).strip() if match else ""

def task_blocks() -> list[dict[str, str]]:
    body = section("Task Inbox Normalization")
    blocks = re.split(r"\n(?=- task: )", body)
    result = []
    for block in blocks:
        task = re.search(r"^- task:\s*(.+)$", block, flags=re.M)
        if not task:
            continue
        status = re.search(r"^\s*status:\s*(.+)$", block, flags=re.M)
        deps = re.search(r"^\s*dependencies:\s*(.+)$", block, flags=re.M)
        result.append({
            "task": task.group(1).strip(),
            "status": status.group(1).strip() if status else "unknown",
            "dependencies": deps.group(1).strip() if deps else "unknown",
        })
    return result

def bullets_from_subheading(body: str, subheading: str, limit: int = 4) -> list[str]:
    pattern = rf"^### {re.escape(subheading)}\n(.*?)(?=^### |\Z)"
    match = re.search(pattern, body, flags=re.M | re.S)
    if not match:
        return []
    lines = []
    for raw in match.group(1).splitlines():
        line = raw.strip()
        if line.startswith("- "):
            lines.append(line[2:].strip())
        if len(lines) >= limit:
            break
    return lines

tasks = task_blocks()
completed = [t for t in tasks if "completed" in t["status"]]
in_progress = [t for t in tasks if "in progress" in t["status"] or "scheduled" in t["status"]]
open_tasks = [
    t for t in tasks
    if "completed" not in t["status"]
    and any(marker in t["status"] for marker in ["carry-over", "needs review", "needs clarification", "blocked"])
]

plan = section("Candidate Plan: next 2-3 days")
known_slots = bullets_from_subheading(plan, "Known Focus slots from calendar", limit=3)

actions = section("Meeting Action Candidates")
pending_match = re.search(r"Pending confirmation count:\s*(\d+)", actions)
pending_count = pending_match.group(1) if pending_match else None

questions_body = section("Questions for Anton")
questions = []
for raw in questions_body.splitlines():
    line = re.sub(r"^\s*\d+\.\s*", "", raw.strip())
    if line:
        questions.append(line)
    if len(questions) >= 3:
        break

lines = [f"Daily Focus — {today}", ""]
lines.append("Картина:")
lines.append(
    f"{len(tasks)} задач в плановом слое: "
    f"{len(completed)} закрыто по календарю, "
    f"{len(in_progress)} в процессе, "
    f"{len(open_tasks)} требуют решения."
)
if pending_count:
    lines.append(f"После встреч есть {pending_count} кандидатов действий на подтверждение.")
lines.append("")

if known_slots:
    lines.append("Ближайшие слоты:")
    lines.extend(f"- {item}" for item in known_slots)
    lines.append("")

if in_progress:
    lines.append("В процессе:")
    lines.extend(f"- {item['task']}" for item in in_progress[:4])
    lines.append("")

if open_tasks:
    lines.append("Открыто / требует решения:")
    for item in open_tasks[:4]:
        lines.append(f"- {item['task']} ({item['status']})")
    lines.append("")

if completed:
    lines.append("Закрыто по календарю:")
    lines.extend(f"- {item['task']}" for item in completed[:5])
    lines.append("")

if pending_count:
    lines.append("После встреч:")
    lines.append(f"- {pending_count} кандидатов ждут решения в /actions")
    lines.append("- подтверждённые через /actions_confirm попадут в Planning Inbox")
    lines.append("")

if questions:
    lines.append("Нужно уточнить:")
    lines.extend(f"{index}. {question}" for index, question in enumerate(questions, start=1))
    lines.append("")

lines.append(f"Файл сохранён: {path}")

message = "\n".join(lines).strip()
if len(message) > 3400:
    message = message[:3350].rstrip() + "\n\n…полная версия сохранена в файле."
print(message)
PY
)"

  if ! curl -fsS \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${message}" \
    --data-urlencode "disable_web_page_preview=true" \
    "$api/sendMessage" >/dev/null; then
    echo "Telegram delivery warning: sendMessage failed" >> "$LOG_FILE"
    return 0
  fi

  if [[ "${DAILY_FOCUS_ATTACH_FULL_BRIEF:-0}" == "1" ]]; then
    if ! curl -fsS \
      -F "chat_id=${TELEGRAM_CHAT_ID}" \
      -F "document=@${latest_brief};filename=$(basename "$latest_brief")" \
      "$api/sendDocument" >/dev/null; then
      echo "Telegram delivery warning: sendDocument failed" >> "$LOG_FILE"
      return 0
    fi
  fi

  echo "Telegram delivery sent successfully: $(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$LOG_FILE"
}

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
elif [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
elif [[ $# -gt 0 ]]; then
  usage >&2
  exit 2
fi

mkdir -p "$OUTPUT_DIR" "$PLANNING_INBOX" "$PLANNING_MEMORY" "$MEETING_ACTION_CANDIDATES" "$(dirname "$LOG_FILE")"

check_path() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    echo "Missing required path: $path" >&2
    exit 1
  fi
}

check_path "$ROOT_CONTEXT"
check_path "$AGENT_CONTEXT"
check_path "$OUTPUT_DIR"
check_path "$PLANNING_INBOX"
check_path "$PLANNING_MEMORY"
check_path "$MEETING_ACTION_CANDIDATES"

if [[ ! -x "$CODEX_BIN" ]]; then
  echo "Codex binary is not executable: $CODEX_BIN" >&2
  exit 1
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  {
    echo "Daily Focus dry run OK"
    echo "Vault: $VAULT"
    echo "Root context: $ROOT_CONTEXT"
    echo "Agent context: $AGENT_CONTEXT"
    echo "Output dir: $OUTPUT_DIR"
    echo "Planning inbox: $PLANNING_INBOX"
    echo "Planning memory: $PLANNING_MEMORY"
    echo "Meeting action candidates: $MEETING_ACTION_CANDIDATES"
    echo "Log file: $LOG_FILE"
    echo "Last message: $LAST_MESSAGE"
    if [[ -f "$TELEGRAM_ENV" ]]; then
      echo "Telegram env: configured at $TELEGRAM_ENV"
    else
      echo "Telegram env: not configured at $TELEGRAM_ENV"
    fi
    echo "Codex: $CODEX_BIN"
  } | tee "$LAST_MESSAGE"
  exit 0
fi

PROMPT=$(cat <<'PROMPT_EOF'
You are Daily Focus Assistant.

Task:
1. Read `/Users/anton/AI AGENT FOLDER/Second Brain/claude.md`.
2. Read `/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/daily focus/CLAUDE.md`.
3. Collect sources using the Daily Focus protocol.
4. Source window: last 7 days by default; expand to 14 only if data is sparse.
5. Use the last 24 hours only as an additional layer, not as the basis of analysis.
6. Read Planning Inbox and Planning Memory:
   - `/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/daily focus/planning inbox/`
   - `/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/daily focus/planning memory/`
   Normalize each planning item with task, type, source, deadline, estimate, dependencies, and status.
   Use `unknown` for missing deadline or estimate; do not invent values.
   Treat completion as calendar-based: a `Focus: [task]` slot whose time is already in the past counts as a completed work block. A task is fully completed only when it has past Focus work and no known future Focus slots with the same task key. Do not require Anton to mark every task with `/done`.
7. Extract possible meeting action items from meeting summaries/transcripts only as Meeting Action Candidates:
   - explicit commitments
   - follow-ups
   - promised materials
   - deadlines
   - open loops where Anton is actor
   - tasks clearly assigned to Anton
   Do not add these meeting-derived items to Planning Inbox.
8. Save pending candidates to `/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/daily focus/meeting action candidates/` using filename `{actions} meeting candidates – YYYY-MM-DD.md`.
   Each candidate must have id, task, source meeting, source path, evidence, actor, confidence, reason, status: pending_confirmation.
   If no credible candidates exist, create no fake candidates and state that none were found.
9. Planning horizon: next 2-3 days.
10. Do not invent calendar slots. If calendar is unavailable, use only candidate blocks: Admin / Deep Work / Outreach / Health.
11. Ask max 3-5 clarification questions if data is missing.
12. Draft a Daily Planning Brief first.
13. Run the internal eval `daily-focus-critic` on the draft.
14. If critic status is REVISE or FAIL, revise the draft and repeat eval.
15. Continue draft -> critic -> revise until critic status is PASS.
16. Only after PASS, save the final file in `/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/daily focus/daily focus messages/`.
17. Filename pattern: `{focus} daily planning brief – YYYY-MM-DD.md`; if a file already exists today, use `{focus} daily planning brief – YYYY-MM-DD HHMM.md`.
18. If needed, update only Daily Focus Planning Memory files; keep them lightweight and limited to 2-3 day carry-over.
19. Only after saving the final file, print the readable chat version titled `Daily Planning Brief`, including the saved path, pending meeting action candidates summary, and critic status.

Do not update canonical context files. Put any context changes only under Proposed context updates.
PROMPT_EOF
)

TMP_OUTPUT="$(mktemp)"
{
  echo "===== Daily Focus run started $(date '+%Y-%m-%d %H:%M:%S %Z') ====="
  "$CODEX_BIN" exec \
    --dangerously-bypass-approvals-and-sandbox \
    --dangerously-bypass-hook-trust \
    -C "$VAULT" \
    "$PROMPT"
  echo "===== Daily Focus run finished $(date '+%Y-%m-%d %H:%M:%S %Z') ====="
} 2>&1 | tee -a "$LOG_FILE" | tee "$TMP_OUTPUT" >/dev/null
STATUS=${PIPESTATUS[0]}

cp "$TMP_OUTPUT" "$LAST_MESSAGE"
rm -f "$TMP_OUTPUT"

if [[ "$STATUS" -eq 0 ]]; then
  send_telegram_delivery
else
  echo "Telegram delivery skipped: Daily Focus run failed with status $STATUS" >> "$LOG_FILE"
fi

exit "$STATUS"
