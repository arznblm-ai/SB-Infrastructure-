#!/usr/local/bin/python3
"""voice_checkin_transcribe.py — транскрипт голосового чек-ина для Telegram-бота.

Отдельный процесс, потому что бот крутится на системном питоне без parakeet /
faster-whisper: они установлены в /usr/local/bin/python3.

    /usr/local/bin/python3 voice_checkin_transcribe.py <аудиофайл>

stdout — транскрипт, stderr — прогресс и ошибки, exit code != 0 при провале.

Движок и чанкование переиспользуются из voice_memos.py (parakeet-tdt-0.6b-v3 по
умолчанию, fallback — faster-whisper small; аудио декодируется через
faster_whisper.audio.decode_audio, поэтому телеграмный ogg-opus .oga читается
без ffmpeg). Прогресс-лог voice_memos перенаправляется в stderr, чтобы чек-ины
не засоряли лог диктофонных записей.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def transcribe(path: Path) -> str:
    import voice_memos

    voice_memos.log = log  # прогресс в stderr, а не в ~/Library/Logs/voice-memos.log
    return voice_memos.transcribe(path)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        log(f"usage: {Path(argv[0]).name} <audio-file>")
        return 2
    path = Path(argv[1]).expanduser()
    if not path.is_file():
        log(f"audio file not found: {path}")
        return 2

    try:
        transcript = transcribe(path).strip()
    except Exception as exc:
        log(f"transcription failed: {type(exc).__name__}: {exc}")
        return 1

    if not transcript:
        log("empty transcript: speech not recognized")
        return 3

    print(transcript)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
