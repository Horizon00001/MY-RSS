"""FastAPI application for RSS API."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

from .config import settings
from .fetcher import Fetcher
from .feed_parser import FeedParser
from .models import RSSEntry, RSSResponse
from .state_manager import StateManager
from .summarizer import Summarizer
from .database import store_article, get_db
from .recommender.api import router as recommender_router

BEIJING_TZ = timezone(timedelta(hours=8))

app = FastAPI(title="RSS内容提取API", description="从RSS源提取和过滤内容的API服务")

# Include recommendation routes
app.include_router(recommender_router)

# Global instances
state_manager = StateManager(settings.state_file)
feed_parser = FeedParser()
fetcher = Fetcher()


def get_summarizer() -> Optional[Summarizer]:
    """Get or create summarizer instance."""
    try:
        return Summarizer()
    except ValueError:
        return None


def format_entry(entry: dict) -> RSSEntry:
    """Convert raw entry dict to RSSEntry model."""
    entry_date = feed_parser.get_entry_date(type("E", (), entry)())
    return RSSEntry(
        title=entry.get("title", ""),
        link=entry.get("link", ""),
        summary=entry.get("summary", ""),
        date=entry_date.strftime("%Y-%m-%d %H:%M:%S (北京时间)") if entry_date else None,
        content=entry.get("content", ""),
        ai_summary=entry.get("ai_summary", ""),
    )


def save_entry_to_db(entry: dict):
    """Save an entry to the database."""
    import hashlib

    entry_date = feed_parser.get_entry_date(type("E", (), entry)())
    article_id = hashlib.md5(entry.get("link", "").encode()).hexdigest()[:12]

    try:
        store_article(
            article_id=article_id,
            title=entry.get("title", ""),
            link=entry.get("link", ""),
            summary=entry.get("summary", ""),
            content=entry.get("content", ""),
            source=entry.get("source", ""),
            source_name=entry.get("feed_title", ""),
            published_at=entry_date,
            tags=[],
        )
    except Exception:
        pass  # Silently fail if DB is not available


def _update_db_sync(entries: list):
    """Update DB with AI summaries synchronously."""
    import hashlib
    for entry in entries:
        try:
            article_id = hashlib.md5(entry.get("link", "").encode()).hexdigest()[:12]
            ai_summary = entry.get("ai_summary", "")
            if ai_summary:
                with get_db().get_cursor() as cursor:
                    cursor.execute(
                        "UPDATE articles SET ai_summary = ? WHERE id = ?",
                        (ai_summary, article_id),
                    )
        except Exception:
            pass


@app.get("/", summary="API根路径")
async def root():
    return {"message": "RSS内容提取API", "docs": "/docs"}


@app.get("/rss/entries", response_model=RSSResponse, summary="获取RSS内容")
async def get_rss_entries(
    days: int = Query(default=None, description="过滤最近几天的内容"),
    limit: int = Query(default=None, description="返回条目数量限制"),
    incremental: bool = Query(default=False, description="是否启用增量更新"),
    use_ai: bool = Query(default=True, description="是否启用AI总结"),
):
    last_fetch = state_manager.last_fetch if incremental else None
    is_incremental = incremental and last_fetch is not None

    if days is None:
        days = settings.default_days
    now = datetime.now(BEIJING_TZ)
    cutoff = now - timedelta(days=days)

    urls = list(settings.rss_feeds.values())
    summarizer = get_summarizer() if use_ai else None

    # Pipeline: fetch -> filter -> summarize -> format
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    results: list = []
    summarizer_done = asyncio.Event()

    async def fetcher_worker():
        async for entry in fetcher.fetch_all(urls):
            entry_date = feed_parser.get_entry_date(entry)
            if last_fetch:
                if entry_date and entry_date > last_fetch:
                    await queue.put(entry)
                    save_entry_to_db(entry)
            else:
                if entry_date and entry_date > cutoff:
                    await queue.put(entry)
                    save_entry_to_db(entry)
        await queue.put(None)

    async def summarizer_worker():
        batch: list = []
        while True:
            entry = await queue.get()
            if entry is None:
                if batch and summarizer:
                    summarized = await summarizer.summarize_batch(batch)
                    results.extend(summarized)
                    # Update DB with AI summaries in thread pool
                    await asyncio.to_thread(_update_db_sync, summarized)
                summarizer_done.set()
                break
            batch.append(entry)
            if len(batch) >= 10:
                if summarizer:
                    summarized = await summarizer.summarize_batch(batch)
                    results.extend(summarized)
                    await asyncio.to_thread(_update_db_sync, summarized)
                else:
                    results.extend(batch)
                batch = []
                if limit and len(results) >= limit:
                    summarizer_done.set()
                    break
        if batch and not summarizer:
            results.extend(batch)

    if summarizer:
        await asyncio.gather(fetcher_worker(), summarizer_worker())
    else:
        async for entry in fetcher.fetch_all(urls):
            entry_date = feed_parser.get_entry_date(entry)
            if last_fetch:
                if entry_date and entry_date > last_fetch:
                    results.append(entry)
                    save_entry_to_db(entry)
            else:
                if entry_date and entry_date > cutoff:
                    results.append(entry)
                    save_entry_to_db(entry)
            if limit and len(results) >= limit:
                break

    if limit:
        results = results[:limit]

    formatted = [format_entry(e) for e in results]
    last_fetch_str = state_manager.update_last_fetch()

    return RSSResponse(
        total=len(formatted),
        entries=formatted,
        incremental=is_incremental,
        last_fetch=last_fetch_str if is_incremental else None,
    )


@app.get("/rss/stream", summary="流式获取RSS内容")
async def stream_rss_entries(
    days: int = Query(default=None, description="过滤最近几天的内容"),
    limit: int = Query(default=10, description="返回条目数量限制"),
    incremental: bool = Query(default=False, description="是否启用增量更新"),
    use_ai: bool = Query(default=True, description="是否启用AI总结"),
):
    last_fetch = state_manager.last_fetch if incremental else None

    if days is None:
        days = settings.default_days
    now = datetime.now(BEIJING_TZ)
    cutoff = now - timedelta(days=days)

    urls = list(settings.rss_feeds.values())
    summarizer = get_summarizer()

    if use_ai and not summarizer:
        raise HTTPException(status_code=400, detail="AI summarizer not available")

    async def generate():
        queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        yielded = 0

        async def fetcher_worker():
            async for entry in fetcher.fetch_all(urls):
                entry_date = feed_parser.get_entry_date(entry)
                if last_fetch:
                    if entry_date and entry_date > last_fetch:
                        await queue.put(entry)
                else:
                    if entry_date and entry_date > cutoff:
                        await queue.put(entry)
            await queue.put(None)

        fetcher_task = asyncio.create_task(fetcher_worker())
        batch: list = []

        try:
            while yielded < limit:
                entry = await queue.get()
                if entry is None:
                    if batch and summarizer:
                        summarized = await summarizer.summarize_batch(batch)
                        for s in summarized:
                            if yielded >= limit:
                                break
                            yield f"data: {json.dumps(format_entry(s).model_dump(), ensure_ascii=False)}\n\n"
                            yielded += 1
                    break
                batch.append(entry)
                if len(batch) >= 3:
                    to_summarize = batch
                    batch = []
                    if summarizer:
                        summarized = await summarizer.summarize_batch(to_summarize)
                        to_summarize = summarized
                    for s in to_summarize:
                        if yielded >= limit:
                            break
                        yield f"data: {json.dumps(format_entry(s).model_dump(), ensure_ascii=False)}\n\n"
                        yielded += 1
        finally:
            fetcher_task.cancel()
            try:
                await fetcher_task
            except asyncio.CancelledError:
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/rss/feeds", summary="获取配置的RSS源列表")
async def get_rss_feeds():
    return {"feeds": list(settings.rss_feeds.values())}


@app.get("/rss/state", summary="获取增量更新状态")
async def get_state():
    return {
        "last_fetch": state_manager.last_fetch,
        "state_file": str(settings.state_file),
    }


@app.post("/rss/state/reset", summary="重置增量状态")
async def reset_state():
    state_manager.reset()
    return {"message": "状态已重置"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
