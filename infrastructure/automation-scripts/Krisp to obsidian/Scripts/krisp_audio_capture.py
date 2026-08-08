#!/usr/local/bin/python3
"""
Capture Krisp's short-lived local MP3 files and transcribe them locally.

The script is intentionally single-pass: launchd invokes it via WatchPaths and
StartInterval. Runtime paths can be overridden with environment variables so
the capture and matching logic can be exercised without touching live data.
"""

from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
HELPER_PATH = SCRIPT_DIR / "krisp_to_obsidian.py"

KRISP_RECORDINGS_DIR = Path(
    os.environ.get(
        "KRISP_RECORDINGS_DIR",
        str(Path.home() / "Library/Application Support/Krisp/call-recordings"),
    )
).expanduser()
AUDIO_STAGING_DIR = Path(
    os.environ.get("AUDIO_STAGING_DIR", str(Path.home() / ".krisp-obsidian/audio"))
).expanduser()
AUDIO_STATE_FILE = Path(
    os.environ.get(
        "AUDIO_STATE_FILE",
        str(Path.home() / ".krisp-obsidian/audio_capture_state.json"),
    )
).expanduser()
TRANSCRIPTS_DIR = Path(
    os.environ.get(
        "TRANSCRIPTS_DIR",
        "/Users/anton/AI AGENT FOLDER/Second Brain/transcripts",
    )
).expanduser()
CALLINFO_PATH = Path(
    os.environ.get(
        "CALLINFO_PATH",
        str(Path.home() / "Library/Application Support/Krisp/callinfo.json"),
    )
).expanduser()
RECORDINGS_ARCHIVE_DIR = Path(
    os.environ.get("RECORDINGS_ARCHIVE_DIR", "/Users/anton/Krisp Recordings")
).expanduser().resolve()

# ASR: по умолчанию parakeet на GPU (×12.6 реального времени против ×5 у whisper small),
# whisper остаётся запасным путём. KRISP_ASR_ENGINE=whisper форсирует старое поведение.
ASR_ENGINE = os.environ.get("KRISP_ASR_ENGINE", "parakeet")
PARAKEET_REPO = os.environ.get("PARAKEET_REPO", "mlx-community/parakeet-tdt-0.6b-v3")
WHISPER_FALLBACK_MODEL = os.environ.get("KRISP_WHISPER_MODEL", "small")
PARAKEET_SR = 16000
PARAKEET_CHUNK_SEC = 120.0
PARAKEET_OVERLAP_SEC = 12.0

_ASR: dict[str, Any] = {"parakeet": None, "whisper": None, "parakeet_failed": False}

SETTLE_SECONDS = int(os.environ.get("SETTLE_SECONDS", "120"))
CALLINFO_WAIT_SECONDS = int(os.environ.get("CALLINFO_WAIT_SECONDS", "3600"))
MIN_TRANSCRIPT_CHARS = int(os.environ.get("MIN_TRANSCRIPT_CHARS", "500"))
LOCK_FILE = Path(
    os.environ.get("AUDIO_LOCK_FILE", f"{AUDIO_STATE_FILE}.lock")
).expanduser()

TERMINAL_STATUSES = {"noted", "skipped_short"}
MP3_NAME_RE = re.compile(
    r"^(?P<background>b_)?(?P<stream_id>[^_]+)_(?P<app_id>.+)_(?P<timestamp>\d+(?:\.\d+)?)\.mp3$",
    re.IGNORECASE,
)


def log(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def load_helper_module():
    spec = importlib.util.spec_from_file_location("krisp_to_obsidian", HELPER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Не удалось загрузить helper: {HELPER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_state() -> dict[str, dict[str, Any]]:
    if not AUDIO_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(AUDIO_STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log(f"⚠ Не удалось прочитать state {AUDIO_STATE_FILE}: {exc}")
        return {}


def save_state(state: dict[str, dict[str, Any]]) -> None:
    AUDIO_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = AUDIO_STATE_FILE.with_name(f".{AUDIO_STATE_FILE.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, AUDIO_STATE_FILE)


def parse_audio_name(filename: str) -> Optional[dict[str, Any]]:
    match = MP3_NAME_RE.match(filename)
    if not match:
        return None
    timestamp = float(match.group("timestamp"))
    if timestamp > 10_000_000_000:
        timestamp /= 1000.0
    return {
        "background": bool(match.group("background")),
        "stream_id": match.group("stream_id"),
        "app_id": match.group("app_id"),
        "timestamp": timestamp,
    }


def stage_live_recordings(state: dict[str, dict[str, Any]]) -> None:
    """Hardlink every visible live MP3 before inspecting any of its metadata."""
    if not KRISP_RECORDINGS_DIR.exists():
        return

    for source in KRISP_RECORDINGS_DIR.glob("*.mp3"):
        if source.name.startswith("."):
            continue
        staged = AUDIO_STAGING_DIR / source.name
        linked = False
        try:
            os.link(source, staged)
            linked = True
            log(f"Обнаружен и захвачен аудиофайл: {source.name}")
        except FileExistsError:
            pass
        except Exception as exc:
            log(f"⚠ Не удалось захватить {source.name}: {exc}")
            continue

        now = time.time()
        entry = state.setdefault(
            source.name,
            {
                "status": "staged",
                "staged_at": now,
                "last_size": None,
                "last_size_at": now,
            },
        )
        if linked:
            entry["status"] = "staged"


def audio_is_ready(
    staged: Path,
    entry: dict[str, Any],
    original: Path,
    now: float,
) -> bool:
    size = staged.stat().st_size
    previous_size = entry.get("last_size")

    if previous_size != size:
        entry["last_size"] = size
        entry["last_size_at"] = now

    if not original.exists():
        log(f"Конец звонка определён (оригинал исчез): {staged.name}")
        return True

    unchanged_for = now - float(entry.get("last_size_at", now))
    if unchanged_for >= SETTLE_SECONDS:
        log(
            f"Конец звонка определён (размер стабилен {int(unchanged_for)}с): "
            f"{staged.name}"
        )
        return True
    return False


def background_has_main(staged: Path, state: dict[str, dict[str, Any]]) -> bool:
    if not staged.name.startswith("b_"):
        return False
    main_name = staged.name[2:]
    return (
        (AUDIO_STAGING_DIR / main_name).exists()
        or (KRISP_RECORDINGS_DIR / main_name).exists()
        or main_name in state
    )


def unique_archive_path(stem: str) -> Path:
    target = RECORDINGS_ARCHIVE_DIR / f"{stem}.mp3"
    if not target.exists():
        return target

    counter = 2
    while True:
        candidate = RECORDINGS_ARCHIVE_DIR / f"{stem} ({counter}).mp3"
        if not candidate.exists():
            return candidate
        counter += 1


def build_archive_path(
    helper,
    call: Optional[dict[str, Any]],
    audio_meta: dict[str, Any],
    use_stream_id: bool,
) -> Path:
    started = call.get("date") if call else None
    if started is None:
        started = datetime.fromtimestamp(audio_meta["timestamp"]).astimezone()

    call_name = call.get("call_name", "") if call else ""
    clean_name = (
        helper.clean_name_for_filename(call_name)
        if call_name and not use_stream_id
        else ""
    )
    title = clean_name or audio_meta["stream_id"]
    stem = f"{started.strftime('%Y-%m-%d %H%M')} – {title}"
    if audio_meta["background"]:
        stem += " (background)"
    return unique_archive_path(stem)


def archive_audio(staged: Path, target: Path) -> Path:
    RECORDINGS_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staged), str(target))
    log(f"Аудио архивировано: {target.name}")
    return target


def finish_paired_background(
    staged: Path,
    state: dict[str, dict[str, Any]],
    entry: dict[str, Any],
    helper,
    calls: list[dict[str, Any]],
    audio_meta: dict[str, Any],
) -> bool:
    """Archive a background track after its paired main track is terminal."""
    if not staged.name.startswith("b_"):
        return False
    main_entry = state.get(staged.name[2:], {})
    if main_entry.get("status") not in TERMINAL_STATUSES:
        return False

    main_archive_path = main_entry.get("archive_path")
    if main_archive_path:
        target = unique_archive_path(
            f"{Path(main_archive_path).stem} (background)"
        )
    else:
        call = match_call_by_time(audio_meta["timestamp"], calls)
        target = build_archive_path(
            helper,
            call,
            audio_meta,
            use_stream_id=call is None,
        )
    archived = archive_audio(staged, target)
    entry.update(
        {
            "status": "noted",
            "archive_path": str(archived),
            "note_result": "paired_main_archived",
        }
    )
    save_state(state)
    return True


def stamp(seconds: float) -> str:
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def parakeet_sentences(audio_path: Path) -> list[tuple[float, float, str]]:
    """Предложения (начало, конец, текст) от parakeet.

    Файл декодируем сами: parakeet-mlx умеет принимать путь, но внутри у него
    ffmpeg, которого в системе нет. Часовые записи режем на чанки с перекрытием —
    целиком такая запись в модель не помещается.
    """
    import mlx.core as mx
    from faster_whisper.audio import decode_audio
    from parakeet_mlx.audio import get_logmel

    model = load_parakeet()
    audio = decode_audio(str(audio_path), sampling_rate=PARAKEET_SR)
    chunk = int(PARAKEET_CHUNK_SEC * PARAKEET_SR)
    step = int((PARAKEET_CHUNK_SEC - PARAKEET_OVERLAP_SEC) * PARAKEET_SR)
    total = len(audio)

    sentences: list[tuple[float, float, str]] = []
    last_end = 0.0
    for offset in range(0, max(total, 1), step):
        piece = audio[offset : offset + chunk]
        if len(piece) < PARAKEET_SR // 2:  # хвост короче 0.5с
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
            # предложение, обрезанное границей чанка, целиком приедет со следующим
            if not is_last and end > piece_end - 1.0:
                continue
            # то, что уже записано предыдущим чанком в зоне перекрытия
            if start < last_end - 0.5:
                continue
            sentences.append((start, end, text))
            last_end = max(last_end, end)
        if is_last:
            break
    return sentences


def load_parakeet():
    if _ASR["parakeet"] is None:
        from parakeet_mlx import from_pretrained

        log(f"Загружаю parakeet: {PARAKEET_REPO}")
        _ASR["parakeet"] = from_pretrained(PARAKEET_REPO)
    return _ASR["parakeet"]


def load_whisper():
    if _ASR["whisper"] is None:
        from faster_whisper import WhisperModel

        log(f"Загружаю faster-whisper: {WHISPER_FALLBACK_MODEL}")
        _ASR["whisper"] = WhisperModel(
            WHISPER_FALLBACK_MODEL, device="cpu", compute_type="int8"
        )
    return _ASR["whisper"]


def transcribe_audio(audio_path: Path) -> tuple[str, int, str]:
    """(транскрипт с метками [HH:MM:SS], число символов речи, язык)."""
    if ASR_ENGINE == "parakeet" and not _ASR["parakeet_failed"]:
        try:
            return transcribe_with_parakeet(audio_path)
        except Exception as exc:
            _ASR["parakeet_failed"] = True
            log(
                f"⚠ parakeet не сработал ({type(exc).__name__}: {exc}); "
                f"дальше этот проход идёт на whisper {WHISPER_FALLBACK_MODEL}"
            )
    return transcribe_with_whisper(audio_path)


def transcribe_with_parakeet(audio_path: Path) -> tuple[str, int, str]:
    started = time.monotonic()
    log(f"Parakeet старт: {audio_path.name}")
    sentences = parakeet_sentences(audio_path)
    _ASR["last_engine"] = f"parakeet:{PARAKEET_REPO.split('/')[-1]}"
    rendered = [f"[{stamp(start)}] {text}" for start, _, text in sentences]
    spoken = [text for _, _, text in sentences]
    elapsed = time.monotonic() - started
    audio_seconds = sentences[-1][1] if sentences else 0.0
    log(
        f"Parakeet готов: {audio_path.name}, предложений={len(sentences)}, "
        f"длительность обработки={elapsed:.1f}с (×{audio_seconds / max(elapsed, 0.1):.1f} реального времени)"
    )
    return "\n\n".join(rendered), len(" ".join(spoken)), "auto"


def transcribe_with_whisper(audio_path: Path) -> tuple[str, int, str]:
    model = load_whisper()
    _ASR["last_engine"] = f"faster-whisper:{WHISPER_FALLBACK_MODEL}"
    started = time.monotonic()
    log(f"Whisper старт: {audio_path.name}")
    segments, info = model.transcribe(str(audio_path), vad_filter=True)
    rendered = []
    spoken = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        spoken.append(text)
        rendered.append(f"[{stamp(segment.start)}] {text}")

    elapsed = time.monotonic() - started
    language = getattr(info, "language", "unknown")
    log(
        f"Whisper готов: {audio_path.name}, язык={language}, "
        f"длительность обработки={elapsed:.1f}с"
    )
    return "\n\n".join(rendered), len(" ".join(spoken)), language


def match_call_by_time(
    audio_timestamp: float,
    calls: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    matches = []
    for call in calls:
        record_time = call.get("date")
        if record_time is None:
            continue
        duration = max(float(call.get("duration_sec") or 0), 0.0)
        if duration <= 0:
            continue
        record_ts = record_time.timestamp()
        start_ts = record_ts - duration
        if start_ts - 300 <= audio_timestamp <= record_ts + 300:
            matches.append((abs(audio_timestamp - start_ts), call))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    return matches[0][1]


def fallback_call(audio_meta: dict[str, Any]) -> dict[str, Any]:
    started = datetime.fromtimestamp(audio_meta["timestamp"]).astimezone()
    app_name = audio_meta["app_id"].replace("-", " ").replace("_", " ").strip()
    return {
        "call_name": f"Krisp audio {app_name}".strip(),
        "date": started,
        "date_str": started.strftime("%Y-%m-%d"),
        "call_id": "",
        "app_name": app_name,
        "duration_sec": 0,
    }


def desired_note_path(helper, call: dict[str, Any]) -> Path:
    clean_name = helper.clean_name_for_filename(call["call_name"])
    filename = f"{{meeting}} {clean_name} – {call['date_str']}.md"
    return TRANSCRIPTS_DIR / filename


def write_local_note(
    helper,
    call: dict[str, Any],
    transcript: str,
    stream_id: str,
    use_stream_id: bool,
    audio_path: Path,
) -> tuple[str, Path]:
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    note_index = helper.build_note_index(TRANSCRIPTS_DIR)
    desired = desired_note_path(helper, call)
    filepath = (
        desired
        if use_stream_id
        else helper.find_existing_note_path(call, desired, note_index)
    )

    is_existing = filepath.exists()
    if is_existing:
        content = filepath.read_text(encoding="utf-8")
    else:
        content = helper.build_note_text(call)

    updated = helper.upsert_frontmatter_field(
        content, "audio_file", str(audio_path)
    )
    if is_existing and not helper.note_is_pending(updated):
        if updated != content:
            filepath.write_text(updated, encoding="utf-8")
            helper.sync_note_timestamp(filepath)
            return "existing_complete", filepath
        return "existing_complete", filepath
    content = updated

    id_field = "krisp_stream_id" if use_stream_id else "krisp_call_id"
    id_value = stream_id if use_stream_id else call.get("call_id", "")
    # ВАЖНО: значение "local-whisper-tiny" — это маркер «черновик, ждёт MCP-замену»,
    # а не имя модели: ровно его сравнивает note_is_pending() в krisp_to_obsidian.py.
    # Менять нельзя, иначе облачный транскрипт перестанет заменять локальный.
    # Реальный движок пишем отдельным полем transcript_engine.
    content = helper.upsert_frontmatter_field(
        content, "transcript_source", "local-whisper-tiny"
    )
    content = helper.upsert_frontmatter_field(
        content, "transcript_engine", _ASR.get("last_engine", ASR_ENGINE)
    )
    if id_value:
        content = helper.upsert_frontmatter_field(content, id_field, id_value)
    filepath.write_text(content, encoding="utf-8")
    helper.inject_mcp_content(filepath, f"## Транскрипт\n\n{transcript}")
    log(f"Заметка записана: {filepath.name}")
    return "written", filepath


def process_staged_files(state: dict[str, dict[str, Any]], helper) -> None:
    helper.KRISP_CALLINFO = CALLINFO_PATH
    calls = helper.read_callinfo()

    staged_files = sorted(
        AUDIO_STAGING_DIR.glob("*.mp3"),
        key=lambda path: (path.name.startswith("b_"), path.name),
    )
    for staged in staged_files:
        if staged.name.startswith("."):
            continue
        try:
            audio_meta = parse_audio_name(staged.name)
            if audio_meta is None:
                log(f"⚠ Пропуск: имя файла не распознано: {staged.name}")
                continue

            entry = state.setdefault(
                staged.name,
                {
                    "status": "staged",
                    "staged_at": time.time(),
                    "last_size": None,
                    "last_size_at": time.time(),
                },
            )
            status = entry.get("status", "staged")
            if status in TERMINAL_STATUSES:
                continue
            if audio_meta["background"] and background_has_main(staged, state):
                if finish_paired_background(
                    staged, state, entry, helper, calls, audio_meta
                ):
                    continue
                log(f"Пропуск background: найден парный основной файл {staged.name[2:]}")
                continue

            if status == "staged":
                now = time.time()
                original = KRISP_RECORDINGS_DIR / staged.name
                if not audio_is_ready(staged, entry, original, now):
                    continue
                transcript, char_count, language = transcribe_audio(staged)
                entry.update(
                    {
                        "status": "transcribed",
                        "transcript": transcript,
                        "transcript_chars": char_count,
                        "language": language,
                        "transcribed_at": time.time(),
                    }
                )
                save_state(state)

                if char_count < MIN_TRANSCRIPT_CHARS:
                    short_call = match_call_by_time(audio_meta["timestamp"], calls)
                    archive_target = build_archive_path(
                        helper,
                        short_call,
                        audio_meta,
                        use_stream_id=short_call is None,
                    )
                    archived = archive_audio(staged, archive_target)
                    entry.update(
                        {
                            "status": "skipped_short",
                            "archive_path": str(archived),
                            "skip_reason": f"transcript shorter than {MIN_TRANSCRIPT_CHARS} chars",
                        }
                    )
                    entry.pop("transcript", None)
                    log(
                        f"Пропуск короткого транскрипта: {staged.name} "
                        f"({char_count} < {MIN_TRANSCRIPT_CHARS} символов)"
                    )
                    save_state(state)
                    continue

            call = match_call_by_time(audio_meta["timestamp"], calls)
            use_stream_id = False
            if call is None:
                waiting_since = float(
                    entry.setdefault("waiting_callinfo_since", time.time())
                )
                if time.time() - waiting_since < CALLINFO_WAIT_SECONDS:
                    entry["status"] = "waiting_callinfo"
                    log(f"Ожидание callinfo для {staged.name}")
                    save_state(state)
                    continue
                use_stream_id = True
                call = fallback_call(audio_meta)
                wait_minutes = max(1, CALLINFO_WAIT_SECONDS // 60)
                log(
                    f"⚠ WARN: callinfo не найден за {wait_minutes} минут для {staged.name}; "
                    f"использую krisp_stream_id={audio_meta['stream_id']}"
                )

            archive_target = build_archive_path(
                helper,
                call,
                audio_meta,
                use_stream_id=use_stream_id,
            )
            result, filepath = write_local_note(
                helper,
                call,
                entry.get("transcript", ""),
                audio_meta["stream_id"],
                use_stream_id,
                archive_target,
            )
            archived = archive_audio(staged, archive_target)
            entry.update(
                {
                    "status": "noted",
                    "archive_path": str(archived),
                    "note_path": str(filepath),
                    "note_result": result,
                }
            )
            entry.pop("transcript", None)
            if result == "existing_complete":
                log(
                    f"Пропуск tiny-транскрипта: заметка уже содержит готовый "
                    f"MCP-транскрипт: {filepath.name}"
                )
            save_state(state)
        except Exception as exc:
            log(f"⚠ Ошибка обработки {staged.name}: {type(exc).__name__}: {exc}")
            try:
                save_state(state)
            except Exception as state_exc:
                log(f"⚠ Не удалось сохранить state после ошибки: {state_exc}")


def main() -> int:
    AUDIO_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    RECORDINGS_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

    with LOCK_FILE.open("a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("Другой проход уже выполняется; выхожу")
            return 0

        try:
            helper = load_helper_module()
            state = load_state()
            stage_live_recordings(state)
            save_state(state)
            process_staged_files(state, helper)
            save_state(state)
        except Exception as exc:
            log(f"⚠ Ошибка прохода: {type(exc).__name__}: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
