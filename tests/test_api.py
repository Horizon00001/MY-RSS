"""Tests for src/api.py."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from src.api import app
from src.models import RSSEntry


@pytest.fixture
def client():
    return TestClient(app)


class TestRoot:
    def test_root(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "message" in response.json()


class TestRSSFeeds:
    def test_get_feeds(self, client):
        response = client.get("/rss/feeds")
        assert response.status_code == 200
        assert "feeds" in response.json()
        assert isinstance(response.json()["feeds"], list)


class TestRSSEntries:
    def test_get_entries_no_ai(self, client, mock_fetcher, mock_feed_parser, mock_state_manager):
        """Test /rss/entries without AI summarization."""
        mock_entry = MagicMock()
        mock_entry.get.side_effect = lambda k: {
            "title": "Test",
            "link": "https://example.com",
            "summary": "Summary",
            "content": "Content",
        }.get(k)

        async def entry_generator():
            yield mock_entry

        with patch("src.api.fetcher") as mock_fetcher_module:
            mock_fetcher_module.Fetcher.return_value = mock_fetcher
            mock_fetcher.fetch_all.return_value = entry_generator()

            with patch("src.api.feed_parser", mock_feed_parser):
                with patch("src.api.state_manager", mock_state_manager):
                    mock_feed_parser.get_entry_date.return_value = None

                    response = client.get("/rss/entries?use_ai=false")
                    assert response.status_code == 200
                    data = response.json()
                    assert "entries" in data
                    assert "total" in data

    def test_get_entries_with_limit(self, client, mock_fetcher, mock_feed_parser, mock_state_manager):
        """Test /rss/entries with limit parameter."""
        mock_entry = MagicMock()
        mock_entry.get.side_effect = lambda k: {
            "title": "Test",
            "link": "https://example.com",
            "summary": "Summary",
            "content": "Content",
        }.get(k)

        async def entry_generator():
            for _ in range(5):
                yield mock_entry

        with patch("src.api.fetcher") as mock_fetcher_module:
            mock_fetcher_module.Fetcher.return_value = mock_fetcher
            mock_fetcher.fetch_all.return_value = entry_generator()

            with patch("src.api.feed_parser", mock_feed_parser):
                with patch("src.api.state_manager", mock_state_manager):
                    from datetime import datetime, timedelta, timezone
                    BEIJING_TZ = timezone(timedelta(hours=8))
                    mock_feed_parser.get_entry_date.return_value = datetime.now(BEIJING_TZ)

                    response = client.get("/rss/entries?limit=2&use_ai=false")
                    assert response.status_code == 200
                    data = response.json()
                    assert data["total"] <= 2

    def test_get_entries_error_handling(self, client):
        """Test error handling when fetching fails."""
        async def failing_generator():
            raise Exception("Fetch failed")
            yield

        with patch("src.api.fetcher") as mock_fetcher_module:
            mock_fetcher = MagicMock()
            mock_fetcher.fetch_all.return_value = failing_generator()
            mock_fetcher_module.Fetcher.return_value = mock_fetcher

            with patch("src.api.state_manager") as mock_state:
                mock_state.last_fetch = None
                mock_state.update_last_fetch.return_value = "2026-04-19 10:00:00 (北京时间)"

                with patch("src.api.feed_parser") as mock_parser:
                    mock_parser.get_entry_date.return_value = None

                    response = client.get("/rss/entries?use_ai=false")
                    assert response.status_code == 500


class TestRSSState:
    def test_get_state(self, client, mock_state_manager):
        """Test /rss/state endpoint."""
        with patch("src.api.state_manager", mock_state_manager):
            response = client.get("/rss/state")
            assert response.status_code == 200
            assert "last_fetch" in response.json()
            assert "state_file" in response.json()

    def test_reset_state(self, client, mock_state_manager):
        """Test /rss/state/reset endpoint."""
        with patch("src.api.state_manager", mock_state_manager):
            response = client.post("/rss/state/reset")
            assert response.status_code == 200
            mock_state_manager.reset.assert_called_once()


class TestRSSStream:
    def test_stream_requires_ai(self, client):
        """Test that streaming requires AI summarizer."""
        with patch("src.api.get_summarizer", return_value=None):
            response = client.get("/rss/stream?use_ai=true")
            assert response.status_code == 400
