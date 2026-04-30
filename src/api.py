"""FastAPI application for RSS API."""

import asyncio
import hashlib
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Optional, Set
from xml.etree import ElementTree as ET

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .fetcher import Fetcher
from .feed_parser import FeedParser
from .models import RSSEntry, RSSResponse
from .state_manager import StateManager
from .summarizer import Summarizer
from .database import (
    store_article,
    get_db,
    batch_update_summaries,
    search_articles,
    get_feed_stats,
)
from .opml import generate_opml, import_feeds_to_config, parse_opml
from .recommender.api import router as recommender_router

logger = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))


# Factory functions for dependency injection
def get_state_manager() -> StateManager:
    return StateManager(settings.state_file)


def get_feed_parser() -> FeedParser:
    return FeedParser()


def get_fetcher() -> Fetcher:
    return app.state.fetcher


def get_summarizer() -> Optional[Summarizer]:
    try:
        return Summarizer()
    except ValueError:
        return None


class WSConnectionManager:
    """Manages WebSocket connections and broadcasting."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients."""
        async with self._lock:
            dead_connections = set()
            for connection in self.active_connections:
                try:
                    await connection.send_json(message)
                except Exception:
                    dead_connections.add(connection)
            for conn in dead_connections:
                self.active_connections.discard(conn)

    @property
    def client_count(self) -> int:
        return len(self.active_connections)


ws_manager = WSConnectionManager()

_watcher_running = False


async def rss_watcher_task():
    """Background task: periodically fetch new RSS entries and broadcast."""
    global _watcher_running
    _watcher_running = True
    fetcher = get_fetcher()
    feed_parser = get_feed_parser()
    state_manager = get_state_manager()

    while _watcher_running:
        try:
            urls = list(settings.rss_feeds.values())
            last_fetch = state_manager.last_fetch

            if last_fetch is None:
                last_fetch = datetime.now(BEIJING_TZ) - timedelta(days=settings.default_days)

            new_entries = []
            async for entry in fetcher.fetch_all(urls):
                entry_date = feed_parser.get_entry_date(entry)
                if entry_date and entry_date > last_fetch:
                    new_entries.append(entry)

            if new_entries:
                await ws_manager.broadcast({
                    "type": "new_entries",
                    "count": len(new_entries),
                    "entries": new_entries[:10]
                })
                state_manager.update_last_fetch()

            await asyncio.sleep(settings.polling_interval_seconds)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("Background watcher error: %s", e)
            await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager - startup and shutdown events."""
    global _watcher_running
    app.state.fetcher = Fetcher()
    _watcher_running = True
    watcher_task = asyncio.create_task(rss_watcher_task())
    try:
        yield
    finally:
        _watcher_running = False
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass
        await app.state.fetcher.close()


static_path = Path(__file__).parent.parent / "static"
app = FastAPI(
    title="RSS内容提取API",
    description="从RSS源提取和过滤内容的API服务",
    lifespan=lifespan,
)
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(recommender_router)


def format_entry(entry: dict, feed_parser: FeedParser) -> RSSEntry:
    """Convert raw entry dict to RSSEntry model."""
    try:
        entry_obj = SimpleNamespace(**entry) if isinstance(entry, dict) else entry
        entry_date = feed_parser.get_entry_date(entry_obj)
    except Exception as e:
        logger.debug("Failed to parse entry date: %s", e)
        entry_date = None
    return RSSEntry(
        title=entry.get("title", ""),
        link=entry.get("link", ""),
        summary=entry.get("summary", ""),
        date=entry_date.strftime("%Y-%m-%d %H:%M:%S (北京时间)") if entry_date else None,
        content=entry.get("content", ""),
        ai_summary=entry.get("ai_summary", ""),
    )


def save_entry_to_db(entry: dict, feed_parser: FeedParser):
    """Save an entry to the database."""
    try:
        entry_obj = SimpleNamespace(**entry) if isinstance(entry, dict) else entry
        entry_date = feed_parser.get_entry_date(entry_obj)
    except Exception as e:
        logger.debug("Failed to parse entry for DB: %s", e)
        entry_date = None
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
    except Exception as e:
        logger.warning("Failed to store article to DB: %s", e)


def _update_db_sync(entries: list):
    """Update DB with AI summaries using batch update."""
    summaries = []
    for entry in entries:
        article_id = hashlib.md5(entry.get("link", "").encode()).hexdigest()[:12]
        ai_summary = entry.get("ai_summary", "")
        if ai_summary and article_id:
            summaries.append({"id": article_id, "ai_summary": ai_summary})
    if summaries:
        try:
            batch_update_summaries(summaries)
        except Exception as e:
            logger.warning("Failed to batch update summaries: %s", e)


@app.get("/", summary="API根路径")
async def root():
    return {"message": "RSS内容提取API", "docs": "/docs"}


@app.get("/rss/entries", response_model=RSSResponse, summary="获取RSS内容")
async def get_rss_entries(
    days: int = Query(default=None, description="过滤最近几天的内容"),
    limit: int = Query(default=None, description="返回条目数量限制"),
    offset: int = Query(default=0, description="跳过的条目数"),
    incremental: bool = Query(default=False, description="是否启用增量更新"),
    use_ai: bool = Query(default=True, description="是否启用AI总结"),
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

    # Pipeline: fetch -> filter -> summarize -> format
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    results: list = []
    seen_links: set = set()
    summarizer_done = asyncio.Event()

    def _normalize_link(link: str) -> str:
        return link.strip().rstrip("/").lower()

    async def fetcher_worker():
        async for entry in fetcher.fetch_all(urls):
            link = entry.get("link", "")
            norm = _normalize_link(link)
            if not link or norm in seen_links:
                continue
            seen_links.add(norm)
            entry_date = feed_parser.get_entry_date(entry)
            if last_fetch:
                if entry_date and entry_date > last_fetch:
                    await queue.put(entry)
                    save_entry_to_db(entry, feed_parser)
            else:
                if entry_date and entry_date > cutoff:
                    await queue.put(entry)
                    save_entry_to_db(entry, feed_parser)
        await queue.put(None)

    async def summarizer_worker():
        batch: list = []
        while True:
            entry = await queue.get()
            if entry is None:
                if batch and summarizer:
                    summarized = await summarizer.summarize_batch(batch)
                    results.extend(summarized)
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

    if summarizer and use_ai:
        await asyncio.gather(fetcher_worker(), summarizer_worker())
    else:
        try:
            async for entry in fetcher.fetch_all(urls):
                entry_date = feed_parser.get_entry_date(entry)
                if last_fetch:
                    if entry_date and entry_date > last_fetch:
                        results.append(entry)
                        save_entry_to_db(entry, feed_parser)
                else:
                    if entry_date and entry_date > cutoff:
                        results.append(entry)
                        save_entry_to_db(entry, feed_parser)
                if limit and len(results) >= limit:
                    break
        except Exception as e:
            logger.warning("Fetch failed in non-AI path: %s", e)

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


@app.get("/rss/stream", summary="流式获取RSS内容")
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


@app.get("/rss/feeds", summary="获取配置的RSS源列表")
async def get_rss_feeds():
    return {"feeds": list(settings.rss_feeds.values())}


@app.get("/rss/state", summary="获取增量更新状态")
async def get_state(state_manager: StateManager = Depends(get_state_manager)):
    return {
        "last_fetch": state_manager.last_fetch,
        "state_file": str(settings.state_file),
    }


@app.post("/rss/state/reset", summary="重置增量状态")
async def reset_state(state_manager: StateManager = Depends(get_state_manager)):
    state_manager.reset()
    return {"message": "状态已重置"}


@app.get("/rss/search", summary="RSS文章搜索")
async def search_rss(
    q: str = Query(default=..., description="搜索关键词"),
    limit: int = Query(default=50, description="返回数量"),
    offset: int = Query(default=0, description="偏移量"),
):
    """Search articles by keyword in title, summary, content, and AI summaries."""
    entries = search_articles(q, limit=limit, offset=offset)
    return {"query": q, "total": len(entries), "entries": entries}


@app.get("/rss/feeds/health", summary="RSS源健康状态")
async def get_feeds_health(
    days: int = Query(default=7, description="统计最近几天的数据"),
):
    """Get per-feed health stats: article counts and latest fetch times."""
    stats = get_feed_stats(days=days)
    configured_urls = set(settings.rss_feeds.values())
    active_urls = set(stats.keys())

    return {
        "total_feeds": len(settings.rss_feeds),
        "active_feeds": len(active_urls),
        "inactive_feeds": len(configured_urls - active_urls),
        "inactive_feed_urls": list(configured_urls - active_urls),
        "feeds": stats,
    }


@app.post("/rss/feeds/import", summary="OPML导入")
async def import_opml(
    file: UploadFile = File(..., description="OPML文件 (.opml / .xml)"),
):
    """
    Import RSS feeds from an OPML file. Accepts .opml or .xml files
    exported from other RSS readers like Feedly, Inoreader, NetNewsWire.
    Returns count of added/skipped feeds.
    """
    if not file.filename or not file.filename.endswith((".opml", ".xml")):
        raise HTTPException(
            status_code=400,
            detail="请上传 .opml 或 .xml 文件",
        )

    try:
        content = await file.read()
        feeds = parse_opml(content)
        result = import_feeds_to_config(feeds)
    except ET.ParseError as e:
        raise HTTPException(
            status_code=400,
            detail=f"OPML 解析失败: {e}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"导入失败: {e}",
        )

    return {
        "message": f"导入完成: 新增 {result['added']} 条, 跳过 {result['skipped']} 条重复",
        **result,
    }


@app.get("/rss/feeds/export", summary="OPML导出")
async def export_opml():
    """
    Export all configured RSS feeds as an OPML file.
    Can be imported into Reeder, NetNewsWire, Feedly, Inoreader, etc.
    """
    if not settings.rss_feeds:
        raise HTTPException(status_code=404, detail="没有配置的RSS源")

    xml_content = generate_opml(settings.rss_feeds)
    return PlainTextResponse(
        content=xml_content,
        media_type="application/xml",
        headers={
            "Content-Disposition": 'attachment; filename="myrss_feeds.opml"'
        },
    )


@app.websocket("/ws/rss")
async def websocket_rss(websocket: WebSocket):
    """WebSocket endpoint for real-time RSS updates."""
    await ws_manager.connect(websocket)
    try:
        await websocket.send_json({
            "type": "connected",
            "message": "WebSocket connected. Waiting for RSS updates...",
            "client_count": ws_manager.client_count
        })

        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                elif msg.get("type") == "fetch":
                    await _trigger_fetch_and_broadcast()
                    await websocket.send_json({"type": "ack", "message": "Fetch completed"})
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)


async def _trigger_fetch_and_broadcast():
    """Background task: fetch RSS and broadcast new entries."""
    urls = list(settings.rss_feeds.values())
    state_manager = get_state_manager()
    feed_parser = get_feed_parser()
    fetcher = get_fetcher()
    summarizer = get_summarizer()

    last_fetch = state_manager.last_fetch
    cutoff = last_fetch
    if cutoff is None:
        cutoff = datetime.now(BEIJING_TZ) - timedelta(days=settings.default_days)

    await ws_manager.broadcast({
        "type": "fetch_started",
        "message": f"Fetching {len(urls)} RSS sources...",
        "total_sources": len(urls)
    })

    fetched_entries = []
    async for entry in fetcher.fetch_all(urls):
        entry_date = feed_parser.get_entry_date(entry)
        if entry_date and entry_date > cutoff:
            fetched_entries.append(entry)

    if fetched_entries:
        await ws_manager.broadcast({
            "type": "new_entries",
            "count": len(fetched_entries),
            "entries": fetched_entries[:10]
        })
        if summarizer:
            batch = fetched_entries[:10]
            summarized = await summarizer.summarize_batch(batch)
            for s in summarized:
                await ws_manager.broadcast({
                    "type": "summarized_entry",
                    "data": format_entry(s, feed_parser).model_dump()
                })

    await ws_manager.broadcast({
        "type": "fetch_completed",
        "count": len(fetched_entries)
    })
    state_manager.update_last_fetch()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
