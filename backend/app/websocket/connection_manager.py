from __future__ import annotations
import asyncio
import logging
import time # Import time for timestamping
from enum import IntEnum
from typing import Dict, Optional, List, Set, Union
from collections import deque
from fastapi import WebSocket
from starlette.websockets import WebSocketState
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
        self.client_queues: Dict[str, asyncio.PriorityQueue] = {} # Now specifically for NORMAL+
        self.low_priority_queues: Dict[str, deque] = {}           # For LOW priority (video, etc.)
        self.signal_queues: Dict[str, asyncio.Queue] = {}         # Signals sender task
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
        
        # Cancel existing ping task if it's running to apply new configuration
        if hasattr(self, "_ping_task") and self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
            
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

            # Handle reconnection: Close existing connection if present
            old_ws = None
            if client_id in self.active_connections:
                old_user_id = self.client_id_to_user_id.get(client_id, "unknown")
                old_role = self.client_id_to_user_role.get(client_id, "unknown")
                logger.warning(f"Collision detected for {client_id} (user: {old_user_id}, role: {old_role}). Replacing old connection.")
                old_ws = self.active_connections[client_id]

            # 1. Establish NEW connection first to minimize broadcast gaps
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

            # Initialize sender queues and task with adaptive sizing and priority
            high_q_size = self._calculate_high_priority_queue_size(client_id)
            low_q_size = self._calculate_low_priority_queue_size(client_id)
            
            self.client_queues[client_id] = asyncio.PriorityQueue(maxsize=high_q_size + 10)
            self.low_priority_queues[client_id] = deque(maxlen=low_q_size)
            self.signal_queues[client_id] = asyncio.Queue()
            self.client_tasks[client_id] = asyncio.create_task(self._client_sender(client_id, websocket))

            # 2. Now clean up the OLD connection resources if they existed
            if old_ws:
                # We use a background task to avoid blocking the new connection's setup
                # and to prevent deadlock if the old task is still hanging.
                asyncio.create_task(self._disconnect_old_connection(client_id, old_ws))

            logger.info(
                f"New authenticated WebSocket connection: client_id={client_id}, user_id={user_id}. "
                f"Total connections: {len(self.active_connections)}"
            )

    async def disconnect(self, client_id: str, websocket: Optional[WebSocket] = None):
        async with await self._get_client_lock(client_id):
            await self._disconnect_unsafe(client_id, websocket)

    async def _disconnect_old_connection(self, client_id: str, old_ws: WebSocket):
        """Safely clean up a replaced connection without risking deadlocks.
        This is called in the background after a new connection has taken over.
        """
        try:
            # 1. Attempt to close the old socket first (non-blocking)
            try:
                await old_ws.close(code=1000, reason="Reconnected")
            except Exception as e:
                logger.debug(f"Error closing old WebSocket for {client_id}: {e}")

            # 2. Handle the sender task cancellation.
            # We do NOT acquire the client lock here to avoid deadlocks if the 
            # sender task is blocked on that same lock.
            # Instead, we only cancel the task if it's still the one associated 
            # with the old connection. However, the new connection has already 
            # overwritten self.client_tasks[client_id]. 
            # To fix this, we would have needed to capture the task reference 
            # in the connect() method. 
            # Since we only have the WebSocket, we rely on the fact that the 
            # old task will likely fail on its next send attempt to the closed socket.
            
            logger.debug(f"Old connection resources for {client_id} processed.")
        except Exception as e:
            logger.error(f"Error during deferred old connection cleanup for {client_id}: {e}")

    async def _await_task_safely(self, task: asyncio.Task):
        """Helper to await a cancelled task without blocking the main flow."""
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error during background task cleanup: {e}")

    async def _client_sender(self, client_id: str, websocket: WebSocket):
        """Background task to send prioritized messages from dual queues to websocket.
        
        Implementation:
        1. Wait for a signal from the signal_queue.
        2. Interleave high-priority (NORMAL+) and low-priority (LOW) messages.
        3. If low-priority frames are available, send 1 frame for every 5 high-priority messages.
        4. If no low-priority frames are available, high-priority messages flow normally.
        """
        logger.info(f"[Sender {client_id}] Task started.")
        high_priority_queue = self.client_queues.get(client_id)
        low_priority_queue = self.low_priority_queues.get(client_id)
        signal_queue = self.signal_queues.get(client_id)
        
        if high_priority_queue is None or low_priority_queue is None or signal_queue is None:
            logger.error(f"[Sender {client_id}] Task exiting: Missing queues. HighQ: {bool(high_priority_queue)}, LowQ: {bool(low_priority_queue)}, SigQ: {bool(signal_queue)}")
            return
        
        logger.info(f"[Sender {client_id}] Task entered main loop.")
        # Diagnostics tracking
        msg_count = 0
        last_diag_time = time.time()
        high_msg_streak = 0

        try:
            while True:
                # Wait for a signal that new data is available
                await signal_queue.get()
                
                # Process queues until both are empty
                while not high_priority_queue.empty() or low_priority_queue:
                    sent_something = False
                    
                    # 1. High-priority send logic
                    # We send high priority if:
                    # - We haven't hit the streak limit (5)
                    # - OR there are no low-priority frames available to interleave
                    if not high_priority_queue.empty():
                        if high_msg_streak < 5 or not low_priority_queue:
                            try:
                                prioritized_msg = high_priority_queue.get_nowait()
                                message = prioritized_msg.message
                                
                                msg_count += 1
                                high_msg_streak += 1
                                sent_something = True
                                
                                if isinstance(message, bytes):
                                    await asyncio.wait_for(websocket.send_bytes(message), timeout=5.0)
                                else:
                                    await asyncio.wait_for(websocket.send_text(message), timeout=5.0)
                                high_priority_queue.task_done()
                            except asyncio.QueueEmpty:
                                pass
                            except (asyncio.TimeoutError, Exception) as e:
                                logger.warning(f"[Sender {client_id}] Timeout or error sending high-priority msg: {repr(e)}. Dropping message.")
                                high_priority_queue.task_done()

                    # 2. Low-priority send logic
                    # We send a frame if:
                    # - We've hit the streak limit (high_msg_streak >= 5)
                    # - OR there are no high-priority messages left
                    if low_priority_queue and (high_msg_streak >= 5 or high_priority_queue.empty()):
                        try:
                            message = low_priority_queue.popleft()
                            msg_count += 1
                            sent_something = True
                            high_msg_streak = 0 # Reset streak after interleaving
                            
                            if isinstance(message, bytes):
                                await asyncio.wait_for(websocket.send_bytes(message), timeout=5.0)
                            else:
                                await asyncio.wait_for(websocket.send_text(message), timeout=5.0)
                        except IndexError:
                            pass
                        except (asyncio.TimeoutError, Exception) as e:
                            logger.warning(f"[Sender {client_id}] Timeout or error sending low-priority msg: {repr(e)}. Dropping message.")
                    
                    # If we hit the streak limit but the low-priority queue was empty,
                    # we must reset the streak to allow high-priority messages to continue flowing.
                    if high_msg_streak >= 5 and not low_priority_queue:
                        high_msg_streak = 0

                    if not sent_something:
                        break

                    # Periodic diagnostic logging
                    now = time.time()
                    if now - last_diag_time > 30.0:
                        logger.debug(f"[Sender {client_id}] Sent {msg_count} msgs in 30s. HighQ: {high_priority_queue.qsize()} | LowQ: {len(low_priority_queue)}")
                        last_diag_time = now
                        msg_count = 0
                    
        except asyncio.CancelledError:
            logger.info(f"[Sender {client_id}] Task cancelled.")
        except Exception as e:
            logger.error(f"Unexpected error in sender task for {client_id}: {e}", exc_info=True)
        finally:
            logger.info(f"[Sender {client_id}] Task exiting.")

    def _calculate_high_priority_queue_size(self, client_id: str) -> int:
        """Calculate adaptive queue size for high-priority messages."""
        latency_ms = self.client_latencies.get(client_id, 50)
        if latency_ms > 200:
            return 1000
        elif latency_ms > 100:
            return 600
        return 300

    def _calculate_low_priority_queue_size(self, client_id: str) -> int:
        """Calculate adaptive queue size for low-priority messages (video).
        For video, we want to avoid large buffers that cause stale frames.
        Higher latency clients should have SMALLER buffers to force real-time updates.
        """
        latency_ms = self.client_latencies.get(client_id, 50)
        if latency_ms > 200:
            return 30   # Very aggressive dropping for high latency
        elif latency_ms > 100:
            return 60
        return 120      # ~2 seconds of 60fps total across feeds (simplified)
    
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
            wrapped_msg = PrioritizedMessage(priority, message)

            # Determine timeout based on priority to avoid blocking the event loop
            if priority == MessagePriority.CRITICAL:
                timeout = None # Wait indefinitely for critical messages
            elif priority == MessagePriority.HIGH:
                timeout = 5.0
            else:
                timeout = 1.5

            # Enqueue to high-priority queue
            queue = self.client_queues[client_id]
            if timeout is None:
                await queue.put(wrapped_msg)
            else:
                await asyncio.wait_for(queue.put(wrapped_msg), timeout=timeout)
            
            # Signal the sender task
            if client_id in self.signal_queues:
                self.signal_queues[client_id].put_nowait(True)
        except asyncio.TimeoutError:
            logger.info(f"Client {client_id} queue full. Dropping reliable message (priority {priority}) after {timeout}s timeout.")
        except asyncio.QueueFull:
            logger.info(f"Client {client_id} queue full. Dropping reliable message (priority {priority}).")
        except Exception as e:
             logger.error(f"Failed to enqueue message for {client_id}: {e}")

    async def send_realtime_message(self, message: str, client_id: str, priority: MessagePriority = MessagePriority.LOW):
        """
        Send a message with 'fire-and-forget' logic.
        Use this for high-frequency data (video frames).
        """
        if client_id in self.low_priority_queues:
            try:
                # Use the deque for LOW priority messages (automatic dropping via maxlen)
                self.low_priority_queues[client_id].append(message)
                
                # Signal the sender task
                if client_id in self.signal_queues:
                    self.signal_queues[client_id].put_nowait(True)
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
            if client_id in self.low_priority_queues:
                try:
                    # Use the deque for binary frames
                    self.low_priority_queues[client_id].append(data)
                    if client_id in self.signal_queues:
                        self.signal_queues[client_id].put_nowait(True)
                except Exception as e:
                    logger.error(f"Failed to enqueue binary message for {client_id}: {e}")

    async def broadcast_to_feed_realtime_bytes(self, feed_id: str, data: bytes, frame_index: int = 0):
        """
        Broadcast binary frame to subscribers.
        LOW priority frames are routed to the low_priority_queue (deque) for automatic dropping.
        High priority (initial frames) go to the client_queue.
        """
        logger.debug(f"[CONN_MGR] broadcast_to_feed_realtime_bytes feed={feed_id} frame={frame_index} data_size={len(data)}")
        
        subscribed_clients = self.get_clients_for_feed(feed_id)
        if not subscribed_clients:
            logger.debug(f"[CONN_MGR] No subscribers for feed {feed_id}, skipping broadcast.")
            return
        
        # Determine priority: Boost the first 10 frames to HIGH to ensure the frontend
        # transitions from 'starting' to 'running' immediately.
        priority = MessagePriority.LOW
        if frame_index < 10:
            priority = MessagePriority.HIGH
            logger.debug(f"[CONN_MGR] Frame {frame_index} boosted to HIGH priority")

        for client_id in subscribed_clients:
            if priority == MessagePriority.HIGH:
                # High priority frames go to the PriorityQueue
                if client_id in self.client_queues:
                    try:
                        wrapped_msg = PrioritizedMessage(priority, data)
                        self.client_queues[client_id].put_nowait(wrapped_msg)
                        if client_id in self.signal_queues:
                            self.signal_queues[client_id].put_nowait(True)
                    except asyncio.QueueFull:
                        logger.warning(f"[CONN_MGR] High-priority queue full for client {client_id}, dropping frame {frame_index}")
                    except Exception as e:
                        logger.error(f"[CONN_MGR] Failed to enqueue high-priority frame for {client_id}: {e}")
            else:
                # LOW priority frames go to the deque for automatic dropping (via maxlen)
                if client_id in self.low_priority_queues:
                    try:
                        self.low_priority_queues[client_id].append(data)
                        if client_id in self.signal_queues:
                            self.signal_queues[client_id].put_nowait(True)
                    except Exception as e:
                        logger.error(f"[CONN_MGR] Failed to enqueue low-priority frame for {client_id}: {e}")
                else:
                    logger.warning(f"[CONN_MGR] Client {client_id} has no low_priority_queue, skipping")
        
        logger.debug(f"[CONN_MGR] broadcast_to_feed_realtime_bytes completed for feed={feed_id} frame={frame_index}")

    def get_user_role(self, client_id: str) -> str:
        """Retrieve the role associated with a specific client connection."""
        return self.client_id_to_user_role.get(client_id, "user")

    def update_user_role(self, client_id: str, role: str):
        """Update the role for an existing client connection."""
        if client_id in self.client_id_to_user_role:
            self.client_id_to_user_role[client_id] = role
            logger.debug(f"Updated role for client {client_id} to {role}")

    async def send_to_user(self, user_id: str, message: str):
        client_ids = self.user_id_to_client_ids.get(user_id, [])
        for client_id in list(client_ids): 
            await self.send_personal_message(message, client_id)

    async def subscribe_to_topic(self, client_id: str, topic: str, on_subscribe_callback: Optional[callable] = None):
        if client_id not in self.active_connections:
            return

        if topic not in self.topic_subscriptions:
            self.topic_subscriptions[topic] = set()
        self.topic_subscriptions[topic].add(client_id)
        self.client_id_to_topics.setdefault(client_id, set()).add(topic)
        logger.info(f"Client {client_id} subscribed to topic: {topic}")
        
        if on_subscribe_callback:
            try:
                await on_subscribe_callback(client_id)
            except Exception as e:
                logger.error(f"Error executing on_subscribe_callback for client {client_id} on topic {topic}: {e}")

    async def subscribe_to_feed(self, client_id: str, feed_id: str):
        if client_id not in self.active_connections:
            logger.warning(f"Attempted to subscribe inactive client {client_id} to feed {feed_id}")
            return

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
            clients = list(self.topic_subscriptions[topic])
            tasks = [self.send_personal_message(message, client_id, priority=priority) 
                    for client_id in clients]
            if tasks:
                await asyncio.gather(*tasks)

    async def _ping_single_client(self, client_id: str, websocket: WebSocket, ping_message_json: str, current_time: float):
        """Helper to ping a single client and check for timeout."""
        if websocket.client_state == WebSocketState.DISCONNECTED:
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

    async def _ping_clients(self):
        logger.info("Ping task started.")
        while not self._shutdown_event.is_set():
            try:
                current_time = time.time()
                
                if not self.active_connections:
                    await asyncio.sleep(self.ping_interval)
                    continue

                # Process all clients concurrently
                tasks = []
                for client_id, websocket in list(self.active_connections.items()):
                    # Generate a per-client correlation ID for more accurate RTT tracking
                    correlation_id = f"{int(current_time * 1000)}_{client_id}"
                    
                    ping_message_obj = WebSocketMessage(
                        type=WebSocketMessageTypeEnum.PING,
                        timestamp=current_time * 1000,
                        correlation_id=correlation_id,
                        data=PingData().model_dump()
                    )
                    ping_message_json = ping_message_obj.model_dump_json()
                    tasks.append(self._ping_single_client(client_id, websocket, ping_message_json, current_time))

                results = await asyncio.gather(*tasks, return_exceptions=True)

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
