"""Formatting helpers for RSS API responses and database rows."""

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from .article_identity import compute_article_id, normalize_article_link
from .feed_parser import FeedParser
from .models import RSSEntry

logger = logging.getLogger(__name__)
BEIJING_TZ = timezone(timedelta(hours=8))


def format_entry(entry: dict, feed_parser: FeedParser) -> RSSEntry:
    """Convert raw entry dict to RSSEntry model."""
    try:
        entry_obj = SimpleNamespace(**entry) if isinstance(entry, dict) else entry
        entry_date = feed_parser.get_entry_date(entry_obj)
    except Exception as e:
        logger.debug("Failed to parse entry date: %s", e)
        entry_date = None
    return RSSEntry(
        title=entry.get("title", ""),
        link=entry.get("link", ""),
        summary=entry_text(entry.get("summary", "")),
        date=entry_date.strftime("%Y-%m-%d %H:%M:%S (北京时间)") if entry_date else None,
        content=entry_text(entry.get("content", "")),
        ai_summary=entry.get("ai_summary", ""),
        is_read=bool(entry.get("is_read", False)),
    )


def entry_text(value) -> str:
    """Convert feedparser string/list/dict fields into plain text."""
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("value") or value.get("content") or "")
    if isinstance(value, list):
        return "\n".join(entry_text(item) for item in value if item)
    return str(value)


def format_db_article(article: dict) -> RSSEntry:
    """Convert a database article row to the public RSS entry shape."""
    published_at = article.get("published_at")
    if isinstance(published_at, datetime):
        date = published_at.strftime("%Y-%m-%d %H:%M:%S (北京时间)")
    else:
        date = str(published_at) if published_at else None

    return RSSEntry(
        title=article.get("title", ""),
        link=article.get("link", ""),
        summary=article.get("summary", ""),
        date=date,
        content=article.get("content", ""),
        ai_summary=article.get("ai_summary", ""),
        is_read=bool(article.get("is_read", False)),
    )


def entry_to_article_row(entry: dict, feed_parser: FeedParser) -> dict:
    try:
        entry_obj = SimpleNamespace(**entry) if isinstance(entry, dict) else entry
        entry_date = feed_parser.get_entry_date(entry_obj)
    except Exception as e:
        logger.debug("Failed to parse entry for DB: %s", e)
        entry_date = None
    link = entry.get("link", "")
    return {
        "article_id": compute_article_id(link),
        "title": entry.get("title", ""),
        "link": link,
        "normalized_link": normalize_article_link(link),
        "summary": entry_text(entry.get("summary", "")),
        "content": entry_text(entry.get("content", "")),
        "source": entry.get("source", ""),
        "source_name": entry.get("feed_title", ""),
        "published_at": entry_date,
        "tags": [],
    }
