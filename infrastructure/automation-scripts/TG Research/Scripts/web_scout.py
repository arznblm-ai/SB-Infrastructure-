#!/usr/bin/env python3
"""Web scout for Research Department."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from research_common import (
    build_markdown_note,
    build_note_path,
    build_source_index,
    canonicalize_url,
    configure_logging,
    detect_language_code,
    get_output_dir,
    load_last_run_state,
    load_runtime_config,
    normalize_queries,
    parse_datetime,
    save_last_run_state,
    tldr_from_text,
    title_from_text,
    topic_from_text,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect fresh web links into the vault.")
    parser.add_argument("--config", help="Path to config.json", default=None)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def parse_result_date(value: str | None) -> datetime | None:
    if not value:
        return None

    parsed = parse_datetime(value)
    if parsed is not None:
        return parsed

    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def freshness_to_timelimit(days: int) -> str | None:
    if days <= 1:
        return "d"
    if days <= 7:
        return "w"
    if days <= 31:
        return "m"
    return None


def run(config_path: str | None = None, *, verbose: bool = False) -> dict[str, Any]:
    config = load_runtime_config(config_path)
    logger = configure_logging(config["log_file"], verbose=verbose).getChild("web")

    try:
        from duckduckgo_search import DDGS
    except ImportError as exc:
        raise RuntimeError(
            "duckduckgo-search is not installed. Run `python3 Scripts/setup.py` first."
        ) from exc

    web_config = config.get("web", {})
    queries = normalize_queries(web_config.get("queries", []))
    if not queries:
        logger.info("No web queries configured. Nothing to do.")
        return {"component": "web", "queries": 0, "saved": 0, "skipped": 0}

    source_index = build_source_index(get_output_dir(config))
    output_dir = get_output_dir(config)
    state = load_last_run_state(config)
    web_state = state.setdefault("web", {})
    last_run_at = parse_datetime(web_state.get("last_run_at"))

    freshness_days = int(web_config.get("freshness_days", 2) or 2)
    cutoff = datetime.now(timezone.utc) - timedelta(days=freshness_days)
    if last_run_at is not None and last_run_at > cutoff:
        cutoff = last_run_at

    timelimit = freshness_to_timelimit(freshness_days)
    max_results = int(web_config.get("max_results_per_query", 10) or 10)
    language_filter = set(web_config.get("language_filter", []))
    region = web_config.get("region", "wt-wt")
    safesearch = web_config.get("safesearch", "off")

    total_saved = 0
    total_skipped = 0
    total_seen = 0
    per_query: list[dict[str, Any]] = []

    with DDGS() as ddgs:
        for query_item in queries:
            query_text = str(query_item.get("query", "")).strip()
            if not query_text:
                continue

            logger.info(f"Searching web for: {query_text}")
            try:
                results = list(
                    ddgs.text(
                        query_text,
                        max_results=max_results,
                        region=region,
                        safesearch=safesearch,
                        timelimit=timelimit,
                    )
                )
            except Exception as exc:  # pragma: no cover - network/runtime guard
                logger.exception(f"Search failed for query `{query_text}`: {exc}")
                per_query.append(
                    {
                        "query": query_text,
                        "results": 0,
                        "saved": 0,
                        "skipped": 0,
                        "error": str(exc),
                    }
                )
                continue

            query_saved = 0
            query_skipped = 0

            for result in results:
                total_seen += 1
                url = canonicalize_url(str(result.get("href", "")).strip())
                if not url or url in source_index:
                    total_skipped += 1
                    query_skipped += 1
                    continue

                result_date = parse_result_date(result.get("date"))
                if result_date is not None and result_date < cutoff:
                    total_skipped += 1
                    query_skipped += 1
                    continue

                title = str(result.get("title", "")).strip() or title_from_text(query_text, max_words=8)
                snippet = str(result.get("body", "")).strip()
                language = detect_language_code(f"{title}\n{snippet}")
                if language_filter and language not in language_filter:
                    total_skipped += 1
                    query_skipped += 1
                    continue

                topic = str(query_item.get("topic", "")).strip() or topic_from_text(
                    f"{query_text}\n{title}\n{snippet}"
                )
                note_date = (
                    result_date.date().isoformat()
                    if result_date is not None
                    else datetime.now(timezone.utc).date().isoformat()
                )
                tldr = tldr_from_text(snippet or title)
                content_lines = [
                    f"Поисковый запрос: `{query_text}`",
                    "",
                    snippet or "DuckDuckGo не вернул сниппет, сохранена только ссылка и заголовок.",
                ]
                if result_date is not None:
                    content_lines.extend(["", f"Опубликовано: {result_date.date().isoformat()}"])

                note = build_markdown_note(
                    source_tag="web",
                    topic=topic,
                    note_date=note_date,
                    source_url=url,
                    source_type="web-search-result",
                    title=title_from_text(title, max_words=15, max_chars=110),
                    tldr=tldr,
                    content_markdown="\n".join(content_lines),
                    source_lines=[
                        f"[Открыть материал]({url})",
                        f"Поисковый запрос: `{query_text}`",
                        "Источник выдачи: `duckduckgo-search`",
                    ],
                    extra_fields={
                        "source_query": query_text,
                    },
                )

                note_path = build_note_path(
                    output_dir=output_dir,
                    description=title,
                    date_str=note_date,
                    unique_hint=topic,
                )
                note_path.write_text(note, encoding="utf-8")
                source_index.add(url)
                total_saved += 1
                query_saved += 1
                logger.info(f"  + {note_path.name}")

            per_query.append(
                {
                    "query": query_text,
                    "results": len(results),
                    "saved": query_saved,
                    "skipped": query_skipped,
                }
            )

    web_state["last_run_at"] = datetime.now(timezone.utc).isoformat()
    save_last_run_state(config, state)

    logger.info(f"Web scout finished: seen={total_seen}, saved={total_saved}, skipped={total_skipped}")
    return {
        "component": "web",
        "queries": len(per_query),
        "seen": total_seen,
        "saved": total_saved,
        "skipped": total_skipped,
        "details": per_query,
    }


def main() -> int:
    args = parse_args()
    run(args.config, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
