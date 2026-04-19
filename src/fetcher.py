"""HTTP fetching layer for RSS feeds."""

import asyncio
from typing import AsyncIterator

import aiohttp
import feedparser

from .config import settings


class Fetcher:
    """Async HTTP fetcher with semaphore control."""

    def __init__(self, semaphore_limit: int | None = None):
        self.semaphore_limit = semaphore_limit or settings.semaphore_limit
        self.user_agent = settings.user_agent

    async def fetch_one(self, session: aiohttp.ClientSession, url: str) -> list:
        """Fetch a single URL and parse entries."""
        headers = {"User-Agent": self.user_agent}
        async with asyncio.Semaphore(self.semaphore_limit):
            try:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    text = await response.text()
                    feed = feedparser.parse(text)
                    return list(feed.entries)
            except Exception:
                return []

    async def fetch_all(self, urls: list[str]) -> AsyncIterator:
        """Fetch all URLs concurrently, yielding entries as they arrive."""
        semaphore = asyncio.Semaphore(self.semaphore_limit)

        async def fetch_task(session: aiohttp.ClientSession, url: str) -> list:
            headers = {"User-Agent": self.user_agent}
            async with semaphore:
                try:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=30)
                    ) as response:
                        text = await response.text()
                        feed = feedparser.parse(text)
                        return list(feed.entries)
                except Exception:
                    return []

        async with aiohttp.ClientSession() as session:
            tasks = {asyncio.create_task(fetch_task(session, url)): url for url in urls}
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
