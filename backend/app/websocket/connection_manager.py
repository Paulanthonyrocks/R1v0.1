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

        # Per-feed "first frames" tracking (LEGACY -- no longer used).
        #
        # The old behaviour boosted frames 0-9 per feed to HIGH priority so the
        # UI transitioned 'starting' -> 'running' quickly. Combined with the
        # latency-aware adaptive broadcast (per-client RTT decides full vs
        # small payload), that produced a visible resolution flip on high-RTT
        # tunnel clients: frames 0-9 sent full-res via the unbounded HIGH
        # priority queue (default 50ms RTT falls under any reasonable
        # threshold), then everything from frame 10 forward on a high-RTT link
        # dropped to the small payload. The HIGH path also bypasses the
        # bounded-deque backpressure safety net, so the first burst could
        # stall the sender and trigger visible "hang" periods on the frontend
        # while bytes kept flowing.
        #
        # We now keep everything on LOW priority; the bounded deque enforces
        # consistent backpressure for every frame, and the per-RTT payload
        # selection in broadcast_to_feed_realtime_bytes_adaptive is the only
        # authority on which payload size ships. The "starting -> running"
        # status transition on the frontend is driven by KPI/first-frame
        # reception over a short window, not by priority routing.
        self._sent_first_frames: set = set()  # retained for backward-compat reads (always empty)

        # Per-client locked payload-size decision ('full' or 'small') used by
        # broadcast_to_feed_realtime_bytes_adaptive. Once the first PONG samples
        # RTT, the choice is frozen so subsequent frames cannot flip size
        # mid-stream even if RTT drifts across the threshold. Cleared on
        # disconnect so a reconnect can re-evaluate.
        #
        # The matching ``_sampled_rtt_clients`` set tracks which clients have
        # at least one real PONG-derived latency sample. ``client_latencies``
        # also carries the connect-time default of 50 ms for sizing purposes,
        # but the adaptive broadcast must NOT treat that default as
        # authoritative -- otherwise the original "first frames full, then
        # flips to small when the first PONG lands" regression reappears on
        # always-high-RTT tunnels where the connect default falls under any
        # sensible threshold.
        self._adaptive_payload_choice: Dict[str, str] = {}
        self._sampled_rtt_clients: Set[str] = set()
        
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
            old_task = None
            if client_id in self.active_connections:
                old_user_id = self.client_id_to_user_id.get(client_id, "unknown")
                old_role = self.client_id_to_user_role.get(client_id, "unknown")
                logger.warning(f"Collision detected for {client_id} (user: {old_user_id}, role: {old_role}). Replacing old connection.")
                old_ws = self.active_connections[client_id]
                old_task = self.client_tasks.get(client_id)

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
                asyncio.create_task(self._disconnect_old_connection(client_id, old_ws, old_task))

            logger.info(
                f"New authenticated WebSocket connection: client_id={client_id}, user_id={user_id}. "
                f"Total connections: {len(self.active_connections)}"
            )

    async def disconnect(self, client_id: str, websocket: Optional[WebSocket] = None):
        async with await self._get_client_lock(client_id):
            await self._disconnect_unsafe(client_id, websocket)

    async def _disconnect_unsafe(self, client_id: str, websocket: Optional[WebSocket] = None):
        """Performs the actual resource cleanup for a client. 
        Assumes the client lock is already held by the caller.
        """
        logger.info(f"Disconnecting client {client_id}...")
        
        # 1. Close WebSocket if provided or found in active_connections
        ws = websocket or self.active_connections.get(client_id)
        if ws:
            try:
                # Only close if not already closed
                if ws.client_state != WebSocketState.DISCONNECTED:
                    await ws.close(code=1000)
            except Exception as e:
                logger.debug(f"Error closing WebSocket for {client_id}: {e}")

        # 2. Cancel and clean up the sender task
        task = self.client_tasks.pop(client_id, None)
        if task and not task.done():
            task.cancel()
            # We don't await the task here to avoid blocking the disconnect flow
            asyncio.create_task(self._await_task_safely(task))

        # 3. Remove from all mappings
        self.active_connections.pop(client_id, None)
        user_id = self.client_id_to_user_id.pop(client_id, None)
        if user_id and user_id in self.user_id_to_client_ids:
            if client_id in self.user_id_to_client_ids[user_id]:
                self.user_id_to_client_ids[user_id].remove(client_id)
            if not self.user_id_to_client_ids[user_id]:
                del self.user_id_to_client_ids[user_id]

        self.client_id_to_user_role.pop(client_id, None)
        self.last_pong_received_time.pop(client_id, None)
        self.client_latencies.pop(client_id, None)
        # Drop the locked payload-size choice so a reconnect re-evaluates.
        self._adaptive_payload_choice.pop(client_id, None)
        # Reset the "has-a-real-PONG" marker so the next session starts in
        # the conservative-small branch again.
        self._sampled_rtt_clients.discard(client_id)
        self.client_queues.pop(client_id, None)
        self.low_priority_queues.pop(client_id, None)
        self.signal_queues.pop(client_id, None)
        
        # Topic cleanup
        topics = self.client_id_to_topics.pop(client_id, set())
        for topic in topics:
            if topic in self.topic_subscriptions:
                self.topic_subscriptions[topic].discard(client_id)
                if not self.topic_subscriptions[topic]:
                    del self.topic_subscriptions[topic]

        # Feed cleanup
        feeds = self.client_id_to_feeds.pop(client_id, set())
        for feed_id in feeds:
            if feed_id in self.feed_subscriptions:
                self.feed_subscriptions[feed_id].discard(client_id)
                if not self.feed_subscriptions[feed_id]:
                    del self.feed_subscriptions[feed_id]

        # Lock cleanup: remove the lock to prevent memory growth
        self._client_locks.pop(client_id, None)
        
        logger.info(f"Client {client_id} successfully disconnected. Total active: {len(self.active_connections)}")

    async def _disconnect_old_connection(self, client_id: str, old_ws: WebSocket, old_task: Optional[asyncio.Task] = None):
        """Safely clean up a replaced connection without risking deadlocks.
        This is called in the background after a new connection has taken over.
        """
        try:
            # 1. Attempt to close the old socket first (non-blocking)
            try:
                await old_ws.close(code=1000, reason="Reconnected")
            except Exception as e:
                logger.debug(f"Error closing old WebSocket for {client_id}: {e}")

            # 2. Explicitly cancel the old sender task to avoid "WebSocketDisconnect" log spam.
            if old_task and not old_task.done():
                old_task.cancel()
                asyncio.create_task(self._await_task_safely(old_task))
            
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
                
                if websocket.client_state != WebSocketState.CONNECTED:
                    logger.info(f"[Sender {client_id}] WebSocket state is {websocket.client_state}. Exiting sender task.")
                    return

                # Process queues until both are empty
                while not high_priority_queue.empty() or low_priority_queue:
                    if websocket.client_state != WebSocketState.CONNECTED:
                        logger.info(f"[Sender {client_id}] WebSocket state is {websocket.client_state}. Stopping sender loop.")
                        return
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
                                if "close message has been sent" in str(e):
                                    logger.info(f"[Sender {client_id}] Connection closed (detected during send). Exiting task.")
                                    return
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
                            if "close message has been sent" in str(e):
                                logger.info(f"[Sender {client_id}] Connection closed (detected during send). Exiting task.")
                                return
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
        The original sizing (30/60/120) was tuned for LAN latency and shredded
        video down to ~2fps over high-latency tunnels (loca.lt / cloudflare /
        ngrok), since the bounded deque auto-drops the oldest frame under
        backpressure. Bumped ~3x so the sender can catch up instead of shedding,
        while remaining bounded to avoid unbounded memory growth per client.
        """
        latency_ms = self.client_latencies.get(client_id, 50)
        if latency_ms > 200:
            return 90    # was 30
        elif latency_ms > 100:
            return 180   # was 60
        return 360       # was 120; ~2.5s of 3-feed video at 15fps per client

    def _maybe_resize_low_priority_queue(self, client_id: str) -> None:
        """Re-create the per-client low_priority deque when RTT crosses a size
        tier boundary.

        ``deque(maxlen=...)`` is fixed at construction; mutating
        ``client_latencies`` alone does NOT change the bound on the existing
        deque. Before this fix, a client that connected with the default 50 ms
        latency sample (deque sized 360) and then had its first PONG sample at
        600 ms kept the 360-deep buffer for the entire session -- the
        "adaptive" sizing was decorative past connect, and a slow tunnel could
        accumulate ~24s of stale frames at 15fps before the deque started
        dropping. We now snapshot the deque contents into a fresh deque of the
        correct size whenever the target size changes.

        Called from ``update_client_latency`` only -- not on every frame -- so
        the cost is paid at most once per RTT transition (a handful of times
        per session).
        """
        if client_id not in self.low_priority_queues:
            return
        target = self._calculate_low_priority_queue_size(client_id)
        current = self.low_priority_queues[client_id]
        if current.maxlen == target:
            return
        # Snapshot into a new deque. Newer frames are at the right; if the
        # new bound is smaller, the auto-drop behavior of deque(maxlen=...)
        # trims from the LEFT (oldest) as we extend -- which is exactly the
        # drop policy we want (drop stale, keep fresh).
        new_q = deque(current, maxlen=target)
        self.low_priority_queues[client_id] = new_q
        logger.debug(
            f"[CONN_MGR] Resized low_priority_queue for {client_id}: "
            f"{current.maxlen} -> {target} (rtt={self.client_latencies.get(client_id)}ms)"
        )

    def update_client_latency(self, client_id: str, rtt_ms: float):
        """Update tracked latency for adaptive behavior."""
        if client_id in self.active_connections:
            self.client_latencies[client_id] = rtt_ms
            # Mark the client as having a real PONG-derived sample so the
            # adaptive broadcast will lock its payload-size decision on the
            # next frame. Before the first PONG lands we conservatively pick
            # the small payload (see broadcast_to_feed_realtime_bytes_adaptive)
            # -- this prevents the connect-time default of 50 ms (which lives
            # in client_latencies for sizing only) from being misread as a
            # low-RTT sample and triggering a full-res -> small-res flip when
            # the first real PONG arrives on a slow tunnel.
            self._sampled_rtt_clients.add(client_id)
            # The bounded deque's maxlen is fixed at construction; re-create
            # the deque if the new RTT puts the client in a different size
            # tier. Otherwise the "adaptive" sizing is a no-op past connect.
            self._maybe_resize_low_priority_queue(client_id)
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
            if client_id in self.signal_queues and self.signal_queues[client_id].qsize() < 100:
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

        Routed through ``_enqueue_frame`` so the bounded-deque backpressure
        and adaptive-resize behaviour are shared with the per-frame VIDEO_FRAME
        fan-out path. The previous inline append+signal reimplemented the same
        logic but silently bypassed any future backpressure improvements.
        """
        try:
            self._enqueue_frame(client_id, message, priority)
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
        """Broadcast fire-and-forget binary message to all (Msgpack) with LOW priority.

        Routed through ``_enqueue_frame`` for the same reason as
        ``send_realtime_message`` -- single source of truth for backpressure.
        """
        for client_id in list(self.active_connections.keys()):
            try:
                self._enqueue_frame(client_id, data, MessagePriority.LOW)
            except Exception as e:
                logger.error(f"Failed to enqueue binary message for {client_id}: {e}")

    def _enqueue_frame(self, client_id: str, data: Union[str, bytes], priority: MessagePriority):
        """Append a frame (binary or text) to the right per-client queue and
        wake the sender.

        Centralising the enqueue path here means every fire-and-forget and
        realtime broadcast -- not just the per-frame VIDEO_FRAME fan-out --
        shares the same backpressure semantics (bounded deque for LOW,
        bounded PriorityQueue for HIGH+) and the same signal-to-sender wake.
        Before this, ``send_realtime_message`` / ``broadcast_realtime`` /
        ``broadcast_realtime_bytes`` each reimplemented the deque append +
        signal inlined, which meant any future change to backpressure (e.g.
        the adaptive deque resize in ``_maybe_resize_low_priority_queue``)
        would silently bypass those three callers.
        """
        if priority == MessagePriority.HIGH:
            # High-priority (initial) frames go to the PriorityQueue
            if client_id in self.client_queues:
                try:
                    wrapped_msg = PrioritizedMessage(priority, data)
                    self.client_queues[client_id].put_nowait(wrapped_msg)
                    if client_id in self.signal_queues:
                        self.signal_queues[client_id].put_nowait(True)
                except asyncio.QueueFull:
                    logger.warning(f"[CONN_MGR] High-priority queue full for client {client_id}, dropping frame")
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

    def _frame_priority(self, feed_id: str, frame_index: int) -> MessagePriority:
        """Return priority for an outgoing VIDEO_FRAME.

        Previously this boosted the first 10 frames per feed to HIGH so the
        UI transitioned 'starting' -> 'running' quickly. Combined with the
        latency-aware adaptive broadcast path, that produced a visible
        resolution flip on high-RTT tunnel clients (frames 0-9 served full-res
        from the unbounded PriorityQueue; frames 10+ routed to the bounded
        deque and resized to the small payload). It also let the first burst
        bypass backpressure entirely, surfacing as a "frames hang then jump"
        symptom on slow networks.

        We now send every frame through the LOW priority path so the bounded
        deque enforces uniform backpressure and the per-RTT payload decision
        in broadcast_to_feed_realtime_bytes_adaptive is the only authority on
        how the frame is sized. The frontend's 'starting'/'running' status is
        driven by feed-status messages, NOT by per-frame priority routing.
        """
        # NOTE: ``_sent_first_frames`` is intentionally left empty/no-op now.
        return MessagePriority.LOW

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
        priority = self._frame_priority(feed_id, frame_index)
        if priority == MessagePriority.HIGH:
            logger.debug(f"[CONN_MGR] Frame {frame_index} boosted to HIGH priority (first frames for {feed_id})")
        for client_id in subscribed_clients:
            self._enqueue_frame(client_id, data, priority)
        logger.debug(f"[CONN_MGR] broadcast_to_feed_realtime_bytes completed for feed={feed_id} frame={frame_index}")

    async def broadcast_to_feed_realtime_bytes_adaptive(
        self, feed_id: str, full_data: bytes, small_data: bytes, frame_index: int = 0, latency_threshold_ms: float = 120
    ):
        """
        Latency-aware frame fan-out. Each subscribed client receives the
        full-resolution payload if its tracked RTT is at or below
        ``latency_threshold_ms`` (LAN / good links -> crisp video), otherwise
        the downscaled payload (high-latency tunnels like loca.lt / cloudflare /
        ngrok -> bandwidth saved). RTT comes from the ping/pong loop
        (``record_pong``); clients with no latency sample default to the small
        payload to stay safe under unknown network conditions.

        Persisted-per-client decision cache
        -----------------------------------
        ``client_latencies[client_id]`` is only populated after the first PONG
        round-trips, which on slow links can take 1-2s. Without a stabiliser,
        the *connect-time default* (50 ms) falls well under any sane
        ``latency_threshold_ms``, so the first frames are sent full-res while
        the very next PONG flips the client to the small tier. The visible
        symptom was a brief burst of crisp frames followed by coarse frames
        and a hang as the priority queue drained the unexpectedly-large
        full-res payloads into a network already at its bandwidth ceiling.

        We resolve this in two complementary ways:

        1. Once we have *any* latency sample for a client, lock that decision
           in ``_adaptive_payload_choice`` (full / small) and reuse it for
           every subsequent frame. This eliminates per-frame jitter when RTT
           hovers around the threshold (e.g. 245 ms vs 250 ms) and prevents
           the size from oscillating each ping.

        2. Until the first PONG arrives, treat the client as "high-latency
           unknown" and route the very first frames through the SMALL payload.
           By the time the first PONG samples the RTT, we have already shipped
           a few small frames; subsequent frames follow the stable cached
           decision. The user never sees a full-res -> small-res flip.

        The reactive backpressure (bounded deque in ``_enqueue_frame``) still
        discards over-age frames under sustained overload, so a brief tunnel
        delay on the first few frames will not stall the stream.
        """
        logger.debug(f"[CONN_MGR] adaptive broadcast feed={feed_id} frame={frame_index} thr={latency_threshold_ms}ms")
        subscribed_clients = self.get_clients_for_feed(feed_id)
        if not subscribed_clients:
            logger.debug(f"[CONN_MGR] No subscribers for feed {feed_id}, skipping adaptive broadcast.")
            return
        priority = self._frame_priority(feed_id, frame_index)
        for client_id in subscribed_clients:
            # Lock the size decision per client once we have a real PONG sample.
            # _sampled_rtt_clients (populated by update_client_latency) is the
            # authoritative signal; client_latencies alone is unreliable because
            # the connect-time 50 ms default lives there too. Without this
            # gate the very first PONG on a slow tunnel would flip the cache
            # from full to small (the original "highest then shittiest"
            # symptom).
            cached_choice = self._adaptive_payload_choice.get(client_id)
            if cached_choice is not None:
                data = full_data if cached_choice == 'full' else small_data
            elif client_id in self._sampled_rtt_clients:
                rtt = self.client_latencies[client_id]
                choice = 'full' if rtt <= latency_threshold_ms else 'small'
                self._adaptive_payload_choice[client_id] = choice
                data = full_data if choice == 'full' else small_data
            else:
                # No PONG yet: conservative pick (small payload). Holds the
                # line until the first RTT sample lands, at which point the
                # cache above locks in the decision for the rest of the
                # session -- no resolution flip mid-stream.
                data = small_data
            self._enqueue_frame(client_id, data, priority)
        logger.debug(f"[CONN_MGR] adaptive broadcast completed for feed={feed_id} frame={frame_index}")

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
