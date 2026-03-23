import asyncio
import logging
import time
from collections import defaultdict
from typing import Dict, List, Set

from fastapi import WebSocket

from app.models.user import User
from app.models.feeds import FeedStatus, FeedStatusData
from app.models.websocket import (FeedStatusUpdate, WebSocketMessage,
                                  WebSocketMessageTypeEnum)

logger = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.client_user_map: Dict[str, str] = {}
        self.user_client_map: Dict[str, Set[str]] = defaultdict(set)
        self.client_pings: Dict[str, dict] = {}
        self.topic_subscriptions: Dict[str, Set[str]] = defaultdict(set)
        self.feed_subscriptions: Dict[str, Set[str]] = defaultdict(set)
        self.client_roles: Dict[str, str] = {}
        self.keepalive_task = None
        self.last_ping_time = 0

    async def connect(self, websocket: WebSocket, client_id: str, user_id: str, role: str):
        # Aggressively close existing connections with the same client_id to prevent zombies
        if client_id in self.active_connections:
            old_ws = self.active_connections[client_id]
            if old_ws != websocket:
                logger.info(f"Closing existing connection for client {client_id} to prevent zombies.")
                try:
                    await old_ws.close(code=1000, reason="New connection established with same ID")
                except Exception as e:
                    logger.debug(f"Error closing old WebSocket for {client_id}: {e}")

        self.active_connections[client_id] = websocket
        self.client_user_map[client_id] = user_id
        self.user_client_map[user_id].add(client_id)
        self.client_roles[client_id] = role
        self.client_pings[client_id] = {"last_pong": time.time(), "rtt": None, "missed_pings": 0}
        logger.info(f"Client {client_id} (user: {user_id}, role: {role}) connected from {websocket.client.host}")

    async def disconnect(self, client_id: str, websocket: WebSocket = None):
        # Identity-aware disconnect: only clean up if this is the active connection
        if websocket and self.active_connections.get(client_id) != websocket:
            logger.info(f"Ignoring disconnect for client {client_id} as it is not the active connection.")
            return

        logger.info(f"Disconnecting and cleaning up client {client_id}")
        
        if client_id in self.active_connections:
            del self.active_connections[client_id]

        user_id = self.client_user_map.pop(client_id, None)
        if user_id and user_id in self.user_client_map:
            self.user_client_map[user_id].discard(client_id)
            if not self.user_client_map[user_id]:
                del self.user_client_map[user_id]

        self.client_pings.pop(client_id, None)
        self.client_roles.pop(client_id, None)

        # Optimize subscription cleanup: remove keys if sets are empty
        for topic in list(self.topic_subscriptions.keys()):
            self.topic_subscriptions[topic].discard(client_id)
            if not self.topic_subscriptions[topic]:
                del self.topic_subscriptions[topic]

        for feed_id in list(self.feed_subscriptions.keys()):
            self.feed_subscriptions[feed_id].discard(client_id)
            if not self.feed_subscriptions[feed_id]:
                del self.feed_subscriptions[feed_id]

        # Close the WebSocket connection if it's still open and we are the owner
        if websocket and websocket.client_state.name == 'CONNECTED':
            try:
                await websocket.close()
                logger.info(f"Successfully closed WebSocket for client {client_id}")
            except RuntimeError as e:
                logger.warning(f"Error closing WebSocket for {client_id}: {e}")
        
        logger.info(f"Client {client_id} cleanup complete.")
        
    async def send_bytes(self, data: bytes, client_id: str):
        if client_id in self.active_connections:
            websocket = self.active_connections[client_id]
            try:
                await websocket.send_bytes(data)
                logger.debug(f"[WS] Sent {len(data)} bytes to client {client_id}")
            except Exception as e:
                logger.error(f"Error sending bytes to {client_id}: {e}")

    async def broadcast_bytes_to_feed(self, data: bytes, feed_id: str):
        if feed_id in self.feed_subscriptions:
            client_ids = list(self.feed_subscriptions[feed_id])
            for client_id in client_ids:
                await self.send_bytes(data, client_id)
            if client_ids:
                logger.info(f"[WS] {len(data)} bytes sent to {len(client_ids)} clients for feed {feed_id}")

    def get_user_role(self, client_id: str) -> str:
        return self.client_roles.get(client_id, "user")

    def has_subscribers(self, feed_id: str) -> bool:
        """Check if a feed has any active subscribers."""
        return bool(self.feed_subscriptions.get(feed_id))

    async def subscribe_to_topic(self, client_id: str, topic: str):
        if client_id not in self.topic_subscriptions[topic]:
            self.topic_subscriptions[topic].add(client_id)
            logger.info(f"Client {client_id} subscribed to topic: {topic}")

    async def unsubscribe_from_topic(self, client_id: str, topic: str):
        if topic in self.topic_subscriptions:
            self.topic_subscriptions[topic].discard(client_id)
            logger.info(f"Client {client_id} unsubscribed from topic: {topic}")

    async def subscribe_to_feed(self, client_id: str, feed_id: str):
        if client_id not in self.feed_subscriptions[feed_id]:
            self.feed_subscriptions[feed_id].add(client_id)
            logger.info(f"Client {client_id} subscribed to feed: {feed_id}")

    async def unsubscribe_from_feed(self, client_id: str, feed_id: str):
        if feed_id in self.feed_subscriptions:
            self.feed_subscriptions[feed_id].discard(client_id)
            logger.info(f"Client {client_id} unsubscribed from feed: {feed_id}")

    async def send_personal_message(self, message: str, client_id: str):
        if client_id in self.active_connections:
            try:
                # Add a small timeout to avoid blocking the entire broadcast for one slow client
                await asyncio.wait_for(self.active_connections[client_id].send_text(message), timeout=1.0)
                logger.info(f"[WS] Text message sent to {client_id}")
            except asyncio.TimeoutError:
                logger.warning(f"Timeout sending message to {client_id}")
            except Exception as e:
                logger.error(f"Failed to send message to {client_id}: {e}")
        else:
            logger.warning(f"Attempted to send message to disconnected client {client_id}")

    async def send_personal_bytes(self, message: bytes, client_id: str):
        if client_id in self.active_connections:
            websocket = self.active_connections[client_id]
            if websocket.client_state.name == 'CONNECTED':
                try:
                    # Add a small timeout to avoid blocking the entire broadcast for one slow client
                    await asyncio.wait_for(websocket.send_bytes(message), timeout=1.0)
                    logger.info(f"[WS] {len(message)} bytes sent to {client_id}")
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout sending bytes to client {client_id}")
                except Exception as e:
                    logger.error(f"Failed to send bytes to client {client_id}: {e}")

    async def broadcast(self, message: str):
        client_ids = list(self.active_connections.keys())
        tasks = [self.send_personal_message(message, client_id) for client_id in client_ids]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def broadcast_to_topic(self, message: str, topic: str):
        if topic in self.topic_subscriptions:
            client_ids = list(self.topic_subscriptions[topic])
            for client_id in client_ids:
                await self.send_personal_message(message, client_id)

    async def broadcast_to_feed(self, message: str, feed_id: str):
        if feed_id in self.feed_subscriptions:
            client_ids = list(self.feed_subscriptions[feed_id])
            for client_id in client_ids:
                await self.send_personal_message(message, client_id)
    
    async def broadcast_bytes_to_feed(self, feed_id: str, message: bytes):
        if feed_id in self.feed_subscriptions:
            client_ids = list(self.feed_subscriptions[feed_id])
            if client_ids:
                tasks = [self.send_personal_bytes(message, client_id) for client_id in client_ids]
                await asyncio.gather(*tasks, return_exceptions=True)

    async def broadcast_status_update(self, feed_id: str, status: FeedStatus):
        status_data = FeedStatusData(feed_id=feed_id, status=status)
        message = WebSocketMessage(
            type=WebSocketMessageTypeEnum.FEED_STATUS_UPDATE,
            data=FeedStatusUpdate(feed_status_data=status_data)
        )
        await self.broadcast_to_feed(message.model_dump_json(), feed_id)
        # Also send to the general 'feed_statuses' topic for overview listeners
        await self.broadcast_to_topic(message.model_dump_json(), "feed_statuses")

    def record_pong(self, client_id: str, rtt_ms: float | None = None):
        if client_id in self.client_pings:
            self.client_pings[client_id]["last_pong"] = time.time()
            self.client_pings[client_id]["missed_pings"] = 0
            if rtt_ms is not None:
                self.client_pings[client_id]["rtt"] = rtt_ms
            logger.debug(f"PONG received from {client_id}. RTT: {rtt_ms}ms")

    async def _keepalive_check(self, ping_interval: int = 10, timeout: int = 30):
        while True:
            await asyncio.sleep(ping_interval)
            
            # Use a copy of client_ids to avoid issues with disconnections during iteration
            client_ids = list(self.active_connections.keys())
            now = time.time()
            self.last_ping_time = now

            for client_id in client_ids:
                # If connection has been removed, skip.
                if client_id not in self.client_pings:
                    continue

                stats = self.client_pings[client_id]
                if now - stats["last_pong"] > timeout:
                    logger.warning(f"Client {client_id} timed out. Last pong was {now - stats['last_pong']:.2f}s ago.")
                    # Grab the WebSocket object before calling disconnect
                    websocket = self.active_connections.get(client_id)
                    await self.disconnect(client_id, websocket)
                    continue
                
                # Send PING with a unique ID for RTT calculation
                correlation_id = f"ping-{client_id}-{int(now * 1000)}"
                ping_message = WebSocketMessage(
                    type=WebSocketMessageTypeEnum.PING,
                    correlation_id=correlation_id,
                    data={"timestamp": now * 1000}
                ).model_dump_json()

                await self.send_personal_message(ping_message, client_id)

    def start_keepalive(self, ping_interval: int = 10, timeout: int = 30):
        if self.keepalive_task is None:
            self.keepalive_task = asyncio.create_task(self._keepalive_check(ping_interval, timeout))
            logger.info("Keepalive check task started.")

    def stop_keepalive(self):
        if self.keepalive_task:
            self.keepalive_task.cancel()
            self.keepalive_task = None
            logger.info("Keepalive check task stopped.")
