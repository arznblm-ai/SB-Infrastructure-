---
name: cleanmymac
description: Регулярная чистка мака вместо подписки CleanMyMac — диагностика фризов (CPU/память/своп), скан и удаление безопасных кэшей, отчёт по крупным файлам, проверка автозапуска. Use when Anton asks to clean the Mac, free disk space, diagnose slowness/freezes, or runs /cleanmymac.
metadata:
  short-description: Чистка мака и диагностика нагрузки вместо CleanMyMac
model: sonnet
---

# CleanMyMac — чистка и диагностика мака

### [[2026-09-01]]

Скилл заменяет подписку CleanMyMac (снесена 2026-09-01). Запускается Антоном вручную, регулярно (раз в 2–4 недели или при фризах).

## Порядок работы

### Фаза 1 — Диагностика (всегда)

```bash
df -h / | tail -1
sysctl vm.swapusage
uptime
top -l 2 -n 12 -o cpu -stats pid,command,cpu,mem | tail -15
ps aux -m | head -12
```

Зафиксируй: свободное место, своп, load average, топ-пожиратели CPU и памяти. Если своп > 4 ГБ или аптайм > 7 дней — в финальном отчёте рекомендуй перезагрузку (сам не перезагружай).

### Фаза 2 — Скан кэшей

```bash
du -sm ~/Library/Caches/* 2>/dev/null | sort -rn | head -15
du -sh ~/Library/Logs ~/.npm/_cacache ~/.cache/uv ~/.cache/selenium ~/.cache/codex-runtimes ~/Library/Caches/Homebrew 2>/dev/null
du -sm ~/.cache/* 2>/dev/null | sort -rn | head -8
```

### Фаза 3 — Удаление (только SAFE-список, без подтверждения)

Удалять можно **только** это — кэши, которые приложения пересоздают сами:

- `~/Library/Caches/Adobe/After Effects`, `~/Library/Caches/Adobe/Premiere Pro` (медиакэши; в 2026-09-01 AE-кэш держал 230 ГБ с 2021 года)
- Кэши браузеров: `~/Library/Caches/Arc`, `~/Library/Caches/company.thebrowser.Browser`, `~/Library/Caches/Google` (ошибки "Directory not empty" при живом браузере — норма, игнорировать)
- `~/Library/Caches/com.spotify.client`, `~/Library/Caches/Steam`, `~/Library/Caches/ms-playwright`
- `~/.npm/_cacache`, `~/.cache/uv`, `~/.cache/selenium`, `~/.cache/codex-runtimes`, `~/Library/Caches/Homebrew`
- `~/Library/Logs/*` старше 30 дней

После удаления — снова `df -h /`, разницу в отчёт.

### Фаза 4 — Крупные файлы (только отчёт, НЕ удалять)

```bash
du -sm ~/Desktop/* 2>/dev/null | sort -rn | head -8
du -sm ~/Downloads/* 2>/dev/null | sort -rn | head -10
du -sm ~/Movies ~/Music ~/Documents 2>/dev/null | sort -rn
```

Показать список Антону. Удалять из Desktop/Downloads — **только то, что Антон явно назвал** в этой сессии. Исключение по стоячему решению Антона (2026-09-01): скачанные сериалы/фильмы (`*.mp4`, `*.mkv` с названиями эпизодов вида S01E02) в Downloads он смотрит через Лампу и хранить не нужно — можно предложить списком и удалить после «да».

### Фаза 5 — Автозапуск и фон (только отчёт)

```bash
launchctl list | grep -v com.apple
```

Отметить подозрительное/новое. Отключать LaunchAgents — только по явной команде.

## ЗАПРЕЩЕНО удалять (protected list)

- `~/.cache/huggingface` — модели parakeet/whisper для диктовки и транскрипции (~12 ГБ, перекачивать долго)
- `~/Krisp Recordings/` — вечное хранилище записей звонков
- Vault `~/AI AGENT FOLDER/` и всё внутри
- `~/Library/Application Support/*` — там данные приложений, не кэши
- `~/.config/second-brain/` — токены и секреты
- Файлы Антона в Desktop/Downloads/Movies/Documents (кроме явно названных)
- Ничего не трогать в `/System`, `/Library` (системные) — скилл работает только в домашней папке

## Финальный отчёт

Одним сообщением: сколько освобождено, состояние памяти/свопа, топ-3 пожирателя ресурсов, крупные файлы-кандидаты (со ссылками-путями), рекомендация по перезагрузке если нужна.

## Связи

- Правило debug-петли: скан-батчи Bash легитимны, при срабатывании hook-ограничителя повторить команду
- История: 2026-09-01 первый прогон освободил 234 ГБ (кэш AE 230 ГБ + браузеры + dev-кэши), снесён CleanMyMac 5 со всеми хвостами
