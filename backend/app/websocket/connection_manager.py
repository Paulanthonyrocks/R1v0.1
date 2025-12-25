from __future__ import annotations
import asyncio
import logging
import time # Import time for timestamping
from typing import Dict, Optional, List, Set
from fastapi import WebSocket
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum, PingData # Import necessary models

logger = logging.getLogger(__name__)

class ConnectionManager:
    _instance: Optional[ConnectionManager] = None

    def __init__(
        self,
        max_connections: int = 1000,
        token_refresh_interval: int = 300,
        ping_interval: int = 15,
        pong_timeout: int = 60, # New: seconds to wait for a pong after a ping
    ):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self.active_connections: Dict[str, WebSocket] = {}
        self.client_id_to_user_id: Dict[str, str] = {}
        self.user_id_to_client_ids: Dict[str, List[str]] = {}
        self.topic_subscriptions: Dict[str, Set[str]] = {}
        self.client_id_to_topics: Dict[str, Set[str]] = {}
        self.feed_subscriptions: Dict[str, Set[str]] = {}
        self.last_pong_received_time: Dict[str, float] = {} # New: Track last pong time
        self.max_connections = max_connections
        self.token_refresh_interval = token_refresh_interval
        self.ping_interval = ping_interval
        self.pong_timeout = pong_timeout # New: store pong timeout
        self._shutdown_event = asyncio.Event()

    async def init(
        self,
        max_connections: int,
        token_refresh_interval: int,
        ping_interval: int,
        pong_timeout: int, # New: include pong_timeout in init
    ):
        self.max_connections = max_connections
        self.token_refresh_interval = token_refresh_interval
        self.ping_interval = ping_interval
        self.pong_timeout = pong_timeout # New: set pong_timeout
        asyncio.create_task(self._ping_clients())
        logger.info(
            f"ConnectionManager initialized with max_connections={max_connections}, "
            f"token_refresh_interval={token_refresh_interval}, ping_interval={ping_interval}, "
            f"pong_timeout={pong_timeout}"
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
        self.last_pong_received_time[client_id] = time.time() # Initialize on connect

        logger.info(
            f"New authenticated WebSocket connection: client_id={client_id}, user_id={user_id}. "
            f"Total connections: {len(self.active_connections)}"
        )

    async def disconnect(self, client_id: str, websocket: Optional[WebSocket] = None):
        if client_id in self.active_connections:
            # Prevent removing a NEW connection if the OLD one is disconnecting
            if websocket and self.active_connections[client_id] != websocket:
                logger.info(f"Disconnect called for {client_id} but connection mismatch (race condition). Ignoring.")
                return

            # Remove from active connections first to prevent further sends
            del self.active_connections[client_id]
            self.last_pong_received_time.pop(client_id, None) # New: remove pong tracking
            
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

    def record_pong(self, client_id: str):
        """Record the time a PONG was received from a client."""
        if client_id in self.active_connections:
            self.last_pong_received_time[client_id] = time.time()
            logger.debug(f"Recorded PONG for client {client_id}")

    async def send_personal_message(self, message: str, client_id: str):
        if client_id in self.active_connections:
            try:
                await self.active_connections[client_id].send_text(message)
                logger.debug(f"Successfully sent message to client {client_id}. Message size: {len(message)} bytes.")
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

    async def subscribe_to_feed(self, client_id: str, feed_id: str):
        if feed_id not in self.feed_subscriptions:
            self.feed_subscriptions[feed_id] = set()
        self.feed_subscriptions[feed_id].add(client_id)
        logger.info(f"Client {client_id} subscribed to feed: {feed_id}")

    def get_clients_for_feed(self, feed_id: str) -> Set[str]:
        return self.feed_subscriptions.get(feed_id, set())

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
                ping_message = WebSocketMessage(
                    type=WebSocketMessageTypeEnum.PING,
                    data=PingData().model_dump()
                ).model_dump_json()

                current_time = time.time()
                clients_to_disconnect = []

                for client_id, connection in list(self.active_connections.items()):
                    if connection.client_state == 2: # WebSocketState.DISCONNECTED
                        clients_to_disconnect.append(client_id)
                        continue

                    # Check if PONG was received within timeout
                    last_pong_time = self.last_pong_received_time.get(client_id, 0)
                    if current_time - last_pong_time > self.pong_timeout + self.ping_interval: # Give some grace for network delay
                        logger.warning(f"Client {client_id} timed out (no PONG received). Disconnecting.")
                        clients_to_disconnect.append(client_id)
                        continue

                    try:
                        await asyncio.wait_for(
                            connection.send_text(ping_message), timeout=5
                        )
                    except Exception:
                        logger.info(f"Failed to send PING to {client_id}, marking for disconnection.")
                        clients_to_disconnect.append(client_id)
                
                for client_id in clients_to_disconnect:
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
        self.last_pong_received_time.clear() # New: Clear pong tracking on shutdown
        logger.info("All WebSocket connections closed.")
