"""Service helpers for RSS fetching, storage, and summarization."""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from .article_identity import compute_article_id, normalize_article_link
from .compat import api_attr
from .config import settings
from .database import batch_store_articles, batch_update_summaries, list_articles_missing_summary
from .dependencies import get_feed_parser, get_fetcher, get_summarizer
from .feed_parser import FeedParser
from .fetcher import Fetcher
from .formatting import BEIJING_TZ, entry_to_article_row, format_entry
from .models import RSSResponse
from .state_manager import StateManager
from .summarizer import Summarizer

logger = logging.getLogger(__name__)


def save_entries_to_db(entries: list, feed_parser: FeedParser) -> None:
    if not entries:
        return
    try:
        batch_store_articles([entry_to_article_row(entry, feed_parser) for entry in entries])
    except Exception as e:
        logger.warning("Failed to batch store articles to DB: %s", e)


def save_entry_to_db(entry: dict, feed_parser: FeedParser):
    """Save an entry to the database."""
    save_entries_to_db([entry], feed_parser)


def _update_db_sync(entries: list):
    """Update DB with AI summaries using batch update."""
    summaries = []
    for entry in entries:
        article_id = compute_article_id(entry.get("link", ""))
        ai_summary = entry.get("ai_summary", "")
        if ai_summary and article_id:
            link = entry.get("link", "")
            summaries.append({
                "id": article_id,
                "link": link,
                "normalized_link": normalize_article_link(link),
                "ai_summary": ai_summary,
            })
    if summaries:
        try:
            batch_update_summaries(summaries)
        except Exception as e:
            logger.warning("Failed to batch update summaries: %s", e)


async def build_rss_entries_response(
    *,
    days: int | None,
    limit: int | None,
    offset: int,
    incremental: bool,
    use_ai: bool,
    state_manager: StateManager,
    feed_parser: FeedParser,
    fetcher: Fetcher,
    summarizer: Optional[Summarizer],
) -> RSSResponse:
    last_fetch = state_manager.last_fetch if incremental else None
    is_incremental = incremental and last_fetch is not None

    if days is None:
        days = settings.default_days
    cutoff = datetime.now(BEIJING_TZ) - timedelta(days=days)

    urls = list(settings.rss_feeds.values())
    target_count = offset + limit if limit else None
    results: list = []
    seen_links: set = set()
    entries_to_store: list = []

    if summarizer and use_ai:
        results, entries_to_store = await _fetch_entries_with_ai(
            urls=urls,
            target_count=target_count,
            last_fetch=last_fetch,
            cutoff=cutoff,
            feed_parser=feed_parser,
            fetcher=fetcher,
            summarizer=summarizer,
            seen_links=seen_links,
        )
    else:
        try:
            async for entry in fetcher.fetch_all(urls):
                if _should_skip_seen(entry, seen_links):
                    continue
                if _is_entry_newer_than_cutoff(entry, feed_parser, last_fetch or cutoff):
                    results.append(entry)
                    entries_to_store.append(entry)
                if limit and len(results) >= limit:
                    break
        except Exception as e:
            logger.warning("Fetch failed in non-AI path: %s", e)

    api_attr("save_entries_to_db", save_entries_to_db)(entries_to_store, feed_parser)

    if limit:
        results = results[offset:offset + limit]
    elif offset:
        results = results[offset:]

    formatted = [format_entry(entry, feed_parser) for entry in results]
    last_fetch_str = state_manager.update_last_fetch()

    return RSSResponse(
        total=len(formatted),
        entries=formatted,
        incremental=is_incremental,
        last_fetch=last_fetch_str if is_incremental else None,
    )


async def iter_rss_stream_events(
    *,
    days: int | None,
    limit: int,
    incremental: bool,
    feed_parser: FeedParser,
    fetcher: Fetcher,
    state_manager: StateManager,
    summarizer: Optional[Summarizer],
):
    last_fetch = state_manager.last_fetch if incremental else None

    if days is None:
        days = settings.default_days
    cutoff = datetime.now(BEIJING_TZ) - timedelta(days=days)

    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    yielded = 0

    async def fetcher_worker():
        async for entry in fetcher.fetch_all(list(settings.rss_feeds.values())):
            if _is_entry_newer_than_cutoff(entry, feed_parser, last_fetch or cutoff):
                await queue.put(entry)
        await queue.put(None)

    fetcher_task = asyncio.create_task(fetcher_worker())
    batch: list = []

    try:
        while yielded < limit:
            entry = await queue.get()
            if entry is None:
                if batch and summarizer:
                    summarized = await summarizer.summarize_batch(batch)
                    for summarized_entry in summarized:
                        if yielded >= limit:
                            break
                        yield _stream_event(summarized_entry, feed_parser)
                        yielded += 1
                break
            batch.append(entry)
            if len(batch) >= 3:
                to_emit = batch
                batch = []
                if summarizer:
                    to_emit = await summarizer.summarize_batch(to_emit)
                for item in to_emit:
                    if yielded >= limit:
                        break
                    yield _stream_event(item, feed_parser)
                    yielded += 1
    finally:
        fetcher_task.cancel()
        try:
            await fetcher_task
        except asyncio.CancelledError:
            pass


async def _fetch_entries_with_ai(
    *,
    urls: list[str],
    target_count: int | None,
    last_fetch,
    cutoff: datetime,
    feed_parser: FeedParser,
    fetcher: Fetcher,
    summarizer: Summarizer,
    seen_links: set,
) -> tuple[list, list]:
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    results: list = []
    entries_to_store: list = []
    stop_fetching = asyncio.Event()
    queued_count = 0

    async def fetcher_worker():
        nonlocal queued_count
        async for entry in fetcher.fetch_all(urls):
            if stop_fetching.is_set():
                break
            if _should_skip_seen(entry, seen_links):
                continue
            if _is_entry_newer_than_cutoff(entry, feed_parser, last_fetch or cutoff):
                await queue.put(entry)
                queued_count += 1
                entries_to_store.append(entry)
                if target_count and queued_count >= target_count:
                    stop_fetching.set()
        await queue.put(None)

    async def summarizer_worker():
        batch: list = []
        batch_size = min(10, target_count) if target_count else 10
        while True:
            entry = await queue.get()
            if entry is None:
                if batch:
                    summarized = await summarizer.summarize_batch(batch)
                    results.extend(summarized)
                    await asyncio.to_thread(api_attr("_update_db_sync", _update_db_sync), summarized)
                break
            batch.append(entry)
            if len(batch) >= batch_size:
                summarized = await summarizer.summarize_batch(batch)
                results.extend(summarized)
                await asyncio.to_thread(api_attr("_update_db_sync", _update_db_sync), summarized)
                batch = []
                if target_count and len(results) >= target_count:
                    stop_fetching.set()
                    break

    fetch_task = asyncio.create_task(fetcher_worker())
    summarize_task = asyncio.create_task(summarizer_worker())
    await summarize_task
    if fetch_task.done():
        await fetch_task
    else:
        stop_fetching.set()
        fetch_task.cancel()
        try:
            await fetch_task
        except asyncio.CancelledError:
            pass
    return results, entries_to_store


def _should_skip_seen(entry: dict, seen_links: set) -> bool:
    link = entry.get("link", "")
    norm = normalize_article_link(link)
    if not link or norm in seen_links:
        return True
    seen_links.add(norm)
    return False


def _is_entry_newer_than_cutoff(entry: dict, feed_parser: FeedParser, cutoff: datetime) -> bool:
    entry_date = feed_parser.get_entry_date(entry)
    return bool(entry_date and entry_date > cutoff)


def _stream_event(entry: dict, feed_parser: FeedParser) -> str:
    payload = json.dumps(format_entry(entry, feed_parser).model_dump(), ensure_ascii=False)
    return f"data: {payload}\n\n"


async def summarize_missing_articles(limit: int = 5) -> int:
    """Summarize stored articles that do not have AI summaries yet."""
    summarizer = get_summarizer()
    if not summarizer:
        return 0
    articles = list_articles_missing_summary(limit=limit)
    if not articles:
        return 0
    entries = [
        {
            "title": article.get("title", ""),
            "link": article.get("link", ""),
            "summary": article.get("summary", ""),
            "content": article.get("content", ""),
            "source": article.get("source", ""),
            "source_name": article.get("source_name", ""),
        }
        for article in articles
    ]
    summarized = await summarizer.summarize_batch(entries)
    await asyncio.to_thread(api_attr("_update_db_sync", _update_db_sync), summarized)
    return len(summarized)


async def refresh_rss_entries_once() -> int:
    """Fetch current RSS entries once and save them without AI summarization."""
    feed_parser = get_feed_parser()
    fetcher = get_fetcher()
    cutoff = datetime.now(BEIJING_TZ) - timedelta(days=settings.default_days)
    seen_links = set()
    entries_to_store = []
    async for entry in fetcher.fetch_all(list(settings.rss_feeds.values())):
        link = entry.get("link", "")
        norm = normalize_article_link(link)
        if not link or norm in seen_links:
            continue
        seen_links.add(norm)
        entry_date = feed_parser.get_entry_date(entry)
        if entry_date and entry_date > cutoff:
            entries_to_store.append(entry)
    api_attr("save_entries_to_db", save_entries_to_db)(entries_to_store, feed_parser)
    return len(entries_to_store)
