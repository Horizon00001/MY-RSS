import asyncio
import aiohttp
import feedparser
import configparser
import pathlib
import dateutil
import json
import logging
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from ai_summarizer import RSSSummarizer

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="RSS内容提取API", description="从RSS源提取和过滤内容的API服务")

BEIJING_TZ = timezone(timedelta(hours=8))
SEMAPHORE_LIMIT = 20

class RSSEntry(BaseModel):
    title: str
    link: str
    summary: str
    date: Optional[str]
    content: str
    ai_summary: str = ""

class RSSResponse(BaseModel):
    total: int
    entries: list[RSSEntry]
    incremental: bool
    last_fetch: Optional[str]

class RSSExtractorAPI:
    def __init__(self):
        self.config_path = pathlib.Path(__file__).parent / 'config.ini'
        self.state_path = pathlib.Path(__file__).parent / 'fetch_state.json'
        self.config = configparser.ConfigParser()
        self.config.read(self.config_path, encoding="utf-8")
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._summarizer: Optional[RSSSummarizer] = None

    def get_summarizer(self) -> Optional[RSSSummarizer]:
        if self._summarizer is None:
            try:
                self._summarizer = RSSSummarizer()
            except ValueError as e:
                logger.warning(f"AI summarizer not available: {e}")
                return None
        return self._summarizer

    def load_rss_feeds(self) -> list[str]:
        urls = []
        for _, url in self.config.items("rss"):
            urls.append(url)
        return urls

    def _load_state(self) -> dict:
        if self.state_path.exists():
            with open(self.state_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def _save_state(self, state: dict) -> None:
        with open(self.state_path, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    async def fetch_one(self, session: aiohttp.ClientSession, url: str) -> list:
        headers = {'User-Agent': self.config.get('headers', 'user_agent')}
        async with self._semaphore:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    text = await response.text()
                    feed = feedparser.parse(text)
                    return list(feed.entries)
            except Exception as e:
                logger.warning(f"获取失败 {url}: {e}")
                return []

    async def fetch_rss_streaming(self, urls: list[str]):
        """Yields entries as they are fetched, enabling pipelined processing."""
        self._semaphore = asyncio.Semaphore(SEMAPHORE_LIMIT)

        async def fetch_task(session, url):
            entries = await self.fetch_one(session, url)
            return entries

        async with aiohttp.ClientSession() as session:
            tasks = {asyncio.create_task(fetch_task(session, url)): url for url in urls}
            done = set()
            while len(done) < len(tasks):
                completed, _ = await asyncio.wait(
                    tasks.keys(),
                    return_when=asyncio.FIRST_COMPLETED
                )
                for task in completed:
                    entries = task.result()
                    for entry in entries:
                        yield entry
                    done.add(task)
                    del tasks[task]

    async def fetch_rss_entries(self, urls: list[str]) -> list[dict]:
        entries = []
        async for entry in self.fetch_rss_streaming(urls):
            entries.append(entry)
        return entries

    def filter_by_date(self, entries: list, days: int = None) -> list:
        filter_entries = []
        if days is None:
            days = int(self.config.get("filter", "days"))
        now = datetime.now(BEIJING_TZ)
        cutoff = now - timedelta(days=days)
        for entry in entries:
            entry_date = self.get_entry_date(entry)
            if entry_date and entry_date > cutoff:
                filter_entries.append(entry)
        return filter_entries

    def filter_by_timestamp(self, entries: list, last_fetch: datetime | None) -> list:
        if last_fetch is None:
            return entries
        filtered = []
        for entry in entries:
            entry_date = self.get_entry_date(entry)
            if entry_date and entry_date > last_fetch:
                filtered.append(entry)
        return filtered

    def get_entry_date(self, entry) -> datetime | None:
        for field in ['updated', 'published', 'date', 'pubDate']:
            if hasattr(entry, field):
                try:
                    parsed = dateutil.parser.parse(getattr(entry, field))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    return parsed.astimezone(BEIJING_TZ)
                except Exception:
                    continue
        return None

    def format_entry(self, entry) -> dict:
        entry_date = self.get_entry_date(entry)
        return {
            "title": entry.get('title', ''),
            "link": entry.get('link', ''),
            "summary": entry.get('summary', ''),
            "date": entry_date.strftime('%Y-%m-%d %H:%M:%S (北京时间)') if entry_date else None,
            "content": entry.get('content', ''),
            "ai_summary": entry.get('ai_summary', ''),
        }

extractor = RSSExtractorAPI()

@app.get("/", summary="API根路径")
async def root():
    return {"message": "RSS内容提取API", "docs": "/docs"}

@app.get("/rss/entries", response_model=RSSResponse, summary="获取RSS内容")
async def get_rss_entries(
    days: int = Query(default=None, description="过滤最近几天的内容,不传则使用配置文件中的值"),
    limit: int = Query(default=None, description="返回条目数量限制"),
    incremental: bool = Query(default=False, description="是否启用增量更新"),
    use_ai: bool = Query(default=True, description="是否启用AI总结")
):
    try:
        state = extractor._load_state()
        last_fetch_str = state.get("last_fetch")
        last_fetch = None
        is_incremental = False

        if incremental and last_fetch_str:
            last_fetch_str_clean = last_fetch_str.replace(' (北京时间)', '')
            last_fetch = datetime.strptime(last_fetch_str_clean, '%Y-%m-%d %H:%M:%S')
            last_fetch = last_fetch.replace(tzinfo=BEIJING_TZ)
            is_incremental = True
            logger.info(f"增量模式，上次抓取时间: {last_fetch}")

        if days is None:
            days = int(extractor.config.get("filter", "days"))
        now = datetime.now(BEIJING_TZ)
        cutoff = now - timedelta(days=days)

        urls = extractor.load_rss_feeds()
        summarizer = extractor.get_summarizer() if use_ai else None

        # 边抓取边总结的流水线
        queue = asyncio.Queue(maxsize=100)
        results: list = []
        summarizer_done = asyncio.Event()

        async def fetcher():
            async for entry in extractor.fetch_rss_streaming(urls):
                entry_date = extractor.get_entry_date(entry)
                if last_fetch:
                    if entry_date and entry_date > last_fetch:
                        await queue.put(entry)
                else:
                    if entry_date and entry_date > cutoff:
                        await queue.put(entry)
            await queue.put(None)  # Signal done

        async def summarizer_worker():
            batch: list = []
            while True:
                entry = await queue.get()
                if entry is None:
                    if batch:
                        summarized = await summarizer.summarize_entries_async(batch)
                        results.extend(summarized)
                    summarizer_done.set()
                    break
                batch.append(entry)
                if len(batch) >= 10:
                    summarized = await summarizer.summarize_entries_async(batch)
                    results.extend(summarized)
                    batch = []
                    if limit and len(results) >= limit:
                        summarizer_done.set()
                        break

        if summarizer:
            await asyncio.gather(fetcher(), summarizer_worker())
        else:
            async for entry in extractor.fetch_rss_streaming(urls):
                entry_date = extractor.get_entry_date(entry)
                if last_fetch:
                    if entry_date and entry_date > last_fetch:
                        results.append(entry)
                else:
                    if entry_date and entry_date > cutoff:
                        results.append(entry)
                if limit and len(results) >= limit:
                    break

        if limit:
            results = results[:limit]
        formatted = [extractor.format_entry(e) for e in results]

        now_str = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S (北京时间)')
        state["last_fetch"] = now_str
        extractor._save_state(state)

        return RSSResponse(
            total=len(formatted),
            entries=formatted,
            incremental=is_incremental,
            last_fetch=now_str if is_incremental else None
        )
    except Exception as e:
        logger.error(f"获取RSS失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def generate_stream(urls: list, limit: int, cutoff: datetime, last_fetch: datetime | None, summarizer):
    """流式生成器，边抓取边总结边产出"""
    queue = asyncio.Queue(maxsize=50)
    yielded = 0

    async def fetcher():
        async for entry in extractor.fetch_rss_streaming(urls):
            entry_date = extractor.get_entry_date(entry)
            if last_fetch:
                if entry_date and entry_date > last_fetch:
                    await queue.put(entry)
            else:
                if entry_date and entry_date > cutoff:
                    await queue.put(entry)
        await queue.put(None)

    # 启动 fetcher 作为后台任务
    fetcher_task = asyncio.create_task(fetcher())

    batch = []
    try:
        while yielded < limit:
            entry = await queue.get()
            if entry is None:
                if batch:
                    summarized = await summarizer.summarize_entries_async(batch)
                    for s in summarized:
                        if yielded >= limit:
                            break
                        yield f"data: {json.dumps(extractor.format_entry(s), ensure_ascii=False)}\n\n"
                        yielded += 1
                break
            batch.append(entry)
            if len(batch) >= 3:
                summarized = await summarizer.summarize_entries_async(batch)
                for s in summarized:
                    if yielded >= limit:
                        break
                    yield f"data: {json.dumps(extractor.format_entry(s), ensure_ascii=False)}\n\n"
                    yielded += 1
                batch = []
    finally:
        fetcher_task.cancel()
        try:
            await fetcher_task
        except asyncio.CancelledError:
            pass


@app.get("/rss/stream", summary="流式获取RSS内容")
async def stream_rss_entries(
    days: int = Query(default=None, description="过滤最近几天的内容,不传则使用配置文件中的值"),
    limit: int = Query(default=10, description="返回条目数量限制"),
    incremental: bool = Query(default=False, description="是否启用增量更新"),
    use_ai: bool = Query(default=True, description="是否启用AI总结")
):
    try:
        state = extractor._load_state()
        last_fetch_str = state.get("last_fetch")
        last_fetch = None

        if incremental and last_fetch_str:
            last_fetch_str_clean = last_fetch_str.replace(' (北京时间)', '')
            last_fetch = datetime.strptime(last_fetch_str_clean, '%Y-%m-%d %H:%M:%S')
            last_fetch = last_fetch.replace(tzinfo=BEIJING_TZ)

        if days is None:
            days = int(extractor.config.get("filter", "days"))
        now = datetime.now(BEIJING_TZ)
        cutoff = now - timedelta(days=days)

        urls = extractor.load_rss_feeds()
        summarizer = extractor.get_summarizer() if use_ai else None

        if not summarizer:
            raise HTTPException(status_code=400, detail="流式输出需要启用AI总结 (use_ai=true)")

        return StreamingResponse(
            generate_stream(urls, limit, cutoff, last_fetch, summarizer),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )
    except Exception as e:
        logger.error(f"流式获取RSS失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/rss/feeds", summary="获取配置的RSS源列表")
async def get_rss_feeds():
    try:
        urls = extractor.load_rss_feeds()
        return {"feeds": urls}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/rss/state", summary="获取增量更新状态")
async def get_state():
    state = extractor._load_state()
    return {
        "last_fetch": state.get("last_fetch"),
        "state_file": str(extractor.state_path)
    }

@app.post("/rss/state/reset", summary="重置增量状态")
async def reset_state():
    extractor._save_state({})
    return {"message": "状态已重置"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
