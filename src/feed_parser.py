"""RSS feed parsing logic."""

import re
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import dateutil.parser


BEIJING_TZ = timezone(timedelta(hours=8))


class FeedParser:
    """Parse RSS/Atom feeds and extract entries."""

    DATE_FIELDS = ["updated", "published", "date", "pubDate"]

    _html_re = re.compile(r"<[^>]+>")

    @classmethod
    def strip_html(cls, text: str) -> str:
        """Remove HTML tags and normalize whitespace."""
        if not text:
            return ""
        return cls._html_re.sub("", text).strip()

    def parse_entry(self, entry: Any) -> dict[str, Any]:
        """Convert feedparser entry to dict format."""
        return {
            "title": self.strip_html(entry.get("title", "")),
            "link": entry.get("link", ""),
            "summary": self.strip_html(entry.get("summary", "")),
            "content": self.strip_html(entry.get("content", "")),
            "updated": getattr(entry, "updated", None),
            "published": getattr(entry, "published", None),
        }

    def get_entry_date(self, entry: Any) -> datetime | None:
        """Extract and parse date from entry."""
        for field in self.DATE_FIELDS:
            if hasattr(entry, field):
                try:
                    value = getattr(entry, field)
                    if value is None:
                        continue
                    parsed = dateutil.parser.parse(value)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    return parsed.astimezone(BEIJING_TZ)
                except Exception:
                    continue
        return None

    def filter_by_date(
        self, entries: list[Any], days: int, now: datetime | None = None
    ) -> list[Any]:
        """Filter entries by date range."""
        if now is None:
            now = datetime.now(BEIJING_TZ)
        cutoff = now - timedelta(days=days)
        return [e for e in entries if self._is_after(e, cutoff)]

    def filter_by_timestamp(
        self, entries: list[Any], last_fetch: datetime | None
    ) -> list[Any]:
        """Filter entries newer than last_fetch."""
        if last_fetch is None:
            return entries
        return [e for e in entries if self._is_after(e, last_fetch)]

    def _is_after(self, entry: Any, cutoff: datetime) -> bool:
        entry_date = self.get_entry_date(entry)
        return entry_date is not None and entry_date > cutoff
