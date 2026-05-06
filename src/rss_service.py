"""Service helpers for RSS fetching, storage, and summarization."""

import asyncio
import logging
from datetime import datetime, timedelta

from .article_identity import compute_article_id, normalize_article_link
from .compat import api_attr
from .config import settings
from .database import batch_store_articles, batch_update_summaries, list_articles_missing_summary
from .dependencies import get_feed_parser, get_fetcher, get_summarizer
from .feed_parser import FeedParser
from .formatting import BEIJING_TZ, entry_to_article_row

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
            "link": article.get("link", ""),
            "summary": article.get("summary", ""),
            "content": article.get("content", ""),
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
