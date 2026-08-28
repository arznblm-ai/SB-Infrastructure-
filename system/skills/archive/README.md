# Архив скиллов

### [[2026-08-09]]

Скиллы, выведенные из активного слоя. **Не удалены** — правило vault: только перемещение. Агенты этот каталог не читают и по интентам сюда не маршрутизируются.

## Выведено 2026-08-09 (ADR-020, аудит использования)

| Скилл | Последний реальный след | Почему выведен |
|---|---|---|
| `saved-video-strategist` | артефакт 2026-06-14 | ноль вызовов; скрипт живёт в `~/.codex/skills/`, Codex недоступен с 26.07; в `claude.md` не было ни строки роутера, ни строки таблицы истины |
| `meeting-insights-analyzer` | прогон 2026-06-08 | ноль вызовов за окно логов; строки в INTENT ROUTER не было — интент «как прошли встречи» никуда не вёл |
| `developer-growth-analysis` | нет | ноль вызовов; анализирует историю Codex-сессий, которых больше не появляется; в роутере не маршрутизировался |

Общее: все три завязаны на исчезнувший Codex и не имели рабочего входа.

`_commands/transcripts.md` — дубль обёртки: `/transcripts` и `/summarize` вели на один и тот же `transcript-summarizer`. Содержательная часть влита в `/summarize`.

## Выведено 2026-08-26 (архив департамента, решение Антона)

| Скилл | Почему выведен |
|---|---|
| `alina` | Департамент `infrastructure/Alina/` архивирован целиком (TG-бот @Alina_portal_bot выключен, тег `producer-final`). Умение считать сметы (бриф → брейкдаун по таймингу и ценам) унаследовал проектный компаньон Крис — `system/skills/kris/SKILL.md`, команда `/kris`. Знания (`knowledge/`, `estimates/`) скопированы в `infrastructure/Kris/memory/`; оригиналы Алины не тронуты, банner-архив — в начале `infrastructure/Alina/CLAUDE.md`. |

## Как вернуть

```bash
mv "/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/archive/<slug>" "/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/<slug>"
python3 "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Obsidian Manager/Scripts/update_index.py" "/Users/anton/AI AGENT FOLDER/Second Brain/system/skills"
python3 "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Agent Operating Standard/Scripts/audit_skill_registry.py" --write
```

После возврата дописать строку в INTENT ROUTER корневого `claude.md` — без неё скилл снова окажется невызываемым.

## Открытый хвост

Копии трёх скиллов остались в `~/.codex/skills/` (runtime-слой Codex, вне vault). Реестр помечает их `missing-in-vault`. Убирать ли их там — решение Антона.
