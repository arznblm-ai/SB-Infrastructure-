---
name: instagram-reel-analyzer
description: Analyze Instagram Reels from reel URLs or downloaded reel videos, extract metadata and transcript, find mentioned tools/links, run a post-analysis with Strategic Board when business/product relevance exists, and save a searchable Markdown note with summary, insights, reusable solutions, skills, source links, and strategic implications. Use when the user sends an Instagram reel link, asks to разбери/проанализируй рилс из инстаграма, wants reel transcripts saved for future search, asks for strategic/business implications of a reel, or asks to build an insight library from Instagram reels.
---

# Instagram Reel Analyzer

Use this skill to turn Instagram Reels (and other external resources) into ONE
durable Second Brain note. This pipeline is **unified with Link Inbox**: a reel
saved via the Telegram bot and a reel analyzed here produce the SAME note, in the
SAME place, in the SAME format.

Canonical storage (single source of truth):

- Note (one per resource): `/Users/anton/AI AGENT FOLDER/Second Brain/transcripts/external resources/`
- Index: `/Users/anton/AI AGENT FOLDER/Second Brain/transcripts/external resources/index.md`
- Source card (URL / status / Telegram message): `/Users/anton/AI AGENT FOLDER/Second Brain/resources/link-inbox/links/`
- Downloaded media / run artifacts: `/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/UGC Downloader/runs/`

Shared note builder (used by both the bot and this skill):
`/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Link Inbox/Scripts/external_resource_note.py`

> The legacy `resources/instagram-reels/` tree is archived under `_archive/instagram-reels-merged-2026-06-28/`. Do not write there.

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
7. Produce the unified note (do NOT use the deprecated `scripts/write_reel_note.py`):
   - If the reel was already saved via the Telegram bot, a `pending` note already exists in `transcripts/external resources/` — find it via `index.md` and skip to enrich.
   - Otherwise, after transcription, create the auto note:
     `external_resource_note.py --path <transcript-file> --rebuild-auto --source-url <url>`
   - Then enrich it (this is the LLM tier — you, the agent, fill the smart sections, verify links, and flip `enrichment: done`):
     `external_resource_note.py --path <note> --summary "..." --essence "..." --insight "..." --solution "..." --link "Label=https://..." --tool "Name" --anton-relevance "..." --strategic "..."`
8. Rebuild the index: `python3 "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Link Inbox/Scripts/build_external_resources_index.py"`.
9. Validate that the note exists and preview the first lines before reporting back.

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

Build the auto note from a transcript file (creates frontmatter + transcript + auto-extracted links/tools, smart sections marked `pending`):

```bash
python3 "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Link Inbox/Scripts/external_resource_note.py" \
  --path "/Users/anton/AI AGENT FOLDER/Second Brain/transcripts/external resources/<note>.md" \
  --rebuild-auto \
  --source-url "https://www.instagram.com/reel/SHORTCODE/"
```

Enrich the note (LLM tier — fills the smart sections, verifies links, flips `enrichment: done`):

```bash
python3 "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Link Inbox/Scripts/external_resource_note.py" \
  --path "/Users/anton/AI AGENT FOLDER/Second Brain/transcripts/external resources/<note>.md" \
  --summary "One useful summary bullet" \
  --essence "Main claim in plain Russian" \
  --insight "One strong insight" \
  --solution "One reusable implementation idea" \
  --anton-relevance "Why this matters for Anton" \
  --strategic "Strategic Board: recommended next move..." \
  --tool "Claude" \
  --link "Claude=https://claude.ai/"
```

Repeatable flags (`--summary`, `--insight`, `--solution`, `--link`, `--tool`, `--strategic`) can be passed multiple times. Only the sections you pass are overwritten; the transcript is preserved.

> `scripts/write_reel_note.py` is DEPRECATED (kept as a fallback that now writes into the canonical folder). Prefer the unified builder above.

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

- `infrastructure/Link Inbox/Scripts/external_resource_note.py`: the shared, canonical note builder (auto + enrich). Use this.
- `references/reel-note-schema.md`: field and section standard (now the unified external-resource schema).
- `scripts/write_reel_note.py`: DEPRECATED fallback; redirects to the canonical folder but prefer the builder above.
