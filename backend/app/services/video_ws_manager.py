
import asyncio
import logging
from typing import Dict, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)

class VideoWSManager:
    """Manages WebSocket connections for video streams."""

    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, stream_id: str):
        """Accepts a new WebSocket connection and adds it to the pool."""
        await websocket.accept()
        if stream_id not in self.active_connections:
            self.active_connections[stream_id] = set()
        self.active_connections[stream_id].add(websocket)
        logger.info(f"WebSocket connected for stream_id: {stream_id}")

    def disconnect(self, websocket: WebSocket, stream_id: str):
        """Removes a WebSocket connection from the pool."""
        if stream_id in self.active_connections:
            self.active_connections[stream_id].remove(websocket)
            if not self.active_connections[stream_id]:
                del self.active_connections[stream_id]
        logger.info(f"WebSocket disconnected for stream_id: {stream_id}")

    async def broadcast_kpis(self, stream_id: str, kpis: Dict):
        """Broadcasts KPIs to all connected clients for a given stream."""
        if stream_id in self.active_connections:
            message = {"type": "kpi_update", "data": kpis}
            # Create a list of tasks to send messages concurrently
            tasks = [
                ws.send_json(message) for ws in self.active_connections[stream_id]
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

# Singleton instance
video_ws_manager = VideoWSManager()
