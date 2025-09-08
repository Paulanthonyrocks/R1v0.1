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
        """Adds a new WebSocket connection to the pool."""
        try:
            # The connection is now accepted in the endpoint handler.
            if stream_id not in self.active_connections:
                self.active_connections[stream_id] = set()
            self.active_connections[stream_id].add(websocket)
            logger.info(f"WebSocket connected for stream_id: {stream_id}")
        except Exception as e:
            logger.error(
                f"Failed to accept/connect websocket for stream_id {stream_id}: {e}"
            )
            try:
                await websocket.close(code=1011, reason="Failed to accept connection")
            except Exception as close_err:
                logger.error(
                    f"Error closing websocket after failed connect: {close_err}"
                )

    def disconnect(self, websocket: WebSocket, stream_id: str):
        """Removes a WebSocket connection from the pool."""
        try:
            if stream_id in self.active_connections:
                self.active_connections[stream_id].remove(websocket)
                if not self.active_connections[stream_id]:
                    del self.active_connections[stream_id]
            logger.info(f"WebSocket disconnected for stream_id: {stream_id}")
        except Exception as e:
            logger.error(
                f"Error during websocket disconnect for stream_id {stream_id}: {e}"
            )

    async def broadcast(self, stream_id: str, message: Dict):
        """Broadcasts a JSON message to all connected clients for a given stream."""
        if stream_id in self.active_connections:
            tasks = [ws.send_json(message) for ws in self.active_connections[stream_id]]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Error broadcasting message to websocket for stream_id {stream_id}: {result}", exc_info=False)

    async def broadcast_bytes(self, stream_id: str, payload: bytes):
        """Broadcasts a binary message to all connected clients for a given stream."""
        if stream_id in self.active_connections:
            tasks = [ws.send_bytes(payload) for ws in self.active_connections[stream_id]]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Error broadcasting bytes to websocket for stream_id {stream_id}: {result}", exc_info=False)


# Singleton instance
video_ws_manager = VideoWSManager()
