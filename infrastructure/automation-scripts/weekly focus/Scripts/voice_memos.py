#!/usr/local/bin/python3
"""voice_memos.py — Voice Memo Assistant (диктофонные встречи).

Следит за iCloud-папкой "Voice Inbox": Антон кидает туда запись с айфона
(Диктофон → Поделиться → Сохранить в Файлы → iCloud Drive → Voice Inbox).
Скрипт транскрибирует её локально (faster-whisper) и создаёт заметку в
transcripts/ с frontmatter `voice_memo_id` — дальше её подхватывает
Meeting Notes Assistant (meeting_notes.py): саммари + задачи в Telegram.

LaunchAgent: com.user.voice-memos (WatchPaths на Voice Inbox + StartInterval 600)
Лог: ~/Library/Logs/voice-memos.log
State: ~/.config/second-brain/voice-memos-state.json (ключ = sha1 файла)

Требует /usr/local/bin/python3 (faster-whisper установлен там).
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from meeting_notes import ENV_FILE, load_env_file, send_message

VAULT = Path("/Users/anton/AI AGENT FOLDER/Second Brain")
TRANSCRIPTS_DIR = VAULT / "transcripts"
INBOX = Path("/Users/anton/Library/Mobile Documents/com~apple~CloudDocs/Voice Inbox")
PROCESSED_DIR = INBOX / "processed"
STATE_FILE = Path("/Users/anton/.config/second-brain/voice-memos-state.json")
LOCK_FILE = Path("/Users/anton/.config/second-brain/voice-memos.lock")
LOG_FILE = Path("/Users/anton/Library/Logs/voice-memos.log")

AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aac", ".aiff", ".ogg", ".flac", ".mp4", ".mov"}
# По умолчанию parakeet на GPU (×10-12 реального времени против ×3-5 у whisper small).
# VOICE_WHISPER_MODEL=small (или tiny/medium) форсирует старый путь через whisper.
ASR_MODEL = os.environ.get("VOICE_WHISPER_MODEL", "parakeet")
WHISPER_FALLBACK_MODEL = "small"
PARAKEET_REPO = os.environ.get("PARAKEET_REPO", "mlx-community/parakeet-tdt-0.6b-v3")
PARAKEET_SR = 16000
PARAKEET_CHUNK_SEC = 120.0
PARAKEET_OVERLAP_SEC = 12.0
MIN_TRANSCRIPT_CHARS = 500  # порог is_ready() в meeting_notes.py
SETTLE_SECONDS = 60         # файл моложе минуты — ещё может синкаться, ждём
MAX_ATTEMPTS = 2
LOCK_STALE_SECONDS = 90 * 60  # транскрибация длинной записи может идти долго


def log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {message}\n")
    print(message, flush=True)


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"files": {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def acquire_lock() -> bool:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        age = time.time() - LOCK_FILE.stat().st_mtime
        if age > LOCK_STALE_SECONDS:
            log(f"lock: stale ({int(age)}s), taking over")
            LOCK_FILE.unlink(missing_ok=True)
            return acquire_lock()
        return False


def release_lock() -> None:
    LOCK_FILE.unlink(missing_ok=True)


def file_sha1(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def audio_minutes(path: Path) -> int | None:
    """Длительность через afinfo (macOS builtin); None если не получилось."""
    try:
        out = subprocess.check_output(["afinfo", str(path)], text=True, stderr=subprocess.DEVNULL, timeout=30)
        m = re.search(r"estimated duration:\s*([\d.]+)", out)
        if m:
            return max(1, round(float(m.group(1)) / 60))
    except Exception:
        pass
    return None


def clean_title(stem: str) -> str:
    cleaned = stem.replace("{", "").replace("}", "").replace("–", "-")
    cleaned = "".join(ch if ch not in '<>:"/\\|?*\n\r\t' else " " for ch in cleaned)
    cleaned = " ".join(cleaned.split()).strip()
    return cleaned or "Диктофонная запись"


def request_icloud_downloads() -> None:
    """Плейсхолдеры .name.ext.icloud — просим iCloud скачать оригиналы."""
    for placeholder in INBOX.glob(".*.icloud"):
        target = placeholder.name[1:-len(".icloud")]
        log(f"icloud placeholder, requesting download: {target}")
        subprocess.run(["brctl", "download", str(INBOX / target)], check=False, capture_output=True)


def transcribe(path: Path) -> str:
    """parakeet по умолчанию, whisper — запасной путь и явный выбор через env."""
    if ASR_MODEL == "parakeet":
        try:
            return transcribe_parakeet(path)
        except Exception as exc:
            log(f"parakeet failed ({type(exc).__name__}: {exc}), "
                f"falling back to whisper {WHISPER_FALLBACK_MODEL}")
        return transcribe_whisper(path, WHISPER_FALLBACK_MODEL)
    return transcribe_whisper(path, ASR_MODEL)


def transcribe_parakeet(path: Path) -> str:
    """Массив, а не путь: parakeet-mlx открывает файлы через ffmpeg, которого нет.

    Длинные записи режем на чанки с перекрытием и склеиваем по границам предложений.
    """
    import mlx.core as mx
    from faster_whisper.audio import decode_audio
    from parakeet_mlx import from_pretrained
    from parakeet_mlx.audio import get_logmel

    audio = decode_audio(str(path), sampling_rate=PARAKEET_SR)
    model = from_pretrained(PARAKEET_REPO)
    chunk = int(PARAKEET_CHUNK_SEC * PARAKEET_SR)
    step = int((PARAKEET_CHUNK_SEC - PARAKEET_OVERLAP_SEC) * PARAKEET_SR)
    total = len(audio)

    parts: list[str] = []
    last_end = 0.0
    for offset in range(0, max(total, 1), step):
        piece = audio[offset : offset + chunk]
        if len(piece) < PARAKEET_SR // 2:
            break
        is_last = offset + chunk >= total
        base = offset / PARAKEET_SR
        piece_end = base + len(piece) / PARAKEET_SR

        result = model.generate(get_logmel(mx.array(piece), model.preprocessor_config))
        for sentence in (result[0].sentences if result else []):
            text = sentence.text.strip()
            start, end = base + sentence.start, base + sentence.end
            if not text:
                continue
            if not is_last and end > piece_end - 1.0:
                continue  # обрезано границей чанка — приедет со следующим
            if start < last_end - 0.5:
                continue  # уже записано в зоне перекрытия
            parts.append(text)
            last_end = max(last_end, end)
        log(f"progress: {len(parts)} sentences, up to {int(piece_end // 60)} min")
        if is_last:
            break

    log(f"transcribed: {len(parts)} sentences, engine=parakeet")
    return "\n\n".join(parts)


def transcribe_whisper(path: Path, model_name: str) -> str:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(path), beam_size=1, vad_filter=True, condition_on_previous_text=False,
    )
    parts: list[str] = []
    count = 0
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        parts.append(text)
        count += 1
        if count % 50 == 0:
            log(f"progress: {count} segments, up to {int(segment.end // 60)} min")
    log(f"transcribed: {count} segments, language={info.language}")
    return "\n\n".join(parts)


def unique_note_path(title: str, date_str: str) -> Path:
    base = f"{{meeting}} {title} – {date_str}"
    path = TRANSCRIPTS_DIR / f"{base}.md"
    counter = 2
    while path.exists():
        path = TRANSCRIPTS_DIR / f"{base} ({counter}).md"
        counter += 1
    return path


def write_note(title: str, date_str: str, voice_id: str, source_name: str, minutes: int | None, transcript: str) -> Path:
    note_path = unique_note_path(title, date_str)
    duration_line = f"**Длительность:** ~{minutes} мин\n" if minutes else ""
    note_path.write_text(
        "---\n"
        "tags:\n"
        "  - type/meeting\n"
        f"date: {date_str}\n"
        "status: active\n"
        "source: voice-memo\n"
        f"voice_memo_id: {voice_id}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"**Дата:** {date_str}\n"
        f"{duration_line}"
        f"**Источник:** Диктофон ({source_name})\n\n"
        "## Транскрипт\n\n"
        f"{transcript}\n",
        encoding="utf-8",
    )
    return note_path


def process_file(path: Path, state: dict, token: str, chat_id: str, dry_run: bool) -> None:
    voice_id = file_sha1(path)
    entry = state["files"].setdefault(voice_id, {"name": path.name})
    if entry.get("status") in {"done", "failed", "too_short"}:
        return
    attempts = entry.get("attempts", 0)
    if attempts >= MAX_ATTEMPTS:
        entry["status"] = "failed"
        return
    entry["attempts"] = attempts + 1
    if not dry_run:
        save_state(state)  # фиксируем attempt до тяжёлой работы

    title = clean_title(path.stem)
    date_str = dt.date.fromtimestamp(path.stat().st_mtime).isoformat()
    minutes = audio_minutes(path)
    log(f"transcribing: {path.name} (~{minutes or '?'} min, model={ASR_MODEL})")

    if not dry_run and entry["attempts"] == 1:
        try:
            dur = f" (~{minutes} мин)" if minutes else ""
            send_message(token, chat_id, f"🎙 Транскрибирую запись «{title}»{dur}. Саммари и задачи пришлю следом.")
        except Exception as exc:
            log(f"start notify failed: {exc}")

    started = time.time()
    transcript = transcribe(path)
    log(f"whisper done in {int(time.time() - started)}s: {path.name}")

    if len(transcript) < MIN_TRANSCRIPT_CHARS:
        entry["status"] = "too_short"
        log(f"too short ({len(transcript)} chars): {path.name}")
        if not dry_run:
            send_message(
                token, chat_id,
                f"🎙 «{title}»: запись слишком короткая для саммари ({len(transcript)} символов текста). "
                f"Транскрипт:\n\n{transcript[:1500]}" if transcript.strip() else
                f"🎙 «{title}»: в записи не распознал речь — саммари не будет.",
            )
        return

    if dry_run:
        print(f"--- would write note for {path.name} ---")
        print(transcript[:800])
        return

    note_path = write_note(title, date_str, voice_id, path.name, minutes, transcript)
    entry.update({"status": "done", "note_path": str(note_path), "done_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    log(f"note created: {note_path.name}")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    try:
        path.rename(PROCESSED_DIR / path.name)
    except Exception as exc:
        log(f"move to processed failed (non-fatal): {exc}")

    # будим meeting_notes сразу, не дожидаясь его WatchPaths/интервала
    try:
        subprocess.Popen(
            ["/usr/bin/python3", str(VAULT / "infrastructure" / "daily focus" / "Scripts" / "meeting_notes.py")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        log(f"meeting_notes kick failed (non-fatal, watcher подхватит сам): {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Voice Memo Assistant watcher")
    parser.add_argument("--dry-run", action="store_true", help="транскрибировать и напечатать, без записи/отправки")
    parser.add_argument("--file", type=str, help="обработать только этот аудиофайл")
    args = parser.parse_args()

    if not acquire_lock():
        log("lock held by another run, exiting")
        return 0
    try:
        INBOX.mkdir(parents=True, exist_ok=True)
        request_icloud_downloads()

        if args.file:
            candidates = [Path(args.file).expanduser()]
        else:
            candidates = [
                p for p in sorted(INBOX.iterdir())
                if p.is_file() and p.suffix.lower() in AUDIO_EXTS and not p.name.startswith(".")
            ]
        now = time.time()
        candidates = [p for p in candidates if args.file or now - p.stat().st_mtime > SETTLE_SECONDS]
        if not candidates:
            return 0

        state = load_state()
        token = chat_id = ""
        if not args.dry_run:
            env = load_env_file(ENV_FILE)
            token = env.get("TELEGRAM_BOT_TOKEN", "")
            chat_id = env.get("TELEGRAM_CHAT_ID", "")
            if not token or not chat_id:
                log("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing in env file")
                return 1

        for path in candidates:
            try:
                process_file(path, state, token, chat_id, args.dry_run)
            except Exception as exc:
                log(f"error processing {path.name}: {exc}")
                if not args.dry_run:
                    try:
                        send_message(token, chat_id, f"❌ Не смог транскрибировать «{path.name}»: {exc}")
                    except Exception:
                        pass
            if not args.dry_run:
                save_state(state)
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
