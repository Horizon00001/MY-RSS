"""WebSocket connection management for RSS updates."""

import asyncio
from typing import Set

from fastapi import WebSocket


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
            connections = list(self.active_connections)

        async def send_one(connection: WebSocket):
            try:
                await connection.send_json(message)
                return None
            except Exception:
                return connection

        dead_connections = {
            connection
            for connection in await asyncio.gather(
                *(send_one(connection) for connection in connections)
            )
            if connection is not None
        }

        if dead_connections:
            async with self._lock:
                self.active_connections.difference_update(dead_connections)

    @property
    def client_count(self) -> int:
        return len(self.active_connections)


ws_manager = WSConnectionManager()
