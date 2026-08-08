#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Session Compiler runner
#
# Раннер для ежедневного автозапуска скилла session-compiler
# (system/skills/session-compiler/SKILL.md) на Ubuntu-VPS через systemd.
#
# Что делает:
#   1. определяет окно компиляции по журналу sessions/compiled/log.md;
#   2. считает кандидатов в sessions/{claude,codex}/exports/ и отсеивает шум
#      (прогоны локальной диктовки, служебные ping/login-сессии);
#   3. если содержательных сессий нет — выходит с кодом 0, не тратя токены;
#   4. иначе один раз вызывает claude -p с промптом «выполни скилл за окно».
#
# Переменные окружения (все — с дефолтами под VPS):
#   VAULT_ROOT      корень vault              (default /root/second-brain)
#   CLAUDE_BIN      путь к бинарю claude      (default /root/.local/bin/claude)
#   LOG_FILE        файл лога прогонов        (default /root/Library/Logs/session-compiler.log)
#   COMPILER_MODEL  модель для компиляции     (default sonnet — алиас, всегда последняя версия)
#   MAX_CANDIDATES  лимит сессий на один прогон (default 40)
#
# Флаги:
#   --dry-run    собрать промпт и напечатать, claude НЕ вызывать
#   --days N     принудительное окно в N дней (перебивает журнал)
#   --max N      лимит сессий на прогон (перебивает MAX_CANDIDATES)
#   --help       справка
#
# Ручной прогон на маке (проверка):
#   VAULT_ROOT="/Users/anton/AI AGENT FOLDER/Second Brain" \
#   CLAUDE_BIN=/Users/anton/.local/bin/claude LOG_FILE=/tmp/sc.log \
#   ./run_compiler.sh --dry-run
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Конфигурация ─────────────────────────────────────────────────────────────
VAULT_ROOT="${VAULT_ROOT:-/root/second-brain}"
CLAUDE_BIN="${CLAUDE_BIN:-/root/.local/bin/claude}"
LOG_FILE="${LOG_FILE:-/root/Library/Logs/session-compiler.log}"
COMPILER_MODEL="${COMPILER_MODEL:-sonnet}"
# Сколько содержательных сессий максимум отдаём в один вызов claude.
# Больше — не влезет в контекст; бэклог доедается за несколько прогонов.
MAX_CANDIDATES="${MAX_CANDIDATES:-40}"

DRY_RUN=0
FORCE_DAYS=""

# ── Разбор аргументов ────────────────────────────────────────────────────────
usage() {
  cat <<'USAGE'
run_compiler.sh — раннер скилла session-compiler

Использование:
  run_compiler.sh [--dry-run] [--days N] [--max N] [--help]

  --dry-run   собрать промпт и напечатать его; claude не вызывается,
              файлы vault не меняются
  --days N    принудительно взять окно в N дней назад от сегодня
              (перебивает дату последнего прогона из журнала)
  --max N     максимум содержательных сессий на один прогон (по умолчанию 40);
              при большем бэклоге верхняя граница окна опускается,
              остаток доедят следующие прогоны
  --help      эта справка

Переменные окружения: VAULT_ROOT, CLAUDE_BIN, LOG_FILE, COMPILER_MODEL,
MAX_CANDIDATES.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --days)
      if [[ $# -lt 2 || ! "${2:-}" =~ ^[0-9]+$ ]]; then
        echo "Ошибка: --days требует целое число дней" >&2
        exit 2
      fi
      FORCE_DAYS="$2"; shift 2 ;;
    --days=*)
      FORCE_DAYS="${1#--days=}"
      if [[ ! "$FORCE_DAYS" =~ ^[0-9]+$ ]]; then
        echo "Ошибка: --days требует целое число дней" >&2
        exit 2
      fi
      shift ;;
    --max)
      if [[ $# -lt 2 || ! "${2:-}" =~ ^[0-9]+$ || "${2:-0}" -lt 1 ]]; then
        echo "Ошибка: --max требует целое число ≥ 1" >&2
        exit 2
      fi
      MAX_CANDIDATES="$2"; shift 2 ;;
    --max=*)
      MAX_CANDIDATES="${1#--max=}"
      if [[ ! "$MAX_CANDIDATES" =~ ^[0-9]+$ || "$MAX_CANDIDATES" -lt 1 ]]; then
        echo "Ошибка: --max требует целое число ≥ 1" >&2
        exit 2
      fi
      shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Неизвестный аргумент: $1 (см. --help)" >&2; exit 2 ;;
  esac
done

# ── Логирование ──────────────────────────────────────────────────────────────
mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true

log() {
  # Формат строки: [YYYY-MM-DD HH:MM:SS] сообщение — и в stdout, и в файл лога
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG_FILE"
}

# ── Даты: считаем строками YYYY-MM-DD, сравниваем лексикографически ──────────
TODAY="$(date '+%Y-%m-%d')"

days_ago() {
  # $1 — сколько дней назад. Ветка BSD (mac) / GNU (Linux).
  local n="$1"
  if date -v-1d '+%Y-%m-%d' >/dev/null 2>&1; then
    date -v-"${n}"d '+%Y-%m-%d'          # BSD date (macOS)
  else
    date -d "${n} days ago" '+%Y-%m-%d'  # GNU date (Ubuntu)
  fi
}

log "=== старт session-compiler runner ==="

# ── Предполётные проверки ────────────────────────────────────────────────────
if [[ ! -d "$VAULT_ROOT" ]]; then
  log "ОШИБКА: VAULT_ROOT не найден: $VAULT_ROOT"
  exit 1
fi

if [[ ! -x "$CLAUDE_BIN" && ! -f "$CLAUDE_BIN" ]]; then
  log "ОШИБКА: CLAUDE_BIN не найден: $CLAUDE_BIN"
  exit 1
fi

LOG_MD="$VAULT_ROOT/sessions/compiled/log.md"

# ── Шаг 1. Определяем окно ───────────────────────────────────────────────────
WINDOW_SOURCE=""
if [[ -n "$FORCE_DAYS" ]]; then
  WINDOW_FROM="$(days_ago "$FORCE_DAYS")"
  WINDOW_SOURCE="--days $FORCE_DAYS"
else
  LAST_RUN=""
  if [[ -f "$LOG_MD" ]]; then
    # Берём максимальную дату из заголовков вида "## Прогон YYYY-MM-DD"
    LAST_RUN="$(grep -Eo '^## Прогон [0-9]{4}-[0-9]{2}-[0-9]{2}' "$LOG_MD" 2>/dev/null \
                 | grep -Eo '[0-9]{4}-[0-9]{2}-[0-9]{2}' | sort | tail -1 || true)"
  fi
  if [[ -n "$LAST_RUN" ]]; then
    WINDOW_FROM="$LAST_RUN"
    WINDOW_SOURCE="журнал (последний прогон $LAST_RUN)"
  else
    WINDOW_FROM="$(days_ago 7)"
    WINDOW_SOURCE="журнал пуст → дефолтные 7 дней"
  fi
fi
WINDOW_TO="$TODAY"

log "окно компиляции: $WINDOW_FROM … $WINDOW_TO (источник: $WINDOW_SOURCE)"

# ── Шаг 2. Считаем кандидатов и фильтруем шум ────────────────────────────────
# Шумовые паттерны: прогоны локальной диктовки и служебные пинги — знаний нет.
NOISE_PATTERNS=(
  "ping"
  "login"
  "say-ok"
  "очист"
  "убери-слова"
  "удалить-слова"
  "постпроцесс"
  "постобработк"
  "слов-параз"
  "слова-параз"
  "clean-up-speech"
  "post-process-voice"
  "voice-dictation"
  "голосовой-диктовк"
  "голосовую-диктовку"
  "диктовку-от"
)
MIN_SIZE_BYTES=2500

total=0    # файлов в окне
noise=0    # отфильтровано как шум
kept=0     # содержательных кандидатов
KEPT_LIST=""

# Даты содержательных кандидатов — нужны, чтобы при большом бэклоге урезать окно
KEPT_DATES_FILE="$(mktemp "${TMPDIR:-/tmp}/session-compiler-dates.XXXXXX")"
trap 'rm -f "$KEPT_DATES_FILE"' EXIT

EXPORT_DIRS=()
[[ -d "$VAULT_ROOT/sessions/claude/exports" ]] && EXPORT_DIRS+=("$VAULT_ROOT/sessions/claude/exports")
[[ -d "$VAULT_ROOT/sessions/codex/exports" ]]  && EXPORT_DIRS+=("$VAULT_ROOT/sessions/codex/exports")

if [[ ${#EXPORT_DIRS[@]} -eq 0 ]]; then
  log "ОШИБКА: не найдено ни одной папки exports в $VAULT_ROOT/sessions/"
  exit 1
fi

is_empty_session() {
  # $1 — путь к файлу. Экспорт без ответов ассистента ("- Messages: user 2, assistant 0"):
  # объём такому файлу даёт вставленный служебный текст (список плагинов и т.п.), знаний нет.
  head -60 "$1" | grep -qiE '^- Messages:.*assistant[[:space:]]*0([^0-9]|$)'
}

is_noise() {
  # $1 — basename без .md. Возвращает 0 (истина), если файл — шум по имени.
  local name="$1" core lc pat
  # Вырезаем «ядро» имени: между "Claude "/"Codex " и хвостом "<hash> – YYYY-MM-DD"
  core="$name"
  if [[ "$core" == *"Claude "* ]]; then
    core="${core#*Claude }"
  elif [[ "$core" == *"Codex "* ]]; then
    core="${core#*Codex }"
  fi
  core="${core% – *}"   # отрезаем " – YYYY-MM-DD"
  core="${core% *}"     # отрезаем хвостовой хэш-токен
  lc="$(printf '%s' "$core" | tr '[:upper:]' '[:lower:]')"
  for pat in "${NOISE_PATTERNS[@]}"; do
    if [[ "$lc" == *"$pat"* || "$core" == *"$pat"* ]]; then
      return 0
    fi
  done
  return 1
}

while IFS= read -r -d '' f; do
  base="$(basename "$f")"
  name="${base%.md}"
  # Дата — последние 10 символов имени перед .md
  fdate="${name: -10}"
  [[ "$fdate" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || continue
  # Сравнение дат — лексикографическое (формат YYYY-MM-DD это позволяет)
  [[ "$fdate" > "$WINDOW_FROM" || "$fdate" == "$WINDOW_FROM" ]] || continue
  [[ "$fdate" < "$WINDOW_TO"  || "$fdate" == "$WINDOW_TO"  ]] || continue

  total=$((total + 1))

  size="$(wc -c < "$f" | tr -d ' ')"
  if [[ "$size" -lt "$MIN_SIZE_BYTES" ]] || is_noise "$name" || is_empty_session "$f"; then
    noise=$((noise + 1))
    continue
  fi

  kept=$((kept + 1))
  KEPT_LIST="${KEPT_LIST}  - ${base}"$'\n'
  printf '%s\n' "$fdate" >> "$KEPT_DATES_FILE"
done < <(find "${EXPORT_DIRS[@]+${EXPORT_DIRS[@]}}" -type f -name '*.md' -print0)

log "в окне: $total файлов; шум отфильтрован: $noise; содержательных кандидатов: $kept"
if [[ "$kept" -gt 0 ]]; then
  # В лог пишем первые 20 имён — чтобы лог не распухал на больших окнах
  { printf '%s' "$KEPT_LIST" | head -20 | tee -a "$LOG_FILE"; } || true
  if [[ "$kept" -gt 20 ]]; then
    log "… и ещё $((kept - 20)) файлов"
  fi
fi

# ── Шаг 3. Нечего компилировать — выходим, токены не жжём ────────────────────
if [[ "$kept" -eq 0 ]]; then
  log "нечего компилировать (окно $WINDOW_FROM…$WINDOW_TO, отфильтровано $noise шумовых)"
  log "=== финиш: нечего делать ==="
  exit 0
fi

# ── Шаг 3b. Ограничитель батча: при большом бэклоге урезаем окно сверху ───────
# В один вызов claude весь месячный бэклог не влезет по контексту. Набираем даты
# от нижней границы вверх, пока не упрёмся в лимит; остаток доедят следующие прогоны.
TRUNCATED=0
BATCH_COUNT="$kept"
if [[ "$kept" -gt "$MAX_CANDIDATES" ]]; then
  acc=0
  last_date=""
  while read -r cnt d; do
    [[ -n "$d" ]] || continue
    if [[ -z "$last_date" ]]; then
      # Первую дату берём всегда — иначе окно окажется пустым,
      # даже если сессий за один день больше лимита
      acc="$cnt"
      last_date="$d"
      continue
    fi
    if [[ $((acc + cnt)) -gt "$MAX_CANDIDATES" ]]; then
      break
    fi
    acc=$((acc + cnt))
    last_date="$d"
  done < <(sort "$KEPT_DATES_FILE" | uniq -c | sort -k2,2)

  if [[ -n "$last_date" && "$last_date" != "$WINDOW_TO" ]]; then
    TRUNCATED=1
    WINDOW_TO="$last_date"
    BATCH_COUNT="$acc"
    log "окно урезано: $WINDOW_FROM…$WINDOW_TO ($BATCH_COUNT кандидатов из $kept всего), бэклог не закрыт — следующий прогон продолжит"
  else
    # Все кандидаты приходятся на одну дату (или на последний день окна) —
    # урезать некуда, отдаём как есть
    BATCH_COUNT="$kept"
    log "кандидатов ($kept) больше лимита $MAX_CANDIDATES, но урезать окно некуда (все на $WINDOW_TO) — отдаём как есть"
  fi
fi

# ── Шаг 4. Промпт ────────────────────────────────────────────────────────────
TRUNCATION_NOTE=""
if [[ "$TRUNCATED" -eq 1 ]]; then
  TRUNCATION_NOTE="- ВАЖНО: окно урезано сверху из-за размера бэклога (всего в бэклоге ${kept} содержательных сессий, в это окно попало ${BATCH_COUNT}). Компилируй ТОЛЬКО указанное окно ${WINDOW_FROM}…${WINDOW_TO}, более поздние сессии не трогай. В записи sessions/compiled/log.md укажи верхней границей окна именно ${WINDOW_TO} — с неё продолжит следующий прогон, и отметь, что бэклог не закрыт.
"
fi

PROMPT="$(cat <<PROMPT_EOF
Прочитай файл system/skills/session-compiler/SKILL.md и выполни компиляцию сессий строго по нему за окно с ${WINDOW_FROM} по ${WINDOW_TO} включительно.

Условия автономного прогона:
- Работай автономно: вопросов не задавай, подтверждений не жди, ничего не спрашивай — ответить в этом прогоне некому.
- Сырьё: sessions/claude/exports/ и sessions/codex/exports/, файлы с датой в имени внутри окна. Raw-слой только читать, не менять.
- Шумовые сессии в daily log не тащить: прогоны локальной диктовки (очистка текста от слов-паразитов, постпроцессинг голосовой диктовки, clean-up-speech / post-process-voice / voice-dictation), служебные ping/login-сессии, файлы меньше 2.5 КБ и экспорты без ответов ассистента (в шапке "- Messages: user N, assistant 0") — знаний в них нет. По предварительному подсчёту раннера: содержательных кандидатов в этом окне ${BATCH_COUNT} (шумовых отсеяно ${noise} на всём просмотренном диапазоне).
${TRUNCATION_NOTE}- Пиши только в sessions/compiled/ (daily/, topics/), контракт по frontmatter и именованию — из SKILL.md.
- Обязательно допиши запись о прогоне в sessions/compiled/log.md (дата прогона, окно, сколько сессий обработано, какие файлы created/updated) — эта запись задаёт нижнюю границу следующего прогона.
- В конце выведи отчёт на 3-5 строк: что скомпилировано и самые важные извлечённые знания.
PROMPT_EOF
)"

if [[ "$DRY_RUN" -eq 1 ]]; then
  log "DRY-RUN: claude не вызывается. Промпт ниже."
  printf '%s\n' "----- PROMPT BEGIN -----"
  printf '%s\n' "$PROMPT"
  printf '%s\n' "----- PROMPT END -----"
  log "=== финиш (dry-run) ==="
  exit 0
fi

# ── Шаг 5. Вызов claude ──────────────────────────────────────────────────────
# Отпечаток журнала: если после прогона он не изменился — компилятор ничего не сделал.
# Защита от тихого ложного зелёного: claude может вернуть 0, не выполнив работу,
# и тогда systemd покажет success при пустом sessions/compiled/.
fingerprint() {
  # $1 — путь к файлу. Нет файла → пустая строка.
  [[ -f "$1" ]] || { printf ''; return 0; }
  if command -v md5sum >/dev/null 2>&1; then
    md5sum < "$1" | awk '{print $1}'          # GNU (Ubuntu)
  elif command -v md5 >/dev/null 2>&1; then
    md5 -q "$1"                                # BSD (macOS)
  else
    # Фолбэк без хэшера: размер + строка mtime из ls
    printf '%s|%s' "$(wc -c < "$1" | tr -d ' ')" "$(ls -l "$1" | awk '{print $6,$7,$8}')"
  fi
}

LOG_MD_BEFORE="$(fingerprint "$LOG_MD")"

log "запуск claude (модель $COMPILER_MODEL, таймаут 3600с)…"

cd "$VAULT_ROOT"

# timeout есть на Ubuntu; на маке может отсутствовать — тогда идём без него
TIMEOUT_CMD=()
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_CMD=(timeout 3600)
fi

log "команда: ${TIMEOUT_CMD[*]+${TIMEOUT_CMD[*]} }$CLAUDE_BIN -p <промпт ${#PROMPT} симв.> --model $COMPILER_MODEL --allowedTools Read Glob Grep Write --disallowedTools Bash Edit WebFetch WebSearch (cwd: $VAULT_ROOT)"

set +e
${TIMEOUT_CMD[@]+"${TIMEOUT_CMD[@]}"} "$CLAUDE_BIN" -p "$PROMPT" \
  --model "$COMPILER_MODEL" \
  --allowedTools "Read" "Glob" "Grep" "Write" \
  --disallowedTools "Bash" "Edit" "WebFetch" "WebSearch" >>"$LOG_FILE" 2>&1
CLAUDE_RC=$?
set -e

if [[ "$CLAUDE_RC" -ne 0 ]]; then
  log "ОШИБКА: claude завершился с кодом $CLAUDE_RC"
  log "=== финиш с ошибкой ==="
  exit 1
fi

log "claude завершился успешно (код 0)"

# ── Шаг 6. Проверка результата: работа реально произошла? ────────────────────
LOG_MD_AFTER="$(fingerprint "$LOG_MD")"

if [[ "$LOG_MD_AFTER" == "$LOG_MD_BEFORE" ]]; then
  log "ОШИБКА: claude вернул 0, но sessions/compiled/log.md не изменился — прогон, вероятно, не выполнен (смотри вывод claude выше)"
  log "=== финиш с ошибкой ==="
  exit 1
fi

# Видимый след работы в логе: что изменилось в compiled/ за последние 2 часа
log "изменённые файлы в sessions/compiled/ (за последние 2 часа):"
{ find "$VAULT_ROOT/sessions/compiled" -type f -name '*.md' -mmin -120 2>/dev/null \
  | sed 's|^|  - |' | tee -a "$LOG_FILE"; } || true

log "прогон выполнен: запись в log.md добавлена"
log "=== финиш ==="
