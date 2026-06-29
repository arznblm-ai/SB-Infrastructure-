# Link Inbox

## Контекст

Link Inbox — автоматизация для Telegram-бота / канала с полезными ссылками.

Цель: Антон видит полезный пост, видео, лекцию или статью, отправляет ссылку в `Saved Links` Telegram bot, агент сохраняет source cards и transcripts в Second Brain, а потом присылает review обратно в Telegram.

## Папки

| Что | Путь |
|---|---|
| Проект | `/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Link Inbox/` |
| Скрипты | `/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Link Inbox/Scripts/` |
| Source cards (URL/статус/TG msg) | `/Users/anton/AI AGENT FOLDER/Second Brain/resources/link-inbox/links/` |
| Единые заметки ресурсов (одна на ресурс) | `/Users/anton/AI AGENT FOLDER/Second Brain/transcripts/external resources/` |
| Индекс внешних ресурсов | `/Users/anton/AI AGENT FOLDER/Second Brain/transcripts/external resources/index.md` |
| Общий builder заметки (auto + enrich) | `/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Link Inbox/Scripts/external_resource_note.py` |
| Summary заметки (DEPRECATED, до 2026-06-28) | `/Users/anton/AI AGENT FOLDER/Second Brain/resources/link-inbox/summaries/` |
| Reviews | `/Users/anton/AI AGENT FOLDER/Second Brain/resources/link-inbox/reviews/` |
| Quality reports | `/Users/anton/AI AGENT FOLDER/Second Brain/resources/link-inbox/quality-reports/` |
| Quarantine | `/Users/anton/AI AGENT FOLDER/Second Brain/resources/link-inbox/quarantine/` |
| Runtime config | `~/.config/link-inbox/config.json` |
| Runtime state | `~/.config/link-inbox/state.json` |
| Runtime env | `~/.config/link-inbox/env` |
| Log | `~/Library/Logs/link-inbox.log` |
| Bot Log | `~/Library/Logs/link-inbox-bot.log` |

## Workflow

1. `telegram_link_bot.py` слушает личные сообщения в Telegram bot через Bot API.
2. Из каждого сообщения достаёт URL и сохраняет link card в `resources/link-inbox/links/`.
3. После сохранения запускает `process_and_notify.py` в фоне, чтобы бот быстро ответил и не зависал на транскрипте.
4. YouTube ссылки транскрибируются через существующий `youtube-transcribe` script и сохраняются в `transcripts/external resources/`.
5. Instagram/TikTok ссылки обрабатываются через `infrastructure/UGC Downloader/ugc_downloader.py`: видео скачивается через `yt-dlp`, транскрибируется через `video-transcribe`, transcript сохраняется в `transcripts/external resources/`, создаётся `remix_brief.md`.
6. После успешной обработки Link Inbox строит **одну богатую заметку на ресурс** через `external_resource_note.py` (auto-уровень: frontmatter, транскрипт, caption, ссылки/инструменты по эвристике; умные секции `enrichment: pending`). Заметка заменяет сырой транскрипт по тому же пути.
7. Web ссылки получают title/description и тоже сводятся к одной заметке того же формата.
8. **Enrich-уровень (LLM):** умные секции (суть, инсайты, готовые решения, Strategic Board, проверенные ссылки) заполняет агент командой `external_resource_note.py --path <note> --summary ... --essence ...` (см. skill `instagram-reel-analyzer`). Авто-LLM в фоне не включён — для него нужен API-ключ в `~/.config/link-inbox/env`.
9. После обработки Link Inbox пересобирает `transcripts/external resources/index.md` (note-centric), чтобы агенты быстро находили ресурсы.
9. После обработки бот присылает короткий digest: содержание, инсайты и пути `summary` / `transcript` / `brief`.
9. Если Instagram/TikTok закрыт, rate-limited или требует login, обработка падает в `failed` с ошибкой. Тогда нужно включить `ugc.cookies_from_browser` или `ugc.cookies`.
10. `/review` в боте или `send_review.py --send` создаёт batch review и отправляет его обратно в Telegram.
11. `collect_links.py` через Telethon оставлен как optional channel-import режим, но основной сценарий теперь bot DM.
12. Weekly quality audit проверяет index/summaries/transcripts/cards, регенерирует missing summaries и переносит safe duplicates/stale artifacts в quarantine.

## External Resource Memory Contract

`transcripts/external resources/index.md` — первый файл для чтения, если агенту нужен контекст из сохранённых ссылок.

Обязательный retrieval order для экономии токенов:

1. `index.md` — найти нужный ресурс.
2. `note` — открыть одну заметку ресурса; она самодостаточна (краткое содержание, суть, ссылки, инструменты, инсайты, готовые решения, Strategic Board, транскрипт).
3. `card` — открыть только если нужны source URL, статус обработки или оригинальное Telegram-сообщение.

Запрещено сканировать все заметки внешних ресурсов до чтения `index.md`.

Правило маршрутизации:

- `index.md` — обзор всех ресурсов и быстрый выбор нужного.
- `note` — единая богатая заметка (one per resource) в `transcripts/external resources/`.
- `card` — источник, URL, Telegram-message, статус обработки в `resources/link-inbox/links/`.
- `enrichment: pending` во frontmatter = умные секции ещё не заполнены агентом; запусти enrich.

Не смешивать эти заметки с `meetings/` и `education/`: это внешние источники, а не личные встречи и не курсовые summary.

## Первый запуск

Скопируй пример конфига:

```bash
mkdir -p ~/.config/link-inbox
cp "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Link Inbox/config/config.example.json" ~/.config/link-inbox/config.json
```

Заполни для bot DM режима:

- env `LINK_INBOX_BOT_TOKEN`
- `telegram.allowed_chat_ids`, если нужно ограничить доступ только Антоном
- `telegram.review_channel`, если нужен scheduled review через Bot API

Telethon поля нужны только для optional channel-import режима:

- `telegram.api_id`
- `telegram.api_hash`
- `telegram.source_channel`
- env `LINK_INBOX_BOT_TOKEN`, если нужен auto-send review

Авторизация Telethon для optional channel-import:

```bash
cd "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Link Inbox"
python3 Scripts/collect_links.py --authorize-only
```

Ручной запуск:

```bash
python3 Scripts/telegram_link_bot.py --once
python3 Scripts/process_links.py --limit 5
python3 Scripts/build_external_resources_index.py
python3 Scripts/link_inbox_runner.py --bot-only --limit 5 --send-review
```

Telegram commands:

- `/help` — как пользоваться ботом
- `/status` — сколько ссылок сохранено / обработано
- `/process` — запустить обработку pending ссылок
- `/review` — прислать обзор обработанных ссылок

Bot reply contract:

- First reply: confirm that the link card was saved and processing started.
- Follow-up reply after processing: compact useful save report, not a debug log.
- The report must answer: what the video/post is about, which tools/resources/links appeared, what system or workflow the author showed, what it gave them, and where the useful artifact was saved.
- Keep paths minimal: include `transcripts/external resources/index.md`, summary path, transcript path, and brief path; do not include video/card paths unless processing failed or Anton asks.
- Duplicate processed link: immediately send the existing digest instead of reprocessing.

Instagram/TikTok notes:

- Public Reels often process without cookies.
- Private / age-gated / region-gated / rate-limited Reels may need browser cookies.
- Cookies are configured outside vault in `~/.config/link-inbox/config.json`:
  - `ugc.cookies_from_browser`: `chrome`, `safari`, `brave`, etc.
  - `ugc.cookies`: path to Netscape `cookies.txt`.
  - Do not store cookies in the vault.

## Автозапуск

Bot token хранится вне vault:

```bash
mkdir -p ~/.config/link-inbox
chmod 700 ~/.config/link-inbox
printf 'export LINK_INBOX_BOT_TOKEN="PASTE_TOKEN_HERE"\n' > ~/.config/link-inbox/env
chmod 600 ~/.config/link-inbox/env
```

Установить ежедневный запуск в 21:30:

```bash
cd "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Link Inbox"
python3 Scripts/install_launch_agent.py --hour 21 --minute 30
```

Установить 24/7 Telegram bot listener:

```bash
cd "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Link Inbox"
python3 Scripts/install_telegram_bot_agent.py
```

Установить weekly quality audit (понедельник 10:15):

```bash
cd "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Link Inbox"
python3 Scripts/install_quality_audit_agent.py
```

Ручной quality audit:

```bash
python3 Scripts/link_quality_audit.py
```

Dry-run без чистки:

```bash
python3 Scripts/link_quality_audit.py --dry-run
```

Снять автозапуск:

```bash
python3 Scripts/install_launch_agent.py --uninstall
```

## Safety

- Не хардкодить Telegram tokens в файлах vault.
- Не удалять source cards при ошибке обработки.
- Не отмечать Instagram/TikTok как processed, пока нет реального download/transcription или хотя бы saved metadata artifact.
- Review должен разделять `processed`, `needs_manual_processing` и `failed`.
- Dry-run review не должен отмечать ссылки как reviewed. Reviewed ставится после `--send` или явного `--mark-reviewed`.
- Capture должен быть дешёвым: обычная ссылка не запускает Codex.
- Секреты живут только в `~/.config/link-inbox/env`.
