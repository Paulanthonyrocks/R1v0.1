from __future__ import annotations
import base64
import asyncio
import logging
import time
import re
import atexit
import json

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
from app.core.processing_worker import process_video
from app.utils.monitoring import check_system_resources
from app.utils.video import FrameTimer
from app.websocket.connection_manager import ConnectionManager
from app.services.analytics_service import AnalyticsService
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
        
        self._connection_manager: Optional[ConnectionManager] = None
        self._prediction_scheduler: Optional[PredictionScheduler] = None
        self._analytics_service: Optional[AnalyticsService] = None
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
        self.persistence_path = Path("backend/data/feeds_config.json")

        # Database processing
        self._db_queue: Optional[MPQueue] = MPQueue(maxsize=5000)
        self._db_reader_task: Optional[asyncio.Task] = None

        # Initialize shared values
        self.initialize_shared_values()

        self._initialize_available_feeds()

        # Register cleanup on exit
        atexit.register(self._atexit_cleanup)

        # Start background reader
        self._result_reader_task = asyncio.create_task(self._read_result_queues())
        self.logger.info("FeedManager initialized and result reader task started.")



    def _atexit_cleanup(self):
        """Synchronous cleanup for interpreter exit."""
        logger.info("FeedManager executing atexit cleanup...")
        for feed_id, entry in list(self.process_registry.items()):
            process = entry.get("process")
            if process and process.is_alive():
                logger.info(f"Terminating process {process.pid} for {feed_id} in atexit")
                process.terminate()
                process.join(timeout=0.5)
                if process.is_alive():
                    process.kill()



    def set_prediction_scheduler(self, scheduler: PredictionScheduler):
        self._prediction_scheduler = scheduler
        self.logger.info("PredictionScheduler set in FeedManager.")

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
                    for _ in range(500):
                        items.append(self._db_queue.get_nowait())
                except queue.Empty:
                    pass

                if not items:
                    await asyncio.sleep(0.5)
                    continue

                if self._analytics_service and self._analytics_service._db_manager:
                    db = self._analytics_service._db_manager
                    
                    # Separate items by type for appropriate processing
                    tracking_batch = []
                    identified_batch = []
                    
                    for item in items:
                        msg_type = item.get("type", "vehicle_data")
                        if msg_type == "vehicle_data":
                            tracking_batch.append(item)
                        elif msg_type == "identified_vehicle":
                            identified_batch.append(item)

                    # Execute tracking data as a batch
                    if tracking_batch:
                        await asyncio.to_thread(db.save_vehicle_data_batch, tracking_batch)
                    
                    # Identified vehicles (usually rarer, process one by one or add batch support later)
                    if identified_batch:
                        for iv in identified_batch:
                            await asyncio.to_thread(db.upsert_identified_vehicle, iv)
                else:
                    logger.warning("DB manager not available for db_queue processing")

                # Small sleep to yield, but fast enough for high throughput
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
                        "result_queue": None,
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
                
                # If the feed is running, signal the worker via command_queue
                if entry["status"] in [FeedOperationalStatusEnum.RUNNING, FeedOperationalStatusEnum.STARTING]:
                    command_queue = entry.get("command_queue")
                    if command_queue:
                        try:
                            command_queue.put_nowait({
                                "type": "config_update", 
                                "data": update_data
                            })
                            logger.info(f"Sent config update to worker for feed {feed_id}")
                        except queue.Full:
                            logger.warning(f"Command queue full for feed {feed_id}, skipping config update.")
                        except Exception as e:
                            logger.error(f"Failed to send config update to worker {feed_id}: {e}")

        await self._broadcast_feed_update(feed_id)

    async def add_and_start_feed(
        self,
        source: str,
        latitude: float,
        longitude: float,
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
                    "result_queue": None,
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
            entry["result_queue"] = MPQueue(maxsize=self.config.get("video_input", {}).get("max_queue_size", 500))
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

    # --- Internal Process & Resource Management ---

    def _launch_worker(self, feed_id: str, source: str):
        """Internal synchronous method to spawn the process."""
        entry = self.process_registry.get(feed_id)
        if not entry:
            return

        vis_options = self.config.get("vis_options_default", {"Tracked Vehicles"})
        
        worker_args = (
            source,
            entry["result_queue"],
            entry["stop_event"],
            None, # alerts_queue (handled via result queue)
            self.config,
            feed_id,
            self.config["vehicle_detection"]["confidence_threshold"],
            self.config["vehicle_detection"]["proximity_threshold"],
            self.config["vehicle_detection"]["track_timeout"],
            vis_options,
            entry["reduce_fps_event"],
            self._global_fps,
            self._db_queue, # Pass the global DB queue
            None, # error_queue
            entry.get("config_info"),
            entry.get("video_writer_queue"),
            entry.get("is_looped_feed", False),
            entry.get("command_queue"),
        )

        process = Process(
            target=process_video,
            args=worker_args,
            daemon=True,
            name=f"Worker-{feed_id}",
        )
        process.start()
        entry["process"] = process
        entry["start_time"] = time.time()
        logger.info(f"Launched process PID {process.pid} for '{feed_id}'")

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
            "result_queue": entry.get("result_queue"),
            "command_queue": entry.get("command_queue"),
            "video_writer_queue": entry.get("video_writer_queue"),
            "video_writer": self.video_writers.pop(feed_id, None)
        }

        # Reset Entry to Stopped state
        entry.update({
            "status": FeedOperationalStatusEnum.STOPPED,
            "error_message": None,
            "process": None,
            "stop_event": None,
            "result_queue": None,
            "command_queue": None,
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
        result_queue = resources.get("result_queue")
        command_queue = resources.get("command_queue")
        writer_queue = resources.get("video_writer_queue")
        video_writer = resources.get("video_writer")
        
        queues = [q for q in [result_queue, command_queue, writer_queue] if q]

        # 1. Signal Stop
        if stop_event:
            stop_event.set()

        # 2. Stop Video Writer
        if video_writer:
            try:
                await asyncio.to_thread(video_writer.stop)
            except Exception as e:
                logger.error(f"Error stopping video writer for {feed_id}: {e}")

        # 3. Aggressively drain queues to unblock the worker's put() calls
        # If the worker is stuck on q.put(), it won't check stop_event until the put succeeds.
        start_wait = time.time()
        while process and process.is_alive() and (time.time() - start_wait < PROCESS_JOIN_TIMEOUT):
            drained_any = False
            for q in queues:
                try:
                    # Drain in chunks
                    while True:
                        q.get_nowait()
                        drained_any = True
                except (queue.Empty, OSError, ValueError):
                    pass
            
            if not drained_any:
                await asyncio.sleep(0.1)
            else:
                # If we drained something, give the process a tiny bit of CPU time to advance
                await asyncio.sleep(0.01)

        # 4. Join or Kill
        if process and process.is_alive():
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, process.join, 1.0)
                
                if process.is_alive():
                    logger.warning(f"Process {process.pid} for {feed_id} hung. Terminating.")
                    process.terminate()
                    # Wait briefly for termination to take effect
                    await asyncio.sleep(0.5) 
                    
                    if process.is_alive():
                        logger.error(f"Process {process.pid} failed to terminate. Killing.")
                        process.kill()
            except Exception as e:
                logger.error(f"Error joining process for {feed_id}: {e}")

        # 5. Close Queues
        for q in queues:
            try:
                q.close()
                q.cancel_join_thread() # Important: Don't wait for background thread to flush
            except Exception:
                pass

    # --- Background Reader ---

    async def _read_result_queues(self):
        logger.info("Result reader task started.")
        while not self._stop_reader_flag:
            try:
                # Snapshot active queues to avoid holding lock during processing
                active_queues = await self._get_active_queues_snapshot()

                if not active_queues:
                    await asyncio.sleep(0.1)
                    continue

                processed_any = False
                feed_ids_to_update = set()
                
                # Iterate over snapshot
                for feed_id, result_q in active_queues:
                    processed = await self._process_single_queue(feed_id, result_q, feed_ids_to_update)
                    if processed:
                        processed_any = True

                # Broadcast updates if status changed
                if feed_ids_to_update:
                    for fid in feed_ids_to_update:
                        await self._broadcast_feed_update(fid)
                    await self._broadcast_kpi_update()

                # Periodic KPI Broadcast
                now = time.time()
                if now - self._last_kpi_broadcast_time >= self._kpi_broadcast_interval:
                    await self._broadcast_kpi_update()
                    self._last_kpi_broadcast_time = now

                # Periodic Resource Check (every 30s)
                now = time.time()
                if now - self._last_queue_log_time >= 30.0:
                    self._last_queue_log_time = now
                    try:
                        self._check_resources()
                    except ResourceLimitError as e:
                        logger.error(f"Resource limit exceeded during operation: {e}")

                # Adaptive sleep
                if processed_any:
                    await asyncio.sleep(0.001) # Minimal yield
                else:
                    await asyncio.sleep(0.01) # Save CPU when idle

            except Exception as e:
                logger.error(f"Error in result reader loop: {e}", exc_info=True)
                await asyncio.sleep(1.0)

    async def _get_active_queues_snapshot(self):
        """Get list of (feed_id, queue) without holding lock long-term."""
        async with self._lock:
            return [
                (fid, entry["result_queue"]) 
                for fid, entry in self.process_registry.items() 
                if entry["status"] in [FeedOperationalStatusEnum.RUNNING, FeedOperationalStatusEnum.STARTING] 
                and entry.get("result_queue")
            ]

    async def _process_single_queue(self, feed_id: str, q: MPQueue, feed_ids_to_update: set) -> bool:
        processed = False
        items_buffer = []

        # 1. Drain Queue (Sync, Fast)
        try:
            for _ in range(QUEUE_DRAIN_LIMIT):
                items_buffer.append(q.get_nowait())
        except queue.Empty:
            pass
        except Exception:
            return False # Queue closed or error

        if not items_buffer:
            return False

        # 2. Process Items
        last_item = None
        
        # We only lock ONCE per batch, not per item
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry:
                return False

            if entry["status"] == FeedOperationalStatusEnum.STARTING:
                entry["status"] = FeedOperationalStatusEnum.RUNNING
                feed_ids_to_update.add(feed_id)
                if feed_id in self._feed_running_events:
                    self._feed_running_events[feed_id].set()

            for item in items_buffer:
                _fid, frame_idx, frame_bytes, metrics, vehicles, _ = item


                processed = True
                
                # Update Metrics
                metrics["timestamp"] = datetime.now(timezone.utc)
                entry["latest_metrics"] = metrics
                if entry.get("timer"):
                    entry["timer"].tick()

                # Update History
                now = time.time()
                if "metrics_history" not in entry or not isinstance(entry["metrics_history"], deque):
                    entry["metrics_history"] = deque(maxlen=MAX_METRICS_HISTORY_LENGTH)
                entry["metrics_history"].append((now, metrics.copy()))
                
                # Efficiently remove old metrics
                while entry["metrics_history"] and entry["metrics_history"][0][0] < now - self._metrics_averaging_window:
                    entry["metrics_history"].popleft()

                # Distribute Frames to Subscribers (Internal)
                if feed_id in self.frame_subscriber_queues:
                    for sub_q in self.frame_subscriber_queues[feed_id]:
                        try:
                            sub_q.put_nowait({"frame": frame_bytes, "metrics": metrics, "vehicles": vehicles})
                        except asyncio.QueueFull:
                            pass

                # Analytics hook (for every frame)
                if self._analytics_service:
                    asyncio.create_task(self._analytics_service.process_feed_metrics(feed_id, metrics))

                last_item = item

        # 3. Handle Broadcast (Outside Lock)
        should_broadcast = False
        if last_item:
            # Check throttling
            now = time.time()
            target_fps = self.config.get("video_output", {}).get("fps", 10) # Use configured output FPS or default to 10
            min_interval = 1.0 / target_fps
            
            async with self._lock:
                entry = self.process_registry.get(feed_id)
                if entry:
                    last_time = entry.get("last_broadcast_time", 0.0)
                    if now - last_time >= min_interval:
                        entry["last_broadcast_time"] = now
                        should_broadcast = True

        if last_item and should_broadcast:
            _fid, frame_idx, frame_bytes, metrics, vehicles, _ = last_item
            
            if frame_bytes and self._connection_manager:
                # logger.debug(f"Received frame_bytes for {feed_id}. Length: {len(frame_bytes) if frame_bytes else 0}")
                # Offload encoding to thread pool to avoid blocking the event loop
                loop = asyncio.get_running_loop()
                try:
                    b64_frame = await loop.run_in_executor(
                        self._cpu_executor, 
                        self._encode_frame, 
                        frame_bytes
                    )
                    
                    vid_msg = VideoFrameData(
                        feed_id=feed_id, frame_index=frame_idx,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        frame=b64_frame, metrics=metrics, vehicles=vehicles
                    )
                    
                    msg_json = WebSocketMessage(type=WebSocketMessageTypeEnum.VIDEO_FRAME, data=vid_msg.model_dump()).model_dump_json()
                        
                    # Get subscribers for this specific feed
                    subscribed_client_ids = self._connection_manager.get_clients_for_feed(feed_id)
                    if subscribed_client_ids:
                        logger.info(f"Broadcasting VIDEO_FRAME for {feed_id} to {len(subscribed_client_ids)} clients. Frame: {frame_idx}")
                    
                    # Use send_realtime_message (fire-and-forget / drop-if-full) for video frames
                    # to prevent slow clients from blocking the feed manager.
                    for client_id in subscribed_client_ids:
                        await self._connection_manager.send_realtime_message(msg_json, client_id)
                    
                except Exception as e:
                    logger.error(f"Error encoding/sending frame for {feed_id}: {e}")
            elif not frame_bytes:
                logger.warning(f"Skipping broadcast for {feed_id}: Empty frame bytes.")
            elif not self._connection_manager:
                logger.warning(f"Skipping broadcast for {feed_id}: No connection manager.")

        return processed

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
        await self._connection_manager.broadcast_to_topic(msg.model_dump_json(), f"feed:{feed_id}")

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
        


        await self.stop_all_feeds()
        if self._result_reader_task:
            await asyncio.wait([self._result_reader_task], timeout=5.0)

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
