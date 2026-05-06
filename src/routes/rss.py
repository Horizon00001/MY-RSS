"""RSS HTTP routes."""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..article_identity import normalize_article_link
from ..compat import api_attr
from ..config import settings
from ..database import (
    get_article_by_link,
    get_feed_stats,
    list_feed_statuses,
    list_recent_articles,
    search_articles,
)
from ..dependencies import get_feed_parser, get_fetcher, get_state_manager, get_summarizer
from ..feed_parser import FeedParser
from ..fetcher import Fetcher
from ..formatting import BEIJING_TZ, format_db_article, format_entry
from ..models import RSSEntry, RSSResponse
from ..rss_service import (
    _update_db_sync,
    refresh_rss_entries_once,
    save_entries_to_db,
    summarize_missing_articles,
)
from ..state_manager import StateManager
from ..summarizer import Summarizer

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/", summary="API根路径")
async def root():
    return {"message": "RSS内容提取API", "docs": "/docs"}


@router.get("/rss/entries", response_model=RSSResponse, summary="获取RSS内容")
async def get_rss_entries(
    days: int = Query(default=None, description="过滤最近几天的内容"),
    limit: int = Query(default=None, description="返回条目数量限制"),
    offset: int = Query(default=0, description="跳过的条目数"),
    incremental: bool = Query(default=False, description="是否启用增量更新"),
    use_ai: bool = Query(default=False, description="是否启用AI总结"),
    state_manager: StateManager = Depends(get_state_manager),
    feed_parser: FeedParser = Depends(get_feed_parser),
    fetcher: Fetcher = Depends(get_fetcher),
    summarizer: Optional[Summarizer] = Depends(get_summarizer),
):
    last_fetch = state_manager.last_fetch if incremental else None
    is_incremental = incremental and last_fetch is not None

    if days is None:
        days = settings.default_days
    now = datetime.now(BEIJING_TZ)
    cutoff = now - timedelta(days=days)

    urls = list(settings.rss_feeds.values())
    target_count = offset + limit if limit else None

    # Pipeline: fetch -> filter -> summarize -> format
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    results: list = []
    seen_links: set = set()
    entries_to_store: list = []
    summarizer_done = asyncio.Event()
    stop_fetching = asyncio.Event()
    queued_count = 0

    async def fetcher_worker():
        nonlocal queued_count
        async for entry in fetcher.fetch_all(urls):
            if stop_fetching.is_set():
                break
            link = entry.get("link", "")
            norm = normalize_article_link(link)
            if not link or norm in seen_links:
                continue
            seen_links.add(norm)
            entry_date = feed_parser.get_entry_date(entry)
            if last_fetch:
                if entry_date and entry_date > last_fetch:
                    await queue.put(entry)
                    queued_count += 1
                    entries_to_store.append(entry)
                    if target_count and queued_count >= target_count:
                        stop_fetching.set()
            else:
                if entry_date and entry_date > cutoff:
                    await queue.put(entry)
                    queued_count += 1
                    entries_to_store.append(entry)
                    if target_count and queued_count >= target_count:
                        stop_fetching.set()
        await queue.put(None)

    async def summarizer_worker():
        batch: list = []
        batch_size = min(10, target_count) if target_count else 10
        while True:
            entry = await queue.get()
            if entry is None:
                if batch and summarizer:
                    summarized = await summarizer.summarize_batch(batch)
                    results.extend(summarized)
                    await asyncio.to_thread(api_attr("_update_db_sync", _update_db_sync), summarized)
                summarizer_done.set()
                break
            batch.append(entry)
            if len(batch) >= batch_size:
                if summarizer:
                    summarized = await summarizer.summarize_batch(batch)
                    results.extend(summarized)
                    await asyncio.to_thread(api_attr("_update_db_sync", _update_db_sync), summarized)
                else:
                    results.extend(batch)
                batch = []
                if target_count and len(results) >= target_count:
                    stop_fetching.set()
                    summarizer_done.set()
                    break
        if batch and not summarizer:
            results.extend(batch)

    if summarizer and use_ai:
        fetch_task = asyncio.create_task(fetcher_worker())
        summarize_task = asyncio.create_task(summarizer_worker())
        await summarize_task
        if fetch_task.done():
            await fetch_task
        else:
            stop_fetching.set()
            fetch_task.cancel()
            try:
                await fetch_task
            except asyncio.CancelledError:
                pass
    else:
        try:
            async for entry in fetcher.fetch_all(urls):
                link = entry.get("link", "")
                norm = normalize_article_link(link)
                if not link or norm in seen_links:
                    continue
                seen_links.add(norm)
                entry_date = feed_parser.get_entry_date(entry)
                if last_fetch:
                    if entry_date and entry_date > last_fetch:
                        results.append(entry)
                        entries_to_store.append(entry)
                else:
                    if entry_date and entry_date > cutoff:
                        results.append(entry)
                        entries_to_store.append(entry)
                if limit and len(results) >= limit:
                    break
        except Exception as e:
            logger.warning("Fetch failed in non-AI path: %s", e)

    api_attr("save_entries_to_db", save_entries_to_db)(entries_to_store, feed_parser)

    if limit:
        results = results[offset:offset + limit]
    elif offset:
        results = results[offset:]

    formatted = [format_entry(e, feed_parser) for e in results]
    last_fetch_str = state_manager.update_last_fetch()

    return RSSResponse(
        total=len(formatted),
        entries=formatted,
        incremental=is_incremental,
        last_fetch=last_fetch_str if is_incremental else None,
    )

@router.post("/rss/refresh", summary="后台刷新RSS")
async def refresh_rss(background_tasks: BackgroundTasks):
    """Trigger a non-AI RSS refresh in the background."""
    background_tasks.add_task(api_attr("refresh_rss_entries_once", refresh_rss_entries_once))
    return {"message": "RSS 刷新已开始", "use_ai": False}


@router.post("/rss/summarize-missing", summary="后台补齐AI摘要")
async def summarize_missing(background_tasks: BackgroundTasks, limit: int = Query(default=5, ge=1, le=20)):
    """Trigger background summarization for stored articles missing AI summaries."""
    background_tasks.add_task(api_attr("summarize_missing_articles", summarize_missing_articles), limit)
    return {"message": "AI 摘要补齐已开始", "limit": limit}

@router.get("/rss/stream", summary="流式获取RSS内容")
async def stream_rss_entries(
    days: int = Query(default=None, description="过滤最近几天的内容"),
    limit: int = Query(default=10, description="返回条目数量限制"),
    incremental: bool = Query(default=False, description="是否启用增量更新"),
    use_ai: bool = Query(default=True, description="是否启用AI总结"),
    state_manager: StateManager = Depends(get_state_manager),
    feed_parser: FeedParser = Depends(get_feed_parser),
    fetcher: Fetcher = Depends(get_fetcher),
    summarizer: Optional[Summarizer] = Depends(get_summarizer),
):
    last_fetch = state_manager.last_fetch if incremental else None

    if days is None:
        days = settings.default_days
    now = datetime.now(BEIJING_TZ)
    cutoff = now - timedelta(days=days)

    urls = list(settings.rss_feeds.values())

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
                            yield f"data: {json.dumps(format_entry(s, feed_parser).model_dump(), ensure_ascii=False)}\n\n"
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
                        yield f"data: {json.dumps(format_entry(s, feed_parser).model_dump(), ensure_ascii=False)}\n\n"
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


@router.get("/rss/feeds", summary="获取配置的RSS源列表")
async def get_rss_feeds():
    return {"feeds": list(settings.rss_feeds.values())}


@router.get("/rss/articles", response_model=RSSResponse, summary="获取本地文章")
async def get_local_articles(
    days: int = Query(default=30, ge=1, le=365, description="读取最近几天的本地文章"),
    limit: int = Query(default=20, ge=1, le=100, description="返回条目数量限制"),
    offset: int = Query(default=0, ge=0, description="跳过的条目数"),
):
    """Return articles already stored in SQLite without fetching RSS or calling AI."""
    articles = api_attr("list_recent_articles", list_recent_articles)(limit=limit, offset=offset, days=days)
    entries = [format_db_article(article) for article in articles]
    return RSSResponse(total=len(entries), entries=entries)


@router.get("/rss/article", response_model=RSSEntry, summary="获取单篇本地文章")
async def get_local_article(link: str = Query(default=..., description="文章链接")):
    """Return one stored article by original or normalized link."""
    article = api_attr("get_article_by_link", get_article_by_link)(normalize_article_link(link)) or api_attr("get_article_by_link", get_article_by_link)(link)
    if not article:
        raise HTTPException(status_code=404, detail="文章未找到")
    return format_db_article(article)


@router.get("/rss/state", summary="获取增量更新状态")
async def get_state(state_manager: StateManager = Depends(get_state_manager)):
    return {
        "last_fetch": state_manager.last_fetch,
        "state_file": str(settings.state_file),
    }


@router.post("/rss/state/reset", summary="重置增量状态")
async def reset_state(state_manager: StateManager = Depends(get_state_manager)):
    state_manager.reset()
    return {"message": "状态已重置"}


@router.get("/rss/search", summary="RSS文章搜索")
async def search_rss(
    q: str = Query(default=..., description="搜索关键词"),
    limit: int = Query(default=50, description="返回数量"),
    offset: int = Query(default=0, description="偏移量"),
):
    """Search articles by keyword in title, summary, content, and AI summaries."""
    entries = api_attr("search_articles", search_articles)(q, limit=limit, offset=offset)
    return {"query": q, "total": len(entries), "entries": entries}


@router.get("/rss/feeds/health", summary="RSS源健康状态")
async def get_feeds_health(
    days: int = Query(default=7, description="统计最近几天的数据"),
):
    """Get per-feed health stats: article counts and latest fetch times."""
    stats = api_attr("get_feed_stats", get_feed_stats)(days=days)
    statuses = api_attr("list_feed_statuses", list_feed_statuses)()
    configured_urls = set(settings.rss_feeds.values())
    active_urls = set(stats.keys())

    feeds = {}
    for url in configured_urls:
        article_stats = stats.get(url, {})
        status = statuses.get(url, {})
        feeds[url] = {
            "source_name": article_stats.get("source_name") or url,
            "count": article_stats.get("count", 0),
            "latest": article_stats.get("latest"),
            "last_status_code": status.get("last_status_code"),
            "last_success_at": status.get("last_success_at"),
            "last_error_at": status.get("last_error_at"),
            "last_error": status.get("last_error"),
            "consecutive_failures": status.get("consecutive_failures", 0),
            "average_fetch_ms": status.get("average_fetch_ms"),
            "cache_enabled": bool(status.get("etag") or status.get("last_modified")),
        }

    return {
        "total_feeds": len(settings.rss_feeds),
        "active_feeds": len(active_urls),
        "inactive_feeds": len(configured_urls - active_urls),
        "inactive_feed_urls": list(configured_urls - active_urls),
        "feeds": feeds,
    }
