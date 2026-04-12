from __future__ import annotations
import base64
import asyncio
import logging
import time
import re
import atexit
import json
import numpy as np
import msgpack
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from multiprocessing import (
    Process,
    Queue as MPQueue,
    Event,
    Value,
)
from typing import Dict, Any, Optional, List
from pathlib import Path
import queue  # For queue.Empty exception
from datetime import datetime, timezone, timedelta
# Import custom exceptions
from app.services.exceptions import FeedNotFoundError, FeedOperationError, ResourceLimitError
# Import Pydantic models
from app.models.feeds import (
    FeedStatusData,
    FeedConfigInfo,
    FeedOperationalStatusEnum,
)
from app.models.websocket import (
    WebSocketMessage,
    WebSocketMessageTypeEnum,
    FeedStatusUpdate,
    GlobalRealtimeMetrics,
)
# Import core worker and utilities
from app.core.ingestion_worker import ingestion_worker
from app.core.inference_worker import inference_worker
# from app.core.analytics_worker import analytics_worker_process
# from app.core.processing_worker import result_reader_worker
from app.utils.monitoring import FrameTimer, check_system_resources
from app.utils.distributed_queue import RedisQueue
from app.websocket.connection_manager import ConnectionManager
from app.services.analytics_service import AnalyticsService
from app.services.reid_manager import GlobalReIDManager
from app.tasks.prediction_scheduler import PredictionScheduler
from app.services.video_writer import VideoWriter
from concurrent.futures import ThreadPoolExecutor
logger = logging.getLogger("app.services.feed_manager")
# Constants
PROCESS_JOIN_TIMEOUT = 3.0
QUEUE_MAX_SIZE = 2000
QUEUE_DRAIN_LIMIT = 100
MAX_METRICS_HISTORY_LENGTH = 1000  # Safety cap for deque
class FeedManager:
    def __init__(self, config: Dict[str, Any]):
        # --- CRITICAL: Set multiprocessing start method for stability ---
        import multiprocessing
        ctx = multiprocessing.get_context('spawn')
        self._mp_ctx = ctx # Store context for future primitive creation
        
        try:
            # Use 'spawn' for better safety with CUDA/AI libraries and to avoid reentrant logging errors
            multiprocessing.set_start_method('spawn', force=True)
            logger.info("Multiprocessing start method set to 'spawn' for stability.")
        except RuntimeError:
            # Already set, ignore
            pass
        self.config = config
        self.process_registry: Dict[str, Dict[str, Any]] = {}
        self.video_writers: Dict[str, VideoWriter] = {}
        self._lock = asyncio.Lock()
        # Dedicated thread pool for CPU bound tasks (Base64 encoding)
        self._cpu_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="FeedEncoder")
        self._global_fps = None
        self._feed_id_counter = 1
        self._stop_reader_flag = False
        self._result_reader_task: Optional[asyncio.Task] = None
        self.frame_subscriber_queues: Dict[str, List[asyncio.Queue]] = {}
        self._active_broadcast_tasks: Dict[str, asyncio.Task] = {} # Track per-feed broadcast tasks
        self._connection_manager: Optional[ConnectionManager] = None
        self._prediction_scheduler: Optional[PredictionScheduler] = None
        self._analytics_service: Optional[AnalyticsService] = None
        self._reid_manager = GlobalReIDManager(config)
        self._is_processing_active: bool = False
        self._last_kpi_broadcast_time = 0.0
        self._kpi_broadcast_interval = self.config.get("kpi_broadcast_interval", 1.0)
        self._sample_feed_ids: List[str] = []
        self._feed_running_events: Dict[str, asyncio.Event] = {}
        self.logger = logger
        # Adaptive delay settings
        self._min_read_delay = self.config.get("min_frame_read_delay_ms", 1) / 1000.0
        self._max_read_delay = self.config.get("max_frame_read_delay_ms", 100) / 1000.0
        self._current_read_delay = self._min_read_delay
        self._delay_adjustment_factor = self.config.get("delay_adjustment_factor", 1.1)
        self._last_queue_log_time = 0.0
        self._queue_log_interval = self.config.get("queue_log_interval", 15.0)
        # Metrics aggregation window
        self._metrics_averaging_window = self.config.get("metrics_averaging_window_seconds", 10)
        # Persistence
        self.persistence_path = Path(self.config.get("feeds_config_path", "backend/data/feeds_config.json"))
        # Broadcast Optimization
        self.broadcast_queue = asyncio.Queue(maxsize=100)
        self._broadcast_worker_task: Optional[asyncio.Task] = None
        # Database processing
        self._db_queue: Optional[MPQueue] = ctx.Queue(maxsize=100000)
        self._db_reader_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        # Decoupled Processing Pool (Partitioned by Feed ID for State consistency)
        self._inference_pool_size = self.config.get("performance", {}).get("inference_pool_size", 2)
        redis_cfg = self.config.get("redis", {})
        # We divide the QUEUE_MAX_SIZE among workers
        per_worker_q_size = max(50, QUEUE_MAX_SIZE // self._inference_pool_size)
        use_redis = redis_cfg.get("enabled", False)
        if use_redis:
            try:
                # Test connection specifically before creating queues
                from app.utils.redis_client import get_redis_client
                # This will raise ConnectionError if Redis is down
                get_redis_client().ping()
                self._inference_input_queues = [
                    RedisQueue(f"inference_input_{i}", maxsize=per_worker_q_size) 
                    for i in range(self._inference_pool_size)
                ]
                self._central_output_queue = RedisQueue("central_output", maxsize=QUEUE_MAX_SIZE)
                
                # Flush queues to avoid stale/corrupt data from previous versions or crashes
                for q in self._inference_input_queues:
                    q.clear()
                self._central_output_queue.clear()
                
                logger.info("Using Redis for inference queues (cleared stale data).")
            except Exception as e:
                logger.warning(f"Redis enabled but connection failed: {e}. Falling back to multiprocessing queues.")
                use_redis = False
        if not use_redis:
            self._inference_input_queues = [ctx.Queue(maxsize=per_worker_q_size) for _ in range(self._inference_pool_size)]
            self._central_output_queue = ctx.Queue(maxsize=QUEUE_MAX_SIZE)
            logger.info("Using Multiprocessing Queues for inference.")
        self._inference_pool: List[Process] = []
        self._inference_command_queues: List[MPQueue] = []
        self._inference_stop_event = ctx.Event()
        # Initialize shared values before starting pool
        self.initialize_shared_values()
        self._start_inference_pool()
# Analytics Process bypassed for efficiency
        self._dropped_analytics_count = 0
        self._initialize_available_feeds()
        # Register cleanup on exit
        atexit.register(self._atexit_cleanup)
        # Start background readers and watchdog
        self._broadcast_worker_task = asyncio.create_task(self._broadcast_worker())
        self._result_reader_task = asyncio.create_task(self._read_result_queues())
        # self._analytics_reader_task = None
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())
        # Executor for blocking ReID calls
        self._reid_executor = ThreadPoolExecutor(max_workers=4)
        self._last_vehicle_db_write: Dict[Tuple[str, int], float] = {}
        # WebRTC Registry: feed_id -> {pc_id -> asyncio.Queue}
        self._webrtc_queues: Dict[str, Dict[int, asyncio.Queue]] = {}
        # Spatial Debouncing: feed_id -> {vehicle_id -> (last_x, last_y, last_vx, last_vy, timestamp)}
        self._last_sent_telemetry: Dict[str, Dict[int, Tuple[float, float, float, float, float]]] = {}
        self.logger.info("FeedManager initialized. Inference, Analytics, and Watchdog tasks started.")
    def _sync_match_or_register(self, feed_id: str, v: Dict) -> Optional[str]:
        """Wrapper for synchronous match_or_register call."""
        if not self._reid_manager or v.get("embedding") is None:
            return None
        try:
            emb_np = np.array(v["embedding"], dtype=np.float32)
            return self._reid_manager.match_or_register(
                feed_id=feed_id,
                local_id=v["vehicle_id"],
                embedding=emb_np,
                metadata={"class_name": v.get("class_name")}
            )
        except Exception as e:
            logger.error(f"ReID match error for {v.get('vehicle_id')}: {e}")
            return None
    # NOTE: _read_db_queue is defined below (after set_incident_manager).
    # A previous duplicate definition was removed here during pipeline optimization.
    def register_webrtc_queue(self, feed_id: str, pc_id: int, q: asyncio.Queue):
        if feed_id not in self._webrtc_queues:
            self._webrtc_queues[feed_id] = {}
        self._webrtc_queues[feed_id][pc_id] = q
        logger.info(f"Registered WebRTC queue for feed '{feed_id}', peer {pc_id}")
    def unregister_webrtc_queue(self, feed_id: str, pc_id: int):
        if feed_id in self._webrtc_queues and pc_id in self._webrtc_queues[feed_id]:
            del self._webrtc_queues[feed_id][pc_id]
            logger.info(f"Unregistered WebRTC queue for feed '{feed_id}', peer {pc_id}")
    def _atexit_cleanup(self):
        """Synchronous cleanup for interpreter exit."""
        logger.info("FeedManager executing atexit cleanup...")
        # 0. Cleanup Analytics Process
#         if self._analytics_process and self._analytics_process.is_alive():
#             logger.info(f"Terminating analytics process {self._analytics_process.pid} in atexit")
#             self._analytics_process.terminate()
#             self._analytics_process.join(timeout=0.5)
#             if self._analytics_process.is_alive():
#                 self._analytics_process.kill()
        # 1. Cleanup Registry (Ingestion Workers)
        for feed_id, entry in list(self.process_registry.items()):
            # Close command queue
            if entry.get("command_queue"):
                try:
                    entry["command_queue"].close()
                    entry["command_queue"].cancel_join_thread()
                except: pass
            process = entry.get("process")
            if process and process.is_alive():
                logger.info(f"Terminating ingestion process {process.pid} for {feed_id} in atexit")
                process.terminate()
                process.join(timeout=0.5)
                if process.is_alive():
                    process.kill()
        # 2. Cleanup Inference Pool
        # Close input queues
        for q in self._inference_input_queues:
            try:
                q.close()
                q.cancel_join_thread()
            except: pass
        # Close command queues
        for q in self._inference_command_queues:
            try:
                q.close()
                q.cancel_join_thread()
            except: pass
        # Close central output queue
        try:
            self._central_output_queue.close()
            self._central_output_queue.cancel_join_thread()
        except: pass
        for p in self._inference_pool:
            if p.is_alive():
                logger.info(f"Terminating inference process {p.pid} in atexit")
                p.terminate()
                p.join(timeout=0.5)
                if p.is_alive():
                    p.kill()
    def set_prediction_scheduler(self, scheduler: PredictionScheduler):
        self._prediction_scheduler = scheduler
        self.logger.info("PredictionScheduler set in FeedManager.")
    def get_prediction_scheduler(self) -> Optional[PredictionScheduler]:
        """Get the prediction scheduler instance."""
        return getattr(self, '_prediction_scheduler', None)
    def is_healthy(self) -> bool:
        """Check if feed manager is healthy."""
        return (
            self._result_reader_task is not None 
            and not self._result_reader_task.done()
        )
    def set_analytics_service(self, service: AnalyticsService):
        self._analytics_service = service
        self.logger.info("AnalyticsService set in FeedManager.")
        # Connect ReID manager to Database
        if self._reid_manager and hasattr(service, "_db_manager"):
            self._reid_manager.set_db_manager(service._db_manager)
            self.logger.info("ReIDManager connected to DatabaseManager.")
        if self._db_reader_task is None:
            self._db_reader_task = asyncio.create_task(self._read_db_queue())
    def set_incident_manager(self, manager: IncidentManager):
        self._incident_manager = manager
        self.logger.info("IncidentManager set in FeedManager.")
    def set_connection_manager(self, manager: ConnectionManager):
        self._connection_manager = manager
        logger.info("WebSocket ConnectionManager set in FeedManager.")
    async def _read_db_queue(self):
        """Task to process database write requests from all workers.
        NOTE: Vehicles arriving here from _read_result_queues are already
        enriched with global_vehicle_id by the ReID manager. We only need
        a fast fallback lookup for items that still lack one.
        """
        logger.info("Database queue reader task started.")
        while not self._stop_reader_flag:
            try:
                items = []
                # Drain queue up to a limit for batching
                try:
                    for _ in range(5000):
                        items.append(self._db_queue.get_nowait())
                except queue.Empty:
                    pass
                if not items:
                    await asyncio.sleep(0.05)
                    continue
                if self._analytics_service and self._analytics_service._db_manager:
                    db = self._analytics_service._db_manager
                    tracking_batch = []
                    identified_batch = []
                    for item in items:
                        msg_type = item.get("type", "vehicle_data")
                        if msg_type == "vehicle_data":
                            # Fast fallback: only lookup if result reader didn't set it
                            if not item.get("global_vehicle_id") and self._reid_manager:
                                global_id = self._reid_manager.get_global_id(
                                    item.get("feed_id", "unknown"), 
                                    item.get("vehicle_id", "unknown")
                                )
                                if global_id:
                                    item["global_vehicle_id"] = global_id
                            tracking_batch.append(item)
                        elif msg_type == "identified_vehicle":
                            identified_batch.append(item)
                        elif msg_type == "snapshot_created":
                            if hasattr(self, '_incident_manager') and self._incident_manager:
                                asyncio.create_task(self._incident_manager.attach_snapshot(
                                    item.get("incident_id"), 
                                    item.get("filename")
                                ))
                    if tracking_batch:
                        await db.save_vehicle_data_batch(tracking_batch)
                    if identified_batch:
                        for iv in identified_batch:
                            await db.upsert_identified_vehicle(iv)
                else:
                    if len(items) > 100:
                        logger.warning(f"DB manager not available. Dropped {len(items)} items from db_queue.")
                if len(items) >= 5000:
                    await asyncio.sleep(0.001)
                else:
                    await asyncio.sleep(0.005)
            except Exception as e:
                logger.error(f"Error in db_queue reader: {e}", exc_info=True)
                await asyncio.sleep(1.0)
    def initialize_shared_values(self):
        """Initializes shared memory primitives for inter-process sync."""
        if self._global_fps is None:
            # Shared array for adaptive skip: one entry per inference worker
            # Size = self._inference_pool_size
            self._shared_skip_array = self._mp_ctx.Array("i", self._inference_pool_size)
            # Use raw ctx.Value for FPS instead of Manager().Value
            self._global_fps = self._mp_ctx.Value("i", self.config.get("fps", 30))
            logger.info(f"FeedManager shared values initialized. Skip array size: {self._inference_pool_size}")
    async def start_processing(self):
        """Starts the overall video processing and prediction scheduling."""
        if self._is_processing_active:
            return
        self.logger.info("Starting overall video processing.")
        self._is_processing_active = True
        await self._check_and_manage_sample_feed()
        if self._prediction_scheduler:
            if self.config.get("prediction_scheduler", {}).get("enabled", True):
                await self._prediction_scheduler.start()
                self.logger.info("Prediction scheduler started.")
        else:
            self.logger.warning("PredictionScheduler not set.")
    async def stop_processing(self):
        """Stops the overall video processing and prediction scheduling."""
        if not self._is_processing_active:
            return
        self.logger.info("Stopping overall video processing.")
        self._is_processing_active = False
        await self._check_and_manage_sample_feed()
        if self._prediction_scheduler:
            await self._prediction_scheduler.stop()
            self.logger.info("Prediction scheduler stopped.")
    def _start_inference_pool(self):
        pool_size = self._inference_pool_size
        logger.info(f"Starting Inference Pool with {pool_size} partitioned workers.")
        self._inference_command_queues = [self._mp_ctx.Queue(maxsize=100) for _ in range(pool_size)]
        for i in range(pool_size):
            p = self._mp_ctx.Process(
                target=inference_worker,
                args=(
                    i,
                    self._inference_input_queues[i],
                    self._central_output_queue,
                    self._inference_command_queues[i],
                    self._inference_stop_event,
                    self.config,
                    self._db_queue,
                    self._shared_skip_array
                ),
                daemon=True,
                name=f"InferenceWorker-{i}"
            )
            p.start()
            self._inference_pool.append(p)
            time.sleep(0.1)
    async def _stop_inference_pool(self):
        logger.info("Stopping Inference Pool...")
        self._inference_stop_event.set()
        # Give them time to finish current task
        for _ in range(50):
            try:
                # Drain output queue to unblock workers
                self._central_output_queue.get(block=False)
            except: pass
            if all(not p.is_alive() for p in self._inference_pool):
                break
            await asyncio.sleep(0.1)
# _start_analytics_worker removed
#     async def _stop_analytics_worker(self):
#         """Gracefully stops the analytics process."""
#         if self._analytics_process:
#             logger.info("Stopping Analytics Worker...")
#             self._analytics_stop_event.set()
#             # Drain output queue to unblock worker if needed
#             for _ in range(20):
#                 try:
#                     self._analytics_output_queue.get_nowait()
#                 except: pass
#                 if not self._analytics_process.is_alive():
#                     break
#                 await asyncio.sleep(0.1)
#             if self._analytics_process.is_alive():
#                 self._analytics_process.terminate()
#             self._analytics_process = None
#         # Explicitly close queues
#         try:
#             self._analytics_input_queue.close()
#             self._analytics_input_queue.cancel_join_thread()
#             self._analytics_output_queue.close()
#             self._analytics_output_queue.cancel_join_thread()
#         except: pass
#     # Explicitly close queues
        for q in self._inference_input_queues:
            try:
                q.close()
                q.cancel_join_thread()
            except: pass
        for q in self._inference_command_queues:
            try:
                q.close()
                q.cancel_join_thread()
            except: pass
        try:
            self._central_output_queue.close()
            self._central_output_queue.cancel_join_thread()
        except: pass
        for p in self._inference_pool:
            if p.is_alive():
                logger.warning(f"Forcing termination of Inference Worker {p.name}")
                p.terminate()
                await asyncio.sleep(0.1)
                if p.is_alive():
                    p.kill()
        self._inference_pool = []
        self._inference_command_queues = []
    def _initialize_available_feeds(self):
        self._load_persisted_feeds()
    def _generate_feed_id(self, source: str, name_hint: Optional[str] = None) -> str:
        if name_hint:
            base_name = re.sub(r"[^\w\-.]+", "_", name_hint)
        elif str(source).startswith("webcam:"):
            base_name = f"Webcam_{str(source).split(':')[1]}"
        else:
            base_name = re.sub(r"[^\w\-.]+", "_", Path(source).stem)
        feed_id = f"Feed_{self._feed_id_counter}_{base_name}"
        while feed_id in self.process_registry:
            self._feed_id_counter += 1
            feed_id = f"Feed_{self._feed_id_counter}_{base_name}"
        self._feed_id_counter += 1
        return feed_id
    def _check_resources(self):
        limit = self.config.get("performance", {}).get("memory_limit_percent", 80)
        cpu, mem = check_system_resources()
        if mem >= limit:
            logger.warning(f"Resource limit reached: Memory {mem:.1f}% >= Limit {limit}%.")
            raise ResourceLimitError(f"Memory usage ({mem:.1f}%) exceeds limit.")
    async def _broadcast(self, message_type: WebSocketMessageTypeEnum, data: Dict):
        if self._connection_manager:
            message = WebSocketMessage(type=message_type, data=data)
            await self._connection_manager.broadcast(message.model_dump())
    # --- Persistence ---
    def _save_persisted_feeds(self):
        """Saves current feeds configuration to disk."""
        try:
            feeds_data = {}
            for feed_id, entry in self.process_registry.items():
                config_info = entry.get("config_info")
                if config_info:
                    # We also need to persist 'is_looped' as it's not in FeedConfigInfo
                    data = config_info.model_dump()
                    data["_is_looped_feed"] = entry.get("is_looped_feed", True)
                    feeds_data[feed_id] = data
            self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.persistence_path, 'w') as f:
                json.dump(feeds_data, f, indent=2)
            logger.info(f"Saved {len(feeds_data)} feeds to {self.persistence_path}")
        except Exception as e:
            logger.error(f"Failed to save feeds persistence: {e}")
    def _load_persisted_feeds(self):
        """Loads feeds configuration from disk."""
        if not self.persistence_path.exists():
            return
        try:
            with open(self.persistence_path, 'r') as f:
                feeds_data = json.load(f)
            loaded_count = 0
            for feed_id, feed_data in feeds_data.items():
                try:
                    # Extract extra metadata
                    is_looped = feed_data.pop("_is_looped_feed", True)
                    # Create FeedConfigInfo object
                    config_info = FeedConfigInfo(**feed_data)
                    # Add to registry (STOPPED state)
                    self.process_registry[feed_id] = {
                        "process": None,
                        "command_queue": None,
                        "stop_event": None,
                        "reduce_fps_event": None,
                        "status": FeedOperationalStatusEnum.STOPPED,
                        "source": config_info.source_identifier,
                        "start_time": None,
                        "error_message": None,
                        "latest_metrics": None,
                        "metrics_history": deque(maxlen=MAX_METRICS_HISTORY_LENGTH),
                        "timer": FrameTimer(),
                        "is_sample_feed": False,
                        "is_looped_feed": is_looped,
                        "config_info": config_info,
                        "last_broadcast_time": 0.0,
                    }
                    # Update ID counter
                    parts = feed_id.split('_')
                    if len(parts) >= 2 and parts[1].isdigit():
                         num = int(parts[1])
                         if num >= self._feed_id_counter:
                             self._feed_id_counter = num + 1
                    loaded_count += 1
                except Exception as e:
                    logger.error(f"Failed to load feed {feed_id}: {e}")
            logger.info(f"Loaded {loaded_count} feeds from {self.persistence_path}")
        except Exception as e:
            logger.error(f"Failed to load feeds persistence: {e}")
    async def remove_feed(self, feed_id: str) -> bool:
        """Removes a feed from the registry and persistence."""
        async with self._lock:
            if feed_id not in self.process_registry:
                return False
            # Stop it first
            try:
                resources = self._detach_resources(feed_id)
                if resources:
                    await self._terminate_resources(resources)
            except Exception as e:
                logger.error(f"Error stopping feed {feed_id} during removal: {e}")
            # Remove from registry
            del self.process_registry[feed_id]
            # Save persistence
            self._save_persisted_feeds()
        return True
    # --- Feed Management ---
    async def update_feed_config(self, feed_id: str, updates: Dict[str, Any]):
        """Updates the configuration for a running or stopped feed."""
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry:
                raise FeedNotFoundError(feed_id)
            # Update the config object
            current_config = entry.get("config_info")
            if current_config:
                update_data = updates.copy()
                # Validate/convert ROI if present
                if "roi" in update_data and isinstance(update_data["roi"], list):
                     # Ensure it matches the expected structure
                     pass
                updated_config = current_config.model_copy(update=update_data)
                entry["config_info"] = updated_config
                self._save_persisted_feeds()
                # If the feed is running, signal the inference workers via command queues (broadcast)
                if entry["status"] in [FeedOperationalStatusEnum.RUNNING, FeedOperationalStatusEnum.STARTING]:
                    await self._send_config_to_workers(feed_id, update_data)
        await self._broadcast_feed_update(feed_id)
    async def _send_config_to_workers(self, feed_id: str, config_data: Dict[str, Any]):
        """Helper to send config update to inference pool via broadcast command queues."""
        cmd = {
            "type": "config_update",
            "feed_id": feed_id,
            "data": config_data
        }
        sent_count = 0
        for i, q in enumerate(self._inference_command_queues):
            try:
                q.put_nowait(cmd)
                sent_count += 1
            except queue.Full:
                logger.warning(f"Inference command queue {i} full, config update might be delayed.")
            except Exception as e:
                logger.error(f"Failed to send config update to worker {i}: {e}")
        logger.info(f"Broadcasted config update for feed {feed_id} to {sent_count} inference workers.")
    async def add_and_start_feed(
        self,
        source: str,
        latitude: Optional[float],
        longitude: Optional[float],
        name_hint: Optional[str] = None,
        is_looped: bool = True,
    ) -> Dict[str, Any]:
        existing_feed_id = None
        async with self._lock:
            # Check for existing feed with same source
            for fid, entry in self.process_registry.items():
                if entry["source"] == source:
                    existing_feed_id = fid
                    break
            if not existing_feed_id:
                self._check_resources()
                feed_id = self._generate_feed_id(source, name_hint)
                logger.info(f"Adding new feed: {feed_id}")
                feed_config = FeedConfigInfo(
                    name=name_hint or Path(source).name,
                    source_type="video_file" if Path(source).suffix else "webcam",
                    source_identifier=source,
                    latitude=latitude,
                    longitude=longitude,
                )
                self.process_registry[feed_id] = {
                    "process": None,
                    "command_queue": None,
                    "stop_event": None,
                    "reduce_fps_event": None,
                    "status": FeedOperationalStatusEnum.STOPPED,
                    "source": source,
                    "start_time": None,
                    "error_message": None,
                    "latest_metrics": None,
                    "metrics_history": deque(maxlen=MAX_METRICS_HISTORY_LENGTH),
                    "timer": FrameTimer(),
                    "is_sample_feed": False,
                    "is_looped_feed": is_looped,
                    "config_info": feed_config,
                    "last_broadcast_time": 0.0,
                }
                self._save_persisted_feeds()
                target_feed_id = feed_id
            else:
                target_feed_id = existing_feed_id
                logger.info(f"Reusing existing feed {target_feed_id} for source {source}")
                # Update coordinates if provided and missing
                if latitude is not None and longitude is not None:
                    entry = self.process_registry[target_feed_id]
                    if entry.get("config_info"):
                        entry["config_info"].latitude = latitude
                        entry["config_info"].longitude = longitude
                        logger.info(f"Updated coordinates for {target_feed_id} to ({latitude}, {longitude})")
        if not existing_feed_id:
            await self._broadcast_feed_update(target_feed_id)
        try:
            await self.start_feed(target_feed_id)
            async with self._lock:
                return {
                    "feed_id": target_feed_id,
                    "status": self.process_registry[target_feed_id]["status"].value,
                    "error": self.process_registry[target_feed_id]["error_message"],
                }
        except Exception as e:
            logger.error(f"Failed to start feed {target_feed_id}: {e}")
            async with self._lock:
                self.process_registry[target_feed_id]["status"] = FeedOperationalStatusEnum.ERROR
                self.process_registry[target_feed_id]["error_message"] = str(e)
            await self._broadcast_feed_update(target_feed_id)
            return {
                "feed_id": target_feed_id,
                "status": FeedOperationalStatusEnum.ERROR.value,
                "error": str(e),
            }
    async def start_multiple_feeds(self, feeds: List[Dict]) -> Dict[str, Any]:
        """Start multiple feeds with isolation."""
        results = {
            "successful": [],
            "failed": []
        }
        for feed_config in feeds:
            try:
                # Extract parameters safely
                source = feed_config.get("source")
                if not source:
                    # Try 'path' for backward compatibility or if config format differs
                    source = feed_config.get("path")
                if not source:
                    raise ValueError("Missing 'source' or 'path' in feed config")
                # Map config keys to add_and_start_feed arguments
                kwargs = {
                    "source": source,
                    "latitude": feed_config.get("latitude"),
                    "longitude": feed_config.get("longitude"),
                    "name_hint": feed_config.get("name") or feed_config.get("name_hint"),
                    "is_looped": feed_config.get("is_looped", True)
                }
                feed_result = await self.add_and_start_feed(**kwargs)
                # Check the result status from add_and_start_feed
                if feed_result.get("status") == "error":
                     results["failed"].append({
                        "config": feed_config,
                        "error": feed_result.get("error")
                    })
                else:
                    results["successful"].append({
                        "feed_id": feed_result.get("feed_id"),
                        "config": feed_config
                    })
            except Exception as e:
                logger.error(f"Failed to start feed {feed_config}: {e}")
                results["failed"].append({
                    "config": feed_config,
                    "error": str(e)
                })
                # Continue with next feed instead of failing completely
                continue
        return results
    async def start_feed(self, feed_id: str):
        resources_to_cleanup = None
        failed_resources_to_cleanup = None
        is_sample = False
        started_real_feed = False
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry:
                raise FeedNotFoundError(feed_id)
            # Clean up if previously in error or running state (force restart logic)
            if entry["status"] != FeedOperationalStatusEnum.STOPPED:
                logger.warning(f"Feed '{feed_id}' is in state '{entry['status']}'. Cleaning up before start.")
                resources_to_cleanup = self._detach_resources(feed_id)
        # Perform CLEANUP BEFORE ALLOCATING NEW RESOURCES (Fix for [Errno 28] /dev/shm exhaustion)
        if resources_to_cleanup:
            await self._terminate_resources(resources_to_cleanup)
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry: # Safety check
                raise FeedNotFoundError(feed_id)
            # Check resources
            is_sample = entry.get("is_sample_feed", False)
            if not is_sample or self._any_real_feeds_active_unsafe():
                self._check_resources()
            logger.info(f"Starting feed: '{feed_id}'")
            # Initialize Queues and Events
            entry["command_queue"] = self._mp_ctx.Queue(maxsize=50) # Small queue for control commands
            # Only create video writer queue if enabled
            video_output_config = self.config.get("video_output", {})
            if video_output_config.get("enabled", False):
                entry["video_writer_queue"] = self._mp_ctx.Queue(maxsize=self.config.get("video_input", {}).get("max_queue_size", 500))
            else:
                entry["video_writer_queue"] = None
            entry["stop_event"] = self._mp_ctx.Event()
            entry["reduce_fps_event"] = self._mp_ctx.Event()
            entry["status"] = FeedOperationalStatusEnum.STARTING
            entry["start_time"] = time.time()
            entry["last_frame_time"] = time.time()
            entry["error_message"] = None
            entry["latest_metrics"] = None
            entry["metrics_history"] = deque(maxlen=MAX_METRICS_HISTORY_LENGTH)
            entry["timer"] = FrameTimer()
            try:
                self._launch_worker(feed_id, entry["source"])
                if not is_sample:
                    started_real_feed = True
            except Exception as e:
                logger.error(f"Failed to launch worker for '{feed_id}': {e}", exc_info=True)
                # Cleanup on failure
                failed_resources_to_cleanup = self._detach_resources(feed_id)
                entry["status"] = FeedOperationalStatusEnum.ERROR
                entry["error_message"] = str(e)
                # We will handle broadcast and raise after cleanup
            # Start Video Writer if enabled (and no error)
            if not failed_resources_to_cleanup:
                video_output_config = self.config.get("video_output", {})
                if video_output_config.get("enabled", False):
                    video_writer = VideoWriter(
                        feed_id=feed_id,
                        output_dir=video_output_config.get("output_directory"),
                        fps=video_output_config.get("fps"),
                        frame_queue=entry["video_writer_queue"],
                        codec=video_output_config.get("codec", "mp4v"),
                    )
                    self.video_writers[feed_id] = video_writer
                    video_writer.start()
        # Perform failed cleanups outside the lock
        if failed_resources_to_cleanup:
            await self._terminate_resources(failed_resources_to_cleanup)
            await self._broadcast_feed_update(feed_id)
            raise FeedOperationError(f"Failed to launch worker for '{feed_id}'")
        # Send initial config to workers (ROI, etc.)
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if entry and entry.get("config_info"):
                await self._send_config_to_workers(feed_id, entry["config_info"].model_dump(exclude_unset=True))
        # Broadcast updates
        await self._broadcast_feed_update(feed_id)
        await self._broadcast_kpi_update()
        if started_real_feed:
            await self._check_and_manage_sample_feed()
    async def stop_feed(self, feed_id: str):
        resources_to_cleanup = None
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry:
                raise FeedNotFoundError(feed_id)
            logger.info(f"Stopping feed: '{feed_id}'")
            resources_to_cleanup = self._detach_resources(feed_id)
        if resources_to_cleanup:
            await self._terminate_resources(resources_to_cleanup)
        await self._broadcast_feed_update(feed_id)
        await self._broadcast_kpi_update()
        await self._check_and_manage_sample_feed()
    async def restart_feed(self, feed_id: str):
        logger.info(f"Restart requested for: '{feed_id}'")
        try:
            await self.stop_feed(feed_id)
            # Give the system a moment to reclaim resources (ports, file handles, memory)
            await asyncio.sleep(2.0)
            await self.start_feed(feed_id)
        except Exception as e:
            logger.error(f"Restart failed for '{feed_id}': {e}", exc_info=True)
            async with self._lock:
                entry = self.process_registry.get(feed_id)
                if entry:
                    entry["status"] = FeedOperationalStatusEnum.ERROR
                    entry["error_message"] = f"Restart failed: {e}"
            await self._broadcast_feed_update(feed_id)
            raise FeedOperationError(f"Restart failed: {e}")
    async def stop_all_feeds(self):
        logger.info("Stopping all active feeds.")
        feeds_to_stop = []
        async with self._lock:
            feeds_to_stop = [
                fid for fid, entry in self.process_registry.items()
                if entry["status"] in [FeedOperationalStatusEnum.RUNNING, FeedOperationalStatusEnum.STARTING, FeedOperationalStatusEnum.ERROR]
            ]
        if feeds_to_stop:
            tasks = [self.stop_feed(feed_id) for feed_id in feeds_to_stop]
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._broadcast_kpi_update()
    async def request_snapshot(self, feed_id: str, incident_id: str):
        """Sends a command to the worker to save a high-res snapshot."""
        # Find which worker this feed is assigned to
        import hashlib
        worker_idx = int(hashlib.md5(feed_id.encode()).hexdigest(), 16) % self._inference_pool_size
        target_queue = self._inference_command_queues[worker_idx]
        cmd = {
            "type": "save_snapshot",
            "feed_id": feed_id,
            "data": {
                "incident_id": incident_id
            }
        }
        try:
            target_queue.put_nowait(cmd)
            logger.info(f"Requested snapshot for feed {feed_id}, incident {incident_id} (Worker {worker_idx})")
        except queue.Full:
            logger.error(f"Failed to put snapshot command for {feed_id}: Inference command queue {worker_idx} full.")
        except Exception as e:
            logger.error(f"Failed to put snapshot command for {feed_id}: {e}")
    # --- Internal Process & Resource Management ---
    def _launch_worker(self, feed_id: str, source: str):
        """Internal synchronous method to spawn the ingestion process."""
        entry = self.process_registry.get(feed_id)
        if not entry:
            return
        # Partitioning logic: Route feed to a specific worker based on hash
        import hashlib
        worker_idx = int(hashlib.md5(feed_id.encode()).hexdigest(), 16) % self._inference_pool_size
        target_queue = self._inference_input_queues[worker_idx]
        # Force disable SHM for Colab environment compatibility
        if 'performance' not in self.config: self.config['performance'] = {}
        self.config['performance']['use_shm'] = False
        logger.info(f"Routing feed {feed_id} to InferenceWorker-{worker_idx}")
        worker_args = (
            source,
            feed_id,
            target_queue,
            entry["stop_event"],
            self.config,
            entry.get("is_looped_feed", False),
            self._shared_skip_array, # New shared skip array
            worker_idx # Index of the inference worker this feed is routed to
        )
        logger.debug(f"Creating Ingestion process for {feed_id} with source {source}")
        process = self._mp_ctx.Process(
            target=ingestion_worker,
            args=worker_args,
            daemon=True,
            name=f"Ingestion-{feed_id}",
        )
        try:
            logger.info(f"Starting ingestion process for {feed_id}...")
            process.start()
            entry["process"] = process
            entry["start_time"] = time.time()
            logger.info(f"Launched ingestion PID {process.pid} for '{feed_id}'")
        except Exception as e:
            logger.error(f"CRITICAL: Failed to start ingestion process for {feed_id}: {e}", exc_info=True)
            raise
    def _detach_resources(self, feed_id: str) -> Optional[Dict[str, Any]]:
        """
        Detaches resources from the registry entry for later cleanup.
        MUST be called while holding self._lock.
        """
        entry = self.process_registry.get(feed_id)
        if not entry:
            return None
        # Gather resources to clean up
        resources = {
            "feed_id": feed_id,
            "process": entry.get("process"),
            "stop_event": entry.get("stop_event"),
            "video_writer_queue": entry.get("video_writer_queue"),
            "video_writer": self.video_writers.pop(feed_id, None)
        }
        # Reset Entry to Stopped state
        entry.update({
            "status": FeedOperationalStatusEnum.STOPPED,
            "error_message": None,
            "process": None,
            "stop_event": None,
            "video_writer_queue": None,
            "timer": None
        })
        if feed_id in self._feed_running_events:
            self._feed_running_events[feed_id].clear()
        return resources
    async def _terminate_resources(self, resources: Dict[str, Any]):
        """
        Robust cleanup sequence to prevent zombie processes and deadlocks.
        """
        feed_id = resources.get("feed_id", "unknown")
        process = resources.get("process")
        stop_event = resources.get("stop_event")
        writer_queue = resources.get("video_writer_queue")
        video_writer = resources.get("video_writer")
        # 1. Signal Stop
        if stop_event:
            stop_event.set()
        # 2. Stop Video Writer
        if video_writer:
            try:
                await asyncio.to_thread(video_writer.stop)
            except Exception as e:
                logger.error(f"Error stopping video writer for {feed_id}: {e}")
        # 3. Join or Kill
        if process and process.is_alive():
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, process.join, 1.0)
                if process.is_alive():
                    logger.warning(f"Process {process.pid} for {feed_id} hung. Terminating.")
                    process.terminate()
                    await asyncio.sleep(0.5) 
                    if process.is_alive():
                        process.kill()
            except Exception as e:
                logger.error(f"Error joining process for {feed_id}: {e}")
        # 4. Close Queues
        if writer_queue:
            try:
                writer_queue.close()
                writer_queue.cancel_join_thread()
            except Exception:
                pass
    # --- Background Reader ---
    async def _broadcast_worker(self):
        """Dedicated worker for high-frequency WebSocket broadcasting with backpressure."""
        logger.info("Broadcast worker task started.")
        while not self._stop_reader_flag:
            try:
                # 1. Wait for message from queue
                msg = await self.broadcast_queue.get()
                m_type = msg.get("type")
                feed_id = msg.get("feed_id")
                # 2. Optimized Routing
                if m_type == "video_frame":
                    if self._connection_manager:
                        await self._connection_manager.broadcast_bytes_to_feed(
                            feed_id, 
                            msg["data"]
                        )
                elif m_type == "kpi_update":
                    if self._connection_manager:
                        await self._connection_manager.broadcast_to_feed(
                            msg["data"],
                            feed_id
                        )
                self.broadcast_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in broadcast worker: {e}")
                await asyncio.sleep(0.1)
    async def _read_result_queues(self):
        """Drains Inference output and forwards frames/raw data to Analytics process."""
        logger.info("Inference Result reader task started.")
        while not self._stop_reader_flag:
            try:
                items_buffer = []
                try:
                    for _ in range(200):
                        items_buffer.append(self._central_output_queue.get(block=False))
                except Exception as e:
                    if 'Queue empty' in str(e) or isinstance(e, queue.Empty):
                        pass
                except queue.Empty:
                    pass
                if not items_buffer:
                    await asyncio.sleep(0.001)
                    continue
                for item in items_buffer:
                    feed_id, frame_idx, frame_bytes, metrics, vehicles, extra = item
                    entry = self.process_registry.get(feed_id)
                    if not entry: continue
                    
                    # Update FPS timer for this feed
                    # [FIX] Only tick if the frame index has changed to avoid over-counting in batches
                    if entry.get("timer") and frame_idx != entry.get("last_processed_idx"):
                        entry["timer"].tick("loop_total")
                        entry["last_processed_idx"] = frame_idx
                    # 0. Enrich vehicles with global IDs from central manager
                    if vehicles and self._reid_manager:
                        reid_tasks = []
                        for v in vehicles:
                            if not v.get("global_vehicle_id"):
                                # 1. Try fast lookup first
                                gid = self._reid_manager.get_global_id(feed_id, v["vehicle_id"])
                                if gid:
                                    v["global_vehicle_id"] = gid
                                elif v.get("embedding") is not None:
                                    # 2. Schedule for background registration if missing
                                    reid_tasks.append((v, asyncio.to_thread(self._sync_match_or_register, feed_id, v)))
                        if reid_tasks:
                            results = await asyncio.gather(*[t[1] for t in reid_tasks])
                            for (v, _), gid in zip(reid_tasks, results):
                                if gid:
                                    v["global_vehicle_id"] = gid
                    # 1. Forward raw data to Analytics process for heavy lifting
                    # item format: (feed_id, frame_index, timestamp, vis_tracks, lane_boundaries, lane_lines, metrics, extra)
                    timestamp = time.time()
                    # Optimization: Strip large binary data from 'extra' for AnalyticsWorker
                    # AnalyticsWorker only needs 'calibration' and metadata, not image bytes
                    analytics_extra = {}
                    if isinstance(extra, dict):
                        # Copy only safe/needed keys
                        for k in ["calibration", "is_keyframe"]:
                            val = extra.get(k)
                            if val is not None:
                                analytics_extra[k] = val
                    analytics_item = (feed_id, frame_idx, timestamp, vehicles, None, None, metrics, analytics_extra)
                    try:
                        await self._process_analytics_frame(feed_id, metrics, vehicles)
                    except Exception as e:
                        logger.error(f"Error processing analytics frame for {feed_id}: {e}")
                        self._dropped_analytics_count += 1
                    # 2. Immediate Frame Distribution (For video subscribers)
                    if feed_id in self.frame_subscriber_queues:
                        for sub_q in self.frame_subscriber_queues[feed_id]:
                            try:
                                sub_q.put_nowait({"frame": frame_bytes, "metrics": metrics, "vehicles": vehicles})
                            except asyncio.QueueFull:
                                pass
                    # 3. Route to Video Writer
                    if entry.get("video_writer_queue"):
                        try:
                            entry["video_writer_queue"].put_nowait((frame_bytes, metrics))
                        except queue.Full:
                            pass
                    # 4. Send to DB Queue for Persistence and ReID Registration
                    if self._db_queue:
                         # Throttle database persistence to save space (Constraint Hardware Optimization)
                         # Default to 5 seconds unless specified in config
                         db_throttle_sec = self.config.get("db_persistence_interval_sec", 5.0)
                         for v in vehicles:
                             vid = v.get("vehicle_id")
                             if vid is None: continue
                             last_write = self._last_vehicle_db_write.get((feed_id, vid), 0)
                             # Write if it's new, or interval elapsed
                             # Significant events like license plate detection already trigger writes in some places
                             if timestamp - last_write >= db_throttle_sec:
                                 # Create a lightweight copy for the queue
                                 track_data = v.copy()
                                 track_data["feed_id"] = feed_id
                                 track_data["type"] = "vehicle_data"
                                 track_data["timestamp"] = timestamp
                                 track_data["frame_index"] = frame_idx
                                 try:
                                     self._db_queue.put_nowait(track_data)
                                     self._last_vehicle_db_write[(feed_id, vid)] = timestamp
                                 except queue.Full:
                                     pass
                    # 5. Broadcast to WebRTC Peers
                    if feed_id in self._webrtc_queues:
                        for pc_id, route_q in list(self._webrtc_queues[feed_id].items()):
                            try:
                                route_q.put_nowait(frame_bytes)
                            except asyncio.QueueFull:
                                pass # Drop frame gracefully if WebRTC socket is lagging
                    # 6. Broadcast Telemetry to WebSocket Clients
                    if self._connection_manager:
                        # Only broadcast if there are subscribers to save resources
                        if hasattr(self._connection_manager, 'has_subscribers') and self._connection_manager.has_subscribers(feed_id):
                            # Direct inline enqueue — avoids per-frame asyncio.Task overhead
                            try:
                                loop = asyncio.get_running_loop()
                                msg_bytes = await loop.run_in_executor(
                                    self._cpu_executor, self._serialize_broadcast_payload,
                                    feed_id, frame_idx, frame_bytes, metrics, vehicles, extra
                                )
                                if msg_bytes:
                                    try:
                                        self.broadcast_queue.put_nowait({
                                            "type": "video_frame",
                                            "feed_id": feed_id,
                                            "data": msg_bytes
                                        })
                                    except asyncio.QueueFull:
                                        # Drop oldest to make room (real-time priority)
                                        try:
                                            self.broadcast_queue.get_nowait()
                                            self.broadcast_queue.put_nowait({
                                                "type": "video_frame",
                                                "feed_id": feed_id,
                                                "data": msg_bytes
                                            })
                                        except Exception:
                                            pass
                            except Exception as e:
                                logger.error(f"Error enqueuing broadcast for {feed_id}: {e}")
                # Yield control
                await asyncio.sleep(0.001)
            except Exception as e:
                logger.error(f"Error in result reader loop: {e}", exc_info=True)
                await asyncio.sleep(1.0)
    def _serialize_broadcast_payload(self, feed_id, frame_idx, frame_bytes, metrics, vehicles, extra):
        """Synchronous: builds payload + JSON/msgpack serializes. Applies spatial debouncing."""
        try:
            now = time.time()
            debounced_vehicles = []
            feed_telemetry = self._last_sent_telemetry.get(feed_id, {})
            # Spatial Debouncing Threshold (in pixels)
            SPATIAL_THRESHOLD = 3.0 
            for v in vehicles:
                vid = v.get("vehicle_id")
                if vid is None:
                    # Fallback for old tracked objects without IDs
                    debounced_vehicles.append(v)
                    continue
                curr_x = (v.get("x1", 0) + v.get("x2", 0)) / 2
                curr_y = (v.get("y1", 0) + v.get("y2", 0)) / 2
                curr_vx = v.get("vx", 0)
                curr_vy = v.get("vy", 0)
                # Check if we have a previous state to dead-reckon against
                last_state = feed_telemetry.get(vid)
                is_debounced = False
                if last_state:
                    lx, ly, lvx, lvy, lts = last_state
                    dt = now - lts
                    # Predict position based on last known velocity
                    # Note: We assume constant velocity for the dead-reckoning interval
                    pred_x = lx + (lvx * dt)
                    pred_y = ly + (lvy * dt)
                    # Calculate spatial error
                    err_sq = (curr_x - pred_x)**2 + (curr_y - pred_y)**2
                    # If error is within threshold, only send the ID (debounced)
                    # This saves bandwidth while letting the frontend know the vehicle is still "alive"
                    if err_sq < (SPATIAL_THRESHOLD**2):
                        is_debounced = True
                if is_debounced:
                    # Lightweight update for debounced vehicles
                    debounced_vehicles.append({"vehicle_id": vid, "d": 1})
                else:
                    # Full update + refresh dead-reckoning state
                    # --- NORMALIZATION STEP ---
                    v_norm = v.copy()
                    res = self.config.get("vehicle_detection", {}).get("frame_resolution", [640, 480])
                    fw, fh = res[0], res[1]
                    if "bbox" in v and len(v["bbox"]) == 4:
                        v_norm["bbox"] = [v["bbox"][0] / fw, v["bbox"][1] / fh, v["bbox"][2] / fw, v["bbox"][3] / fh]
                    if "vx" in v: v_norm["vx"] = v["vx"] / fw
                    if "vy" in v: v_norm["vy"] = v["vy"] / fh
                    
                    debounced_vehicles.append(v_norm)
                    if feed_id not in self._last_sent_telemetry:
                        self._last_sent_telemetry[feed_id] = {}
                    self._last_sent_telemetry[feed_id][vid] = (curr_x, curr_y, curr_vx, curr_vy, now)
            # Cleanup dead-reckoning state for vehicles no longer in frame
            active_ids = {v.get("vehicle_id") for v in vehicles if "vehicle_id" in v}
            for vid in list(feed_telemetry.keys()):
                if vid not in active_ids:
                    del feed_telemetry[vid]
            payload = {
                "t": WebSocketMessageTypeEnum.VIDEO_FRAME.value,
                "f": feed_id,
                "i": frame_idx,
                "ts": now,
                "v": debounced_vehicles,
                "m": metrics
            }
            # Phase 14: Video transmission is moved entirely to WebRTC Native.
            # We explicitly drop frame_bytes and bg images from the WebSocket packet.
            # UPDATE: Re-enabled WebSocket fallback for environments where WebRTC fails (e.g. Colab/Localtunnel)
            if frame_bytes:
                payload["frame"] = frame_bytes
            
            if extra:
                if "rois" in extra:
                    payload["rois"] = extra.get("rois")
                if "bg" in extra:
                    payload["bg"] = extra.get("bg")

            return self._serialize_msgpack(payload)
        except Exception as e:
            logger.error(f"Error serializing broadcast payload for {feed_id}: {e}")
            return None
    def _serialize_msgpack(self, payload):
        """Synchronous msgpack serialization helper for thread pool."""
        def msgpack_default(obj):
            if isinstance(obj, (np.integer, np.int64, np.int32)): return int(obj)
            if isinstance(obj, (np.floating, np.float64, np.float32)): return float(obj)
            if isinstance(obj, np.ndarray): return obj.tolist()
            return str(obj)
        return msgpack.packb(payload, default=msgpack_default, use_bin_type=True)
    def _filter_vehicles_for_delta(self, feed_id: str, vehicles: List[Dict]) -> List[Dict]:
        """Filters vehicles using dead-reckoning to send only significant updates."""
        if not vehicles:
            return []
        
        if feed_id not in self._last_sent_telemetry:
            self._last_sent_telemetry[feed_id] = {}
        
        last_sent = self._last_sent_telemetry[feed_id]
        deltas = []
        now = time.time()
        SPATIAL_THRESHOLD_SQ = 3.0**2
        
        for v in vehicles:
            vid = v.get("vehicle_id")
            if vid is None: continue
            
            # Current state
            cx, cy = v.get("centroid", (0, 0))
            vx, vy = v.get("velocity", (0, 0))
            
            if vid not in last_sent:
                deltas.append(v)
            else:
                lx, ly, lvx, lvy, lts = last_sent[vid]
                dt = now - lts
                pred_x = lx + (lvx * dt)
                pred_y = ly + (lvy * dt)
                err_sq = (cx - pred_x)**2 + (cy - pred_y)**2
                
                if err_sq > SPATIAL_THRESHOLD_SQ or abs(vx - lvx) > 0.5 or abs(vy - lvy) > 0.5:
                    deltas.append(v)
            
            last_sent[vid] = (cx, cy, vx, vy, now)
            
        return deltas



    async def _broadcast_analytics_update(self, feed_id: str, metrics: Dict, vehicles: List[Dict]):
        """Sends a specialized analytics update (Delta) to the UI for a specific feed."""
        try:
            now = time.time()
            # APPLY DELTA FILTERING
            delta_vehicles = self._filter_vehicles_for_delta(feed_id, vehicles)
            
            payload = {
                "t": "analytics_delta", # Changed type to 'analytics_delta'
                "f": feed_id,
                "m": metrics,
                "v": delta_vehicles,
                "ts": now
            }
            serialized = self._serialize_msgpack(payload)
            if serialized:
                await self._connection_manager.broadcast_to_feed(feed_id, serialized)
        except Exception as e:
            logger.error(f"Error in _broadcast_analytics_update for {feed_id}: {e}")
    async def _process_analytics_frame(self, feed_id: str, metrics: Dict, vehicles: List[Dict]):
        """Processes analytics results and triggers UI broadcasts (formerly in AnalyticsWorker)."""
        entry = self.process_registry.get(feed_id)
        if not entry: return

        # Update Status to RUNNING if starting
        if entry["status"] == FeedOperationalStatusEnum.STARTING:
            entry["status"] = FeedOperationalStatusEnum.RUNNING
            logger.info(f"Feed '{feed_id}' transitioned from STARTING to RUNNING.")
        
        # Update Latest Metrics
        entry["latest_metrics"] = metrics
        entry["last_frame_time"] = time.time()

        # Pass metrics to AnalyticsService for DB persistence
        if self._analytics_service:
            asyncio.create_task(self._analytics_service.process_feed_metrics(
                feed_id, metrics, vehicles
            ))

        # Enrich metrics with coordinates from registry if missing
        if "latitude" not in metrics:
            if "latitude" in entry:
                metrics["latitude"] = entry["latitude"]
            elif entry.get("config_info"):
                metrics["latitude"] = entry["config_info"].latitude
        if "longitude" not in metrics:
            if "longitude" in entry:
                metrics["longitude"] = entry["longitude"]
            elif entry.get("config_info"):
                metrics["longitude"] = entry["config_info"].longitude

        # Enrich with global IDs before broadcast
        if vehicles and self._reid_manager:
            for v in vehicles:
                if "vehicle_id" in v:
                    gid = self._reid_manager.get_global_id(feed_id, v["vehicle_id"])
                    if gid:
                        v["global_vehicle_id"] = gid

        # Broadcast to UI (Throttled via task checking)
        prev_task = self._active_broadcast_tasks.get(feed_id)
        if prev_task is None or prev_task.done():
            task = asyncio.create_task(self._broadcast_analytics_update(feed_id, metrics, vehicles))
            self._active_broadcast_tasks[feed_id] = task

        """Broadcasts processed analytics data to subscribers."""
        # Note: UI expects specific format. We reuse parts of _broadcast_video_frame logic
        # but without the base64 frame data to save bandwidth.
        # The frontend useVideoSocket.ts handles separate metric updates.
        update = WebSocketMessage(
            type=WebSocketMessageTypeEnum.FEED_STATUS_UPDATE,
            data={
                "feed_id": feed_id,
                "metrics": metrics,
                "vehicles": self._compute_vehicle_deltas(feed_id, vehicles, metrics.get("frame_index", 0))
            }
        )
        await self._connection_manager.broadcast_to_topic(f"feed:{feed_id}", update.model_dump())
    async def _handle_periodic_tasks(self):
        now = time.time()
        # Periodic KPI Broadcast
        if now - self._last_kpi_broadcast_time >= self._kpi_broadcast_interval:
            await self._broadcast_kpi_update()
            self._last_kpi_broadcast_time = now
        # Periodic Resource Check (every 30s)
        if now - self._last_queue_log_time >= 30.0:
            self._last_queue_log_time = now
            try:
                self._check_resources()
            except ResourceLimitError as e:
                logger.error(f"Resource limit exceeded during operation: {e}")
    def _compute_vehicle_deltas(self, feed_id: str, vehicles: List[Dict], frame_idx: int) -> List[Dict]:
        """
        Computes delta updates for vehicles to reduce bandwidth.
        - KEYFRAME (every 30 frames): Send FULL data.
        - DELTA: Send only changed fields + mandatory (id, bbox, velocity).
        """
        KEYFRAME_INTERVAL = 30
        is_keyframe = (frame_idx % KEYFRAME_INTERVAL == 0)
        # Access feed entry to store state
        entry = self.process_registry.get(feed_id)
        if not entry:
            return vehicles
        if "vehicle_states" not in entry:
            entry["vehicle_states"] = {}
        last_states = entry["vehicle_states"]
        current_ids = set()
        delta_vehicles = []
        # Fields that are effectively static or low-frequency
        static_fields = [
            "class_name", "class_id", "car_model", 
            "license_plate", "color", "behavior", 
            "car_model_confidence", "gallery_size" # gallery size changes slowly
        ]
        for v in vehicles:
            vid = v["vehicle_id"]
            current_ids.add(vid)
            
            # --- NORMALIZATION SETUP ---
            res = self.config.get("vehicle_detection", {}).get("frame_resolution", [640, 480])
            fw, fh = res[0], res[1]

            # If Keyframe or New Vehicle -> Full Update (Excluding large embeddings)
            if is_keyframe or vid not in last_states:
                safe_v = {k: val for k, val in v.items() if k != "embedding"}
                # Normalize coordinates in full update
                if "bbox" in safe_v and len(safe_v["bbox"]) == 4:
                    safe_v["bbox"] = [safe_v["bbox"][0] / fw, safe_v["bbox"][1] / fh, safe_v["bbox"][2] / fw, safe_v["bbox"][3] / fh]
                if "vx" in safe_v: safe_v["vx"] = safe_v["vx"] / fw
                if "vy" in safe_v: safe_v["vy"] = safe_v["vy"] / fh
                
                delta_vehicles.append(safe_v)
                last_states[vid] = safe_v
                continue
            # Compute Delta
            last_v = last_states[vid]
            delta = {
                "vehicle_id": vid,
                "bbox": [v["bbox"][0] / fw, v["bbox"][1] / fh, v["bbox"][2] / fw, v["bbox"][3] / fh] if "bbox" in v and len(v["bbox"]) == 4 else v.get("bbox"),
                "speed": v.get("speed", 0), # Always send speed
                "vx": v.get("vx", 0) / fw if "vx" in v else 0,
                "vy": v.get("vy", 0) / fh if "vy" in v else 0
            }
            # Check static fields for changes (Note: embedding is never in static_fields)
            for field in static_fields:
                val = v.get(field)
                if val != last_v.get(field):
                    delta[field] = val
            # Add global_vehicle_id if it was just assigned
            if "global_vehicle_id" in v and "global_vehicle_id" not in last_v:
                 delta["global_vehicle_id"] = v["global_vehicle_id"]
            # Add any other dynamic fields if strictly needed, or just status
            if v.get("status") != last_v.get("status"):
                delta["status"] = v.get("status")
            if v.get("is_occluded") != last_v.get("is_occluded"):
                delta["is_occluded"] = v.get("is_occluded")
            delta_vehicles.append(delta)
            # Update last state with sanitized version
            last_states[vid] = {k: val for k, val in v.items() if k != "embedding"}
        # Clean up stale states
        for missing_id in list(last_states.keys()):
            if missing_id not in current_ids:
                del last_states[missing_id]
                # Also clean up from write throttling cache
                if (feed_id, missing_id) in self._last_vehicle_db_write:
                    del self._last_vehicle_db_write[(feed_id, missing_id)]
        return delta_vehicles
    # _broadcast_video_frame removed during pipeline optimization.
    # Its logic is consolidated into _serialize_broadcast_payload + broadcast_worker.
    # See _serialize_broadcast_payload and _broadcast_worker for the unified path.
    async def _maintenance_loop(self):
        """Periodically prunes old database records and snapshot files to reclaim space."""
        logger.info("Maintenance loop started.")
        while not self._stop_reader_flag:
            try:
                interval_hours = self.config.get("maintenance_interval_hours", 24)
                # Wait for the next interval
                await asyncio.sleep(interval_hours * 3600)
                if self._analytics_service and self._analytics_service._db_manager:
                    db = self._analytics_service._db_manager
                    retention = self.config.get("db_retention_days", 7)
                    logger.info(f"Starting scheduled maintenance (retention: {retention} days)...")
                    # Run pruning in a separate thread to avoid blocking the event loop
                    results = await asyncio.to_thread(db.prune_old_data, config=self.config, retention_days=retention)
                    logger.info(f"Maintenance complete: {results}")
                else:
                    logger.warning("Maintenance skipped: DatabaseManager not ready.")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in maintenance loop: {e}", exc_info=True)
                await asyncio.sleep(3600) # Wait an hour before retrying on error
    async def _watchdog_loop(self):
        """Periodically checks if processing workers are alive and responsive."""
        logger.info("Watchdog task started.")
        while not self._stop_reader_flag:
            try:
                await asyncio.sleep(5.0)  # Check every 5 seconds
                feeds_to_restart = []
                async with self._lock:
                    for feed_id, entry in self.process_registry.items():
                        # Only monitor feeds that are supposed to be running or in error (to retry)
                        if entry["status"] not in [FeedOperationalStatusEnum.RUNNING, FeedOperationalStatusEnum.STARTING]:
                            continue
                        process = entry.get("process")
                        # If process is missing but status is running, or process died
                        if not process or not process.is_alive():
                            exit_code = process.exitcode if process else "N/A"
                            if exit_code is not None and exit_code != 0:
                                logger.warning(
                                    f"Video process {feed_id} exited with error code: {exit_code}"
                                )
                            else:
                                logger.info(f"Video process {feed_id} ended (likely reached EOF).")
                            feeds_to_restart.append(feed_id)
                # Restart outside the lock to avoid deadlocks and allow other operations
                for feed_id in feeds_to_restart:
                    try:
                        logger.info(f"Watchdog: Restarting video feed: {feed_id}")
                        await self.restart_feed(feed_id)
                        logger.info(f"Watchdog: Video feed restarted successfully: {feed_id}")
                    except Exception as e:
                        logger.error(
                            f"Watchdog: Failed to restart video feed {feed_id}: {e}"
                        )
                # --- Periodic Summary Logging ---
                now = time.time()
                if (now - getattr(self, "_last_watchdog_log_time", 0)) > 30:
                    self._last_watchdog_log_time = now
                    if self._dropped_analytics_count > 0:
                        logger.warning(f"[Watchdog] Cumulative dropped analytics events: {self._dropped_analytics_count}")
                    # Log active worker count for health monitoring
                    async with self._lock:
                        active_count = sum(1 for e in self.process_registry.values() if e.get("status") == FeedOperationalStatusEnum.RUNNING)
                        if active_count > 0:
                            logger.info(f"[Watchdog] Pipeline Healthy: {active_count} active ingestion workers.")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in watchdog loop: {e}", exc_info=True)
                await asyncio.sleep(10.0)  # Sleep longer on error
    @staticmethod
    def _encode_frame(frame_bytes: bytes) -> str:
        # This runs in a separate thread
        return base64.b64encode(frame_bytes).decode('utf-8')
    # --- Sample Feed Management ---
    def _any_real_feeds_active_unsafe(self) -> bool:
        for entry in self.process_registry.values():
            if not entry.get("is_sample_feed", False) and entry["status"] in [
                FeedOperationalStatusEnum.RUNNING, FeedOperationalStatusEnum.STARTING
            ]:
                return True
        return False
    async def _check_and_manage_sample_feed(self):
        if not self._sample_feed_ids:
            return
        to_start = []
        to_stop = []
        async with self._lock:
            real_active = self._any_real_feeds_active_unsafe()
            # If real feeds are active, stop all running sample feeds
            if real_active:
                for fid in self._sample_feed_ids:
                    status = self.process_registry.get(fid, {}).get("status")
                    if status in [FeedOperationalStatusEnum.RUNNING, FeedOperationalStatusEnum.STARTING]:
                        to_stop.append(fid)
            else:
                # If no real feeds, ensure sample feeds are running (up to limit)
                active_count = 0
                for entry in self.process_registry.values():
                    if entry["status"] in [FeedOperationalStatusEnum.RUNNING, FeedOperationalStatusEnum.STARTING]:
                        active_count += 1
                max_feeds = self.config.get("feed_manager", {}).get("max_concurrent_feeds", 10)
                for fid in self._sample_feed_ids:
                    status = self.process_registry.get(fid, {}).get("status")
                    if active_count < max_feeds and status in [FeedOperationalStatusEnum.STOPPED, FeedOperationalStatusEnum.ERROR]:
                        to_start.append(fid)
                        active_count += 1
        # Perform actions outside lock
        for fid in to_stop:
            try:
                await self.stop_feed(fid)
            except Exception: pass  # noqa: E701
        for fid in to_start:
            try:
                await self.start_feed(fid)
            except Exception: pass  # noqa: E701
    async def update_global_config(self, new_config: Dict[str, Any]):
        """
        Updates the global configuration and propagates changes to all active inference workers.
        """
        async with self._lock:
            self.config = new_config
            logger.info("FeedManager global configuration updated.")
        
        # Broadcast the updated config to all inference workers
        # We send a generic 'config_update' command with the full config
        cmd = {
            "type": "config_update",
            "feed_id": "GLOBAL",
            "data": new_config
        }
        
        sent_count = 0
        for i, q in enumerate(self._inference_command_queues):
            try:
                q.put_nowait(cmd)
                sent_count += 1
            except queue.Full:
                logger.warning(f"Inference command queue {i} full, global config update might be delayed.")
            except Exception as e:
                logger.error(f"Failed to send global config update to worker {i}: {e}")
        
        logger.info(f"Global configuration broadcasted to {sent_count} inference workers.")

    async def get_feed_config(self, feed_id: str) -> Optional[FeedConfigInfo]:
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if entry:
                return entry.get("config_info")
        return None
    async def get_all_statuses(self) -> List[FeedStatusData]:
        statuses = []
        async with self._lock:
            for fid, entry in self.process_registry.items():
                statuses.append(self._entry_to_status_data(fid, entry))
        return statuses
    def _entry_to_status_data(self, feed_id: str, entry: Dict) -> FeedStatusData:
        op_status = entry["status"]
        config = entry.get("config_info") or FeedConfigInfo(
            name="Unknown", source_type="unknown", source_identifier=entry["source"]
        )
        return FeedStatusData(
            feed_id=feed_id, config=config, source=entry["source"],
            status=op_status,
            current_fps=entry["timer"].get_fps("loop_total") if entry.get("timer") else None,
            last_error=entry.get("error_message"),
            latest_metrics=entry.get("latest_metrics")
        )
    async def _broadcast_feed_update(self, feed_id: str):
        if not self._connection_manager:
            return
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry:
                return
            data = self._entry_to_status_data(feed_id, entry)
        msg = WebSocketMessage(
            type=WebSocketMessageTypeEnum.FEED_STATUS_UPDATE,
            data=FeedStatusUpdate(feed_status_data=data).model_dump()
        )
        # Broadcast to ALL connected clients so the dashboard gets the update
        # regardless of specific topic subscriptions.
        await self._connection_manager.broadcast(msg.model_dump())
    async def _broadcast_kpi_update(self):
        if not self._connection_manager:
            return
        total_vehicles_active = 0
        total_vehicles_cumulative = 0
        total_speed_sum = 0.0
        total_speed_count = 0
        total_congestion_sum = 0.0
        total_health_sum = 0.0
        active_feeds_count = 0
        # Aggregate metrics from all running feeds
        async with self._lock:
            for feed_id, entry in self.process_registry.items():
                status = entry.get("status")
                metrics = entry.get("latest_metrics")
                if status in [FeedOperationalStatusEnum.RUNNING, FeedOperationalStatusEnum.STARTING] and metrics:
                    # Vehicle Count (Current)
                    v_active = metrics.get("total_vehicles", 0)
                    total_vehicles_active += v_active
                    # Vehicle Count (Cumulative)
                    v_cum = metrics.get("total_vehicles_cumulative", v_active)
                    total_vehicles_cumulative += v_cum
                    # Speed (Session Average for stability)
                    # We use the session average if available, otherwise instantaneous
                    avg_speed = metrics.get("session_average_speed_kmh")
                    if avg_speed is None or avg_speed == 0:
                        avg_speed = metrics.get("average_speed_kmh", 0.0)
                    if avg_speed > 0:
                        total_speed_sum += avg_speed
                        total_speed_count += 1
                    # Congestion (Session Average for stability)
                    congestion = metrics.get("session_average_congestion_score")
                    if congestion is None or congestion == 0:
                        congestion = metrics.get("congestion_score", 0.0)
                    total_congestion_sum += congestion
                    # Health Score
                    health = metrics.get("health_score", 100.0)
                    total_health_sum += health
                    active_feeds_count += 1
        # Calculate Global Averages
        global_avg_speed = (total_speed_sum / total_speed_count) if total_speed_count > 0 else 0.0
        global_congestion_index = (total_congestion_sum / active_feeds_count) if active_feeds_count > 0 else 0.0
        global_health_score = (total_health_sum / active_feeds_count) if active_feeds_count > 0 else 100.0
        # Construct Payload
        kpi_data = GlobalRealtimeMetrics(
            timestamp=datetime.now(timezone.utc).isoformat(),
            metrics_source="aggregated_feeds",
            total_flow=total_vehicles_cumulative,
            average_speed_kmh=round(global_avg_speed, 1),
            congestion_index=round(global_congestion_index, 1),
            active_incidents_count=0,
            feed_statuses={
                "active": active_feeds_count,
                "total": len(self.process_registry)
            },
            custom_metrics={
                "active_vehicles": total_vehicles_active,
                "global_health_score": round(global_health_score, 1)
            }
        )
        # Broadcast
        message = WebSocketMessage(
            type=WebSocketMessageTypeEnum.KPI_UPDATE,
            data=kpi_data.model_dump()
        )
        await self._connection_manager.broadcast_to_topic("kpi", message.model_dump())
    async def _perform_broadcasts(self, feeds_to_update, kpi_needed, sample_needed):
        for fid in feeds_to_update:
            await self._broadcast_feed_update(fid)
        now = time.time()
        if kpi_needed or (now - self._last_kpi_broadcast_time >= self._kpi_broadcast_interval):
            await self._broadcast_kpi_update()
            self._last_kpi_broadcast_time = now
        if sample_needed:
            await self._check_and_manage_sample_feed()
    async def subscribe_to_frames(self, feed_id: str) -> asyncio.Queue:
        async with self._lock:
            if feed_id not in self.frame_subscriber_queues:
                self.frame_subscriber_queues[feed_id] = []
            q = asyncio.Queue(maxsize=30) # Drop older frames if consumer is slow
            self.frame_subscriber_queues[feed_id].append(q)
            return q
    async def unsubscribe_from_frames(self, feed_id: str, q: asyncio.Queue):
        async with self._lock:
            if feed_id in self.frame_subscriber_queues:
                if q in self.frame_subscriber_queues[feed_id]:
                    self.frame_subscriber_queues[feed_id].remove(q)
                if not self.frame_subscriber_queues[feed_id]:
                    del self.frame_subscriber_queues[feed_id]
    async def shutdown(self):
        logger.info("Shutdown initiated.")
        self._stop_reader_flag = True
        await self.stop_processing()
        await self.stop_all_feeds()
        await self._stop_inference_pool()
        # await self._stop_analytics_worker()  # Removed: Analytics now handled by AnalyticsService
        # Save ReID state before shutting down
        if self._reid_manager:
            await asyncio.to_thread(self._reid_manager.save_state)
            logger.info("ReID state saved during shutdown.")
        tasks = []
        if self._result_reader_task:
            tasks.append(self._result_reader_task)
        # pass
        if self._watchdog_task:
            tasks.append(self._watchdog_task)
        if self._maintenance_task:
            tasks.append(self._maintenance_task)
        if self._db_reader_task:
            tasks.append(self._db_reader_task)
        if tasks:
            await asyncio.wait(tasks, timeout=5.0)



