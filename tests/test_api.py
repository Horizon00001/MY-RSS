"""Tests for src/api.py."""

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from src.api import app, get_feed_parser, get_fetcher, get_state_manager, get_summarizer, normalize_article_link, WSConnectionManager


def test_src_api_preserves_public_imports():
    import src.api as api

    assert api.app is app
    assert api.get_feed_parser is get_feed_parser
    assert api.get_fetcher is get_fetcher
    assert api.get_state_manager is get_state_manager
    assert api.get_summarizer is get_summarizer
    assert api.normalize_article_link is normalize_article_link
    assert api.WSConnectionManager is WSConnectionManager



@pytest.fixture
def client():
    app.dependency_overrides.clear()
    test_client = TestClient(app)
    yield test_client
    test_client.close()
    app.dependency_overrides.clear()


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

    def test_get_feeds_health_includes_cache_and_error_status(self, client):
        with patch("src.api.settings.rss_feeds", {"Example": "https://example.com/rss"}):
            with patch("src.api.get_feed_stats", return_value={}):
                with patch("src.api.list_feed_statuses", return_value={
                    "https://example.com/rss": {
                        "etag": '"abc"',
                        "last_status_code": 304,
                        "last_success_at": "2026-05-01 10:00:00",
                        "last_error_at": None,
                        "last_error": None,
                        "consecutive_failures": 0,
                        "average_fetch_ms": 12.3,
                    }
                }):
                    response = client.get("/rss/feeds/health")

        assert response.status_code == 200
        feed = response.json()["feeds"]["https://example.com/rss"]
        assert feed["last_status_code"] == 304
        assert feed["cache_enabled"] is True
        assert feed["average_fetch_ms"] == 12.3


class TestRefreshEndpoints:
    def test_refresh_endpoint_starts_background_task(self, client):
        with patch("src.api.refresh_rss_entries_once", return_value=0):
            response = client.post("/rss/refresh")

        assert response.status_code == 200
        assert "刷新已开始" in response.json()["message"]

    def test_summarize_missing_endpoint_starts_background_task(self, client):
        with patch("src.api.summarize_missing_articles", return_value=0):
            response = client.post("/rss/summarize-missing?limit=3")

        assert response.status_code == 200
        assert response.json()["limit"] == 3


class TestLocalArticles:
    def test_get_local_articles(self, client):
        """Test /rss/articles reads from local database only."""
        with patch("src.api.list_recent_articles", return_value=[]):
            response = client.get("/rss/articles")
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 0
            assert data["entries"] == []

    def test_get_local_article_by_link(self, client):
        with patch("src.api.get_article_by_link", return_value={
            "title": "Test Article",
            "link": "https://example.com/a",
            "summary": "Summary",
            "content": "Content",
            "ai_summary": "AI summary",
            "published_at": "2026-05-01 10:00:00",
        }):
            response = client.get("/rss/article?link=https%3A%2F%2Fexample.com%2Fa")

        assert response.status_code == 200
        assert response.json()["title"] == "Test Article"


class TestNormalizeArticleLink:
    def test_removes_tracking_params_and_fragment(self):
        link = "https://example.com/news/123/?utm_source=rss&utm_campaign=test&keep=1#comments"

        assert normalize_article_link(link) == "https://example.com/news/123?keep=1"

    def test_normalizes_scheme_host_and_trailing_slash(self):
        link = "HTTP://EXAMPLE.com/news/123/"

        assert normalize_article_link(link) == "https://example.com/news/123"


class TestWSConnectionManager:
    def test_broadcast_sends_concurrently_and_removes_dead_connections(self):
        class FakeConnection:
            def __init__(self, name, delay=0, fail=False):
                self.name = name
                self.delay = delay
                self.fail = fail
                self.started = None
                self.messages = []

            async def send_json(self, message):
                self.started = asyncio.get_running_loop().time()
                if self.delay:
                    await asyncio.sleep(self.delay)
                if self.fail:
                    raise RuntimeError("send failed")
                self.messages.append(message)

        async def _run():
            manager = WSConnectionManager()
            slow = FakeConnection("slow", delay=0.03)
            fast = FakeConnection("fast")
            dead = FakeConnection("dead", fail=True)
            manager.active_connections.update({slow, fast, dead})

            await manager.broadcast({"type": "test"})

            assert slow.messages == [{"type": "test"}]
            assert fast.messages == [{"type": "test"}]
            assert abs(slow.started - fast.started) < 0.02
            assert dead not in manager.active_connections
            assert slow in manager.active_connections
            assert fast in manager.active_connections

        asyncio.run(_run())


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

        app.dependency_overrides[get_fetcher] = lambda: mock_fetcher_instance
        app.dependency_overrides[get_feed_parser] = lambda: mock_feed_parser
        app.dependency_overrides[get_state_manager] = lambda: mock_state_manager

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

        app.dependency_overrides[get_fetcher] = lambda: mock_fetcher_instance
        app.dependency_overrides[get_feed_parser] = lambda: mock_feed_parser
        app.dependency_overrides[get_state_manager] = lambda: mock_state_manager

        response = client.get("/rss/entries?limit=2&use_ai=false")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] <= 2

    def test_get_entries_ai_limit_stops_fetching_early(self, client):
        from datetime import datetime, timedelta, timezone

        emitted = []
        fetch_calls = []

        def make_entry(i):
            entry = MagicMock()
            entry.get.side_effect = lambda k, default=None: {
                "title": f"Title {i}",
                "link": f"https://example.com/{i}",
                "summary": f"Summary {i}",
                "content": f"Content {i}",
            }.get(k, default)
            return entry

        async def entry_generator():
            for i in range(5):
                fetch_calls.append(i)
                await __import__("asyncio").sleep(0)
                yield make_entry(i)

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.fetch_all.return_value = entry_generator()

        mock_feed_parser_instance = MagicMock()
        mock_feed_parser_instance.get_entry_date.return_value = datetime.now(timezone(timedelta(hours=8)))

        mock_state_manager_instance = MagicMock()
        mock_state_manager_instance.last_fetch = None
        mock_state_manager_instance.update_last_fetch.return_value = "2026-05-01 10:00:00 (北京时间)"

        mock_summarizer_instance = MagicMock()

        async def summarize_batch(batch):
            emitted.extend(item.get("link") for item in batch)
            return batch

        mock_summarizer_instance.summarize_batch = AsyncMock(side_effect=summarize_batch)

        app.dependency_overrides[get_fetcher] = lambda: mock_fetcher_instance
        app.dependency_overrides[get_feed_parser] = lambda: mock_feed_parser_instance
        app.dependency_overrides[get_state_manager] = lambda: mock_state_manager_instance
        app.dependency_overrides[get_summarizer] = lambda: mock_summarizer_instance

        with patch("src.api.save_entries_to_db", lambda *args, **kwargs: None), patch("src.api._update_db_sync", lambda *args, **kwargs: None):
            response = client.get("/rss/entries?use_ai=true&limit=2")

        assert response.status_code == 200
        assert response.json()["total"] == 2
        assert len(emitted) == 2
        assert len(fetch_calls) < 5

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

        app.dependency_overrides[get_fetcher] = lambda: mock_fetcher_instance
        app.dependency_overrides[get_feed_parser] = lambda: mock_feed_parser_instance
        app.dependency_overrides[get_state_manager] = lambda: mock_state_manager_instance

        response = client.get("/rss/entries?use_ai=false")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["entries"] == []


class TestRSSState:
    def test_get_state(self, client, mock_state_manager):
        """Test /rss/state endpoint."""
        app.dependency_overrides[get_state_manager] = lambda: mock_state_manager

        response = client.get("/rss/state")

        assert response.status_code == 200
        assert "last_fetch" in response.json()
        assert "state_file" in response.json()

    def test_reset_state(self, client, mock_state_manager):
        """Test /rss/state/reset endpoint."""
        app.dependency_overrides[get_state_manager] = lambda: mock_state_manager

        response = client.post("/rss/state/reset")

        assert response.status_code == 200
        mock_state_manager.reset.assert_called_once()


class TestRSSStream:
    def test_stream_requires_ai(self, client):
        """Test that streaming requires AI summarizer."""
        app.dependency_overrides[get_summarizer] = lambda: None
        app.dependency_overrides[get_fetcher] = lambda: MagicMock()

        response = client.get("/rss/stream?use_ai=true")

        assert response.status_code == 400

    def test_stream_with_ai_returns_sse(self, client):
        from datetime import datetime, timedelta, timezone

        def make_entry(i):
            entry = MagicMock()
            entry.get.side_effect = lambda k, default=None: {
                "title": f"Title {i}",
                "link": f"https://example.com/{i}",
                "summary": f"Summary {i}",
                "content": f"Content {i}",
            }.get(k, default)
            return entry

        async def entry_generator():
            for i in range(4):
                yield make_entry(i)

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.fetch_all.return_value = entry_generator()

        mock_feed_parser_instance = MagicMock()
        mock_feed_parser_instance.get_entry_date.return_value = datetime.now(timezone(timedelta(hours=8)))

        mock_state_manager_instance = MagicMock()
        mock_state_manager_instance.last_fetch = None

        mock_summarizer_instance = MagicMock()

        async def summarize_batch(batch):
            return batch

        mock_summarizer_instance.summarize_batch = AsyncMock(side_effect=summarize_batch)

        app.dependency_overrides[get_fetcher] = lambda: mock_fetcher_instance
        app.dependency_overrides[get_feed_parser] = lambda: mock_feed_parser_instance
        app.dependency_overrides[get_state_manager] = lambda: mock_state_manager_instance
        app.dependency_overrides[get_summarizer] = lambda: mock_summarizer_instance

        response = client.get("/rss/stream?use_ai=true&limit=2")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = response.text
        assert body.count("data: ") == 2
        assert "Title 0" in body
        assert "Title 1" in body
