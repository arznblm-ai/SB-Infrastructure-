#!/usr/bin/env python3
"""Claude TG Bridge — Telegram-бот, мостящий сообщения в `claude` CLI на VPS.

Запуск: python claude_tg_bridge.py
Требует python-telegram-bot v21 (async API).
Юнит подгружает /root/.config/claude-tg-bridge/.env через EnvironmentFile.
"""

import asyncio
import collections
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from functools import wraps

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
try:
    ALLOWED_USER = int(os.environ.get("TELEGRAM_ALLOWED_USER", "324186708"))
except ValueError:
    ALLOWED_USER = 324186708

CLAUDE_BIN = "/root/.local/bin/claude"
WORKSPACE = "/root/claude-workspace"
STATE_DIR = "/root/.config/claude-tg-bridge"
STATE_FILE = STATE_DIR + "/state.json"
LOG_FILE = STATE_DIR + "/bridge.log"
CLAUDE_TIMEOUT = 600  # секунд
TG_LIMIT = 4000
HISTORY_MAX = 8

QUOTA_MARKERS = [
    "usage limit",
    "rate limit",
    "exceeded",
    "limit reached",
    "out of",
    "quota",
]

# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------
os.makedirs(STATE_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("claude-tg-bridge")

# ---------------------------------------------------------------------------
# Одиночность / очередь
# ---------------------------------------------------------------------------
claude_lock = asyncio.Lock()
pending_queue: collections.deque = collections.deque()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Авторизация
# ---------------------------------------------------------------------------
def authorized(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user is None or user.id != ALLOWED_USER:
            uid = user.id if user else "unknown"
            logger.info("Отклонён неавторизованный пользователь id=%s", uid)
            return
        return await func(update, context)

    return wrapper


# ---------------------------------------------------------------------------
# Состояние (атомарная запись)
# ---------------------------------------------------------------------------
def default_state() -> dict:
    return {"session_id": None, "history": [], "last_run_at": None}


def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("state.json не объект")
        data.setdefault("session_id", None)
        data.setdefault("history", [])
        data.setdefault("last_run_at", None)
        return data
    except FileNotFoundError:
        return default_state()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось прочитать state.json (%s), беру дефолт", exc)
        return default_state()


def save_state(state: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, prefix=".state.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Вызов Claude CLI
# ---------------------------------------------------------------------------
async def run_claude(prompt: str, session_id):
    """Возвращает (reply_text, new_session_id, error_kind).

    error_kind: None | "quota" | "timeout" | "error".
    """
    cmd = [
        CLAUDE_BIN,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
    ]
    if session_id:
        cmd += ["--resume", session_id]

    env = os.environ.copy()
    env["HOME"] = "/root"  # PATH сохраняем — claude зависит от node в PATH

    start = datetime.now(timezone.utc)
    mode = "resume" if session_id else "new"
    logger.info("Claude call: mode=%s prompt_len=%d", mode, len(prompt))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=WORKSPACE,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=CLAUDE_TIMEOUT
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except Exception:  # noqa: BLE001
            pass
        dur = (datetime.now(timezone.utc) - start).total_seconds()
        logger.warning("Claude timeout after %.1fs (mode=%s)", dur, mode)
        return ("", None, "timeout")

    dur = (datetime.now(timezone.utc) - start).total_seconds()
    stdout = (stdout_b or b"").decode("utf-8", errors="replace")
    stderr = (stderr_b or b"").decode("utf-8", errors="replace")
    rc = proc.returncode

    # Пробуем распарсить JSON
    parsed = None
    try:
        parsed = json.loads(stdout.strip()) if stdout.strip() else None
    except (json.JSONDecodeError, ValueError):
        parsed = None

    if rc != 0 or parsed is None:
        combined = (stdout + "\n" + stderr).lower()
        if any(marker in combined for marker in QUOTA_MARKERS):
            logger.warning(
                "Claude quota: rc=%s mode=%s dur=%.1fs", rc, mode, dur
            )
            return ("", None, "quota")
        tail_src = stderr if stderr.strip() else stdout
        reply = tail_src[-500:] if tail_src else "неизвестная ошибка"
        logger.warning("Claude error: rc=%s mode=%s dur=%.1fs", rc, mode, dur)
        return (reply, None, "error")

    # JSON распарсился
    result_text = parsed.get("result", "") if isinstance(parsed, dict) else str(parsed)
    new_sid = parsed.get("session_id") if isinstance(parsed, dict) else None
    is_error = bool(parsed.get("is_error")) if isinstance(parsed, dict) else False

    if is_error:
        logger.warning("Claude is_error=true: mode=%s dur=%.1fs", mode, dur)
        return (result_text or "ошибка выполнения", None, "error")

    logger.info(
        "Claude ok: mode=%s dur=%.1fs reply_len=%d sid=%s",
        mode,
        dur,
        len(result_text or ""),
        (new_sid[:8] if new_sid else "none"),
    )
    return (result_text or "", new_sid, None)


# ---------------------------------------------------------------------------
# Разбиение длинных ответов
# ---------------------------------------------------------------------------
def split_message(text: str, limit: int = TG_LIMIT):
    """Возвращает список непустых чанков не длиннее limit."""
    if text is None:
        return []
    text = str(text)
    if not text.strip():
        return []

    # Сначала разбиваем на атомы, не превышающие limit
    atoms = []
    for para in text.split("\n\n"):
        if len(para) <= limit:
            atoms.append(para)
            continue
        for line in para.split("\n"):
            if len(line) <= limit:
                atoms.append(line)
                continue
            # Хардкат
            for i in range(0, len(line), limit):
                atoms.append(line[i : i + limit])

    # Склеиваем атомы, пока влезают
    chunks = []
    current = ""
    for atom in atoms:
        if not current:
            current = atom
        elif len(current) + 2 + len(atom) <= limit:
            current = current + "\n\n" + atom
        else:
            chunks.append(current)
            current = atom
    if current:
        chunks.append(current)

    return [c for c in chunks if c.strip()]


# ---------------------------------------------------------------------------
# Индикатор набора текста
# ---------------------------------------------------------------------------
async def _typing_loop(bot, chat_id):
    try:
        while True:
            try:
                await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        return


# ---------------------------------------------------------------------------
# Обработка результата и отправка
# ---------------------------------------------------------------------------
async def _handle_result(bot, chat_id, first_msg, reply_text, new_sid, error_kind):
    if error_kind == "quota":
        await bot.send_message(
            chat_id=chat_id,
            text="⚠️ Лимит подписки Claude исчерпан. Попробуй позже — "
            "квота общая с ежедневным дайджестом.",
        )
        return
    if error_kind == "timeout":
        await bot.send_message(
            chat_id=chat_id,
            text="⚠️ Claude не ответил за 10 минут (прогон прерван).",
        )
        return
    if error_kind == "error":
        for chunk in split_message("⚠️ Ошибка Claude:\n" + (reply_text or "")):
            await bot.send_message(chat_id=chat_id, text=chunk)
        return

    # Успех — обновляем состояние
    state = load_state()
    was_new = state.get("session_id") is None
    if new_sid:
        state["session_id"] = new_sid
        if was_new:
            state.setdefault("history", [])
            if not any(h.get("id") == new_sid for h in state["history"]):
                state["history"].append(
                    {
                        "id": new_sid,
                        "started_at": now_iso(),
                        "first_msg": (first_msg or "")[:80],
                    }
                )
    state["last_run_at"] = now_iso()
    save_state(state)

    chunks = split_message(reply_text)
    if not chunks:
        await bot.send_message(chat_id=chat_id, text="(пустой ответ)")
        return
    for chunk in chunks:
        await bot.send_message(chat_id=chat_id, text=chunk)


# ---------------------------------------------------------------------------
# Хендлер текстовых сообщений
# ---------------------------------------------------------------------------
@authorized
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text if update.message else None
    if not text:
        return
    chat_id = update.effective_chat.id
    bot = context.bot

    # Если Claude занят — в очередь
    if claude_lock.locked():
        pending_queue.append(text)
        await bot.send_message(
            chat_id=chat_id, text="⏳ В очереди (Claude сейчас занят)"
        )
        return

    async with claude_lock:
        current = text
        while True:
            # Собираем промпт: текущее сообщение + всё из очереди
            parts = [current]
            while pending_queue:
                parts.append(pending_queue.popleft())
            prompt = "\n\n".join(p for p in parts if p)
            first_msg = parts[0] if parts else prompt

            state = load_state()
            session_id = state.get("session_id")

            typing_task = asyncio.create_task(_typing_loop(bot, chat_id))
            try:
                reply_text, new_sid, error_kind = await run_claude(prompt, session_id)
            finally:
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass

            try:
                await _handle_result(
                    bot, chat_id, first_msg, reply_text, new_sid, error_kind
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Ошибка при отправке ответа: %s", exc)

            # Если пока работали — накопилось ещё, повторяем цикл
            if pending_queue:
                current = pending_queue.popleft()
                continue
            break


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------
@authorized
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Claude на VPS. Пиши сообщение — отвечу с сервера. "
        "/new — новая сессия, /sessions — список, /status — статус."
    )


@authorized
async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    sid = state.get("session_id")
    if sid:
        state.setdefault("history", [])
        if not any(h.get("id") == sid for h in state["history"]):
            state["history"].append(
                {"id": sid, "started_at": now_iso(), "first_msg": ""}
            )
    state["session_id"] = None
    save_state(state)
    await update.message.reply_text("🆕 Начата новая сессия. Контекст сброшен.")


@authorized
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    sid = state.get("session_id")
    sid_txt = sid if sid else "нет"
    last = state.get("last_run_at") or "никогда"
    busy = "⚙️ обрабатываю…" if claude_lock.locked() else "💤 свободен"
    await update.message.reply_text(
        f"Сессия: {sid_txt}\nПоследний запуск: {last}\nСтатус: {busy}"
    )


@authorized
async def cmd_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    history = state.get("history", [])[-HISTORY_MAX:]
    current = state.get("session_id")

    buttons = []
    for h in reversed(history):
        hid = h.get("id", "")
        label = (h.get("first_msg") or "")[:40] or hid[:8]
        buttons.append([InlineKeyboardButton(label, callback_data="sess:" + hid)])

    cur_label = "текущая: " + (current[:8] if current else "нет")
    buttons.append([InlineKeyboardButton(cur_label, callback_data="sess:current")])

    markup = InlineKeyboardMarkup(buttons)
    await update.message.reply_text(
        "Выбери сессию для продолжения:", reply_markup=markup
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    if user is None or user.id != ALLOWED_USER:
        uid = user.id if user else "unknown"
        logger.info("Отклонён callback от неавторизованного id=%s", uid)
        if query:
            await query.answer()
        return

    data = query.data or ""
    if not data.startswith("sess:"):
        await query.answer()
        return

    sid = data[len("sess:") :]
    if sid == "current":
        await query.answer("Уже текущая")
        return

    state = load_state()
    state["session_id"] = sid
    save_state(state)
    await query.answer("↩️ Переключено на сессию " + sid[:8])
    try:
        await query.edit_message_text("↩️ Переключено на сессию " + sid[:8])
    except Exception:  # noqa: BLE001
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="↩️ Переключено на сессию " + sid[:8],
        )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> None:
    if not BOT_TOKEN:
        sys.exit(
            "TELEGRAM_BOT_TOKEN не задан. "
            "Заполни /root/.config/claude-tg-bridge/.env"
        )

    app: Application = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("sessions", cmd_sessions))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^sess:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    logger.info("Claude TG Bridge запущен (allowed_user=%s)", ALLOWED_USER)
    app.run_polling()


if __name__ == "__main__":
    main()
