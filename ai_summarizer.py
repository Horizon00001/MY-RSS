"""AI summarizer for RSS entries using DeepSeek API."""

import asyncio
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI

SUMMARIZE_PROMPT = """你是一个新闻摘要助手。请用50-150字总结以下内容，提取关键信息。直接输出摘要，不要其他解释：

{content}
"""


class RSSSummarizer:
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        model: str = "deepseek-chat",
        max_concurrent: int = 20,
    ):
        env_path = Path(__file__).parent / ".env"
        if not env_path.exists():
            env_path = Path(__file__).parent.parent / ".env"
        load_dotenv(dotenv_path=env_path)

        self.api_key = api_key or os.getenv("API_KEY", "")
        self.api_url = api_url or os.getenv("API_URL", "https://api.deepseek.com/v1")
        self.model = model
        self._semaphore = asyncio.Semaphore(max_concurrent)

        if not self.api_key or self.api_key == "your_api_key_here":
            raise ValueError("API_KEY not configured. Please set your DeepSeek API key in .env")

        self.client = OpenAI(api_key=self.api_key, base_url=self.api_url)

    def summarize(self, text: str, max_retries: int = 3) -> str:
        if not text or not text.strip():
            return ""

        prompt = SUMMARIZE_PROMPT.format(content=text[:8000])

        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    stream=False,
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                if attempt < max_retries - 1:
                    import time
                    time.sleep(2 ** attempt)
                    continue
                return f"[总结失败: {e}]"

        return "[总结失败]"

    async def _summarize_one(self, entry: dict) -> dict:
        async with self._semaphore:
            content = entry.get("content") or entry.get("summary") or ""
            entry["ai_summary"] = await asyncio.to_thread(self.summarize, content)
            return entry

    def summarize_entries(self, entries: list[dict]) -> list[dict]:
        for entry in entries:
            content = entry.get("content") or entry.get("summary") or ""
            entry["ai_summary"] = self.summarize(content)
        return entries

    async def summarize_entries_async(self, entries: list[dict]) -> list[dict]:
        if not entries:
            return entries
        tasks = [self._summarize_one(entry) for entry in entries]
        return await asyncio.gather(*tasks)
