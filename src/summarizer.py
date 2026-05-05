"""AI summarizer for RSS entries."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

import httpx

from .database import get_article_summary, get_article_summary_by_link
from .article_identity import compute_article_id, normalize_article_link

logger = logging.getLogger(__name__)

SUMMARIZE_PROMPT = """你是一个新闻摘要助手。请用50-150字总结以下内容，提取关键信息。直接输出摘要，不要其他解释：

{content}
"""


class Summarizer:
    """AI-powered content summarizer using Anthropic-compatible API."""

    OPENCODE_CONFIG = Path.home() / ".config" / "opencode" / "opencode.json"
    DEFAULT_PROVIDER = "xlab"
    DEFAULT_MODEL = "gpt-5.5"

    @classmethod
    def _load_opencode_provider(cls) -> dict:
        if not cls.OPENCODE_CONFIG.exists():
            return {}
        with cls.OPENCODE_CONFIG.open(encoding="utf-8") as f:
            config = json.load(f)
        provider = config.get("provider", {}).get(cls.DEFAULT_PROVIDER, {})
        return provider.get("options", {})

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        model: Optional[str] = None,
        max_concurrent: int = 5,
    ):
        opencode_options = self._load_opencode_provider()
        self.api_key = api_key or opencode_options.get("apiKey", "")
        self.api_url = api_url or opencode_options.get("baseURL", "")
        self.model = model or self.DEFAULT_MODEL
        self._semaphore = asyncio.Semaphore(max_concurrent)

        if not self.api_key or self.api_key == "your_api_key_here":
            raise ValueError("API_KEY not configured")

        self.endpoint = self.api_url.rstrip("/") + "/chat/completions"

    @staticmethod
    def _compute_article_id(entry: dict) -> str:
        link = entry.get("link", "")
        normalized_link = normalize_article_link(link)
        return compute_article_id(normalized_link or link)

    def summarize(self, text: str, max_retries: int = 3) -> str:
        """Synchronous summarization with retry."""
        if not text or not text.strip():
            return ""

        prompt = SUMMARIZE_PROMPT.format(content=text[:8000])

        for attempt in range(max_retries):
            try:
                response = httpx.post(
                    self.endpoint,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                    timeout=60,
                )
                response.raise_for_status()
                result = response.json()["choices"][0]["message"]["content"] or ""
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
        link = entry.get("link", "")
        article_id = self._compute_article_id(entry)

        cached_summary = get_article_summary_by_link(link) or get_article_summary(article_id)
        if cached_summary:
            entry["ai_summary"] = cached_summary
            return entry

        async with self._semaphore:
            content = _entry_text(entry.get("content")) or _entry_text(entry.get("summary"))
            entry["ai_summary"] = await asyncio.to_thread(self.summarize, content)
            return entry

    async def summarize_batch(self, entries: list[dict]) -> list[dict]:
        """Summarize multiple entries concurrently, skipping already-cached ones."""
        if not entries:
            return entries

        to_summarize = []
        for entry in entries:
            link = entry.get("link", "")
            article_id = self._compute_article_id(entry)
            cached_summary = get_article_summary_by_link(link) or get_article_summary(article_id)
            if cached_summary:
                entry["ai_summary"] = cached_summary
                logger.debug("Skipping cached summary for %s", entry.get("link"))
            else:
                to_summarize.append(entry)

        if to_summarize:
            logger.info("Summarizing %d new entries (skipped %d cached)",
                        len(to_summarize), len(entries) - len(to_summarize))
            tasks = [self._summarize_one(entry) for entry in to_summarize]
            await asyncio.gather(*tasks)

        return entries


def _entry_text(value) -> str:
    """Convert feedparser string/list/dict fields into text for summarization."""
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("value") or value.get("content") or "")
    if isinstance(value, list):
        return "\n".join(_entry_text(item) for item in value if item)
    return str(value)
