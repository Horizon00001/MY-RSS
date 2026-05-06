"""HTTP fetching layer for RSS feeds."""

import asyncio
import logging
import time
from types import TracebackType
from typing import AsyncIterator, Optional, Protocol

import aiohttp
import feedparser

from .config import settings
from .database import batch_get_feed_statuses, get_feed_status, record_feed_error, record_feed_success

logger = logging.getLogger(__name__)


_DEFAULT_STATUS_STORE = object()


class FeedStatusStore(Protocol):
    def get(self, feed_url: str) -> Optional[dict]:
        ...

    def batch_get(self, feed_urls: list[str]) -> dict[str, dict]:
        ...

    def record_success(
        self,
        feed_url: str,
        status_code: int,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        fetch_ms: Optional[float] = None,
    ) -> None:
        ...

    def record_error(
        self,
        feed_url: str,
        error: str,
        status_code: Optional[int] = None,
        fetch_ms: Optional[float] = None,
    ) -> None:
        ...


class DatabaseFeedStatusStore:
    def get(self, feed_url: str) -> Optional[dict]:
        return get_feed_status(feed_url)

    def batch_get(self, feed_urls: list[str]) -> dict[str, dict]:
        return batch_get_feed_statuses(feed_urls)

    def record_success(
        self,
        feed_url: str,
        status_code: int,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        fetch_ms: Optional[float] = None,
    ) -> None:
        record_feed_success(
            feed_url=feed_url,
            status_code=status_code,
            etag=etag,
            last_modified=last_modified,
            fetch_ms=fetch_ms,
        )

    def record_error(
        self,
        feed_url: str,
        error: str,
        status_code: Optional[int] = None,
        fetch_ms: Optional[float] = None,
    ) -> None:
        record_feed_error(
            feed_url=feed_url,
            error=error,
            status_code=status_code,
            fetch_ms=fetch_ms,
        )


class NullFeedStatusStore:
    def get(self, feed_url: str) -> Optional[dict]:
        return None

    def batch_get(self, feed_urls: list[str]) -> dict[str, dict]:
        return {}

    def record_success(
        self,
        feed_url: str,
        status_code: int,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        fetch_ms: Optional[float] = None,
    ) -> None:
        return None

    def record_error(
        self,
        feed_url: str,
        error: str,
        status_code: Optional[int] = None,
        fetch_ms: Optional[float] = None,
    ) -> None:
        return None


class Fetcher:
    """Async HTTP fetcher with semaphore control and connection reuse."""

    def __init__(
        self,
        semaphore_limit: int | None = None,
        status_store: FeedStatusStore | None | object = _DEFAULT_STATUS_STORE,
    ):
        self.semaphore_limit = semaphore_limit or settings.semaphore_limit
        self.user_agent = settings.user_agent
        self._semaphore = asyncio.Semaphore(self.semaphore_limit)
        self._session: Optional[aiohttp.ClientSession] = None
        self._feed_status_cache: dict[str, dict] | None = None
        if status_store is _DEFAULT_STATUS_STORE:
            self._status_store: FeedStatusStore = DatabaseFeedStatusStore()
        elif status_store is None:
            self._status_store = NullFeedStatusStore()
        else:
            self._status_store = status_store

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create a shared session for connection reuse."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": self.user_agent},
            )
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> "Fetcher":
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        await self.close()

    async def fetch_one(self, url: str) -> list:
        """Fetch a single URL and parse entries."""
        self._feed_status_cache = self._status_store.batch_get([url])
        try:
            session = await self._get_session()
            async with self._semaphore:
                started = time.monotonic()
                headers = self._conditional_headers(url)
                try:
                    async with session.get(url, headers=headers) as response:
                        fetch_ms = (time.monotonic() - started) * 1000
                        if response.status == 304:
                            self._record_success(url, response, fetch_ms)
                            return []
                        if response.status >= 400:
                            self._record_error(url, f"HTTP {response.status}", response.status, fetch_ms)
                            return []
                        text = await response.text()
                        feed = feedparser.parse(text)
                        self._record_success(url, response, fetch_ms)
                        return list(feed.entries)
                except Exception as e:
                    fetch_ms = (time.monotonic() - started) * 1000
                    self._record_error(url, str(e), fetch_ms=fetch_ms)
                    logger.warning("Failed to fetch %s: %s", url, e)
                    return []
        finally:
            self._feed_status_cache = None

    async def fetch_all(self, urls: list[str]) -> AsyncIterator:
        """Fetch all URLs concurrently, yielding entries as they arrive."""
        try:
            self._feed_status_cache = self._status_store.batch_get(urls)
            session = await self._get_session()

            async def fetch_task(url: str) -> list:
                async with self._semaphore:
                    started = time.monotonic()
                    headers = self._conditional_headers(url)
                    try:
                        async with session.get(url, headers=headers) as response:
                            fetch_ms = (time.monotonic() - started) * 1000
                            if response.status == 304:
                                self._record_success(url, response, fetch_ms)
                                return []
                            if response.status >= 400:
                                self._record_error(url, f"HTTP {response.status}", response.status, fetch_ms)
                                return []
                            text = await response.text()
                            feed = feedparser.parse(text)
                            self._record_success(url, response, fetch_ms)
                            return list(feed.entries)
                    except Exception as e:
                        fetch_ms = (time.monotonic() - started) * 1000
                        self._record_error(url, str(e), fetch_ms=fetch_ms)
                        logger.warning("Failed to fetch %s: %s", url, e)
                        return []

            tasks = {asyncio.create_task(fetch_task(url)): url for url in urls}
            while tasks:
                completed, _ = await asyncio.wait(
                    tasks.keys(), return_when=asyncio.FIRST_COMPLETED
                )
                for task in completed:
                    for entry in task.result():
                        yield entry
                    del tasks[task]
        finally:
            for task in locals().get("tasks", {}):
                task.cancel()
            self._feed_status_cache = None

    def _conditional_headers(self, url: str) -> dict[str, str]:
        if self._feed_status_cache is None:
            status = self._status_store.get(url) or {}
        else:
            status = self._feed_status_cache.get(url) or {}
        headers = {}
        if status.get("etag"):
            headers["If-None-Match"] = status["etag"]
        if status.get("last_modified"):
            headers["If-Modified-Since"] = status["last_modified"]
        return headers

    def _record_success(self, url: str, response: aiohttp.ClientResponse, fetch_ms: float) -> None:
        self._status_store.record_success(
            feed_url=url,
            status_code=response.status,
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            fetch_ms=round(fetch_ms, 2),
        )

    def _record_error(
        self,
        url: str,
        error: str,
        status_code: int | None = None,
        fetch_ms: float | None = None,
    ) -> None:
        self._status_store.record_error(
            feed_url=url,
            error=error,
            status_code=status_code,
            fetch_ms=round(fetch_ms, 2) if fetch_ms is not None else None,
        )
