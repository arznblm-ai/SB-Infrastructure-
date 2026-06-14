---
name: instagram-reel-analyzer
description: Analyze Instagram Reels from reel URLs or downloaded reel videos, extract metadata and transcript, find mentioned tools/links, run a post-analysis with Strategic Board when business/product relevance exists, and save a searchable Markdown note with summary, insights, reusable solutions, skills, source links, and strategic implications. Use when the user sends an Instagram reel link, asks to разбери/проанализируй рилс из инстаграма, wants reel transcripts saved for future search, asks for strategic/business implications of a reel, or asks to build an insight library from Instagram reels.
---

# Instagram Reel Analyzer

Use this skill to turn Instagram Reels into durable Second Brain notes.

Default storage:

- Notes: `/Users/anton/AI AGENT FOLDER/Second Brain/resources/instagram-reels/transcripts`
- Downloaded media: `/Users/anton/AI AGENT FOLDER/Second Brain/resources/instagram-reels/media`
- Extra reports/HTML/screenshots: `/Users/anton/AI AGENT FOLDER/Second Brain/resources/instagram-reels/reports`
- Indexes and future rollups: `/Users/anton/AI AGENT FOLDER/Second Brain/resources/instagram-reels/index`

## Workflow

1. Resolve the reel source.
   - If the user gives a URL, try the public Instagram page first.
   - If Instagram blocks video access, use page metadata and ask for the downloaded video only when audio/transcript cannot be obtained.
   - If the user gives a local video file, skip URL extraction and transcribe the file directly.
2. Extract available metadata.
   - Source URL, shortcode, media id, author username/full name/profile URL, post date, like/comment/view counts, caption, cover image, video URL, audio metadata, external links, mentioned products.
   - If metadata is unavailable, mark fields as `unknown`; do not invent.
3. Download or locate the media when possible.
   - Store media in `resources/instagram-reels/media/`.
   - Use `/private/tmp` for temporary HTML/JSON files.
4. Transcribe the audio.
   - Prefer local `faster_whisper` with `tiny` for speed or `small` for better English quality.
   - Preserve timestamps.
   - If the reel has no audio or transcription fails, save the caption and visual/metadata analysis anyway.
5. Research mentioned tools and links.
   - Browse when the reel mentions tools, communities, SaaS, frameworks, courses, or “link in bio”.
   - Prefer official websites for tool links.
   - Clearly separate confirmed links from inferred/likely links.
6. Run Strategic Board post-analysis when the reel has business, product, marketing, distribution, automation, or agency relevance.
   - Read `/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/strategic-board/SKILL.md`.
   - Use the reel transcript as the source artifact and relevant vault context only when it changes the recommendation.
   - Add a concise decision frame, facts vs interpretations, strategic implications, risks, and next move.
   - If the reel is purely entertainment or low strategic value, write `not applicable` in the strategic section.
7. Write the durable note with `scripts/write_reel_note.py`.
8. Validate that the note exists and preview the first lines before reporting back.

## Note Requirements

Every reel note must include:

- YAML frontmatter for search: source URL, author, shortcode, dates, tools, skills, tags.
- `## Краткое содержание` with 3-7 bullets.
- `## Суть` with the main claim in plain Russian.
- `## Полезные ссылки` with confirmed official links and source links.
- `## Упомянутые инструменты и skills`.
- `## Главные инсайты`.
- `## Готовые решения / как применить`.
- `## Оценка применимости для Антона` when useful.
- `## Strategic Board analysis` as the only analysis section for business/product implications, strategic fit, risks, and next moves; write `not applicable` only for low-value/non-business reels.
- `## Транскрипт` with timestamps.
- `## Raw caption / metadata` for original caption and counts.

Read `references/reel-note-schema.md` before changing the note format or creating rollups.

## Commands

Create a note from an already prepared transcript:

```bash
python3 "/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/instagram-reel-analyzer/scripts/write_reel_note.py" \
  --source-url "https://www.instagram.com/reel/SHORTCODE/" \
  --shortcode "SHORTCODE" \
  --author-username "creator" \
  --title "Human title" \
  --published-at "2026-06-06" \
  --transcript-file "/private/tmp/reel_transcript.txt" \
  --summary "One useful summary bullet" \
  --insight "One strong insight" \
  --solution "One reusable implementation idea" \
  --strategic-analysis "Strategic Board: recommended next move..." \
  --tool "Claude" \
  --link "Claude=https://claude.ai/"
```

If the transcript is short, pass repeated `--transcript-line` values instead of a file.

## Instagram Extraction Hints

Public Instagram pages often expose useful metadata even when the visible UI is blocked:

- `meta name="description"` can contain caption, author, likes, comments, and post date.
- `meta property="og:url"` can reveal the canonical author/reel URL.
- `al:ios:url` can reveal `instagram://media?id=...`.
- JS Relay bundles may expose GraphQL operation ids, but these change. Use this only as a fallback.

For a URL like `https://www.instagram.com/reel/DZQZOVhgNvK/`, the shortcode is `DZQZOVhgNvK`.

When a GraphQL route is needed and current page tokens are available, a useful current pattern is:

- Fetch the reel page with cookies.
- Extract `csrf_token`, `LSD`, and `jazoest`.
- Find the current `PolarisPostRootQuery_instagramRelayOperation` id in loaded JS.
- Query `https://www.instagram.com/graphql/query` with variables `{"shortcode":"..."}`.

Treat this as opportunistic, not guaranteed. If it fails, fall back to the visible page metadata or ask for the downloaded video.

## Quality Rules

- Do not save only a raw transcript; always add summary, links, insights, and reusable actions.
- Keep search keywords explicit: tools, skills, business domain, content format, funnel type.
- If a link is not present in Instagram metadata or public search, say `not found` rather than guessing.
- Save one note per reel. Use rollups only after notes exist.
- Keep temporary files out of the vault unless the user asks for raw dumps.

## Resources

- `scripts/write_reel_note.py`: create the normalized Markdown note.
- `references/reel-note-schema.md`: field and section standard for future search.
