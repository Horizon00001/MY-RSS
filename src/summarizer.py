"""AI summarizer for RSS entries."""

import asyncio
import hashlib
import logging
import os
from typing import Optional

import anthropic

logger = logging.getLogger(__name__)

SUMMARIZE_PROMPT = """你是一个新闻摘要助手。请用50-150字总结以下内容，提取关键信息。直接输出摘要，不要其他解释：

{content}
"""


class Summarizer:
    """AI-powered content summarizer using Anthropic-compatible API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        model: str = "deepseek/deepseek-v4-pro-free",
        max_concurrent: int = 20,
    ):
        self.api_key = api_key or os.getenv("API_KEY", "")
        self.api_url = api_url or os.getenv("API_URL", "https://zenmux.ai/api/anthropic")
        self.model = model or os.getenv("MODEL", "deepseek/deepseek-v4-pro-free")
        self._semaphore = asyncio.Semaphore(max_concurrent)

        if not self.api_key or self.api_key == "your_api_key_here":
            raise ValueError("API_KEY not configured")

        self.client = anthropic.Anthropic(
            api_key=self.api_key,
            base_url=self.api_url,
        )

    @staticmethod
    def _compute_article_id(entry: dict) -> str:
        link = entry.get("link", "")
        return hashlib.md5(link.encode()).hexdigest()[:12]

    @staticmethod
    def _has_cached_summary(article_id: str) -> bool:
        from .database import article_has_summary
        return article_has_summary(article_id)

    def summarize(self, text: str, max_retries: int = 3) -> str:
        """Synchronous summarization with retry."""
        if not text or not text.strip():
            return ""

        prompt = SUMMARIZE_PROMPT.format(content=text[:8000])

        for attempt in range(max_retries):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=200,
                    messages=[{"role": "user", "content": prompt}],
                )
                result = response.content[0].text or ""
                return result
            except Exception as e:
                logger.warning("AI summarize attempt %d failed: %s", attempt + 1, e)
                if attempt < max_retries - 1:
                    import time
                    time.sleep(2 ** attempt)
                    continue
                return f"[总结失败: {e}]"
        return "[总结失败]"

    async def _summarize_one(self, entry: dict) -> dict:
        """Summarize a single entry with semaphore control and cache check."""
        article_id = self._compute_article_id(entry)

        # Skip if already cached in DB
        if self._has_cached_summary(article_id):
            return entry

        async with self._semaphore:
            content = entry.get("content") or entry.get("summary") or ""
            entry["ai_summary"] = await asyncio.to_thread(self.summarize, content)
            return entry

    async def summarize_batch(self, entries: list[dict]) -> list[dict]:
        """Summarize multiple entries concurrently, skipping already-cached ones."""
        if not entries:
            return entries

        to_summarize = []
        for entry in entries:
            article_id = self._compute_article_id(entry)
            if self._has_cached_summary(article_id):
                logger.debug("Skipping cached summary for %s", entry.get("link"))
            else:
                to_summarize.append(entry)

        if to_summarize:
            logger.info("Summarizing %d new entries (skipped %d cached)",
                        len(to_summarize), len(entries) - len(to_summarize))
            tasks = [self._summarize_one(entry) for entry in to_summarize]
            await asyncio.gather(*tasks)

        return entries
