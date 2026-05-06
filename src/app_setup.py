"""FastAPI application setup for MY-RSS."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .dependencies import get_feed_parser, get_fetcher, get_state_manager, set_app
from .fetcher import Fetcher
from .formatting import BEIJING_TZ
from .routes.opml import router as opml_router
from .routes.rss import router as rss_router
from .routes.websocket import router as websocket_router
from .websocket_manager import ws_manager

try:
    from .recommender.api import router as recommender_router
except ImportError as exc:
    recommender_router = None
    recommender_import_error = exc
else:
    recommender_import_error = None

logger = logging.getLogger(__name__)
_watcher_running = False


async def rss_watcher_task(app: FastAPI | None = None):
    """Background task: periodically fetch new RSS entries and broadcast."""
    global _watcher_running
    _watcher_running = True
    fetcher = app.state.fetcher if app is not None else get_fetcher()
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
    watcher_task = asyncio.create_task(rss_watcher_task(app))
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


def create_app() -> FastAPI:
    static_path = Path(__file__).parent.parent / "static"
    app = FastAPI(
        title="RSS内容提取API",
        description="从RSS源提取和过滤内容的API服务",
        lifespan=lifespan,
    )
    set_app(app)

    if static_path.exists():
        app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(rss_router)
    app.include_router(opml_router)
    app.include_router(websocket_router)

    if recommender_router is not None:
        app.include_router(recommender_router)
    else:
        logger.warning("Recommendation router unavailable: %s", recommender_import_error)

    return app
