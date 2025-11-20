from __future__ import annotations
import asyncio
import logging
from typing import Dict, Optional, List, Set
from fastapi import WebSocket
import time

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
        self.topic_subscriptions: Dict[str, Set[str]] = {}  # New: topic -> set of client_ids
        self.client_id_to_topics: Dict[str, Set[str]] = {}  # New: client_id -> set of topics
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
            await websocket.close(
                code=4000, reason="Connection limit exceeded"
            )
            return

        await websocket.accept()
        self.active_connections[client_id] = websocket
        self.client_id_to_user_id[client_id] = user_id

        if user_id not in self.user_id_to_client_ids:
            self.user_id_to_client_ids[user_id] = []
        self.user_id_to_client_ids[user_id].append(client_id)
        self.client_id_to_topics[client_id] = set() # Initialize topics for new client

        logger.info(
            f"New authenticated WebSocket connection: client_id={client_id}, user_id={user_id}. "
            f"Total connections: {len(self.active_connections)}"
        )

    async def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            user_id = self.client_id_to_user_id.pop(client_id, None)

            if user_id and user_id in self.user_id_to_client_ids:
                if client_id in self.user_id_to_client_ids[user_id]:
                    self.user_id_to_client_ids[user_id].remove(client_id)
                if not self.user_id_to_client_ids[user_id]:
                    del self.user_id_to_client_ids[user_id]
            
            # Remove client from all topic subscriptions
            if client_id in self.client_id_to_topics:
                for topic in list(self.client_id_to_topics[client_id]): # Iterate over a copy
                    await self.unsubscribe_from_topic(client_id, topic)
                del self.client_id_to_topics[client_id]

            logger.info(
                f"WebSocket connection closed: client_id={client_id}. "
                f"Total connections: {len(self.active_connections)}"
            )

    async def send_personal_message(self, message: str, client_id: str):
        if client_id in self.active_connections:
            await self.active_connections[client_id].send_text(message)

    async def broadcast(self, message: str):
        for connection in self.active_connections.values():
            await connection.send_text(message)

    async def send_to_user(self, user_id: str, message: str):
        client_ids = self.user_id_to_client_ids.get(user_id, [])
        for client_id in client_ids:
            if client_id in self.active_connections:
                await self.active_connections[client_id].send_text(message)

    async def subscribe_to_topic(self, client_id: str, topic: str):
        if client_id not in self.active_connections:
            logger.warning(f"Client {client_id} not active. Cannot subscribe to topic {topic}.")
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
            for client_id in list(self.topic_subscriptions[topic]): # Iterate over a copy to allow modification during iteration
                if client_id in self.active_connections:
                    try:
                        await self.active_connections[client_id].send_text(message)
                    except Exception as e:
                        logger.error(f"Failed to send message to client {client_id} on topic {topic}: {e}")
                        # Consider disconnecting client if send fails consistently
                else:
                    logger.warning(f"Client {client_id} in topic {topic} is no longer active. Removing subscription.")
                    self.topic_subscriptions[topic].remove(client_id)
                    if client_id in self.client_id_to_topics:
                        self.client_id_to_topics[client_id].discard(topic)

    async def _ping_clients(self):
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(self.ping_interval)
                disconnected_clients = []
                for client_id, connection in self.active_connections.items():
                    if connection.client_state == 2: # WebSocketState.DISCONNECTED
                        disconnected_clients.append(client_id)
                        continue
                    try:
                        await asyncio.wait_for(
                            connection.send_text("ping"), timeout=30
                        )
                    except (asyncio.TimeoutError, ConnectionError, RuntimeError):
                        logger.warning(
                            f"Client {client_id} failed to respond to ping or is already closed. Marking for disconnection."
                        )
                        disconnected_clients.append(client_id)
                
                for client_id in disconnected_clients:
                    await self.disconnect(client_id)

            except asyncio.CancelledError:
                logger.info("Ping task cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in ping task: {e}", exc_info=True)

    async def shutdown(self):
        logger.info("Shutting down ConnectionManager...")
        self._shutdown_event.set()
        for ws in self.active_connections.values():
            await ws.close()
        self.active_connections.clear()
        self.client_id_to_user_id.clear()
        self.user_id_to_client_ids.clear()
        self.topic_subscriptions.clear() # Clear topic subscriptions on shutdown
        self.client_id_to_topics.clear() # Clear client topics on shutdown
        logger.info("All WebSocket connections closed.")