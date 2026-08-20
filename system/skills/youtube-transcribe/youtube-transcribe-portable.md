# youtube-transcribe

### [[2026-04-06]]

Переносимая версия навыка для быстрого копирования в другие системы.

Если софт поддерживает структуру навыка с папками, используй весь пакет:
- `system/skills/youtube-transcribe/SKILL.md`
- `system/skills/youtube-transcribe/agents/openai.yaml`
- `system/skills/youtube-transcribe/scripts/transcribe_youtube.py`

Если нужен один файл для быстрого копирования, используй блок ниже как основной текст навыка:

```xml
<skill>
<name>youtube-transcribe</name>
<path>/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/youtube-transcribe/SKILL.md</path>
---
name: youtube-transcribe
description: Transcribe public YouTube videos from a URL into Markdown notes, create a companion summary, and save both into Second Brain. Use when the user pastes a YouTube link and wants the transcript stored in the Vault, by default in `/Users/anton/AI AGENT FOLDER/Second Brain/transcripts/youtube transcrpts` unless they name another folder.
---

# YouTube Transcribe

Use this skill when the user pastes a public YouTube URL and wants the transcript saved into the Vault.

Default destination:
- `/Users/anton/AI AGENT FOLDER/Second Brain/transcripts/youtube transcrpts`

You may override the destination folder if the user explicitly asks for another folder in the Vault.

Workflow:
1. Download the best available audio from the YouTube URL
2. Transcribe it locally with faster-whisper
3. Save the transcript as Markdown in the Vault
4. Create a separate summary note next to the transcript
5. Verify both files and report the saved paths

</skill>
```
