# Git Update Automation

## Контекст

Git Update — weekly automation для безопасного обновления приватного GitHub repo `SB-Infrastructure-` из Second Brain infrastructure snapshot.

Цель: раз в неделю сохранить reusable infrastructure в GitHub без утечки meetings, transcripts, sessions, runtime state, logs, tokens и личных данных.

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
