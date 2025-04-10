# /content/drive/MyDrive/R1v0.1/backend/app/websocket/connection_manager.py

import asyncio
import logging
from typing import List, Dict, Any, Set
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

class ConnectionManager:
    """Manages active WebSocket connections."""
    def __init__(self):
        # Use a set for potentially faster add/remove/check operations
        self.active_connections: Set[WebSocket] = set()
        # Use an asyncio Lock to protect access to the active_connections set
        self._lock = asyncio.Lock()
        logger.info("ConnectionManager initialized.")

    async def connect(self, websocket: WebSocket):
        """Accepts a new WebSocket connection and adds it to the active set."""
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)
        client_host = websocket.client.host if websocket.client else "Unknown"
        client_port = websocket.client.port if websocket.client else "Unknown"
        logger.info(f"New WebSocket connection from {client_host}:{client_port} ({len(self.active_connections)} total)")

    async def disconnect(self, websocket: WebSocket):
        """Removes a WebSocket connection from the active set."""
        async with self._lock:
            # Remove the websocket if it's present
            self.active_connections.discard(websocket)
        client_host = websocket.client.host if websocket.client else "Unknown"
        client_port = websocket.client.port if websocket.client else "Unknown"
        logger.info(f"WebSocket connection closed for {client_host}:{client_port} ({len(self.active_connections)} remaining)")
        # Note: We don't explicitly close the websocket here,
        # as this method is usually called *after* a disconnect occurs.

    async def send_personal_message(self, message: Dict[str, Any], websocket: WebSocket):
        """Sends a JSON message to a specific WebSocket connection."""
        # Check if the connection is still considered active before sending
        async with self._lock:
             if websocket not in self.active_connections:
                  logger.warning(f"Attempted to send message to inactive/disconnected websocket: {websocket.client}")
                  return # Don't try to send
        try:
            await websocket.send_json(message)
            # logger.debug(f"Sent personal message to {websocket.client}: {message.get('type')}")
        except (WebSocketDisconnect, RuntimeError) as e: # Catch potential errors during send
            logger.warning(f"Failed to send personal message to {websocket.client} (likely disconnected): {e}. Removing.")
            await self.disconnect(websocket) # Remove connection on send failure
        except Exception as e:
            logger.error(f"Unexpected error sending personal message to {websocket.client}: {e}", exc_info=True)
            await self.disconnect(websocket) # Remove connection on other errors too

    async def broadcast(self, message: Dict[str, Any]):
        """Sends a JSON message to all currently active WebSocket connections."""
        async with self._lock:
            # Create a snapshot of connections within the lock
            connections_to_send = list(self.active_connections)

        if not connections_to_send:
            return # No one to send to

        logger.debug(f"Broadcasting message type '{message.get('type')}' to {len(connections_to_send)} client(s).")

        # Send messages concurrently
        results = await asyncio.gather(
            *(self._safe_send_json(websocket, message) for websocket in connections_to_send),
            return_exceptions=True # Capture exceptions instead of stopping gather
        )

        # Process results to find and remove failed connections
        failed_connections = []
        for i, result in enumerate(results):
            if isinstance(result, Exception): # Check if the result is an Exception object
                failed_websocket = connections_to_send[i]
                logger.warning(f"Broadcast failed for client {failed_websocket.client}: {result}. Removing connection.")
                failed_connections.append(failed_websocket)

        # Remove failed connections after iteration
        if failed_connections:
             async with self._lock:
                 for ws in failed_connections:
                     self.active_connections.discard(ws) # Use discard for sets


    async def _safe_send_json(self, websocket: WebSocket, message: Dict[str, Any]):
        """Internal helper to send JSON and handle immediate errors."""
        try:
            await websocket.send_json(message)
        except (WebSocketDisconnect, RuntimeError) as e:
            # Raise specific exception to be caught by gather
            raise ConnectionError(f"Send failed, client disconnected: {e}") from e
        except Exception as e:
            raise ConnectionError(f"Unexpected send error: {e}") from e


    async def disconnect_all(self):
        """Closes all active WebSocket connections gracefully."""
        logger.info(f"Disconnecting all ({len(self.active_connections)}) WebSocket connections...")
        async with self._lock:
            connections_to_close = list(self.active_connections)
            self.active_connections.clear() # Clear the active set

        # Attempt to close connections concurrently
        results = await asyncio.gather(
            *(websocket.close(code=1000, reason="Server shutting down") for websocket in connections_to_close),
            return_exceptions=True
        )

        # Log any errors during closure
        for i, result in enumerate(results):
             if isinstance(result, Exception):
                  ws = connections_to_close[i]
                  logger.warning(f"Error closing WebSocket for {ws.client} during shutdown: {result}")
        logger.info("Finished disconnecting all WebSocket connections.")