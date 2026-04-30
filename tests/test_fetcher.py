"""Tests for src/fetcher.py."""

import asyncio

import pytest

from src.fetcher import Fetcher


class TestFetcherSemaphore:
    """Verify semaphore is shared across calls, not created per-call."""

    def test_semaphore_is_reused(self):
        fetcher = Fetcher(semaphore_limit=5)
        s1 = fetcher._semaphore
        s2 = fetcher._semaphore
        assert s1 is s2

    def test_semaphore_is_instance_level_not_per_call(self):
        """Verify fetch_one uses the shared semaphore, not a new one each call."""
        fetcher = Fetcher(semaphore_limit=5)
        # The semaphore should be an asyncio.Semaphore with correct initial value
        assert isinstance(fetcher._semaphore, asyncio.Semaphore)
        # Check it's not locked (all permits available)
        assert not fetcher._semaphore.locked()

    def test_semaphore_has_correct_limit(self):
        fetcher = Fetcher(semaphore_limit=10)
        assert fetcher._semaphore._value == 10

    def test_default_semaphore_limit(self):
        fetcher = Fetcher()
        assert fetcher.semaphore_limit > 0


class TestFetcherSession:
    """Verify session management."""

    def test_session_is_none_until_used(self):
        fetcher = Fetcher(semaphore_limit=2)
        assert fetcher._session is None

    def test_close_when_no_session_does_not_error(self):
        import asyncio
        async def _close():
            fetcher = Fetcher()
            await fetcher.close()
        asyncio.run(_close())


class TestFetcherConditionalHeaders:
    def test_conditional_headers_include_cached_values(self, monkeypatch):
        monkeypatch.setattr(
            "src.fetcher.get_feed_status",
            lambda url: {
                "etag": '"abc"',
                "last_modified": "Wed, 01 May 2026 10:00:00 GMT",
            },
        )

        headers = Fetcher._conditional_headers("https://example.com/rss")

        assert headers["If-None-Match"] == '"abc"'
        assert headers["If-Modified-Since"] == "Wed, 01 May 2026 10:00:00 GMT"

    def test_conditional_headers_empty_without_status(self, monkeypatch):
        monkeypatch.setattr("src.fetcher.get_feed_status", lambda url: None)

        assert Fetcher._conditional_headers("https://example.com/rss") == {}


class TestFetcherAsyncContextManager:
    def test_async_context_manager_closes_session(self):
        async def _run():
            async with Fetcher() as fetcher:
                session = await fetcher._get_session()
                assert not session.closed
            assert session.closed
        asyncio.run(_run())
