### [[2026-04-04]]

Прочитай skill-файл `/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/transcript-summarizer/SKILL.md` и выполни его протокол.

Режим выбирай по аргументу:
- если аргумент пустой — запусти scan и покажи, какие source files в `transcripts/` ещё не обработаны
- если аргумент описывает конкретный файл или тему — запусти single для этого запроса
- если аргумент явно просит обработать всё — запусти batch

Работай по текущим правилам vault:
- raw/source files брать из `transcripts/`
- course summaries сохранять в `education/`
- meeting summaries сохранять в `meetings/`
- Claude sessions не трогать: они живут в `sessions/`

Запрос пользователя: $ARGUMENTS
