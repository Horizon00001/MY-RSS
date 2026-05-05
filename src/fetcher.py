"""HTTP fetching layer for RSS feeds."""

import asyncio
import logging
import time
from types import TracebackType
from typing import AsyncIterator, Optional

import aiohttp
import feedparser

from .config import settings
from .database import get_feed_status, record_feed_error, record_feed_success

logger = logging.getLogger(__name__)


class Fetcher:
    """Async HTTP fetcher with semaphore control and connection reuse."""

    def __init__(self, semaphore_limit: int | None = None):
        self.semaphore_limit = semaphore_limit or settings.semaphore_limit
        self.user_agent = settings.user_agent
        self._semaphore = asyncio.Semaphore(self.semaphore_limit)
        self._session: Optional[aiohttp.ClientSession] = None

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

    async def fetch_all(self, urls: list[str]) -> AsyncIterator:
        """Fetch all URLs concurrently, yielding entries as they arrive."""
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
        try:
            while tasks:
                completed, _ = await asyncio.wait(
                    tasks.keys(), return_when=asyncio.FIRST_COMPLETED
                )
                for task in completed:
                    for entry in task.result():
                        yield entry
                    del tasks[task]
        finally:
            for task in tasks:
                task.cancel()

    @staticmethod
    def _conditional_headers(url: str) -> dict[str, str]:
        status = get_feed_status(url) or {}
        headers = {}
        if status.get("etag"):
            headers["If-None-Match"] = status["etag"]
        if status.get("last_modified"):
            headers["If-Modified-Since"] = status["last_modified"]
        return headers

    @staticmethod
    def _record_success(url: str, response: aiohttp.ClientResponse, fetch_ms: float) -> None:
        record_feed_success(
            feed_url=url,
            status_code=response.status,
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            fetch_ms=round(fetch_ms, 2),
        )

    @staticmethod
    def _record_error(
        url: str,
        error: str,
        status_code: int | None = None,
        fetch_ms: float | None = None,
    ) -> None:
        record_feed_error(
            feed_url=url,
            error=error,
            status_code=status_code,
            fetch_ms=round(fetch_ms, 2) if fetch_ms is not None else None,
        )
