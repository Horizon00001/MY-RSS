"""Tests for src/api.py."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from src.api import app


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
        def _make_entry_getter():
            data = {"title": "Test", "link": "https://example.com", "summary": "Summary", "content": "Content"}
            return lambda k, default=None: data.get(k, default)

        mock_entry = MagicMock()
        mock_entry.get.side_effect = _make_entry_getter()

        async def entry_generator():
            yield mock_entry

        def mock_fetch_all(urls):
            return entry_generator()

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.fetch_all = mock_fetch_all

        with patch("src.api.get_fetcher", return_value=mock_fetcher_instance):
            with patch("src.api.get_feed_parser", return_value=mock_feed_parser):
                with patch("src.api.get_state_manager", return_value=mock_state_manager):
                    response = client.get("/rss/entries?use_ai=false")
                    assert response.status_code == 200
                    data = response.json()
                    assert "entries" in data
                    assert "total" in data

    def test_get_entries_with_limit(self, client, mock_fetcher, mock_feed_parser, mock_state_manager):
        """Test /rss/entries with limit parameter."""
        from datetime import datetime, timedelta, timezone

        def _make_entry_getter():
            data = {"title": "Test", "link": "https://example.com", "summary": "Summary", "content": "Content"}
            return lambda k, default=None: data.get(k, default)

        mock_entry = MagicMock()
        mock_entry.get.side_effect = _make_entry_getter()

        BEIJING_TZ = timezone(timedelta(hours=8))

        async def entry_generator():
            for _ in range(5):
                yield mock_entry

        def mock_fetch_all(urls):
            return entry_generator()

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.fetch_all = mock_fetch_all

        with patch("src.api.get_fetcher", return_value=mock_fetcher_instance):
            with patch("src.api.get_feed_parser", return_value=mock_feed_parser):
                with patch("src.api.get_state_manager", return_value=mock_state_manager):
                    response = client.get("/rss/entries?limit=2&use_ai=false")
                    assert response.status_code == 200
                    data = response.json()
                    assert data["total"] <= 2

    def test_get_entries_error_handling(self, client):
        """Test error handling when fetching fails - returns empty results gracefully."""
        async def error_generator():
            raise Exception("Fetch failed")
            yield

        def mock_fetch_all(urls):
            return error_generator()

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.fetch_all = mock_fetch_all
        mock_feed_parser_instance = MagicMock()
        mock_state_manager_instance = MagicMock()

        with patch("src.api.get_fetcher", return_value=mock_fetcher_instance):
            with patch("src.api.get_feed_parser", return_value=mock_feed_parser_instance):
                with patch("src.api.get_state_manager", return_value=mock_state_manager_instance):
                    response = client.get("/rss/entries?use_ai=false")
                    assert response.status_code == 200
                    data = response.json()
                    assert data["total"] == 0
                    assert data["entries"] == []


class TestRSSState:
    def test_get_state(self, client, mock_state_manager):
        """Test /rss/state endpoint."""
        with patch("src.api.get_state_manager", return_value=mock_state_manager):
            response = client.get("/rss/state")
            assert response.status_code == 200
            assert "last_fetch" in response.json()
            assert "state_file" in response.json()

    def test_reset_state(self, client, mock_state_manager):
        """Test /rss/state/reset endpoint."""
        with patch("src.api.get_state_manager", return_value=mock_state_manager):
            response = client.post("/rss/state/reset")
            assert response.status_code == 200
            mock_state_manager.reset.assert_called_once()


class TestRSSStream:
    def test_stream_requires_ai(self, client):
        """Test that streaming requires AI summarizer."""
        with patch("src.api.get_summarizer", return_value=None):
            response = client.get("/rss/stream?use_ai=true")
            assert response.status_code == 400
