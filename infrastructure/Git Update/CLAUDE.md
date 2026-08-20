# Git Update Automation

### [[2026-08-08]]

## Контекст

Git Update — **ежедневная** automation (с 2026-08-08, решение Антона; до этого weekly) для безопасного обновления **публичного** GitHub repo `SB-Infrastructure-` из Second Brain infrastructure snapshot. Public — намеренно (решение Антона 2026-08-08): шарибельный слой скиллов/инфраструктуры для ревью живыми людьми.

Цель: раз в день (10:45, при спящем маке — при пробуждении) сохранять актуальную reusable infrastructure в GitHub без утечки meetings, transcripts, sessions, runtime state, logs, tokens и личных данных. При провале прогона (safety scan, push, краш) runner шлёт ❌-алерт в TG (токен из `daily-focus.env`); успех — молчание. Имена Label/файлов сохраняют слово «weekly» исторически — переименование не стоит риска сломать пути.

## Папки

| Что | Путь |
|---|---|
| Проект | `/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Git Update/` |
| Скрипты | `/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Git Update/Scripts/` |
| Sync skill | `/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/git-update/` |
| Git repo | `/Users/anton/AI AGENT FOLDER/SB-Infrastructure-/` |
| LaunchAgent | `~/Library/LaunchAgents/com.anton.weekly-git-update.plist` |
| Log | `~/Library/Logs/weekly-git-update.log` |

## Workflow

1. Weekly LaunchAgent запускает `Scripts/run_weekly_git_update.sh`.
2. Runner вызывает `system/skills/git-update/scripts/sync_infrastructure_repo.py --push`.
3. Sync script копирует только whitelist reusable infrastructure в `SB-Infrastructure-`.
4. Safety scan проверяет forbidden folders/files and secret patterns.
5. Если есть изменения и scan clean — создаётся commit.
6. Если terminal GitHub auth работает — commit пушится в `origin/main`.
7. Если push не прошёл — commit остаётся локально, push можно сделать через GitHub Desktop.
8. Если прогон завершился неуспехом — runner шлёт один ❌-алерт в Telegram (см. ниже). Успешный прогон молчит.

## Failure Alerting (добавлено 2026-08-08)

Причина: два еженедельных прогона подряд молча упали на `SAFETY SCAN FAILED` — Антон узнал случайно.

- Алерт живёт в runner'е `Scripts/run_weekly_git_update.sh`, а не в sync-скрипте: ручной `$git-update` не должен слать сообщения в Telegram.
- Триггер: ненулевой код возврата python-скрипта **или** `SAFETY SCAN FAILED` в выводе (страховка на случай, если код вернут 0).
- Формат: `❌ Weekly git-update не прошёл: <причина>. Лог: ~/Library/Logs/weekly-git-update.log`
- Причины: `SAFETY SCAN FAILED — <первая находка>` / `push в origin/main не прошёл` / `код возврата N — <последняя строка вывода>`.
- Канал: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` из `~/.config/second-brain/daily-focus.env` — тот же, что у остальных мак-алертов.
- Best-effort: нет env-файла, нет сети или Telegram вернул 4xx (`curl --fail`) — пишется строка `[alert] ...` в лог, прогон не падает из-за алерта.
- Exit code python-скрипта пробрасывается наружу (2 — safety scan, 3 — push), чтобы launchd тоже видел провал.
- Успешный прогон и прогон без изменений (`No infrastructure changes to commit.`) сообщений не шлют.

## Safety Contract

- Не коммитить весь Second Brain.
- Не коммитить `meetings/`, `transcripts/`, `sessions/`, runtime state, logs, reports, raw exports.
- Не коммитить `.env`, tokens, credentials, cookies, private keys.
- Любой weekly run должен использовать тот же safety scan, что и ручной `$git-update`.
- Если safety scan падает, commit/push не создаётся.

## Manual Commands

Dry-run:

```bash
python3 "/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/git-update/scripts/sync_infrastructure_repo.py" --dry-run --no-commit
```

Manual sync + push:

```bash
python3 "/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/git-update/scripts/sync_infrastructure_repo.py" --push --message "Update Second Brain infrastructure snapshot"
```

Install weekly automation:

```bash
cd "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Git Update"
python3 Scripts/install_weekly_git_update_agent.py
```

Run weekly automation now:

```bash
python3 Scripts/install_weekly_git_update_agent.py --run-now
```

Uninstall:

```bash
python3 Scripts/install_weekly_git_update_agent.py --uninstall
```

## Inputs

- Whitelisted infrastructure files from Second Brain.
- Sync rules in `system/skills/git-update/scripts/sync_infrastructure_repo.py`.
- Local Git repository state in `/Users/anton/AI AGENT FOLDER/SB-Infrastructure-/`.
- GitHub authentication available through the terminal or GitHub Desktop.

## GitHub Auth (настроено 2026-07-08)

- Remote `origin` переключён на SSH: `git@github.com:arznblm-ai/SB-Infrastructure-.git`.
- Ключ: `~/.ssh/id_ed25519_github` (ed25519, без passphrase — нужен для headless LaunchAgent), прописан в `~/.ssh/config` для `github.com`.
- Публичный ключ добавлен в GitHub-аккаунт `arznblm-ai` (comment: `anton-mac-sb-infrastructure`).
- До 2026-07-08 push падал молча (HTTPS без кредов) — коммиты копились локально; 7 штук допушены вручную через GitHub Desktop.
- Если push снова падает: проверь `ssh -T git@github.com` — ключ мог быть удалён из GitHub.

## Human Confirmation Gates

Require Anton confirmation before changing whitelist/blacklist policy, adding new infrastructure folders to Git sync, force-pushing/rebasing/resetting, or committing any folder that may contain transcripts, meetings, sessions, runtime state, logs, tokens, or personal data.
