"""Pydantic models for RSS data."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class RSSEntry(BaseModel):
    """Single RSS entry."""

    title: str = ""
    link: str = ""
    summary: str = ""
    date: Optional[str] = None
    content: str = ""
    ai_summary: str = ""
    is_read: bool = False

    def to_dict(self) -> dict:
        return self.model_dump()


class RSSResponse(BaseModel):
    """API response for RSS entries."""

    total: int
    entries: list[RSSEntry]
    incremental: bool = False
    last_fetch: Optional[str] = None


class FeedInfo(BaseModel):
    """Information about a feed source."""

    url: str
    title: str = ""
    description: str = ""
    entry_count: int = 0
