"""Tests for src/feed_parser.py."""

from datetime import datetime, timedelta, timezone

from src.feed_parser import BEIJING_TZ, FeedParser


class TestStripHtml:
    def test_removes_simple_tags(self):
        result = FeedParser.strip_html("<p>Hello World</p>")
        assert result == "Hello World"

    def test_removes_nested_tags(self):
        result = FeedParser.strip_html("<div><p>Hello <b>World</b></p></div>")
        assert result == "Hello World"

    def test_normalizes_whitespace(self):
        result = FeedParser.strip_html("<p>  Hello   World  </p>")
        assert "Hello" in result

    def test_preserves_text_without_tags(self):
        result = FeedParser.strip_html("Plain text")
        assert result == "Plain text"

    def test_handles_empty_string(self):
        result = FeedParser.strip_html("")
        assert result == ""

    def test_handles_none(self):
        result = FeedParser.strip_html(None)
        assert result == ""


class TestParseEntry:
    def test_parse_entry_strips_html(self):
        parser = FeedParser()
        entry = {
            "title": "<b>Breaking News</b>",
            "link": "https://example.com",
            "summary": "<p>A summary with <a href='#'>link</a></p>",
            "content": "<div>Full content here</div>",
        }
        result = parser.parse_entry(entry)
        assert result["title"] == "Breaking News"
        assert result["summary"] == "A summary with link"
        assert result["content"] == "Full content here"


class TestFilterByDate:
    def test_filters_old_entries(self):
        parser = FeedParser()
        now = datetime(2026, 4, 24, 12, 0, tzinfo=BEIJING_TZ)
        recent = type("E", (), {
            "updated": "2026-04-24T10:00:00+08:00",
            "published": None,
            "date": None,
            "pubDate": None,
        })()
        old = type("E", (), {
            "updated": "2026-04-10T10:00:00+08:00",
            "published": None,
            "date": None,
            "pubDate": None,
        })()
        result = parser.filter_by_date([recent, old], days=2, now=now)
        assert len(result) == 1
        assert result[0] is recent


class TestGetEntryDate:
    def test_returns_beijing_tz(self):
        parser = FeedParser()
        entry = type("E", (), {
            "updated": "2026-04-24T10:00:00+00:00",
            "published": None,
            "date": None,
            "pubDate": None,
        })()
        dt = parser.get_entry_date(entry)
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt.utcoffset() == timedelta(hours=8)
