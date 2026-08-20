# Instagram Reel Note Schema

### [[2026-06-13]]

Use this schema for every saved reel note.

## Filename

Default:

`{published_date}_{author_username}_{shortcode}_{slug}.md`

Fallbacks:

- `unknown-date` when the post date is unavailable.
- `unknown-author` when the author is unavailable.
- `reel` when no title/slug can be derived.

## Frontmatter

Required keys:

```yaml
type: instagram-reel-transcript
source_url:
shortcode:
media_id:
author_username:
author_name:
author_url:
published_at:
captured_at:
language:
duration_seconds:
likes:
comments:
views:
tools:
skills:
links:
tags:
```

Use arrays for `tools`, `skills`, `links`, and `tags`.

## Sections

Use this exact section order:

1. `## Краткое содержание`
2. `## Суть`
3. `## Полезные ссылки`
4. `## Упомянутые инструменты и skills`
5. `## Главные инсайты`
6. `## Готовые решения / как применить`
7. `## Оценка применимости для Антона`
8. `## Strategic Board analysis`
9. `## Транскрипт`
10. `## Raw caption / metadata`

## Writing Rules

- Write summary and insights in Russian by default.
- Preserve transcript language unless the user asks for translation.
- Mark missing facts as `unknown`.
- Separate confirmed links from inferred links.
- Make `Готовые решения` operational: each item should be reusable as a workflow, prompt, automation, or product idea.
- Use `Strategic Board analysis` as the only analysis section. Include decision framing, mechanism, strategic fit, risks, unknowns, market/product implications, and recommended next move when a reel affects Anton's business/product choices.
- Include tags that support later search, such as `instagram-reels`, `content-intelligence`, `ugc`, `claude`, `apify`, `viral-analysis`.
