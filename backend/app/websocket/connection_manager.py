import asyncio
import logging
import time
import json
from datetime import datetime
from uuid import UUID
from collections import defaultdict
from typing import Dict, List, Set, Any, Optional

from fastapi import WebSocket

from app.models.user import User
from app.models.feeds import FeedStatus, FeedStatusData
from app.models.websocket import (FeedStatusUpdate, WebSocketMessage,
                                  WebSocketMessageTypeEnum)

logger = logging.getLogger(__name__)

class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, UUID):
            return str(obj)
        return super(DateTimeEncoder, self).default(obj)

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.feed_subscriptions: Dict[str, Set[str]] = defaultdict(set)
        self.client_roles: Dict[str, str] = {}
        self.last_pong: Dict[str, float] = {}
        self.keepalive_task: Optional[asyncio.Task] = None
        # Per-client delivery buffers to isolate slow clients
        self.client_queues: Dict[str, asyncio.Queue] = {}
        self.client_tasks: Dict[str, asyncio.Task] = {}

    async def connect(self, websocket: WebSocket, client_id: str, username: Optional[str] = None, role: str = "user"): 
        self.active_connections[client_id] = websocket
        self.client_roles[client_id] = role
        self.last_pong[client_id] = time.time()
        
        # Create a dedicated delivery queue and sender task for this client
        self.client_queues[client_id] = asyncio.Queue(maxsize=10)
        self.client_tasks[client_id] = asyncio.create_task(self._client_sender_loop(client_id))
        
        logger.info(f"Client {client_id} (role: {role}) connected. Delivery task started.")

    async def _client_sender_loop(self, client_id: str):
        """Dedicated loop per client to handle outgoing messages without blocking the global broadcaster."""
        try:
            while True:
                msg = await self.client_queues[client_id].get()
                ws = self.active_connections.get(client_id)
                if not ws:
                    break
                
                try:
                    if isinstance(msg, bytes):
                        await ws.send_bytes(msg)
                    elif isinstance(msg, str):
                        await ws.send_text(msg)
                    elif isinstance(msg, dict):
                        await ws.send_json(msg)
                    else:
                        await ws.send_text(str(msg))
                except Exception as e:
                    logger.warning(f"Send error for client {client_id}: {e}")
                    await self.disconnect(client_id)
                    break
                finally:
                    self.client_queues[client_id].task_done()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Critical error in sender loop for {client_id}: {e}")

    async def disconnect(self, client_id: str, websocket: Any = None):
        # 1. Cancel sender task
        if client_id in self.client_tasks:
            self.client_tasks[client_id].cancel()
            del self.client_tasks[client_id]
        
        # 2. Cleanup queue
        if client_id in self.client_queues:
            del self.client_queues[client_id]

        if client_id in self.active_connections:
            del self.active_connections[client_id]
        if client_id in self.client_roles:
            del self.client_roles[client_id]
        if client_id in self.last_pong:
            del self.last_pong[client_id]
        for feed_id in self.feed_subscriptions:
            if client_id in self.feed_subscriptions[feed_id]:
                self.feed_subscriptions[feed_id].remove(client_id)
        logger.info(f"Client {client_id} disconnected and delivery task stopped.")

    async def send_personal_message(self, message: str, client_id: str):
        websocket = self.active_connections.get(client_id)
        if websocket:
            await websocket.send_text(message)

    async def send_personal_bytes(self, data: bytes, client_id: str):
        websocket = self.active_connections.get(client_id)
        if websocket:
            await websocket.send_bytes(data)

    async def broadcast_bytes_to_feed(self, feed_id: str, data: bytes):
        if feed_id in self.feed_subscriptions:
            client_ids = list(self.feed_subscriptions[feed_id])
            for cid in client_ids:
                q = self.client_queues.get(cid)
                if q:
                    try:
                        if q.full():
                            try:
                                q.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                        q.put_nowait(data)
                    except Exception as e:
                        logger.debug(f"Failed to queue bytes for {cid}: {e}")

    def has_subscribers(self, feed_id: str) -> bool:
        return bool(self.feed_subscriptions.get(feed_id))

    def get_user_role(self, client_id: str) -> str:
        return self.client_roles.get(client_id, "user")

    async def broadcast(self, message: Any, feed_id: Optional[str] = None):
        """Generic broadcast method for compatibility with FeedManager."""
        if isinstance(message, bytes):
            if feed_id:
                await self.broadcast_bytes_to_feed(feed_id, message)
            return
        
        msg_str = json.dumps(message, cls=DateTimeEncoder) if isinstance(message, dict) else str(message)
        targets = list(self.feed_subscriptions[feed_id]) if feed_id else list(self.active_connections.keys())
        for cid in targets:
            q = self.client_queues.get(cid)
            if q:
                try:
                    if q.full():
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    q.put_nowait(msg_str)
                except Exception as e:
                    logger.debug(f"Failed to queue message for {cid}: {e}")

    async def broadcast_to_feed(self, message: Any, feed_id: str):
        """Broadcast a message to all clients subscribed to a specific feed."""
        await self.broadcast(message, feed_id=feed_id)

    async def broadcast_to_topic(self, topic: str, message: Any):
        """Broadcast a message to all clients subscribed to a specific topic (e.g., incidents)."""
        msg_str = json.dumps(message, cls=DateTimeEncoder) if isinstance(message, (dict, list)) else str(message)
        if topic in self.feed_subscriptions:
            targets = list(self.feed_subscriptions[topic])
            to_disconnect = []
            for cid in targets:
                ws = self.active_connections.get(cid)
                if ws:
                    try:
                        await ws.send_text(msg_str)
                    except Exception:
                        to_disconnect.append(cid)
            for cid in to_disconnect:
                await self.disconnect(cid)
        else:
            logger.debug(f"No subscribers for topic {topic}. Skipping broadcast.")

    async def subscribe_to_topic(self, client_id: str, topic: str):
        self.feed_subscriptions[topic].add(client_id)
        logger.info(f"Client {client_id} subscribed to topic: {topic}")

    async def unsubscribe_from_topic(self, client_id: str, topic: str):
        if topic in self.feed_subscriptions and client_id in self.feed_subscriptions[topic]:
            self.feed_subscriptions[topic].remove(client_id)
            logger.info(f"Client {client_id} unsubscribed from topic: {topic}")

    async def subscribe_to_feed(self, client_id: str, feed_id: str):
        self.feed_subscriptions[feed_id].add(client_id)
        logger.info(f"Client {client_id} subscribed to feed: {feed_id}")

    async def unsubscribe_from_feed(self, client_id: str, feed_id: str):
        if feed_id in self.feed_subscriptions and client_id in self.feed_subscriptions[feed_id]:
            self.feed_subscriptions[feed_id].remove(client_id)
            logger.info(f"Client {client_id} unsubscribed from feed: {feed_id}")

    def record_pong(self, client_id: str, rtt_ms: float | None = None):
        self.last_pong[client_id] = time.time()

    def start_keepalive(self, ping_interval: int = 30, ping_timeout: int = 60, **kwargs):
        ping_timeout = kwargs.get('timeout', ping_timeout)
        if self.keepalive_task:
            return
        async def keepalive_loop():
            try:
                while True:
                    await asyncio.sleep(ping_interval)
                    now = time.time()
                    to_disconnect = []
                    for client_id in list(self.active_connections.keys()):
                        last_time = self.last_pong.get(client_id, 0)
                        if now - last_time > ping_timeout:
                            to_disconnect.append(client_id)
                        else:
                            try:
                                ws = self.active_connections[client_id]
                                await ws.send_json({"type": "ping"})
                            except Exception:
                                to_disconnect.append(client_id)
                    for client_id in to_disconnect:
                        logger.warning(f"Keepalive timeout for {client_id}. Disconnecting.")
                        await self.disconnect(client_id)
            except asyncio.CancelledError:
                logger.info("Keepalive check task stopped.")
        self.keepalive_task = asyncio.create_task(keepalive_loop())
        logger.info("Keepalive check task started.")

    def stop_keepalive(self):
        if self.keepalive_task:
            self.keepalive_task.cancel()
            self.keepalive_task = None
