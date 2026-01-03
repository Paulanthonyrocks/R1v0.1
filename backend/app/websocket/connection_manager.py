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

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(ConnectionManager, cls).__new__(cls)
        return cls._instance

    def __init__(
        self,
        max_connections: int = 1000,
        token_refresh_interval: int = 300,
        ping_interval: int = 15,
        pong_timeout: int = 60, # New: seconds to wait for a pong after a ping
    ):
        if hasattr(self, "_initialized") and self._initialized:
            return
        self._initialized = True
        self.active_connections: Dict[str, WebSocket] = {}
        self.client_id_to_user_id: Dict[str, str] = {}
        self.user_id_to_client_ids: Dict[str, List[str]] = {}
        self.topic_subscriptions: Dict[str, Set[str]] = {}
        self.client_id_to_topics: Dict[str, Set[str]] = {}
        self.feed_subscriptions: Dict[str, Set[str]] = {}
        self.client_id_to_feeds: Dict[str, Set[str]] = {}
        self.last_pong_received_time: Dict[str, float] = {} # New: Track last pong time
        
        # Output queues for backpressure management
        self.client_queues: Dict[str, asyncio.Queue] = {}
        self.client_tasks: Dict[str, asyncio.Task] = {}
        
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
        # Update config even if already initialized
        self.max_connections = max_connections
        self.token_refresh_interval = token_refresh_interval
        self.ping_interval = ping_interval
        self.pong_timeout = pong_timeout # New: set pong_timeout
        
        # Only start ping task if not already running
        if not hasattr(self, "_ping_task") or self._ping_task.done():
            self._ping_task = asyncio.create_task(self._ping_clients())
            
        logger.info(
            f"ConnectionManager initialized with max_connections={max_connections}, "
            f"token_refresh_interval={token_refresh_interval}, ping_interval={ping_interval}, "
            f"pong_timeout={pong_timeout}"
        )

    @classmethod
    def get_instance(cls) -> "ConnectionManager":
        if cls._instance is None:
             # Fallback: create instance if accessed before explicit init (should rarely happen in strict flow)
             cls._instance = ConnectionManager()
        return cls._instance

    async def connect(self, websocket: WebSocket, client_id: str, user_id: str):
        if len(self.active_connections) >= self.max_connections:
            logger.warning(
                f"Connection limit exceeded. Cannot accept new connection for client {client_id}."
            )
            await websocket.close(code=4000, reason="Connection limit exceeded")
            return

        # Handle reconnection: Close existing connection if present
        if client_id in self.active_connections:
            logger.warning(f"Collision detected for {client_id}. Closing OLD connection to accept NEW one. (This is normal during page reloads, but indicates tab duplication if frequent)")
            old_ws = self.active_connections[client_id]
            # Force disconnect the old socket structure
            await self.disconnect(client_id, old_ws)
            try:
                # Ensure the old socket is actually closed
                await old_ws.close(code=1000, reason="Reconnected")
            except Exception:
                pass

        # websocket.accept() is now handled by the endpoint router before calling connect
        self.active_connections[client_id] = websocket
        self.client_id_to_user_id[client_id] = user_id

        if user_id not in self.user_id_to_client_ids:
            self.user_id_to_client_ids[user_id] = []
        
        if client_id not in self.user_id_to_client_ids[user_id]:
            self.user_id_to_client_ids[user_id].append(client_id)
            
        self.client_id_to_topics.setdefault(client_id, set())
        self.client_id_to_feeds.setdefault(client_id, set())
        self.last_pong_received_time[client_id] = time.time() # Initialize on connect

        # Initialize sender queue and task
        self.client_queues[client_id] = asyncio.Queue(maxsize=50)
        self.client_tasks[client_id] = asyncio.create_task(self._client_sender(client_id, websocket))

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

            # 1. Handle Task Cancellation (Safely)
            task = self.client_tasks.pop(client_id, None)
            if task:
                if task != asyncio.current_task():
                    task.cancel()
                    try:
                        # Awaiting task might yield control
                        await task
                    except asyncio.CancelledError:
                        pass
                else:
                    logger.debug(f"Task {client_id} disconnecting itself. Skipping cancel/await.")
            
            # 2. Remove queue (Idempotent)
            self.client_queues.pop(client_id, None)

            # 3. Remove from active connections (Idempotent)
            # Check again because control might have been yielded during task await
            if client_id not in self.active_connections:
                return
                
            if websocket and self.active_connections[client_id] != websocket:
                return

            del self.active_connections[client_id]
            self.last_pong_received_time.pop(client_id, None)
            
            user_id = self.client_id_to_user_id.pop(client_id, None)

            if user_id and user_id in self.user_id_to_client_ids:
                if client_id in self.user_id_to_client_ids[user_id]:
                    self.user_id_to_client_ids[user_id].remove(client_id)
                if not self.user_id_to_client_ids[user_id]:
                    del self.user_id_to_client_ids[user_id]
            
            # 4. Clean up subscriptions (Check presence before iterating)
            topics_set = self.client_id_to_topics.pop(client_id, None)
            if topics_set:
                for topic in list(topics_set):
                    await self.unsubscribe_from_topic(client_id, topic)

            feeds_set = self.client_id_to_feeds.pop(client_id, None)
            if feeds_set:
                for feed_id in list(feeds_set):
                    await self.unsubscribe_from_feed(client_id, feed_id)

            logger.info(
                f"WebSocket connection closed: client_id={client_id}. "
                f"Total connections: {len(self.active_connections)}"
            )

    async def _client_sender(self, client_id: str, websocket: WebSocket):
        """Background task to send messages from queue to websocket."""
        queue = self.client_queues.get(client_id)
        if not queue:
            return

        try:
            while True:
                message = await queue.get()
                try:
                    # Use a timeout for the actual socket send to detect dead sockets faster
                    await asyncio.wait_for(websocket.send_text(message), timeout=5.0)
                    queue.task_done()
                except (asyncio.TimeoutError, Exception) as e:
                    logger.warning(f"Error sending to {client_id}: {e}. Disconnecting.")
                    await self.disconnect(client_id, websocket)
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Unexpected error in sender task for {client_id}: {e}")

    def record_pong(self, client_id: str):
        """Record the time a PONG was received from a client."""
        if client_id in self.active_connections:
            self.last_pong_received_time[client_id] = time.time()
            logger.debug(f"Recorded PONG for client {client_id}")

    async def send_personal_message(self, message: str, client_id: str):
        """
        Send a message reliably (waits for queue space). 
        Use this for control messages (config updates, status changes).
        """
        if client_id in self.client_queues:
            try:
                # Wait for slot in queue with timeout to avoid blocking forever
                await asyncio.wait_for(self.client_queues[client_id].put(message), timeout=0.5)
            except asyncio.TimeoutError:
                 logger.warning(f"Client {client_id} queue full. Dropping reliable message to avoid blocking.")
            except Exception as e:
                 logger.error(f"Failed to enqueue message for {client_id}: {e}")

    async def send_realtime_message(self, message: str, client_id: str):
        """
        Send a message with 'fire-and-forget' logic.
        Use this for high-frequency data (video frames).
        Drops the message if the client is slow (queue full).
        """
        if client_id in self.client_queues:
            try:
                self.client_queues[client_id].put_nowait(message)
            except asyncio.QueueFull:
                # Queue is full, drop frame to prevent backing up backend
                # Optional: Log sporadically to avoid spam
                pass 
            except Exception as e:
                logger.error(f"Failed to enqueue realtime message for {client_id}: {e}")

    async def broadcast(self, message: str):
        """Broadcast reliable message to all."""
        # Iterate over a copy to allow modification (disconnection) during iteration
        for client_id in list(self.active_connections.keys()):
            await self.send_personal_message(message, client_id)

    async def broadcast_realtime(self, message: str):
        """Broadcast fire-and-forget message to all."""
        for client_id in list(self.active_connections.keys()):
            await self.send_realtime_message(message, client_id)

    async def send_to_user(self, user_id: str, message: str):
        client_ids = self.user_id_to_client_ids.get(user_id, [])
        for client_id in list(client_ids): 
            await self.send_personal_message(message, client_id)

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
        self.client_id_to_feeds.setdefault(client_id, set()).add(feed_id)
        logger.info(f"Client {client_id} subscribed to feed: {feed_id}")

    async def unsubscribe_from_feed(self, client_id: str, feed_id: str):
        if feed_id in self.feed_subscriptions and client_id in self.feed_subscriptions[feed_id]:
            self.feed_subscriptions[feed_id].remove(client_id)
            if not self.feed_subscriptions[feed_id]:
                del self.feed_subscriptions[feed_id]
            logger.info(f"Client {client_id} unsubscribed from feed: {feed_id}")
        
        if client_id in self.client_id_to_feeds and feed_id in self.client_id_to_feeds[client_id]:
            self.client_id_to_feeds[client_id].remove(feed_id)
            if not self.client_id_to_feeds[client_id]:
                del self.client_id_to_feeds[client_id]

    def get_clients_for_feed(self, feed_id: str) -> List[str]:
        return list(self.feed_subscriptions.get(feed_id, set()))

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
                await self.send_personal_message(message, client_id)

    async def _ping_clients(self):
        logger.info("Ping task started.")
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(self.ping_interval)
                # logger.debug("Ping task waking up.") 
                ping_message = WebSocketMessage(
                    type=WebSocketMessageTypeEnum.PING,
                    data=PingData().model_dump()
                ).model_dump_json()

                current_time = time.time()
                clients_to_disconnect = []

                if not self.active_connections:
                    continue

                for client_id, connection in list(self.active_connections.items()):
                    if connection.client_state == 2: # WebSocketState.DISCONNECTED
                        clients_to_disconnect.append(client_id)
                        continue

                    # Check if PONG was received within timeout
                    last_pong_time = self.last_pong_received_time.get(client_id, 0)
                    if current_time - last_pong_time > self.pong_timeout + self.ping_interval: 
                        logger.warning(f"Client {client_id} timed out (no PONG received). Last pong: {last_pong_time}, Now: {current_time}. Disconnecting.")
                        clients_to_disconnect.append(client_id)
                        continue

                    # Send PING via reliable queue
                    await self.send_personal_message(ping_message, client_id)
                
                for client_id in clients_to_disconnect:
                    await self.disconnect(client_id)

            except asyncio.CancelledError:
                logger.info("Ping task cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in ping task: {e}", exc_info=True)

    async def shutdown(self):
        logger.info("Shutting down ConnectionManager...")
        self._shutdown_event.set()
        
        # Cancel all sender tasks
        tasks = list(self.client_tasks.values())
        for task in tasks:
            task.cancel()
        
        if tasks:
            # Wait for all tasks to cancel to avoid "Task destroyed but pending"
            await asyncio.gather(*tasks, return_exceptions=True)
        
        for ws in self.active_connections.values():
            try:
                await ws.close()
            except Exception:
                pass
                
        self.active_connections.clear()
        self.client_queues.clear()
        self.client_tasks.clear()
        self.client_id_to_user_id.clear()
        self.user_id_to_client_ids.clear()
        self.topic_subscriptions.clear()
        self.client_id_to_topics.clear()
        self.last_pong_received_time.clear() # New: Clear pong tracking on shutdown
        logger.info("All WebSocket connections closed.")
