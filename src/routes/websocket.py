"""WebSocket routes for RSS updates."""

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..compat import api_attr
from ..config import settings
from ..dependencies import get_feed_parser, get_fetcher, get_state_manager, get_summarizer
from ..formatting import BEIJING_TZ, format_entry
from ..websocket_manager import ws_manager

router = APIRouter()

@router.websocket("/ws/rss")
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
    state_manager = api_attr("get_state_manager", get_state_manager)()
    feed_parser = api_attr("get_feed_parser", get_feed_parser)()
    fetcher = api_attr("get_fetcher", get_fetcher)()
    summarizer = api_attr("get_summarizer", get_summarizer)()

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
