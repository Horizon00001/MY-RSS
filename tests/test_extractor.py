"""Tests for src modules."""

import pytest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.feed_parser import FeedParser
from src.state_manager import StateManager
from src.fetcher import Fetcher
from src.summarizer import Summarizer
from src.models import RSSEntry, RSSResponse


BEIJING_TZ = timezone(timedelta(hours=8))


class TestFeedParser:
    @pytest.fixture
    def parser(self):
        return FeedParser()

    def test_get_entry_date_with_updated(self, parser):
        entry = SimpleNamespace(updated="2026-04-19T10:00:00Z")
        result = parser.get_entry_date(entry)
        assert result is not None
        assert result.tzinfo is not None

    def test_get_entry_date_with_published(self, parser):
        entry = SimpleNamespace(published="2026-04-19T10:00:00Z")
        result = parser.get_entry_date(entry)
        assert result is not None
        assert result.tzinfo is not None

    def test_get_entry_date_missing_all_fields(self, parser):
        entry = MagicMock(spec=[])
        result = parser.get_entry_date(entry)
        assert result is None

    def test_filter_by_date_within_range(self, parser):
        now = datetime.now(BEIJING_TZ)
        entry = SimpleNamespace(updated=(now - timedelta(hours=1)).isoformat())
        result = parser.filter_by_date([entry], days=7)
        assert len(result) == 1

    def test_filter_by_date_out_of_range(self, parser):
        entry = SimpleNamespace(updated="2020-01-01T00:00:00Z")
        result = parser.filter_by_date([entry], days=7)
        assert len(result) == 0

    def test_parse_entry(self, parser):
        entry = MagicMock()
        entry.get.side_effect = lambda k, default=None: {
            "title": "Test Title",
            "link": "https://example.com/article",
            "summary": "Test summary",
            "content": "Test content",
        }.get(k, default)
        result = parser.parse_entry(entry)
        assert result["title"] == "Test Title"
        assert result["link"] == "https://example.com/article"


class TestStateManager:
    def test_last_fetch_none_when_empty(self, tmp_path):
        state_file = tmp_path / "state.json"
        manager = StateManager(state_file)
        assert manager.last_fetch is None

    def test_update_last_fetch(self, tmp_path):
        state_file = tmp_path / "state.json"
        manager = StateManager(state_file)
        result = manager.update_last_fetch()
        assert "(北京时间)" in result
        assert manager.last_fetch is not None

    def test_reset(self, tmp_path):
        state_file = tmp_path / "state.json"
        manager = StateManager(state_file)
        manager.update_last_fetch()
        manager.reset()
        assert manager.last_fetch is None


class TestModels:
    def test_rss_entry_defaults(self):
        entry = RSSEntry()
        assert entry.title == ""
        assert entry.ai_summary == ""

    def test_rss_response(self):
        entry = RSSEntry(title="Test", link="https://example.com")
        response = RSSResponse(total=1, entries=[entry])
        assert response.total == 1
        assert len(response.entries) == 1
        assert response.incremental is False

    def test_rss_entry_to_dict(self):
        entry = RSSEntry(title="Test", link="https://example.com", ai_summary="Summary")
        d = entry.to_dict()
        assert d["title"] == "Test"
        assert d["ai_summary"] == "Summary"


class TestSummarizer:
    def test_summarizer_init_no_key(self):
        with pytest.raises(ValueError):
            Summarizer(api_key="your_api_key_here")
