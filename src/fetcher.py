"""HTTP fetching layer for RSS feeds."""

import asyncio
import logging
from types import TracebackType
from typing import AsyncIterator, Optional

import aiohttp
import feedparser

from .config import settings

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
            try:
                async with session.get(url) as response:
                    text = await response.text()
                    feed = feedparser.parse(text)
                    return list(feed.entries)
            except Exception as e:
                logger.warning("Failed to fetch %s: %s", url, e)
                return []

    async def fetch_all(self, urls: list[str]) -> AsyncIterator:
        """Fetch all URLs concurrently, yielding entries as they arrive."""
        session = await self._get_session()

        async def fetch_task(url: str) -> list:
            async with self._semaphore:
                try:
                    async with session.get(url) as response:
                        text = await response.text()
                        feed = feedparser.parse(text)
                        return list(feed.entries)
                except Exception as e:
                    logger.warning("Failed to fetch %s: %s", url, e)
                    return []

        tasks = {asyncio.create_task(fetch_task(url)): url for url in urls}
        done = set()
        while len(done) < len(tasks):
            completed, _ = await asyncio.wait(
                tasks.keys(), return_when=asyncio.FIRST_COMPLETED
            )
            for task in completed:
                for entry in task.result():
                    yield entry
                done.add(task)
                del tasks[task]
