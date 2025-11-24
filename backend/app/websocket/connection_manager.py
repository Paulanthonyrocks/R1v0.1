from __future__ import annotations
import asyncio
import logging
from typing import Dict, Optional, List, Set
from fastapi import WebSocket

logger = logging.getLogger(__name__)

class ConnectionManager:
    _instance: Optional[ConnectionManager] = None

    def __init__(
        self,
        max_connections: int = 1000,
        token_refresh_interval: int = 300,
        ping_interval: int = 15,
    ):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self.active_connections: Dict[str, WebSocket] = {}
        self.client_id_to_user_id: Dict[str, str] = {}
        self.user_id_to_client_ids: Dict[str, List[str]] = {}
        self.topic_subscriptions: Dict[str, Set[str]] = {}
        self.client_id_to_topics: Dict[str, Set[str]] = {}
        self.max_connections = max_connections
        self.token_refresh_interval = token_refresh_interval
        self.ping_interval = ping_interval
        self._shutdown_event = asyncio.Event()

    async def init(
        self,
        max_connections: int,
        token_refresh_interval: int,
        ping_interval: int,
    ):
        self.max_connections = max_connections
        self.token_refresh_interval = token_refresh_interval
        self.ping_interval = ping_interval
        asyncio.create_task(self._ping_clients())
        logger.info(
            f"ConnectionManager initialized with max_connections={max_connections}, "
            f"token_refresh_interval={token_refresh_interval}, ping_interval={ping_interval}"
        )

    @classmethod
    def get_instance(cls) -> "ConnectionManager":
        if cls._instance is None:
            raise RuntimeError(
                "ConnectionManager not initialized. Ensure initialize_services() is called before accessing services."
            )
        return cls._instance

    async def connect(self, websocket: WebSocket, client_id: str, user_id: str):
        if len(self.active_connections) >= self.max_connections:
            logger.warning(
                f"Connection limit exceeded. Cannot accept new connection for client {client_id}."
            )
            await websocket.close(code=4000, reason="Connection limit exceeded")
            return

        await websocket.accept()
        self.active_connections[client_id] = websocket
        self.client_id_to_user_id[client_id] = user_id

        if user_id not in self.user_id_to_client_ids:
            self.user_id_to_client_ids[user_id] = []
        self.user_id_to_client_ids[user_id].append(client_id)
        self.client_id_to_topics[client_id] = set()

        logger.info(
            f"New authenticated WebSocket connection: client_id={client_id}, user_id={user_id}. "
            f"Total connections: {len(self.active_connections)}"
        )

    async def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            # Remove from active connections first to prevent further sends
            del self.active_connections[client_id]
            
            user_id = self.client_id_to_user_id.pop(client_id, None)

            if user_id and user_id in self.user_id_to_client_ids:
                if client_id in self.user_id_to_client_ids[user_id]:
                    self.user_id_to_client_ids[user_id].remove(client_id)
                if not self.user_id_to_client_ids[user_id]:
                    del self.user_id_to_client_ids[user_id]
            
            # Clean up subscriptions
            if client_id in self.client_id_to_topics:
                topics = list(self.client_id_to_topics[client_id])
                for topic in topics:
                    await self.unsubscribe_from_topic(client_id, topic)
                
                if client_id in self.client_id_to_topics:
                    del self.client_id_to_topics[client_id]

            logger.info(
                f"WebSocket connection closed: client_id={client_id}. "
                f"Total connections: {len(self.active_connections)}"
            )

    async def send_personal_message(self, message: str, client_id: str):
        if client_id in self.active_connections:
            try:
                await self.active_connections[client_id].send_text(message)
            except Exception:
                # Sockets can close unexpectedly; log quietly and cleanup
                logger.info(f"Failed to send to {client_id}, removing dead connection.")
                await self.disconnect(client_id)

    async def broadcast(self, message: str):
        # Iterate over a copy to allow modification (disconnection) during iteration
        for client_id, connection in list(self.active_connections.items()):
            try:
                await connection.send_text(message)
            except Exception:
                # If sending fails, assume the client is gone and clean up immediately
                # This prevents "Unexpected ASGI message" logs on subsequent frames
                logger.info(f"Broadcasting failed for {client_id}, removing dead connection.")
                await self.disconnect(client_id)

    async def send_to_user(self, user_id: str, message: str):
        client_ids = self.user_id_to_client_ids.get(user_id, [])
        for client_id in list(client_ids): # Iterate copy
            if client_id in self.active_connections:
                try:
                    await self.active_connections[client_id].send_text(message)
                except Exception:
                    logger.info(f"Failed to send to user {user_id} (client {client_id}), removing connection.")
                    await self.disconnect(client_id)

    async def subscribe_to_topic(self, client_id: str, topic: str):
        if client_id not in self.active_connections:
            return

        if topic not in self.topic_subscriptions:
            self.topic_subscriptions[topic] = set()
        self.topic_subscriptions[topic].add(client_id)
        self.client_id_to_topics.setdefault(client_id, set()).add(topic)
        logger.info(f"Client {client_id} subscribed to topic: {topic}")

    async def unsubscribe_from_topic(self, client_id: str, topic: str):
        if topic in self.topic_subscriptions and client_id in self.topic_subscriptions[topic]:
            self.topic_subscriptions[topic].remove(client_id)
            if not self.topic_subscriptions[topic]:
                del self.topic_subscriptions[topic]
            logger.info(f"Client {client_id} unsubscribed from topic: {topic}")
        
        if client_id in self.client_id_to_topics and topic in self.client_id_to_topics[client_id]:
            self.client_id_to_topics[client_id].remove(topic)
            if not self.client_id_to_topics[client_id]:
                del self.client_id_to_topics[client_id]

    async def broadcast_to_topic(self, message: str, topic: str):
        if topic in self.topic_subscriptions:
            for client_id in list(self.topic_subscriptions[topic]):
                if client_id in self.active_connections:
                    try:
                        await self.active_connections[client_id].send_text(message)
                    except Exception:
                        logger.info(f"Topic broadcast failed for {client_id}, removing subscription/connection.")
                        await self.disconnect(client_id)
                else:
                    # Cleanup stale subscription if client is already gone from active_connections
                    self.topic_subscriptions[topic].discard(client_id)
                    if client_id in self.client_id_to_topics:
                        self.client_id_to_topics[client_id].discard(topic)

    async def _ping_clients(self):
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(self.ping_interval)
                for client_id, connection in list(self.active_connections.items()):
                    if connection.client_state == 2: # WebSocketState.DISCONNECTED
                        await self.disconnect(client_id)
                        continue
                    try:
                        await asyncio.wait_for(
                            connection.send_text("ping"), timeout=5
                        )
                    except Exception:
                        await self.disconnect(client_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in ping task: {e}")

    async def shutdown(self):
        logger.info("Shutting down ConnectionManager...")
        self._shutdown_event.set()
        for ws in self.active_connections.values():
            try:
                await ws.close()
            except Exception:
                pass
        self.active_connections.clear()
        self.client_id_to_user_id.clear()
        self.user_id_to_client_ids.clear()
        self.topic_subscriptions.clear()
        self.client_id_to_topics.clear()
        logger.info("All WebSocket connections closed.")
