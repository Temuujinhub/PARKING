"""WebSocket холболтын менежер — dashboard, касс, PAX терминалд real-time event түгээнэ."""
import asyncio
import json
from datetime import datetime

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        # site_id -> set(WebSocket); "*" = бүх site-ийн event сонсогчид
        self.connections: dict[str, set[WebSocket]] = {}
        self.lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, site_id: str = "*"):
        await ws.accept()
        async with self.lock:
            self.connections.setdefault(site_id, set()).add(ws)

    async def disconnect(self, ws: WebSocket, site_id: str = "*"):
        async with self.lock:
            self.connections.get(site_id, set()).discard(ws)

    async def broadcast(self, site_id: str, event_type: str, data: dict):
        message = json.dumps(
            {"type": event_type, "site_id": site_id, "ts": datetime.utcnow().isoformat(), "data": data},
            ensure_ascii=False, default=str,
        )
        targets = list(self.connections.get(site_id, set()) | self.connections.get("*", set()))
        for ws in targets:
            try:
                await ws.send_text(message)
            except Exception:
                for group in self.connections.values():
                    group.discard(ws)


manager = ConnectionManager()


def broadcast_sync(site_id: str, event_type: str, data: dict):
    """Sync код (router) дотроос дуудахад event loop руу даалгана."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(manager.broadcast(site_id, event_type, data))
    except RuntimeError:
        pass
