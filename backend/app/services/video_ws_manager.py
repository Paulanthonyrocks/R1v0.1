import asyncio
import logging
from typing import Dict, Set, List

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class VideoWSManager:
    """Manages WebSocket connections for video streams."""

    def __init__(self, max_connections_per_stream: int = 100):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}  # Add locks per stream
        self._global_lock = asyncio.Lock()  # For lock creation
        self.max_connections_per_stream = max_connections_per_stream

    async def connect(self, websocket: WebSocket, stream_id: str):
        """Adds a new WebSocket connection to the pool."""
        try:
            # Get or create lock for this stream
            async with self._global_lock:
                if stream_id not in self._locks:
                    self._locks[stream_id] = asyncio.Lock()
            
            # Check limit BEFORE acquiring stream lock (quick check)
            current_count = self.get_connection_count(stream_id)
            if current_count >= self.max_connections_per_stream:
                logger.warning(
                    f"Connection limit reached for stream {stream_id}: "
                    f"{current_count}/{self.max_connections_per_stream}"
                )
                await websocket.close(code=1008, reason="Connection limit reached")
                return

            async with self._locks[stream_id]:
                # Double-check after acquiring lock (TOCTOU protection)
                if stream_id not in self.active_connections:
                    self.active_connections[stream_id] = set()
                
                if len(self.active_connections[stream_id]) >= self.max_connections_per_stream:
                    logger.warning(f"Connection limit reached (race) for stream {stream_id}")
                    await websocket.close(code=1008, reason="Connection limit reached")
                    return
                
                self.active_connections[stream_id].add(websocket)
            
            logger.info(
                f"WebSocket connected for stream_id: {stream_id} "
                f"({len(self.active_connections[stream_id])} total)"
            )
        except Exception as e:
            logger.error(f"Failed to connect websocket for stream_id {stream_id}: {e}")
            try:
                await websocket.close(code=1011, reason="Failed to accept connection")
            except Exception as close_err:
                logger.error(f"Error closing websocket after failed connect: {close_err}")

    def disconnect(self, websocket: WebSocket, stream_id: str):
        """Removes a WebSocket connection from the pool."""
        try:
            if stream_id in self.active_connections:
                self.active_connections[stream_id].discard(websocket)
                if not self.active_connections[stream_id]:
                    del self.active_connections[stream_id]
                    # Keep locks to avoid frequent creation/deletion
            logger.info(f"WebSocket disconnected for stream_id: {stream_id}")
        except Exception as e:
            logger.error(
                f"Error during websocket disconnect for stream_id {stream_id}: {e}"
            )

    async def broadcast(self, stream_id: str, message: Dict) -> Dict[str, int]:
        """Broadcasts a JSON message to all connected clients for a given stream."""
        if stream_id not in self.active_connections:
            return {"sent": 0, "failed": 0, "total": 0}
        
        dead_connections = set()
        websockets = list(self.active_connections[stream_id])
        sent_count = 0
        failed_count = 0
        
        for ws in websockets:
            try:
                # Check WebSocket state
                if ws.client_state.name == "DISCONNECTED":
                    dead_connections.add(ws)
                    failed_count += 1
                    continue
                
                await ws.send_json(message)
                sent_count += 1
            except Exception as e:
                logger.warning(
                    f"Failed to send to websocket for stream {stream_id}: {e}"
                )
                dead_connections.add(ws)
                failed_count += 1
        
        # Clean up dead connections
        for ws in dead_connections:
            self.disconnect(ws, stream_id)
            
        return {
            "sent": sent_count,
            "failed": failed_count,
            "total": len(websockets)
        }

    async def broadcast_bytes(self, stream_id: str, payload: bytes) -> Dict[str, int]:
        """Broadcasts a binary message to all connected clients for a given stream."""
        if stream_id not in self.active_connections:
            return {"sent": 0, "failed": 0, "total": 0}
        
        dead_connections = set()
        websockets = list(self.active_connections[stream_id])
        sent_count = 0
        failed_count = 0
        
        for ws in websockets:
            try:
                if ws.client_state.name == "DISCONNECTED":
                    dead_connections.add(ws)
                    failed_count += 1
                    continue
                
                await ws.send_bytes(payload)
                sent_count += 1
            except Exception as e:
                logger.warning(
                    f"Failed to send bytes to websocket for stream {stream_id}: {e}"
                )
                dead_connections.add(ws)
                failed_count += 1
        
        # Clean up dead connections
        for ws in dead_connections:
            self.disconnect(ws, stream_id)
            
        return {
            "sent": sent_count,
            "failed": failed_count,
            "total": len(websockets)
        }

    def get_connection_count(self, stream_id: str) -> int:
        """Returns the number of active connections for a stream."""
        return len(self.active_connections.get(stream_id, set()))

    def get_all_stream_ids(self) -> Set[str]:
        """Returns all active stream IDs."""
        return set(self.active_connections.keys())


# Singleton instance
video_ws_manager = VideoWSManager()