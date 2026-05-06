from __future__ import annotations
import asyncio
import logging
import time # Import time for timestamping
from enum import IntEnum
from typing import Dict, Optional, List, Set, Union
from fastapi import WebSocket
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum, PingData # Import necessary models

logger = logging.getLogger(__name__)

class MessagePriority(IntEnum):
    CRITICAL = 0   # Auth, Errors
    HIGH = 1       # Alerts, Incidents
    NORMAL = 2     # KPI Updates, Status
    LOW = 3        # Video Frames, Metrics

class PrioritizedMessage:
    def __init__(self, priority: MessagePriority, message: Union[str, bytes]):
        self.priority = priority
        self.message = message
        self.timestamp = time.time()
    
    def __lt__(self, other):
        # Higher priority (lower value) comes first
        if self.priority != other.priority:
            return self.priority < other.priority
        # For same priority, use FIFO (earlier timestamp first)
        return self.timestamp < other.timestamp

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
        pong_timeout: int = 120, # New: seconds to wait for a pong after a ping
    ):
        if hasattr(self, "_initialized") and self._initialized:
            return
        self._initialized = True
        self.active_connections: Dict[str, WebSocket] = {}
        self.client_id_to_user_id: Dict[str, str] = {}
        self.client_id_to_user_role: Dict[str, str] = {} # New: Track user roles
        self.user_id_to_client_ids: Dict[str, List[str]] = {}
        self.topic_subscriptions: Dict[str, Set[str]] = {}
        self.client_id_to_topics: Dict[str, Set[str]] = {}
        self.feed_subscriptions: Dict[str, Set[str]] = {}
        self.client_id_to_feeds: Dict[str, Set[str]] = {}
        self.last_pong_received_time: Dict[str, float] = {} # New: Track last pong time
        self.client_latencies: Dict[str, float] = {}  # Track RTT for adaptive behavior
        self._client_locks: Dict[str, asyncio.Lock] = {}
        
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

    async def _get_client_lock(self, client_id: str) -> asyncio.Lock:
        """Get or create an asyncio.Lock for a specific client_id to ensure atomic operations."""
        if client_id not in self._client_locks:
            self._client_locks[client_id] = asyncio.Lock()
        return self._client_locks[client_id]

    async def connect(self, websocket: WebSocket, client_id: str, user_id: str, user_role: str = "user"):
        async with await self._get_client_lock(client_id):
            if len(self.active_connections) >= self.max_connections:
                logger.warning(
                    f"Connection limit exceeded. Cannot accept new connection for client {client_id}."
                )
                await websocket.close(code=4000, reason="Connection limit exceeded")
                return

            # ... (Collision logic) ...
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
            self.client_id_to_user_role[client_id] = user_role # Store role

            if user_id not in self.user_id_to_client_ids:
                self.user_id_to_client_ids[user_id] = []
            
            if client_id not in self.user_id_to_client_ids[user_id]:
                self.user_id_to_client_ids[user_id].append(client_id)
                
            self.client_id_to_topics.setdefault(client_id, set())
            self.client_id_to_feeds.setdefault(client_id, set())
            self.last_pong_received_time[client_id] = time.time() # Initialize on connect

            # Initialize sender queue and task with adaptive sizing and priority
            queue_size = self._calculate_queue_size(client_id)
            # Use PriorityQueue to allow alerts to bypass video frames
            self.client_queues[client_id] = asyncio.PriorityQueue(maxsize=queue_size + 10) # Buffering for priority shifts
            self.client_tasks[client_id] = asyncio.create_task(self._client_sender(client_id, websocket))

            logger.info(
                f"New authenticated WebSocket connection: client_id={client_id}, user_id={user_id}. "
                f"Total connections: {len(self.active_connections)}"
            )

    async def disconnect(self, client_id: str, websocket: Optional[WebSocket] = None):
        async with await self._get_client_lock(client_id):
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
                self.client_id_to_user_role.pop(client_id, None) # Remove role
                self.last_pong_received_time.pop(client_id, None)
                self.client_latencies.pop(client_id, None)  # Clean up latency tracking
                
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
            
            # Cleanup the lock itself to prevent memory leak
            self._client_locks.pop(client_id, None)

    async def _client_sender(self, client_id: str, websocket: WebSocket):
        """Background task to send prioritized messages from queue to websocket."""
        queue = self.client_queues.get(client_id)
        if not queue:
            return
        
        # Diagnostics tracking
        msg_count = 0
        last_diag_time = time.time()

        try:
            while True:
                # PriorityQueue returns the highest priority (lowest value) item
                prioritized_msg = await queue.get()
                message = prioritized_msg.message
                
                # Periodic diagnostic logging
                msg_count += 1
                now = time.time()
                if now - last_diag_time > 30.0:
                    logger.debug(f"[Sender {client_id}] Queue size: {queue.qsize()} | Sent {msg_count} msgs in 30s")
                    last_diag_time = now
                    msg_count = 0
                
                try:
                    # Use a timeout for the actual socket send to detect dead sockets faster
                    if isinstance(message, bytes):
                        logger.debug(f"Sending binary frame to {client_id}, size: {len(message)} bytes")
                        await asyncio.wait_for(websocket.send_bytes(message), timeout=5.0)
                    else:
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

    def _calculate_queue_size(self, client_id: str) -> int:
        """Calculate adaptive queue size based on client latency."""
        base_queue_size = 100
        latency_ms = self.client_latencies.get(client_id, 50)  # Default 50ms
        
        # Higher latency = larger queue to buffer more frames
        if latency_ms > 200:
            return 500
        elif latency_ms > 100:
            return 200
        else:
            return base_queue_size
    
    def update_client_latency(self, client_id: str, rtt_ms: float):
        """Update tracked latency for adaptive behavior."""
        if client_id in self.active_connections:
            self.client_latencies[client_id] = rtt_ms
            logger.debug(f"Updated latency for client {client_id}: {rtt_ms}ms")
    
    def record_pong(self, client_id: str, rtt_ms: Optional[float] = None):
        """Record the time a PONG was received from a client."""
        if client_id in self.active_connections:
            self.last_pong_received_time[client_id] = time.time()
            if rtt_ms is not None:
                self.update_client_latency(client_id, rtt_ms)
            logger.debug(f"Recorded PONG for client {client_id}")

    async def send_personal_message(self, message: str, client_id: str, priority: MessagePriority = MessagePriority.NORMAL):
        """
        Send a message reliably (waits for queue space).
        Use this for control messages (config updates, status changes).
        """
        if client_id not in self.client_queues:
            return
        try:
            # Wrap in prioritized object
            wrapped_msg = PrioritizedMessage(priority, message)

            # Determine timeout based on priority to avoid blocking the event loop
            # indefinitely on a saturated CPU.
            if priority in (MessagePriority.CRITICAL, MessagePriority.HIGH):
                timeout = 5.0
            else:
                timeout = 1.5  # Increased from 0.5 for better resilience

            # Wait for slot in queue with timeout to avoid blocking forever.
            # PriorityQueue naturally places NORMAL/HIGH ahead of LOW, so KPIs
            # will be sent before video frames if the event loop can run.
            await asyncio.wait_for(self.client_queues[client_id].put(wrapped_msg), timeout=timeout)
        except asyncio.TimeoutError:
            # Log at INFO because this is actionable backpressure
            logger.info(f"Client {client_id} queue full. Dropping reliable message (priority {priority}) after {timeout}s timeout to avoid blocking.")
        except asyncio.QueueFull:
            logger.info(f"Client {client_id} queue full. Dropping reliable message (priority {priority}) – QueueFull.")
        except Exception as e:
             logger.error(f"Failed to enqueue message for {client_id}: {e}")

    async def send_realtime_message(self, message: str, client_id: str, priority: MessagePriority = MessagePriority.LOW):
        """
        Send a message with 'fire-and-forget' logic.
        Use this for high-frequency data (video frames).
        Drops the message if the client is slow (queue full).
        """
        if client_id in self.client_queues:
            try:
                wrapped_msg = PrioritizedMessage(priority, message)
                self.client_queues[client_id].put_nowait(wrapped_msg)
            except asyncio.QueueFull:
                # Queue is full, drop frame to prevent backing up backend
                pass 
            except Exception as e:
                logger.error(f"Failed to enqueue realtime message for {client_id}: {e}")

    async def broadcast(self, message: str, priority: MessagePriority = MessagePriority.NORMAL):
        """Broadcast reliable message to all with specific priority."""
        # Iterate over a copy to allow modification (disconnection) during iteration
        for client_id in list(self.active_connections.keys()):
            await self.send_personal_message(message, client_id, priority=priority)

    async def broadcast_realtime(self, message: str, priority: MessagePriority = MessagePriority.LOW):
        """Broadcast fire-and-forget message to all with specific priority."""
        tasks = []
        for client_id in list(self.active_connections.keys()):
            tasks.append(self.send_realtime_message(message, client_id, priority=priority))
        if tasks:
            await asyncio.gather(*tasks)

    async def broadcast_realtime_bytes(self, data: bytes):
        """Broadcast fire-and-forget binary message to all (Msgpack) with LOW priority."""
        for client_id in list(self.active_connections.keys()):
            if client_id in self.client_queues:
                try:
                    wrapped_msg = PrioritizedMessage(MessagePriority.LOW, data)
                    self.client_queues[client_id].put_nowait(wrapped_msg)
                except asyncio.QueueFull:
                    pass
                except Exception as e:
                    logger.error(f"Failed to enqueue binary message for {client_id}: {e}")

    async def broadcast_to_feed_realtime_bytes(self, feed_id: str, data: bytes, frame_index: int = 0):
        """
        Broadcast binary frame to subscribers with Graceful Degradation.
        Skips frames if client queue is heavily backlogged.
        """
        logger.debug(f"[CONN_MGR] broadcast_to_feed_realtime_bytes feed={feed_id} frame={frame_index} data_size={len(data)}")
        
        subscribed_clients = self.get_clients_for_feed(feed_id)
        if not subscribed_clients:
            logger.debug(f"[CONN_MGR] No subscribers for feed {feed_id}, skipping broadcast.")
            return
        
        logger.debug(f"[CONN_MGR] Found {len(subscribed_clients)} subscribed clients for feed={feed_id}")
        
        # Determine priority: Boost the first 10 frames to HIGH to ensure the frontend
        # transitions from 'starting' to 'running' immediately.
        priority = MessagePriority.LOW
        if frame_index < 10:
            priority = MessagePriority.HIGH
            logger.debug(f"[CONN_MGR] Frame {frame_index} boosted to HIGH priority")

        for client_id in subscribed_clients:
            if client_id not in self.client_queues:
                logger.warning(f"[CONN_MGR] Client {client_id} not in client_queues, skipping")
                continue
            
            # Graceful Degradation: Frame Skipping
            queue = self.client_queues[client_id]
            q_size = queue.qsize()
            q_max = queue.maxsize
            
            logger.debug(f"[CONN_MGR] Client {client_id} queue: {q_size}/{q_max} (priority={priority})")
            
            # Only apply skipping to LOW priority frames
            if priority == MessagePriority.LOW:
                skip_threshold = 0.0
                if q_size >= q_max * 0.9:
                    skip_threshold = 0.67
                elif q_size >= q_max * 0.75:
                    skip_threshold = 0.5
                
                if skip_threshold > 0:
                    if (frame_index % int(1/(1-skip_threshold))) != 0:
                        logger.info(f"[CONN_MGR] Skipping frame {frame_index} for client {client_id} (Queue: {q_size}/{q_max}, threshold={skip_threshold})")
                        continue

            try:
                wrapped_msg = PrioritizedMessage(priority, data)
                self.client_queues[client_id].put_nowait(wrapped_msg)
                logger.debug(f"[CONN_MGR] Enqueued frame {frame_index} for client {client_id} (queue now: {q_size+1}/{q_max})")
            except asyncio.QueueFull:
                logger.warning(f"[CONN_MGR] Queue full for client {client_id}, dropping frame {frame_index}")
                pass 
            except Exception as e:
                logger.error(f"[CONN_MGR] Failed to enqueue targeted binary message for {client_id}: {e}")
        
        logger.debug(f"[CONN_MGR] broadcast_to_feed_realtime_bytes completed for feed={feed_id} frame={frame_index}")

    def get_user_role(self, client_id: str) -> str:
        """Retrieve the role associated with a specific client connection."""
        return self.client_id_to_user_role.get(client_id, "user")

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

    async def broadcast_to_topic(self, message: str, topic: str, priority: MessagePriority = MessagePriority.NORMAL):
        if topic in self.topic_subscriptions:
            for client_id in list(self.topic_subscriptions[topic]):
                await self.send_personal_message(message, client_id, priority=priority)

    async def _ping_clients(self):
        logger.info("Ping task started.")
        while not self._shutdown_event.is_set():
            try:
                current_time = time.time()
                common_correlation_id = str(int(current_time * 1000))
                
                ping_message_obj = WebSocketMessage(
                    type=WebSocketMessageTypeEnum.PING,
                    timestamp=current_time * 1000,
                    correlation_id=common_correlation_id,
                    data=PingData().model_dump()
                )
                ping_message_json = ping_message_obj.model_dump_json()

                if not self.active_connections:
                    await asyncio.sleep(self.ping_interval)
                    continue

                # Process all clients concurrently to prevent one slow connection from blocking others
                async def ping_client(client_id, connection):
                    if connection.client_state == 2: # WebSocketState.DISCONNECTED
                        return client_id
                    
                    # Check if PONG was received within timeout
                    last_pong_time = self.last_pong_received_time.get(client_id, 0)
                    if current_time - last_pong_time > self.pong_timeout + self.ping_interval: 
                        logger.warning(f"Client {client_id} timed out (no PONG received). Disconnecting.")
                        return client_id
                    
                    try:
                        await self.send_personal_message(ping_message_json, client_id)
                    except Exception:
                        return client_id
                    return None

                results = await asyncio.gather(
                    *[ping_client(cid, conn) for cid, conn in self.active_connections.items()],
                    return_exceptions=True
                )

                # Disconnect timed-out clients
                for res in results:
                    if isinstance(res, str):
                        await self.disconnect(res)
                
                await asyncio.sleep(self.ping_interval)
            except asyncio.CancelledError:
                logger.info("Ping task cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in ping task: {e}", exc_info=True)
                await asyncio.sleep(1) # Prevent tight error loop

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
        self.client_latencies.clear()  # Clear latency tracking
        logger.info("All WebSocket connections closed.")
