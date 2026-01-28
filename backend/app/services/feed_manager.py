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

from collections import deque
from multiprocessing import (
    Process,
    Queue as MPQueue,
    Event,
)
from typing import Dict, Any, Optional, List
from pathlib import Path
import queue  # For queue.Empty exception
from datetime import datetime, timezone

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
    VideoFrameData,
)

# Import core worker and utilities
from app.core.ingestion_worker import ingestion_worker
from app.core.inference_worker import inference_worker
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
QUEUE_MAX_SIZE = 500
QUEUE_DRAIN_LIMIT = 100
MAX_METRICS_HISTORY_LENGTH = 1000  # Safety cap for deque


class FeedManager:
    def __init__(self, config: Dict[str, Any]):
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

        # Database processing
        self._db_queue: Optional[MPQueue] = MPQueue(maxsize=100000)
        self._db_reader_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None

        # Decoupled Processing Pool (Partitioned by Feed ID for State consistency)
        self._inference_pool_size = self.config.get("performance", {}).get("inference_pool_size", 2)
        self.redis_url = self.config.get("performance", {}).get("redis_url")
        
        # We divide the QUEUE_MAX_SIZE among workers
        per_worker_q_size = max(50, QUEUE_MAX_SIZE // self._inference_pool_size)
        
        if self.redis_url:
            self._inference_input_queues = [
                RedisQueue(self.redis_url, f"inference_input_{i}", maxsize=per_worker_q_size) 
                for i in range(self._inference_pool_size)
            ]
            self._central_output_queue = RedisQueue(self.redis_url, "central_output", maxsize=QUEUE_MAX_SIZE)
        else:
            self._inference_input_queues = [MPQueue(maxsize=per_worker_q_size) for _ in range(self._inference_pool_size)]
            self._central_output_queue = MPQueue(maxsize=QUEUE_MAX_SIZE)
            
        self._inference_pool: List[Process] = []
        self._inference_command_queues: List[MPQueue] = []
        self._inference_stop_event = Event()
        self._start_inference_pool()

        # Initialize shared values
        self.initialize_shared_values()

        self._initialize_available_feeds()

        # Register cleanup on exit
        atexit.register(self._atexit_cleanup)

        # Start background reader and watchdog
        self._result_reader_task = asyncio.create_task(self._read_result_queues())
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        self.logger.info("FeedManager initialized. Reader and Watchdog tasks started.")



    def _atexit_cleanup(self):
        """Synchronous cleanup for interpreter exit."""
        logger.info("FeedManager executing atexit cleanup...")
        
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
        if self._db_reader_task is None:
            self._db_reader_task = asyncio.create_task(self._read_db_queue())

    def set_connection_manager(self, manager: ConnectionManager):
        self._connection_manager = manager
        logger.info("WebSocket ConnectionManager set in FeedManager.")

    async def _read_db_queue(self):
        """Task to process database write requests from all workers."""
        logger.info("Database queue reader task started.")
        
        while not self._stop_reader_flag:
            try:
                items = []
                # Drain queue up to a limit for batching
                try:
                    # Increase batch size for higher throughput
                    for _ in range(5000):
                        items.append(self._db_queue.get_nowait())
                except queue.Empty:
                    pass

                if not items:
                    await asyncio.sleep(0.05)
                    continue

                if self._analytics_service and self._analytics_service._db_manager:
                    db = self._analytics_service._db_manager
                    
                    # Separate items by type for appropriate processing
                    tracking_batch = []
                    identified_batch = []
                    
                    # Group items needing Re-ID to process in a single executor call
                    items_needing_reid = []
                    loop = asyncio.get_running_loop()
                    
                    for item in items:
                        msg_type = item.get("type", "vehicle_data")
                        if msg_type == "vehicle_data":
                            if item.get("embedding"):
                                items_needing_reid.append(item)
                            else:
                                # Try fast lookup for already mapped tracks
                                global_id = self._reid_manager.get_global_id(
                                    item.get("feed_id", "unknown"), 
                                    item.get("vehicle_id", "unknown")
                                )
                                if global_id:
                                    item["global_vehicle_id"] = global_id
                            tracking_batch.append(item)
                        elif msg_type == "identified_vehicle":
                            identified_batch.append(item)

                    # Execute Re-ID matching as a bulk operation in a thread
                    if items_needing_reid:
                        def bulk_reid_process(reid_items):
                            for itm in reid_items:
                                try:
                                    emb_np = np.array(itm["embedding"], dtype=np.float32)
                                    itm["global_vehicle_id"] = self._reid_manager.match_or_register(
                                        feed_id=itm.get("feed_id", "unknown"),
                                        local_id=itm.get("vehicle_id", "unknown"),
                                        embedding=emb_np,
                                        metadata={"class_name": itm.get("class_name")}
                                    )
                                except Exception as e:
                                    logger.error(f"Re-ID bulk match error: {e}")

                        await loop.run_in_executor(None, bulk_reid_process, items_needing_reid)

                    # Execute tracking data as a batch
                    if tracking_batch:
                        await asyncio.to_thread(db.save_vehicle_data_batch, tracking_batch)
                    
                    # Identified vehicles (usually rarer, process one by one or add batch support later)
                    if identified_batch:
                        for iv in identified_batch:
                            await asyncio.to_thread(db.upsert_identified_vehicle, iv)
                else:
                    # If DB manager is not ready, we must drop the items to prevent queue overflow
                    # or re-queue them if critical. For vehicle tracking, dropping is often acceptable 
                    # during startup/shutdown race conditions.
                    if len(items) > 100:
                        logger.warning(f"DB manager not available. Dropped {len(items)} items from db_queue.")

                # If we processed a full batch, yield briefly but don't sleep long
                if len(items) >= 5000:
                    await asyncio.sleep(0.001)
                else:
                    await asyncio.sleep(0.005)

            except Exception as e:
                logger.error(f"Error in db_queue reader: {e}", exc_info=True)
                await asyncio.sleep(1.0)

    def initialize_shared_values(self):
        import multiprocessing
        if self._global_fps is None:
            manager = multiprocessing.Manager()
            self._global_fps = manager.Value("i", self.config.get("fps", 30))
            logger.info("FeedManager shared values initialized.")

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
        self._inference_command_queues = [MPQueue(maxsize=100) for _ in range(pool_size)]
        for i in range(pool_size):
            print(f"DEBUG: Starting InferenceWorker-{i}")
            p = Process(
                target=inference_worker,
                args=(
                    i,
                    self._inference_input_queues[i], # Pass the partitioned queue
                    self._central_output_queue,
                    self._inference_command_queues[i],
                    self._inference_stop_event,
                    self.config,
                    self._db_queue
                ),
                daemon=True,
                name=f"InferenceWorker-{i}"
            )
            p.start()
            self._inference_pool.append(p)
            # Add small delay to avoid excessive RAM spike during spawn imports
            time.sleep(0.1)

    async def _stop_inference_pool(self):
        logger.info("Stopping Inference Pool...")
        self._inference_stop_event.set()
        
        # Give them time to finish current task
        for _ in range(50):
            try:
                # Drain output queue to unblock workers
                self._central_output_queue.get_nowait()
            except: pass
            
            if all(not p.is_alive() for p in self._inference_pool):
                break
            await asyncio.sleep(0.1)

        # Explicitly close queues
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
            await self._connection_manager.broadcast(message.model_dump_json())

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

            # Check resources
            is_sample = entry.get("is_sample_feed", False)
            if not is_sample or self._any_real_feeds_active_unsafe():
                self._check_resources()

            logger.info(f"Starting feed: '{feed_id}'")

            # Initialize Queues and Events
            entry["command_queue"] = MPQueue(maxsize=50) # Small queue for control commands
            
            # Only create video writer queue if enabled
            video_output_config = self.config.get("video_output", {})
            if video_output_config.get("enabled", False):
                entry["video_writer_queue"] = MPQueue(maxsize=self.config.get("video_input", {}).get("max_queue_size", 500))
            else:
                entry["video_writer_queue"] = None

            entry["stop_event"] = Event()
            entry["reduce_fps_event"] = Event()
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

        # Perform cleanups outside the lock
        if resources_to_cleanup:
            await self._terminate_resources(resources_to_cleanup)
        
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
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry or not entry.get("command_queue"):
                logger.warning(f"Cannot request snapshot for {feed_id}: Feed not running or no command queue.")
                return
            
            try:
                entry["command_queue"].put_nowait({
                    "type": "save_snapshot",
                    "incident_id": incident_id
                })
                logger.info(f"Requested snapshot for feed {feed_id}, incident {incident_id}")
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
        
        logger.info(f"Routing feed {feed_id} to InferenceWorker-{worker_idx}")

        worker_args = (
            source,
            feed_id,
            target_queue,
            entry["stop_event"],
            self.config,
            entry.get("is_looped_feed", False),
        )

        process = Process(
            target=ingestion_worker,
            args=worker_args,
            daemon=True,
            name=f"Ingestion-{feed_id}",
        )
        process.start()
        entry["process"] = process
        entry["start_time"] = time.time()
        logger.info(f"Launched ingestion PID {process.pid} for '{feed_id}'")

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

    async def _read_result_queues(self):
        logger.info("Result reader task started (Decoupled Mode).")
        while not self._stop_reader_flag:
            try:
                items_buffer = []
                # Drain Central Output Queue
                try:
                    # Increased drain limit for high-throughput
                    for _ in range(200):
                        items_buffer.append(self._central_output_queue.get_nowait())
                except queue.Empty:
                    pass

                if not items_buffer:
                    # Adaptive sleep when idle (reduced for lower jitter)
                    await asyncio.sleep(0.001)
                    # Still need to handle periodic tasks
                    await self._handle_periodic_tasks()
                    continue

                feed_ids_to_update = set()
                
                # Process the items
                async with self._lock:
                    for item in items_buffer:
                        feed_id, frame_idx, frame_bytes, metrics, vehicles, extra = item
                        
                        entry = self.process_registry.get(feed_id)
                        if not entry: continue

                        if entry["status"] == FeedOperationalStatusEnum.STARTING:
                            entry["status"] = FeedOperationalStatusEnum.RUNNING
                            feed_ids_to_update.add(feed_id)
                            if feed_id in self._feed_running_events:
                                self._feed_running_events[feed_id].set()

                        # Update Metrics
                        now = time.time()
                        metrics["timestamp"] = datetime.now(timezone.utc)
                        
                        # Add location data from config
                        if entry.get("config_info"):
                            metrics["latitude"] = entry["config_info"].latitude
                            metrics["longitude"] = entry["config_info"].longitude
                            metrics["location_name"] = entry["config_info"].name

                        entry["latest_metrics"] = metrics
                        entry["last_frame_time"] = now
                        if entry.get("timer"):
                            entry["timer"].tick()

                        # Handle Special Message Types from Worker
                        if extra and extra.get("type") == "snapshot":
                            inc_id = extra.get("incident_id")
                            path = extra.get("path")
                            # Update the incident in DB with the snapshot path
                            if self._analytics_service and inc_id:
                                asyncio.create_task(self._analytics_service.update_incident_snapshot(inc_id, path))
                            continue

                        # Route to Video Writer
                        if entry.get("video_writer_queue"):
                            try:
                                entry["video_writer_queue"].put_nowait((frame_bytes, metrics))
                            except queue.Full:
                                pass

                        # Update History
                        if "metrics_history" not in entry or not isinstance(entry["metrics_history"], deque):
                            entry["metrics_history"] = deque(maxlen=MAX_METRICS_HISTORY_LENGTH)
                        entry["metrics_history"].append((now, metrics.copy()))
                        
                        while entry["metrics_history"] and entry["metrics_history"][0][0] < now - self._metrics_averaging_window:
                            entry["metrics_history"].popleft()

                        # Distribute Frames to Subscribers (Internal)
                        if feed_id in self.frame_subscriber_queues:
                            for sub_q in self.frame_subscriber_queues[feed_id]:
                                try:
                                    sub_q.put_nowait({"frame": frame_bytes, "metrics": metrics, "vehicles": vehicles})
                                except asyncio.QueueFull:
                                    pass

                        # Analytics and Broadcast Throttling
                        target_fps = self.config.get("video_output", {}).get("fps", 10)
                        min_interval = 1.0 / target_fps
                        last_broadcast = entry.get("last_broadcast_time", 0.0)
                        
                        if now - last_broadcast >= min_interval:
                            # Only spawn if previous task for this feed finished (Backpressure)
                                if feed_id not in self._active_broadcast_tasks or self._active_broadcast_tasks[feed_id].done():
                                    entry["last_broadcast_time"] = now
                                    task = asyncio.create_task(self._broadcast_video_frame(feed_id, frame_idx, frame_bytes, metrics, vehicles, extra))
                                    self._active_broadcast_tasks[feed_id] = task

                        # Analytics hook
                        if self._analytics_service:
                            # We can also track analytics tasks or just fire-and-forget if they are fast
                            asyncio.create_task(self._analytics_service.process_feed_metrics(feed_id, metrics))

                # Handle broadcasts and periodic tasks
                await self._perform_broadcasts(feed_ids_to_update, False, False)
                await self._handle_periodic_tasks()

                # If we processed a full buffer, skip sleep to maintain high throughput
                # but yield control to other tasks
                if len(items_buffer) < 200:
                    await asyncio.sleep(0.001)
                else:
                    await asyncio.sleep(0)

            except Exception as e:
                logger.error(f"Error in result reader loop: {e}", exc_info=True)
                await asyncio.sleep(1.0)

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

    async def _broadcast_video_frame(self, feed_id, frame_idx, frame_bytes, metrics, vehicles, extra_payload=None):
        if not self._connection_manager or not frame_bytes: return
        
        try:
            # 1. Prepare Payload
            payload = {
                "t": WebSocketMessageTypeEnum.VIDEO_FRAME,
                "f": feed_id,
                "i": frame_idx,
                "ts": time.time(),
                "v": vehicles,
                "m": metrics
            }

            # 2. Check for Adaptive Streaming (ROIs)
            if extra_payload and "bg" in extra_payload:
                payload["bg"] = extra_payload["bg"]
                payload["rois"] = extra_payload.get("rois", [])
                # Use smaller original frame if available (future: could drop frame_bytes here)
            else:
                payload["frame"] = frame_bytes

            # 3. Binary Serialization with msgpack
            # Use raw bytes for performance
            msg_bytes = msgpack.packb(payload, use_bin_type=True)
            
            subscribed_client_ids = self._connection_manager.get_clients_for_feed(feed_id)
            if not subscribed_client_ids:
                return

            # 4. Binary Delivery
            await self._connection_manager.broadcast_realtime_bytes(msg_bytes)
            
        except Exception as e:
            logger.error(f"Binary broadcast error for {feed_id}: {e}")

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

    # --- Helper Methods ---
    
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
        await self._connection_manager.broadcast(msg.model_dump_json())

    async def _broadcast_kpi_update(self):
        if not self._connection_manager:
            return

        total_vehicles = 0
        total_speed_sum = 0.0
        total_speed_count = 0
        total_congestion_score = 0.0
        active_feeds_count = 0
        
        # Aggregate metrics from all running feeds
        async with self._lock:
            for feed_id, entry in self.process_registry.items():
                if entry["status"] == FeedOperationalStatusEnum.RUNNING and entry.get("latest_metrics"):
                    metrics = entry["latest_metrics"]
                    
                    # Vehicle Count
                    v_count = metrics.get("total_vehicles", 0)
                    total_vehicles += v_count
                    
                    # Speed (Weighted Average)
                    avg_speed = metrics.get("average_speed_kmh", 0.0)
                    if v_count > 0:
                        total_speed_sum += avg_speed * v_count
                        total_speed_count += v_count
                    
                    # Congestion
                    congestion = metrics.get("congestion_score", 0.0)
                    total_congestion_score += congestion
                    
                    active_feeds_count += 1

        # Calculate Global Averages
        global_avg_speed = (total_speed_sum / total_speed_count) if total_speed_count > 0 else 0.0
        global_congestion_index = (total_congestion_score / active_feeds_count) if active_feeds_count > 0 else 0.0

        # Construct Payload
        kpi_data = GlobalRealtimeMetrics(
            timestamp=datetime.now(timezone.utc).isoformat(),
            metrics_source="aggregated_feeds",
            total_flow=total_vehicles,
            average_speed_kmh=round(global_avg_speed, 1),
            congestion_index=round(global_congestion_index, 1),
            active_incidents_count=0, # Placeholder until IncidentManager is integrated
            feed_statuses={
                "active": active_feeds_count,
                "total": len(self.process_registry)
            }
        )

        # Broadcast
        message = WebSocketMessage(
            type=WebSocketMessageTypeEnum.KPI_UPDATE,
            data=kpi_data.model_dump()
        )
        await self._connection_manager.broadcast_realtime(message.model_dump_json())

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
        
        # Save ReID state before shutting down
        if self._reid_manager:
            await asyncio.to_thread(self._reid_manager.save_state)
            logger.info("ReID state saved during shutdown.")
        
        tasks = []
        if self._result_reader_task:
            tasks.append(self._result_reader_task)
        if self._watchdog_task:
            tasks.append(self._watchdog_task)
        if self._db_reader_task:
            tasks.append(self._db_reader_task)
            
        if tasks:
            await asyncio.wait(tasks, timeout=5.0)

    # ... (Add/Remove dynamic sample feeds, WebSocket handlers match your original structure)

# Global Instance Management
feed_manager_instance: Optional[FeedManager] = None

async def initialize_feed_manager(config: dict):
    global feed_manager_instance
    if feed_manager_instance is None:
        feed_manager_instance = FeedManager(config)
    return feed_manager_instance

def get_feed_manager() -> FeedManager:
    if feed_manager_instance is None:
        raise RuntimeError("FeedManager not initialized.")
    return feed_manager_instance
