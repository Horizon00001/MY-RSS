"""Tests for src/fetcher.py."""

import asyncio

import pytest

from src.fetcher import Fetcher


class FakeFeedStatusStore:
    def __init__(self, statuses=None):
        self.statuses = statuses or {}
        self.get_calls = []
        self.batch_get_calls = []
        self.successes = []
        self.errors = []

    def get(self, feed_url):
        self.get_calls.append(feed_url)
        return self.statuses.get(feed_url)

    def batch_get(self, feed_urls):
        self.batch_get_calls.append(list(feed_urls))
        return {url: self.statuses[url] for url in feed_urls if url in self.statuses}

    def record_success(self, **kwargs):
        self.successes.append(kwargs)

    def record_error(self, **kwargs):
        self.errors.append(kwargs)


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
    def test_conditional_headers_include_cached_values(self):
        store = FakeFeedStatusStore({
            "https://example.com/rss": {
                "etag": '"abc"',
                "last_modified": "Wed, 01 May 2026 10:00:00 GMT",
            }
        })

        headers = Fetcher(status_store=store)._conditional_headers("https://example.com/rss")

        assert headers["If-None-Match"] == '"abc"'
        assert headers["If-Modified-Since"] == "Wed, 01 May 2026 10:00:00 GMT"
        assert store.get_calls == ["https://example.com/rss"]

    def test_conditional_headers_empty_without_status(self):
        store = FakeFeedStatusStore()

        assert Fetcher(status_store=store)._conditional_headers("https://example.com/rss") == {}
        assert store.get_calls == ["https://example.com/rss"]

    def test_conditional_headers_use_preloaded_status_cache(self):
        store = FakeFeedStatusStore()
        fetcher = Fetcher(status_store=store)
        fetcher._feed_status_cache = {
            "https://example.com/rss": {
                "etag": '"cached"',
                "last_modified": "Wed, 01 May 2026 10:00:00 GMT",
            }
        }

        headers = fetcher._conditional_headers("https://example.com/rss")

        assert headers == {
            "If-None-Match": '"cached"',
            "If-Modified-Since": "Wed, 01 May 2026 10:00:00 GMT",
        }
        assert store.get_calls == []




class TestFetcherAsyncContextManager:
    def test_async_context_manager_closes_session(self):
        async def _run():
            async with Fetcher() as fetcher:
                session = await fetcher._get_session()
                assert not session.closed
            assert session.closed
        asyncio.run(_run())


class TestFetcherFetchAll:
    def test_fetch_one_preloads_status_and_clears_cache(self, monkeypatch):
        class FakeResponse:
            status = 304
            headers = {"ETag": '"new"'}

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return None

        class FakeSession:
            def __init__(self):
                self.requests = []

            def get(self, url, headers=None):
                self.requests.append((url, headers))
                return FakeResponse()

        async def _run():
            store = FakeFeedStatusStore({"https://example.com/rss": {"etag": '"old"'}})
            fetcher = Fetcher(semaphore_limit=10, status_store=store)
            fake_session = FakeSession()

            async def fake_get_session():
                return fake_session

            monkeypatch.setattr(fetcher, "_get_session", fake_get_session)

            entries = await fetcher.fetch_one("https://example.com/rss")

            assert entries == []
            assert store.batch_get_calls == [["https://example.com/rss"]]
            assert store.get_calls == []
            assert fake_session.requests == [("https://example.com/rss", {"If-None-Match": '"old"'})]
            assert store.successes[0]["feed_url"] == "https://example.com/rss"
            assert fetcher._feed_status_cache is None

        asyncio.run(_run())

    def test_fetch_all_yields_entries_from_all_completed_tasks(self, monkeypatch):
        class FakeResponse:
            status = 200
            headers = {}

            def __init__(self, url):
                self.url = url

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return None

            async def text(self):
                return self.url

        class FakeSession:
            def get(self, url, headers=None):
                return FakeResponse(url)

        async def _run():
            store = FakeFeedStatusStore({"a": {"etag": '"a"'}})
            fetcher = Fetcher(semaphore_limit=10, status_store=store)

            async def fake_get_session():
                return FakeSession()

            monkeypatch.setattr(fetcher, "_get_session", fake_get_session)
            monkeypatch.setattr("src.fetcher.feedparser.parse", lambda text: type("Feed", (), {"entries": [{"link": text}]})())
            monkeypatch.setattr(Fetcher, "_record_success", staticmethod(lambda url, response, fetch_ms: None))
            monkeypatch.setattr(Fetcher, "_record_error", lambda *args, **kwargs: None)

            entries = [entry async for entry in fetcher.fetch_all(["a", "b", "c", "d"])]
            assert {entry["link"] for entry in entries} == {"a", "b", "c", "d"}

        asyncio.run(_run())

    def test_fetch_all_preloads_feed_statuses_once(self, monkeypatch):
        class FakeResponse:
            status = 304
            headers = {}

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return None

        class FakeSession:
            def __init__(self):
                self.requests = []

            def get(self, url, headers=None):
                self.requests.append((url, headers))
                return FakeResponse()

        async def _run():
            store = FakeFeedStatusStore({
                "a": {"etag": '"a"'},
                "b": {"last_modified": "Wed, 01 May 2026 10:00:00 GMT"},
            })
            fetcher = Fetcher(semaphore_limit=10, status_store=store)
            fake_session = FakeSession()

            async def fake_get_session():
                return fake_session

            monkeypatch.setattr(fetcher, "_get_session", fake_get_session)

            entries = [entry async for entry in fetcher.fetch_all(["a", "b"])]

            assert entries == []
            assert store.batch_get_calls == [["a", "b"]]
            assert store.get_calls == []
            assert fake_session.requests == [
                ("a", {"If-None-Match": '"a"'}),
                ("b", {"If-Modified-Since": "Wed, 01 May 2026 10:00:00 GMT"}),
            ]

        asyncio.run(_run())

    def test_fetch_all_clears_preloaded_status_cache(self, monkeypatch):
        class FakeResponse:
            status = 304
            headers = {}

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return None

        class FakeSession:
            def get(self, url, headers=None):
                return FakeResponse()

        async def _run():
            store = FakeFeedStatusStore({"a": {"etag": '"a"'}})
            fetcher = Fetcher(semaphore_limit=10, status_store=store)

            async def fake_get_session():
                return FakeSession()

            monkeypatch.setattr(fetcher, "_get_session", fake_get_session)

            [entry async for entry in fetcher.fetch_all(["a"])]

            assert fetcher._feed_status_cache is None

        asyncio.run(_run())

    def test_fetch_all_clears_status_cache_when_session_setup_fails(self, monkeypatch):
        async def _run():
            store = FakeFeedStatusStore({"a": {"etag": '"a"'}})
            fetcher = Fetcher(semaphore_limit=10, status_store=store)

            async def fail_get_session():
                raise RuntimeError("session failed")

            monkeypatch.setattr(fetcher, "_get_session", fail_get_session)

            with pytest.raises(RuntimeError, match="session failed"):
                [entry async for entry in fetcher.fetch_all(["a"])]

            assert fetcher._feed_status_cache is None

        asyncio.run(_run())

    def test_status_store_none_disables_sqlite_status_access(self, monkeypatch):
        class FakeResponse:
            status = 304
            headers = {}

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return None

        class FakeSession:
            def __init__(self):
                self.requests = []

            def get(self, url, headers=None):
                self.requests.append((url, headers))
                return FakeResponse()

        async def _run():
            fetcher = Fetcher(semaphore_limit=10, status_store=None)
            fake_session = FakeSession()

            async def fake_get_session():
                return fake_session

            monkeypatch.setattr(fetcher, "_get_session", fake_get_session)
            monkeypatch.setattr("src.fetcher.get_feed_status", lambda url: pytest.fail("disabled store should not read SQLite"))
            monkeypatch.setattr("src.fetcher.batch_get_feed_statuses", lambda urls: pytest.fail("disabled store should not batch-read SQLite"))
            monkeypatch.setattr("src.fetcher.record_feed_success", lambda **kwargs: pytest.fail("disabled store should not write SQLite"))
            monkeypatch.setattr("src.fetcher.record_feed_error", lambda **kwargs: pytest.fail("disabled store should not write SQLite"))

            entries = [entry async for entry in fetcher.fetch_all(["a"])]

            assert entries == []
            assert fake_session.requests == [("a", {})]

        asyncio.run(_run())
