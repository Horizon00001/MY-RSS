"""Compatibility facade for the FastAPI RSS API.

The implementation is split across smaller modules, while this module keeps
legacy imports and test patch paths stable.
"""

from .app_setup import create_app, lifespan, rss_watcher_task
from .article_identity import normalize_article_link
from .config import settings
from .database import (
    get_article_by_link,
    get_feed_stats,
    list_feed_statuses,
    list_recent_articles,
    search_articles,
    set_article_read_state,
)
from .dependencies import get_feed_parser, get_fetcher, get_state_manager, get_summarizer
from .formatting import (
    BEIJING_TZ,
    entry_text,
    entry_to_article_row,
    format_db_article,
    format_entry,
)
from .rss_service import (
    _update_db_sync,
    refresh_rss_entries_once,
    save_entries_to_db,
    save_entry_to_db,
    summarize_missing_articles,
)
from .routes.opml import export_opml, import_opml
from .routes.rss import (
    get_feeds_health,
    get_local_article,
    get_local_articles,
    get_rss_entries,
    get_rss_feeds,
    get_state,
    refresh_rss,
    reset_state,
    root,
    search_rss,
    stream_rss_entries,
    summarize_missing,
    update_article_read_state,
)
from .routes.websocket import _trigger_fetch_and_broadcast, websocket_rss
from .websocket_manager import WSConnectionManager, ws_manager

app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
