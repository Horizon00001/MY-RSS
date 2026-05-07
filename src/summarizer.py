"""AI summarizer for RSS entries."""

import asyncio
import html
import json
import logging
from pathlib import Path
import re
from typing import Optional

import httpx

from .database import batch_get_article_summaries, get_article_summary, get_article_summary_by_link
from .article_identity import compute_article_id, normalize_article_link

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

SUMMARIZE_PROMPT = """你是一个 RSS 新闻摘要编辑。请基于输入内容生成高信息密度摘要。

要求：
- 使用简体中文，保留必要的英文专有名词、产品名、人名和数字。
- 只总结原文明确出现的信息，不要编造背景、结论或影响。
- 优先说明“发生了什么 / 谁受影响 / 关键数字或时间 / 为什么值得看”。
- 如果输入信息不足，请明确写出“信息不足”，不要假装确定。
- 直接输出 2-3 行纯文本，不要 Markdown，不要列表符号，不要额外解释。

输出格式：
一句话：不超过 45 个中文字符概括核心事件或观点。
要点：1-2 句补充关键事实、背景、数字或争议。
看点：说明这条内容对读者的意义；如果无法判断，写“信息不足，无法判断”。

输入内容：

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
                return result.strip()
            except Exception as e:
                logger.warning("AI summarize attempt %d failed: %s", attempt + 1, e)
                if attempt < max_retries - 1:
                    import time
                    time.sleep(2 ** attempt)
                    continue
                return f"[总结失败: {e}]"
        return "[总结失败]"

    async def _summarize_uncached_one(self, entry: dict) -> dict:
        """Summarize a single entry known to be missing from the cache."""
        async with self._semaphore:
            content = _entry_summary_text(entry)
            entry["ai_summary"] = await asyncio.to_thread(self.summarize, content)
            return entry

    async def _summarize_one(self, entry: dict) -> dict:
        """Summarize a single entry with semaphore control and cache check."""
        link = entry.get("link", "")
        article_id = entry.get("id") or self._compute_article_id(entry)

        cached_summary = get_article_summary_by_link(link) or get_article_summary(article_id)
        if cached_summary:
            entry["ai_summary"] = cached_summary
            return entry

        return await self._summarize_uncached_one(entry)

    async def summarize_batch(self, entries: list[dict]) -> list[dict]:
        """Summarize multiple entries concurrently, skipping already-cached ones."""
        if not entries:
            return entries

        cache_requests = []
        for entry in entries:
            article_id = entry.get("id") or self._compute_article_id(entry)
            cache_requests.append({
                "id": article_id,
                "link": entry.get("link", ""),
                "normalized_link": normalize_article_link(entry.get("link", "")),
            })

        cached_summaries = batch_get_article_summaries(cache_requests)
        to_summarize = []
        for entry, cache_request in zip(entries, cache_requests):
            cached_summary = cached_summaries.get(cache_request["id"], "")
            if cached_summary:
                entry["ai_summary"] = cached_summary
                logger.debug("Skipping cached summary for %s", entry.get("link"))
            else:
                to_summarize.append(entry)

        if to_summarize:
            logger.info("Summarizing %d new entries (skipped %d cached)",
                        len(to_summarize), len(entries) - len(to_summarize))
            tasks = [self._summarize_uncached_one(entry) for entry in to_summarize]
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


def _clean_text(value) -> str:
    """Normalize RSS text fields before sending them to the summary model."""
    text = _entry_text(value)
    if not text:
        return ""
    text = _HTML_TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _entry_summary_text(entry: dict) -> str:
    """Build a structured article payload for summarization."""
    title = _clean_text(entry.get("title"))
    source = _clean_text(entry.get("source_name") or entry.get("feed_title") or entry.get("source"))
    content = _clean_text(entry.get("content")) or _clean_text(entry.get("summary"))

    parts = []
    if title:
        parts.append(f"标题：{title}")
    if source:
        parts.append(f"来源：{source}")
    if content:
        parts.append(f"正文：{content}")
    return "\n".join(parts)
