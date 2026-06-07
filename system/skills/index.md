# Index: skills

> Автоматически сгенерировано Obsidian Manager.
> Последнее обновление: 2026-05-14 20:46.
> Не редактировать вручную — будет перезаписан при следующей индексации.

| Скил | Описание |
|------|----------|
| [[design department]] | Operating system for presentation design: use Design Department when a request should enter through `design-orchestrator`, be classified into the right workflow mode, routed through specialist roles, and return with explicit artifacts plus review status. |
| [[research department]] | Operating system for factual research: use Research Department when a request should enter through the canonical research router, be classified into the right factual workflow, routed through specialist roles, and return with explicit artifacts plus readable exports. |
| [[research-scout]] | Ежедневный разведчик: ищет свежие статьи, посты и новости по трём направлениям Антона (AI-native бизнес, креативная индустрия + AI, vibecoding), фильтрует по релевантности и сохраняет карточки в vault. Используй когда нужно: найти свежие материалы по теме, запустить ежедневный патруль, сделать research scout, 'что нового в AI', 'найди статьи про', 'research scout', 'патруль', 'свежие новости'. Также используется как scheduled task для автоматического сбора. |
| [[transcript-summarizer]] | Обрабатывает source materials из папки transcripts/: классифицирует (лекция или встреча), создаёт structured summary по documentation framework (Minto + MECE + BFO + DRY), раскладывает в education/ или meetings/. Claude-сессии не трогает: они живут в sessions/. Используй этот skill когда нужно: обработать новые транскрипты, создать summary из сырого текста, разобрать лекцию или встречу, 'обработай транскрипты', 'разложи сырьё', 'сделай summary', 'что нового в transcripts', 'переработай запись'. |
| [[vibecoding-mentor]] | Персональный Silicon Valley ментор по вайбкодингу уровня Андрея Карпатого. Используй этот skill каждый раз, когда пользователь хочет: получить оценку своего прогресса в обучении, разобрать технический термин или концепцию вайбкодинга, получить рекомендации что изучать дальше, провести ревью своих skills или структуры проекта, обсудить что-то из лекций или курсов, спросить 'как мне стать лучше', 'что учить', 'оцени мой прогресс', 'менторская сессия', 'vibecoding', 'вайбкодинг'. Также триггерится на: 'проверь мой second brain', 'что нового в моём обучении', 'дай задание', 'домашка', 'практика'. По умолчанию объясняй развёрнуто: не только что делать, но и зачем это нужно, какой навык это тренирует и что ученик потеряет, если полностью отдаст задачу AI. |
| [[video-transcribe]] | Transcribe local video files into Markdown notes with faster-whisper, save raw transcripts into `transcripts/`, then run `transcript-summarizer` to place structured summaries into `education/` or `meetings/`. |
| [[youtube-transcribe]] | Transcribe public YouTube videos from a URL into Markdown notes, then run `transcript-summarizer` and save the resulting summary into Second Brain. Use when the user pastes a YouTube link and wants the transcript stored in `/Users/anton/AI AGENT FOLDER/Second Brain/transcripts` and the summary routed into `education/` or `meetings/`. |

**Статистика:** 7 скилов.
