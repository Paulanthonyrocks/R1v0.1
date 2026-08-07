from __future__ import annotations

import base64
import asyncio
import logging
import multiprocessing
import threading
import time
import re
import atexit
import json
import numpy as np
import msgpack

from collections import deque
from typing import Dict, Any, Optional, List
from pathlib import Path
import queue  # For queue.Empty exception
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Process

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
from app.utils.monitoring import FrameTimer, check_system_resources
from app.utils.distributed_queue import RedisQueue, RedisStreamQueue, RedisEvent, RedisValue
from app.utils.redis_client import get_redis_client
from app.utils.shared_frame_buffer import SharedFrameBuffer
from app.websocket.connection_manager import ConnectionManager, MessagePriority
from app.services.analytics_service import AnalyticsService
from app.services.reid_manager import GlobalReIDManager
from app.tasks.prediction_scheduler import PredictionScheduler
from app.services.video_writer import VideoWriter
from app.services.feed_broadcaster import FeedBroadcaster
from app.services.inference_pool_manager import InferencePoolManager
from app.services.feed_registry import FeedRegistry
from app.services.result_processor import ResultProcessor
from app.services.feed_watchdog import FeedWatchdog

logger = logging.getLogger("app.services.feed_manager")

from app.services.constants import FeedManagerConstants

class FeedManager:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logger

        # Emergency SHM cleanup before initializing anything to prevent restart failures
        SharedFrameBuffer.force_cleanup()

        self.video_writers: Dict[str, VideoWriter] = {}
        self._lock = asyncio.Lock()
        self._feed_locks: Dict[str, asyncio.Lock] = {}

        self._global_fps = None
        self._stop_reader_flag = False
        self._is_shutting_down = False
        self._result_reader_task: Optional[asyncio.Task] = None

        # Communication
        # NOTE: Frames are delivered to clients via FeedBroadcaster ->
        # ConnectionManager (WebSocket). For in-process consumers that
        # need decoded frame bytes (e.g. VideoProcessor for recording),
        # we expose a minimal per-feed subscriber mechanism below. Each
        # subscriber owns its own bounded queue. The result_processor
        # pumps a copy of every decoded frame into each active subscriber.
        # This is intentionally opt-in: no default subscriber exists,
        # so empty subscriber maps cost nothing.

        # Map: feed_id -> list of asyncio.Queue subscribed for in-process consumers
        self._frame_subscribers: Dict[str, List[asyncio.Queue]] = {}
        self._frame_subscribers_lock = asyncio.Lock()

        self._connection_manager: Optional[ConnectionManager] = None
        self._prediction_scheduler: Optional[PredictionScheduler] = None
        self._analytics_service: Optional[AnalyticsService] = None
        self._reid_manager = GlobalReIDManager(config)
        shm_pool_size = self.config.get('performance', {}).get('shm_pool_size', 100)
        self.logger.info(f"Initializing SharedFrameBuffer with pool_size={shm_pool_size}")
        self.frame_buffer = SharedFrameBuffer(pool_size=shm_pool_size, owner=True)
        self.pipeline_pressure = RedisValue('f', 0.0, 'pipeline_pressure')
        self._is_processing_active: bool = False

        self._executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="FeedMgr-Proc")

        # Broadcaster for streaming updates to frontend is initialized in set_connection_manager()
        self.broadcaster: Optional[FeedBroadcaster] = None

        self._last_kpi_broadcast_time = 0.0
        self._kpi_broadcast_interval = self.config.get("kpi_broadcast_interval", FeedManagerConstants.KPI_BROADCAST_INTERVAL_DEFAULT)
        self._sample_feed_ids: List[str] = []
        self._feed_running_events: Dict[str, asyncio.Event] = {}

        # Adaptive delay settings
        self._min_read_delay = self.config.get("min_frame_read_delay_ms", FeedManagerConstants.MIN_READ_DELAY_MS_DEFAULT) / 1000.0
        self._max_read_delay = self.config.get("max_frame_read_delay_ms", FeedManagerConstants.MAX_READ_DELAY_MS_DEFAULT) / 1000.0
        self._current_read_delay = self._min_read_delay
        self._delay_adjustment_factor = self.config.get("delay_adjustment_factor", FeedManagerConstants.DELAY_ADJUSTMENT_FACTOR_DEFAULT)
        self._last_queue_log_time = 0.0
        self._queue_log_interval = self.config.get("queue_log_interval", FeedManagerConstants.QUEUE_LOG_INTERVAL_DEFAULT)

        # Metrics aggregation window
        self._metrics_averaging_window = self.config.get("metrics_averaging_window_seconds", FeedManagerConstants.METRICS_WINDOW_DEFAULT)

        # Persistence
        self.persistence_path = Path(
            self.config.get("feeds_config_path", "backend/data/feeds_config.json")
        )
        self.registry = FeedRegistry(persistence_path=self.persistence_path)

        # Database processing
        self._db_queue: Optional[RedisQueue] = RedisQueue('db_writes', maxsize=FeedManagerConstants.DB_QUEUE_MAXSIZE)
        self._db_reader_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._last_scale_time = 0.0
        self._startup_time = time.time()

        # Virtual Slot Architecture for Dynamic Scaling
        self.slot_count = FeedManagerConstants.SLOT_COUNT
        # Counters used by _launch_worker to co-locate feeds onto the same
        # worker's slots so inference batches actually fill (see comment there).
        # _launch_worker is synchronous, so a threading.Lock guards the
        # counters (self._lock is the async lock used elsewhere).
        self._feed_launch_seq = 0
        self._per_worker_feed_count: Dict[int, int] = {}
        self._route_lock = threading.Lock()
        self.use_redis = self.config.get("redis", {}).get("enabled", False)

        if self.use_redis:
            # Each slot gets its own Redis stream key to prevent cross-slot consumer
            # group pollution where orphaned groups accumulate pending messages.
            self._inference_input_queues = [
                RedisStreamQueue(f'inference_input:slot_{i}', group_name='workers')
                for i in range(self.slot_count)
            ]
            self._central_output_queue = RedisStreamQueue('central_output', group_name='output-readers')

            # Purge stale consumer groups from the old monolithic stream (pre-fix migration)
            try:
                _r = get_redis_client(decode_responses=False)
                _old_key = b"stream:inference_input"
                if _r.exists(_old_key):
                    for _gi in range(16):
                        _old_group = f"worker_{_gi}"
                        try:
                            _r.xgroup_destroy(_old_key, _old_group)
                        except Exception:
                            pass
                    logger.info("Purged stale consumer groups from old monolithic inference_input stream")
                    if _r.xlen(_old_key) == 0:
                        _r.delete(_old_key)
            except Exception as e:
                logger.debug(f"Old stream cleanup skipped: {e}")
        else:
            self._inference_input_queues = [
                RedisQueue(f'slot_{i}', maxsize=100)
                for i in range(self.slot_count)
            ]
            self._central_output_queue = RedisQueue('central_output', maxsize=FeedManagerConstants.QUEUE_MAX_SIZE)

        self._inference_stop_event = RedisEvent('inference_stop')
        self._startup_ready = asyncio.Event()
        
        # Initialize Worker Pool Manager now that queues and slot_count are available
        self.pool_manager = InferencePoolManager(
            config=self.config,
            slot_count=self.slot_count,
            inference_input_queues=self._inference_input_queues,
            db_queue=self._db_queue,
            stop_event=self._inference_stop_event
        )

        # Initialize Result Processor
        self.result_processor = ResultProcessor(
            central_output_queue=self._central_output_queue,
            frame_buffer=self.frame_buffer,
            executor=self._executor,
            config=self.config,
            registry=self.registry,
            broadcaster=self.broadcaster
        )

        # Initialize Watchdog
        self.watchdog = FeedWatchdog(
            registry=self.registry,
            pool_manager=self.pool_manager,
            restart_callback=self.restart_feed
        )

        # Use _resolve_pool_size so this fallback chain matches the rest of
        # the codebase: performance.inference_pool_size first, then
        # inference.num_workers, then the hard default of 2. Reading
        # inference_pool_size directly here caused a divergence where a
        # config that set only inference.num_workers (legacy) would boot
        # with pool_size=2 even when _resolve_pool_size elsewhere would
        # return a different number.
        self._initial_inference_pool_size = self._resolve_pool_size()

        self.initialize_shared_values()
        self._initialize_available_feeds()

        # Register cleanup on exit
        atexit.register(self._atexit_cleanup)

    @property
    def process_registry(self):
        """Delegates access to the FeedRegistry's process_registry dictionary."""
        return self.registry.process_registry

    def _purge_stale_streams(self):
        """Aggressively remove stale Redis stream data so workers don't process dead SHM references."""
        try:
            # Nuke central_output entirely – it's recreated anyway
            rc = self._central_output_queue.redis
            central_key = self._central_output_queue.key
            if rc.exists(central_key):
                rc.delete(central_key)
                self.logger.info(f"Deleted stale stream {central_key}")
            self._central_output_queue._ensure_group()

            # For each slot stream, check if it has pending messages
            for slot_q in self._inference_input_queues:
                if hasattr(slot_q, 'key') and hasattr(slot_q, 'redis'):
                    slot_key = slot_q.key
                    if slot_q.redis.exists(slot_key):
                        try:
                            # Delete and recreate the stream to wipe all old data
                            slot_q.redis.delete(slot_key)
                            # Re‑create group
                            slot_q._ensure_group()
                            self.logger.info(f"Purged stale slot stream {slot_key}")
                        except Exception as e:
                            self.logger.warning(f"Failed to purge slot stream {slot_key}: {e}")
        except Exception as e:
            self.logger.warning(f"Could not purge stale streams: {e}")

    async def initialize(self):
        """Asynchronous initialization: start background tasks after the event loop is running."""
        if (
            self._result_reader_task is not None
            and not self._result_reader_task.done()
        ):
            self.logger.info("FeedManager already initialized (result reader active). Skipping.")
            return

        # Purge stale Redis stream data BEFORE starting the result reader so that
        # dead SHM references don't cause "Invalid size 0" error spam.
        self._purge_stale_streams()

        try:
            multiprocessing.set_start_method('spawn', force=True)
            logger.info("Multiprocessing start method set to 'spawn' for CUDA safety.")
        except RuntimeError:
            pass  # Already set — ignore

        self._result_reader_task = asyncio.create_task(
            self.result_processor.process_results_loop(self._handle_periodic_tasks)
        )
        self._pressure_task = asyncio.create_task(self._update_pipeline_pressure())
        self._scaling_task = asyncio.create_task(self._scaling_monitor())
        self._watchdog_task = asyncio.create_task(self.watchdog.watchdog_loop())
        self.logger.info(
            "FeedManager initialized. Reader, Watchdog, Pressure and Scaling tasks started."
        )

    def _get_feed_lock(self, feed_id: str) -> asyncio.Lock:
        """Returns a per-feed lock to ensure atomic operations like restart."""
        return self._feed_locks.setdefault(feed_id, asyncio.Lock())

    def _resolve_pool_size(self) -> int:
        """Resolve the desired inference worker count.

        Preference order:
          1. performance.inference_pool_size  (the documented "pinned pool"
             target; config.yaml sets this to 8 with pin_inference_pool: true)
          2. inference.num_workers            (legacy key, default 2)
        Historically only `num_workers` was read, so a pinned pool of 8 in
        config.yaml still booted at 2 and then (pre-pin fix) auto-scaled under
        load. All pool-size reads now go through this helper so startup, the
        "pool empty -> auto-scale" path, and feed routing agree on one number.
        """
        # self.config is a dict (converted from AppConfig via to_dict() in main.py).
        # AppConfig.performance is PerformanceConfig (pydantic model). After
        # model_dump() the performance fields survive because PerformanceConfig
        # explicitly declares pin_inference_pool and inference_pool_size.
        # The isinstance check is defensive for tests that pass a plain dict.
        perf = self.config.get("performance", {}) if isinstance(self.config, dict) else {}
        size = perf.get("inference_pool_size")
        if isinstance(size, int) and size >= 1:
            return size
        return int(self.config.get("inference", {}).get("num_workers", 2))

    def _spawn_worker(self, worker_id: int):
        """Spawns a single inference worker assigned to specific slots."""
        self.pool_manager.spawn_worker(worker_id)

    def scale_pool(self, target_size: int):
        """Dynamically adjusts the number of active workers and rebalances slots."""
        # Force clear stop signals to prevent workers from exiting immediately on boot
        # if there's a stale signal in Redis or a set Event.
        try:
            from app.utils.redis_client import get_redis_client
            rc = get_redis_client()
            if rc.exists("signal:pipeline_stop"):
                self.logger.info("Clearing stale 'signal:pipeline_stop' before scaling pool.")
                rc.delete("signal:pipeline_stop")
        except Exception as e:
            self.logger.debug(f"Could not clear stale pipeline stop signal: {e}")

        self._inference_stop_event.clear()
        self.pool_manager.scale_pool(target_size)

    def _atexit_cleanup(self):
        """Synchronous cleanup for interpreter exit."""
        logger.info("FeedManager executing atexit cleanup...")

        # 1. Cleanup ingestion workers
        for feed_id, entry in list(self.process_registry.items()):
            process = entry.get("process")
            if process and process.is_alive():
                logger.info(f"Terminating ingestion process {process.pid} for {feed_id} in atexit")
                process.terminate()
                process.join(timeout=0.5)
                if process.is_alive():
                    process.kill()

        # 2. Cleanup inference pool
        self.pool_manager.cleanup()

        # 3. Shut down executor to release active memoryviews
        try:
            logger.info("Shutting down result processing executor...")
            self._executor.shutdown(wait=True)
        except Exception as e:
            logger.error(f"Error shutting down executor: {e}")

        # 4. Cleanup shared memory
        try:
            self.frame_buffer.cleanup()
            logger.info("SharedFrameBuffer cleaned up successfully.")
        except Exception as e:
            logger.error(f"Error cleaning up SharedFrameBuffer: {e}")

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

        # Wire the analytics ingestion hook so tracked-vehicle data reaches
        # SafetyMonitor / incident creation / metric history (audit C1).
        if self.result_processor:
            self.result_processor.set_analytics_hook(service.process_feed_metrics)
            self.logger.info("Analytics ingestion hook wired into ResultProcessor.")

        if self._reid_manager and hasattr(service, "_db_manager"):
            self._reid_manager.set_db_manager(service._db_manager)
            self.logger.info("ReIDManager connected to DatabaseManager.")

        if self._db_reader_task is None:
            self._db_reader_task = asyncio.create_task(self._read_db_queue())

    def set_connection_manager(self, manager: ConnectionManager):
        self._connection_manager = manager
        self.broadcaster = FeedBroadcaster(manager)
        if self.result_processor:
            self.result_processor.set_broadcaster(self.broadcaster)
            # Wire the in-process subscriber pump once both sides exist.
            # Recording (VideoProcessor) and any future in-process frame
            # consumer uses this hook; trivial no-op when nothing is
            # subscribed.
            self.result_processor.set_subscriber_pump(self.deliver_to_subscribers)
        logger.info("WebSocket ConnectionManager set in FeedManager. Broadcaster initialized and pushed to ResultProcessor.")

    async def _scaling_monitor(self):
        """Monitors queue depth and scales the worker pool dynamically."""
        # Wait until startup is complete before starting to scale
        await self._startup_ready.wait()
        
        while not self._stop_reader_flag:
            if self._is_shutting_down:
                break
            # Stand down when processing is stopped (POST /feeds/stop). Without
            # this, the monitor keeps running after stop_processing() (which
            # does not set _is_shutting_down) and keeps calling scale_pool
            # against a dead feed set -- leaking scale actions on a stopped
            # pipeline. Startup re-sets _is_processing_active=True and clears
            # _startup_ready so normal scaling resumes.
            if not self._is_processing_active:
                await asyncio.sleep(FeedManagerConstants.SCALE_COOLDOWN)
                continue
            try:
                total_depth = sum(
                    q.qsize() for q in self._inference_input_queues if hasattr(q, 'qsize')
                )
                # Per-WORKER backlog, not per-slot. Dividing by slot_count (was 16)
                # diluted the signal ~8x (3 feeds over 16 slots -> avg ~0.2, never
                # tripping SCALE_UP_THRESHOLD). Dividing by live worker count makes
                # the metric rise as soon as any worker's feeds back up, so scale-up
                # actually fires under real load.
                current_size = self.pool_manager.pool_size
                avg_depth = total_depth / max(1, current_size)

                # Scale thresholds are config-overridable; without explicit
                # keys the constant defaults (SCALE_UP_THRESHOLD=10 etc.)
                # still apply, matching the previous behaviour.
                up_threshold = float(
                    self.config.get("performance", {}).get(
                        "scale_up_threshold", FeedManagerConstants.SCALE_UP_THRESHOLD
                    )
                )
                down_threshold = float(
                    self.config.get("performance", {}).get(
                        "scale_down_threshold", FeedManagerConstants.SCALE_DOWN_THRESHOLD
                    )
                )

                # PINNED POOL: when set, the autoscaler never scales up OR down.
                # Rationale: each scale action respawns a worker that must reload
                # the YOLO + ReID models onto the GPU. Under oscillating load the
                # scaler thrashes (scale-up on a backlog spike, scale-down a moment
                # later), reloading models dozens of times and -- if a model file
                # is corrupt/truncated -- killing workers mid-run (the
                # "PytorchStreamReader failed reading zip archive" crash seen at
                # boot). A pinned pool removes that churn entirely: pick
                # `inference_pool_size` to cover peak load and leave it fixed.
                # Default False preserves the old adaptive behavior.
                #
                # Check pin BEFORE the cap warning: when pinned, hitting the cap
                # is the *intended* steady state, not an anomaly. Without this
                # ordering, a pinned deploy logs "[ScalingMonitor] Worker cap (N)
                # reached" every SCALE_COOLDOWN seconds for the entire run
                # (observed 16 occurrences in a 7-min run at 14:20:05 in
                # backend_main.log), drowning real warnings.
                if self.config.get("performance", {}).get("pin_inference_pool", False):
                    await asyncio.sleep(FeedManagerConstants.SCALE_COOLDOWN)
                    continue

                # Cap at the configured inference pool size (was a hard '4', which
                # ignored inference_pool_size: 8 and MAX_WORKERS: 8, so the
                # pool could never scale past 4 even under heavy load).
                pool_cap = int(self.config.get("performance", {}).get("inference_pool_size", FeedManagerConstants.MAX_WORKERS))
                if current_size >= pool_cap:
                    self.logger.warning(f"[ScalingMonitor] Worker cap ({pool_cap}) reached. Skipping scale-up.")
                    await asyncio.sleep(FeedManagerConstants.SCALE_COOLDOWN)
                    continue

                self.logger.debug(
                    f"[ScalingMonitor] avg_depth={avg_depth:.1f}, current_workers={current_size}, "
                    f"up_threshold={up_threshold:.0f}, down_threshold={down_threshold:.0f}"
                )

                if avg_depth > up_threshold and current_size < FeedManagerConstants.MAX_WORKERS:
                    now = time.time()
                    if now - self._last_scale_time >= FeedManagerConstants.SCALE_COOLDOWN:
                        # Memory-based guard: do not scale up if memory usage is already high
                        cpu, mem = check_system_resources()
                        mem_limit = self.config.get("performance", {}).get("memory_limit_percent", 80)
                        if mem < mem_limit:
                            logger.info(f"High load detected (avg depth {avg_depth:.1f}). Scaling up...")
                            self.pool_manager.scale_pool(current_size + 1)
                            self._last_scale_time = now
                        else:
                            logger.warning(f"High load detected but memory limit reached ({mem:.1f}% >= {mem_limit}%). Skipping scale-up.")
                elif avg_depth < down_threshold and current_size > FeedManagerConstants.MIN_WORKERS:
                    now = time.time()
                    # Don't scale down during early startup - wait at least 60s
                    # for the system to reach steady state
                    if now - getattr(self, '_startup_time', now) < 60.0:
                        await asyncio.sleep(FeedManagerConstants.SCALE_COOLDOWN)
                        continue
                    if now - self._last_scale_time >= FeedManagerConstants.SCALE_COOLDOWN:
                        logger.info(f"Low load detected (avg depth {avg_depth:.1f}). Scaling down...")
                        self.pool_manager.scale_pool(current_size - 1)
                        self._last_scale_time = now

                await asyncio.sleep(FeedManagerConstants.SCALE_COOLDOWN)
            except Exception as e:
                logger.error(f"Error in scaling monitor: {e}")
                await asyncio.sleep(5.0)

    async def _update_pipeline_pressure(self):
        """Updates the global pressure signal based on inference input queue depths."""
        while not self._stop_reader_flag:
            try:
                total_depth = sum(
                    q.qsize() for q in self._inference_input_queues if hasattr(q, 'qsize')
                )
                # Per-WORKER backlog, not per-slot. Dividing by slot_count (was 16)
                # diluted the signal ~8x (3 feeds over 16 slots -> avg ~0.2, never
                # tripping SCALE_UP_THRESHOLD). Dividing by live worker count makes
                # the metric rise as soon as any worker's feeds back up, so scale-up
                # actually fires under real load.
                current_size = self.pool_manager.pool_size
                avg_depth = total_depth / max(1, current_size)
                
                # Normalize pressure relative to a reasonable backlog (e.g., 10 frames per slot)
                # This provides a 0.0 to 1.0 signal to ingestion workers
                max_backlog_threshold = 10.0 
                pressure = min(1.0, avg_depth / max_backlog_threshold)
                
                self.pipeline_pressure.value = pressure
                
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Error updating pipeline pressure: {e}")
                await asyncio.sleep(1.0)

    async def _read_db_queue(self):
        """Task to process database write requests from all workers."""
        logger.info("Database queue reader task started.")

        while not self._stop_reader_flag:
            try:
                items = []
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
                    items_needing_reid = []
                    loop = asyncio.get_running_loop()

                    for item in items:
                        msg_type = item.get("type", "vehicle_data")
                        if msg_type == "vehicle_data":
                            if item.get("embedding"):
                                items_needing_reid.append(item)
                            else:
                                global_id = self._reid_manager.get_global_id(
                                    item.get("feed_id", "unknown"),
                                    item.get("vehicle_id", "unknown"),
                                )
                                if global_id:
                                    item["global_vehicle_id"] = global_id
                            tracking_batch.append(item)
                        elif msg_type == "identified_vehicle":
                            identified_batch.append(item)

                    if items_needing_reid:
                        def bulk_reid_process(reid_items):
                            for itm in reid_items:
                                try:
                                    emb_np = np.array(itm["embedding"], dtype=np.float32)
                                    itm["global_vehicle_id"] = self._reid_manager.match_or_register(
                                        feed_id=itm.get("feed_id", "unknown"),
                                        local_id=itm.get("vehicle_id", "unknown"),
                                        embedding=emb_np,
                                        metadata={"class_name": itm.get("class_name")},
                                    )
                                except Exception as e:
                                    logger.error(f"Re-ID bulk match error: {e}")

                        await loop.run_in_executor(None, bulk_reid_process, items_needing_reid)

                    if tracking_batch:
                        await asyncio.to_thread(db.save_vehicle_data_batch, tracking_batch)

                    for iv in identified_batch:
                        await asyncio.to_thread(db.upsert_identified_vehicle, iv)
                else:
                    # DB manager not ready — re-queue items to maintain order
                    for item in items:
                        try:
                            self._db_queue.put(item)
                        except Exception:
                            pass
                    await asyncio.sleep(1.0)

                # Brief yield; sleep longer when batch was undersized
                await asyncio.sleep(0.001 if len(items) >= 5000 else 0.005)

            except Exception as e:
                logger.error(f"Error in db_queue reader: {e}", exc_info=True)
                await asyncio.sleep(1.0)

    def initialize_shared_values(self):
        if self._global_fps is None:
            self._global_fps = RedisValue("i", self.config.get("fps", 30), "global_fps")
            logger.info("FeedManager shared values initialized via Redis.")

    async def get_all_statuses(self) -> List[FeedStatusData]:
        statuses = []
        async with self._lock:
            for fid, entry in self.registry.process_registry.items():
                statuses.append(self._entry_to_status_data(fid, entry))
        return statuses

    def _entry_to_status_data(self, feed_id: str, entry: Dict) -> FeedStatusData:
        op_status = entry["status"]
        config = entry.get("config_info") or FeedConfigInfo(
            name="Unknown", source_type="unknown", source_identifier=entry["source"]
        )
        return FeedStatusData(
            feed_id=feed_id,
            config=config,
            source=entry["source"],
            status=op_status,
            current_fps=entry["timer"].get_fps("loop_total") if entry.get("timer") else None,
            last_error=entry.get("error_message"),
            latest_metrics=entry.get("latest_metrics"),
        )

    def _any_real_feeds_active_unsafe(self) -> bool:
        for entry in self.registry.process_registry.values():
            if not entry.get("is_sample_feed", False) and entry["status"] in [
                FeedOperationalStatusEnum.RUNNING,
                FeedOperationalStatusEnum.STARTING,
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

            if real_active:
                for fid in self._sample_feed_ids:
                    status = self.registry.process_registry.get(fid, {}).get("status")
                    if status in [FeedOperationalStatusEnum.RUNNING, FeedOperationalStatusEnum.STARTING]:
                        to_stop.append(fid)
            else:
                active_count = sum(
                    1 for entry in self.registry.process_registry.values()
                    if entry["status"] in [
                        FeedOperationalStatusEnum.RUNNING,
                        FeedOperationalStatusEnum.STARTING,
                    ]
                )
                max_feeds = self.config.get("feed_manager", {}).get("max_concurrent_feeds", 10)

                for fid in self._sample_feed_ids:
                    status = self.registry.process_registry.get(fid, {}).get("status")
                    if (
                        active_count < max_feeds
                        and status in [FeedOperationalStatusEnum.STOPPED, FeedOperationalStatusEnum.ERROR]
                    ):
                        to_start.append(fid)
                        active_count += 1

        for fid in to_stop:
            try:
                await self._stop_feed_internal(fid)
            except Exception:
                pass

        if self._is_processing_active:
            for fid in to_start:
                try:
                    await self._start_feed_internal(fid)
                except Exception:
                    pass

    async def _wait_for_workers_ready(self, expected_count: int, timeout: float = 120.0):
        """Wait until all expected inference workers have signaled readiness in Redis."""
        if expected_count <= 0:
            return

        self.logger.info(f"Waiting for {expected_count} inference workers to signal readiness (timeout={timeout}s)...")
        start_time = time.time()
        ready_count = 0
        try:
            rc = get_redis_client()
            while time.time() - start_time < timeout:
                # Clean up stale worker PIDs that are no longer alive
                ready_workers = rc.smembers("workers:ready_set")
                if ready_workers:
                    for wid_raw in ready_workers:
                        # Convert bytes to string/int safely
                        try:
                            if isinstance(wid_raw, bytes):
                                wid_str = wid_raw.decode('utf-8')
                            else:
                                wid_str = str(wid_raw)
                            wid = int(wid_str)
                        except (ValueError, UnicodeDecodeError) as e:
                            self.logger.warning(f"Invalid worker ID in ready_set: {wid_raw} - {e}")
                            rc.srem("workers:ready_set", wid_raw)
                            continue
                            
                        pid_key = f"worker:{wid}:pid"
                        pid = rc.get(pid_key)
                        if pid:
                            try:
                                import os
                                os.kill(int(pid), 0)  # Check if process exists
                            except (OSError, ValueError):
                                # Process doesn't exist, remove from ready set
                                rc.srem("workers:ready_set", wid_raw)
                                self.logger.debug(f"Removed stale worker {wid} (PID {pid}) from ready set")
                    
                    # Re-fetch after cleanup
                    ready_workers = rc.smembers("workers:ready_set")
                
                ready_count = len(ready_workers) if ready_workers else 0
                
                if ready_count >= expected_count:
                    self.logger.info(f"All {expected_count} workers are ready (found {ready_count}). Proceeding to start feeds.")
                    return
                
                self.logger.debug(f"Workers ready: {ready_count}/{expected_count}. Waiting...")
                await asyncio.sleep(1.0)
        except Exception as e:
            self.logger.warning(f"Error while waiting for worker readiness: {e}", exc_info=True)

        self.logger.warning(f"Timed out waiting for workers. Only {ready_count}/{expected_count} ready. Starting feeds anyway.")
        
        # Warn if we have less than 50% of expected workers
        if ready_count < expected_count * 0.5:
            self.logger.error(
                f"[CRITICAL] Severe worker shortage: {ready_count}/{expected_count}. "
                f"Expected inference throughput will be severely degraded. "
                f"Check: 1) GPU memory, 2) Model paths, 3) Worker logs for errors."
            )

    async def start_processing(self):
        """Starts the overall video processing and prediction scheduling."""
        if self._is_processing_active:
            return

        self.logger.info("Starting overall video processing.")
        await self.initialize()
        self._is_processing_active = True

        self._startup_ready.clear() # Pause scaling monitor during startup

        # Clear stale stop signals so workers don't exit immediately on startup
        try:
            rc = get_redis_client()
            rc.delete("signal:pipeline_stop")
            self.logger.info("Cleared stale pipeline stop signal from Redis.")
        except Exception as e:
            self.logger.warning(f"Could not clear stop signal: {e}")

        # Also clear the Redis-backed inference stop event (key: event:inference_stop)
        # Without this, workers from a prior run see the stale event as "set" and exit
        # before loading models.
        try:
            self._inference_stop_event.clear()
            rc = get_redis_client()
            rc.delete("workers:ready_set")
            self.logger.info("Cleared stale inference stop event and ready set from Redis.")
        except Exception as e:
            self.logger.warning(f"Could not clear inference stop event/ready set: {e}")

        # Purge stale pending messages from central_output stream
        try:
            stale_key = self._central_output_queue.key
            if (
                hasattr(self._central_output_queue, 'redis')
                and self._central_output_queue.redis.exists(stale_key)
            ):
                pending_info = self._central_output_queue.redis.xpending(
                    stale_key, self._central_output_queue.group_name
                )
                pending_count = (
                    pending_info.get("pending", 0)
                    if isinstance(pending_info, dict)
                    else (pending_info[0] if pending_info else 0)
                )
                if pending_count > 0:
                    self.logger.info(
                        f"Purging {pending_count} stale pending messages from {stale_key}"
                    )
                    try:
                        redis_ver = (
                            self._central_output_queue.redis
                            .info('server')
                            .get('redis_version', '0.0.0')
                        )
                        ver_parts = tuple(int(x) for x in redis_ver.split('.')[:2])
                        supports_autoclaim = ver_parts >= (6, 2)
                    except Exception:
                        supports_autoclaim = False

                    if supports_autoclaim:
                        try:
                            claimed = self._central_output_queue.redis.xautoclaim(
                                stale_key,
                                self._central_output_queue.group_name,
                                self._central_output_queue.consumer_id,
                                min_idle_time=0,
                                start_id="0-0",
                                count=100,
                            )
                            if claimed and len(claimed) > 1 and claimed[1]:
                                for msg_id, _ in claimed[1]:
                                    self._central_output_queue.redis.xack(
                                        stale_key,
                                        self._central_output_queue.group_name,
                                        msg_id,
                                    )
                                self.logger.info(
                                    f"ACKed {len(claimed[1])} stale messages via xautoclaim"
                                )
                        except Exception as claim_err:
                            self.logger.warning(f"xautoclaim failed: {claim_err}")
                            supports_autoclaim = False

                    if not supports_autoclaim:
                        try:
                            msgs = self._central_output_queue.redis.xreadgroup(
                                self._central_output_queue.group_name,
                                self._central_output_queue.consumer_id,
                                {stale_key: "0"},
                                count=200,
                            )
                            if msgs and msgs[0][1]:
                                for msg_id, _ in msgs[0][1]:
                                    self._central_output_queue.redis.xack(
                                        stale_key,
                                        self._central_output_queue.group_name,
                                        msg_id,
                                    )
                                self.logger.info(
                                    f"ACKed {len(msgs[0][1])} stale messages via xreadgroup (Redis < 6.2)"
                                )
                        except Exception as fb_err:
                            self.logger.warning(f"Fallback purge failed: {fb_err}")
        except Exception as e:
            self.logger.warning(f"Could not purge stale central_output messages: {e}")
        pool_size = self._resolve_pool_size()
        self.scale_pool(pool_size)

        # Wait for workers to finish loading models before starting feeds
        await self._wait_for_workers_ready(pool_size)

        await self._check_and_manage_sample_feed()
        self._startup_ready.set() # Allow scaling monitor to resume

        if self._prediction_scheduler:
            if self.config.get("prediction_scheduler", {}).get("enabled", True):
                await self._prediction_scheduler.start()
                self.logger.info("Prediction scheduler started.")
            else:
                self.logger.debug("PredictionScheduler disabled in config.")

    async def stop_processing(self):
        """Stops the overall video processing and prediction scheduling.

        Warm-pool-safe: stands the pipeline DOWN (sample feed, scheduler, and
        the background monitor/watchdog/result-reader tasks) but keeps the
        inference worker pool HOT so a subsequent start_processing() can resume
        without reloading models. Previously stop_processing() only stopped the
        sample feed + scheduler, leaving the scaling monitor, watchdog, and
        result-reader tasks running against a dead feed set (they only exit on
        _is_shutting_down, which only shutdown() sets). Every stop/start cycle
        therefore leaked zombie tasks and kept firing scale_pool on a stopped
        pipeline. The monitor already stands down via _is_processing_active
        (set False here); we additionally cancel the tasks so stop is a true
        pause counterpart to start.
        """
        if not self._is_processing_active:
            return

        self.logger.info("Stopping overall video processing.")
        self._is_processing_active = False

        # Cancel background tasks (monitor/watchdog/reader). These are the
        # SAME tasks shutdown() cancels -- we just do it without tearing down
        # the worker pool.
        for task_name in ("_scaling_task", "_watchdog_task", "_result_reader_task"):
            task = getattr(self, task_name, None)
            if task is not None and not task.done():
                task.cancel()
                self.logger.info(f"Cancelled background task {task_name}.")

        await self._check_and_manage_sample_feed()

        if self._prediction_scheduler:
            await self._prediction_scheduler.stop()
            self.logger.info("Prediction scheduler stopped.")

    async def _stop_inference_pool(self):
        logger.info("Stopping Inference Pool...")
        self._inference_stop_event.set()

        for _ in range(50):
            try:
                self._central_output_queue.get_nowait()
            except Exception:
                pass

            if all(not p.is_alive() for p in self.pool_manager._inference_pool.values()):
                break
            await asyncio.sleep(0.1)

        for p in self.pool_manager._inference_pool.values():
            if p.is_alive():
                logger.warning(f"Forcing termination of Inference Worker {p.name}")
                p.terminate()
                await asyncio.sleep(0.1)
                if p.is_alive():
                    p.kill()

        self.pool_manager._inference_pool = {}
        self.pool_manager._inference_command_queues = {}

    def _initialize_available_feeds(self):
        self._load_persisted_feeds()

    def _generate_feed_id(self, source: str, name_hint: Optional[str] = None) -> str:
        return self.registry.generate_feed_id(source, name_hint)

    def _check_resources(self):
        limit = self.config.get("performance", {}).get("memory_limit_percent", 80)
        cpu, mem = check_system_resources()
        if mem >= limit:
            logger.warning(f"Resource limit reached: Memory {mem:.1f}% >= Limit {limit}%.")
            raise ResourceLimitError(f"Memory usage ({mem:.1f}%) exceeds limit.")

    async def _broadcast(self, message_type: WebSocketMessageTypeEnum, data: Dict):
        if self.broadcaster:
            await self.broadcaster.broadcast(message_type, data)

    # --- Persistence ---

    def _save_persisted_feeds(self):
        """Saves current feeds configuration to disk."""
        self.registry.save_persistence()

    def _load_persisted_feeds(self):
        """Loads feeds configuration from disk."""
        loaded_ids = self.registry.load_persistence()
        
        for feed_id in loaded_ids:
            entry = self.registry.get_entry(feed_id)
            if entry and entry.get("is_sample_feed") and feed_id not in self._sample_feed_ids:
                self._sample_feed_ids.append(feed_id)
        
        return loaded_ids

    async def remove_feed(self, feed_id: str) -> bool:
        """Removes a feed from the registry and persistence."""
        async with self._lock:
            if not self.registry.get_entry(feed_id):
                return False

            try:
                resources = self._detach_resources(feed_id)
                if resources:
                    await self._terminate_resources(resources)
            except Exception as e:
                logger.error(f"Error stopping feed {feed_id} during removal: {e}")

            # Cleanup locks and tasks
            self._feed_locks.pop(feed_id, None)

            self.registry.remove_entry(feed_id)
            self._save_persisted_feeds()

        return True

    # --- Feed Management ---

    async def update_feed_config(self, feed_id: str, updates: Dict[str, Any]):
        """Updates the configuration for a running or stopped feed."""
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry:
                raise FeedNotFoundError(feed_id)

            current_config = entry.get("config_info")
            if current_config:
                update_data = updates.copy()
                if "roi" in update_data and isinstance(update_data["roi"], list):
                    if not all(
                        isinstance(roi, (list, tuple)) and len(roi) == 4
                        for roi in update_data["roi"]
                    ):
                        raise ValueError("ROI must be a list of [x1, y1, x2, y2] coordinates.")

                entry["config_info"] = current_config.model_copy(update=update_data)
                self._save_persisted_feeds()

                if entry["status"] in [
                    FeedOperationalStatusEnum.RUNNING,
                    FeedOperationalStatusEnum.STARTING,
                ]:
                    await self._send_config_to_workers(feed_id, update_data)

        await self._broadcast_feed_update(feed_id)

    async def _send_config_to_workers(self, feed_id: str, config_data: Dict[str, Any]):
        """Broadcast a config update to all inference workers."""
        cmd = {"type": "config_update", "feed_id": feed_id, "data": config_data}
        sent_count = 0
        for worker_id, q in self.pool_manager._inference_command_queues.items():
            try:
                q.put_nowait(cmd)
                sent_count += 1
            except queue.Full:
                logger.warning(
                    f"Inference command queue {worker_id} full; config update may be delayed."
                )
            except Exception as e:
                logger.error(f"Failed to send config update to worker {worker_id}: {e}")

        logger.info(
            f"Broadcasted config update for feed {feed_id} to {sent_count} inference workers."
        )

    async def add_and_start_feed(
        self,
        source: str,
        latitude: Optional[float],
        longitude: Optional[float],
        name_hint: Optional[str] = None,
        is_looped: bool = True,
        is_sample_feed: bool = False,
        start: bool = True,
    ) -> Dict[str, Any]:
        existing_feed_id = None

        async with self._lock:
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
                    "metrics_history": deque(maxlen=FeedManagerConstants.MAX_METRICS_HISTORY_LENGTH),
                    "timer": FrameTimer(),
                    "is_sample_feed": is_sample_feed,
                    "is_looped_feed": is_looped,
                    "config_info": feed_config,
                    "last_broadcast_time": 0.0,
                }

                if is_sample_feed and feed_id not in self._sample_feed_ids:
                    self._sample_feed_ids.append(feed_id)

                self._save_persisted_feeds()
                target_feed_id = feed_id
            else:
                target_feed_id = existing_feed_id
                logger.info(f"Reusing existing feed {target_feed_id} for source {source}")

                if latitude is not None and longitude is not None:
                    entry = self.process_registry[target_feed_id]
                    if entry.get("config_info"):
                        entry["config_info"].latitude = latitude
                        entry["config_info"].longitude = longitude
                        logger.info(
                            f"Updated coordinates for {target_feed_id} to ({latitude}, {longitude})"
                        )

                if is_sample_feed:
                    self.process_registry[target_feed_id]["is_sample_feed"] = True
                    if target_feed_id not in self._sample_feed_ids:
                        self._sample_feed_ids.append(target_feed_id)

        if not existing_feed_id:
            await self._broadcast_feed_update(target_feed_id)

        if start:
            try:
                # --- Idempotency Check ---
                # If the feed is already active, just update config and return.
                async with self._lock:
                    entry = self.process_registry.get(target_feed_id)
                    if entry and entry["status"] in (FeedOperationalStatusEnum.RUNNING, FeedOperationalStatusEnum.STARTING):
                        logger.info(f"Feed '{target_feed_id}' is already {entry['status'].value}. Skipping restart.")
                        return {
                            "feed_id": target_feed_id,
                            "status": entry["status"].value,
                            "error": entry["error_message"],
                        }

                await self._start_feed_internal(target_feed_id)
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
        else:
            async with self._lock:
                return {
                    "feed_id": target_feed_id,
                    "status": self.process_registry[target_feed_id]["status"].value,
                    "error": self.process_registry[target_feed_id]["error_message"],
                }

    async def start_multiple_feeds(self, feeds: List[Dict]) -> Dict[str, Any]:
        """Start multiple feeds with per-feed isolation."""
        results: Dict[str, list] = {"successful": [], "failed": []}

        for feed_config in feeds:
            try:
                source = feed_config.get("source") or feed_config.get("path")
                if not source:
                    raise ValueError("Missing 'source' or 'path' in feed config")

                kwargs = {
                    "source": source,
                    "latitude": feed_config.get("latitude"),
                    "longitude": feed_config.get("longitude"),
                    "name_hint": feed_config.get("name") or feed_config.get("name_hint"),
                    "is_looped": feed_config.get("is_looped", True),
                }

                feed_result = await self.add_and_start_feed(**kwargs)
                await asyncio.sleep(0.5) # Stagger startups to prevent SHM bursts

                if feed_result.get("status") == "error":
                    results["failed"].append({"config": feed_config, "error": feed_result.get("error")})
                else:
                    results["successful"].append({"feed_id": feed_result.get("feed_id"), "config": feed_config})

            except Exception as e:
                logger.error(f"Failed to start feed {feed_config}: {e}")
                results["failed"].append({"config": feed_config, "error": str(e)})

        return results

    async def start_feed(self, feed_id: str):
        """Public method to start a feed. Acquires per-feed lock for atomicity."""
        async with self._get_feed_lock(feed_id):
            await self._start_feed_internal(feed_id)

    async def _stop_feed_internal(self, feed_id: str, skip_sample_mgmt: bool = False):
        resources_to_cleanup = None
        async with self._lock:
            entry = self.registry.process_registry.get(feed_id)
            if not entry:
                raise FeedNotFoundError(feed_id)

            logger.info(f"Stopping feed: '{feed_id}'")
            resources_to_cleanup = self._detach_resources(feed_id)

        if resources_to_cleanup:
            await self._terminate_resources(resources_to_cleanup)

        await self._broadcast_feed_update(feed_id)
        await self._broadcast_kpi_update()
        if not skip_sample_mgmt:
            await self._check_and_manage_sample_feed()

    async def _start_feed_internal(self, feed_id: str):
        """Internal method to start a feed, handling resource allocation and worker launch."""
        resources_to_cleanup = None
        failed_resources_to_cleanup = None
        is_sample = False
        started_real_feed = False

        async with self._lock:
            entry = self.registry.process_registry.get(feed_id)
            if not entry:
                raise FeedNotFoundError(feed_id)

            if entry["status"] != FeedOperationalStatusEnum.STOPPED:
                logger.warning(
                    f"Feed '{feed_id}' is in state '{entry['status']}'. Cleaning up before start."
                )
                resources_to_cleanup = self._detach_resources(feed_id)

            entry["stop_requested"] = False
            is_sample = entry.get("is_sample_feed", False)
            if not is_sample or self._any_real_feeds_active_unsafe():
                self._check_resources()

            logger.info(f"Starting feed: '{feed_id}'")

            # Auto-initialize inference pool if not running
            if not self.pool_manager._inference_pool:
                pool_size = self._resolve_pool_size()
                logger.warning(
                    f"Inference pool is empty — auto-scaling to {pool_size} worker(s) before starting feed."
                )
                try:
                    rc = get_redis_client()
                    rc.delete("signal:pipeline_stop")
                    self.logger.info("Cleared stale pipeline_stop signal before auto-scaling inference pool.")
                except Exception as e:
                    self.logger.warning(f"Could not clear stop signal: {e}")

                self._inference_stop_event.clear()
                self.scale_pool(pool_size)
                if not self._is_processing_active:
                    self._is_processing_active = True

            entry["command_queue"] = RedisQueue('feed_cmd_' + feed_id, maxsize=FeedManagerConstants.FEED_CMD_QUEUE_MAXSIZE)

            video_output_config = self.config.get("video_output", {})
            if video_output_config.get("enabled", False):
                entry["video_writer_queue"] = RedisQueue(
                    'feed_video_' + feed_id,
                    maxsize=self.config.get("video_input", {}).get("max_queue_size", 500),
                )
            else:
                entry["video_writer_queue"] = None

            entry["stop_event"] = RedisEvent('feed_stop_' + feed_id)
            entry["reduce_fps_event"] = RedisEvent('feed_fps_reduce_' + feed_id)
            entry["status"] = FeedOperationalStatusEnum.STARTING
            entry["start_time"] = time.time()
            entry["last_frame_time"] = time.time()
            entry["error_message"] = None
            entry["latest_metrics"] = None
            entry["metrics_history"] = deque(maxlen=FeedManagerConstants.MAX_METRICS_HISTORY_LENGTH)
            entry["timer"] = FrameTimer()

            try:
                self._launch_worker(feed_id, entry["source"])
                if not is_sample:
                    started_real_feed = True
            except Exception as e:
                logger.error(f"Failed to launch worker for '{feed_id}': {e}", exc_info=True)
                failed_resources_to_cleanup = self._detach_resources(feed_id)
                entry["status"] = FeedOperationalStatusEnum.ERROR
                entry["error_message"] = str(e)

            # Start Video Writer if enabled and no launch error
            if not failed_resources_to_cleanup and video_output_config.get("enabled", False):
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
                await self._send_config_to_workers(
                    feed_id, entry["config_info"].model_dump(exclude_unset=True)
                )

        await self._broadcast_feed_update(feed_id)
        await self._broadcast_kpi_update()
        if started_real_feed:
            await self._check_and_manage_sample_feed()

    async def stop_feed(self, feed_id: str):
        """Public method to stop a feed. Acquires per-feed lock for atomicity."""
        async with self._get_feed_lock(feed_id):
            async with self._lock:
                entry = self.process_registry.get(feed_id)
                is_sample = entry.get("is_sample_feed", False) if entry else False
            await self._stop_feed_internal(feed_id, skip_sample_mgmt=is_sample)
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
        if not is_sample:
            await self._check_and_manage_sample_feed()

    async def restart_feed(self, feed_id: str):
        logger.info(f"Restart requested for: '{feed_id}'")
        async with self._get_feed_lock(feed_id):
            try:
                async with self._lock:
                    entry = self.process_registry.get(feed_id)
                    if entry:
                        entry["status"] = FeedOperationalStatusEnum.RESTARTING

                await self._stop_feed_internal(feed_id, skip_sample_mgmt=True)
                await asyncio.sleep(2.0)  # Let the OS reclaim ports, file handles, memory
                await self._start_feed_internal(feed_id)
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
        """Stop all active feeds without triggering auto-restart of sample feeds."""
        logger.info("Stopping all active feeds.")
        async with self._lock:
            feeds_to_stop = [
                fid for fid, entry in self.process_registry.items()
                if entry["status"] in [
                    FeedOperationalStatusEnum.RUNNING,
                    FeedOperationalStatusEnum.STARTING,
                    FeedOperationalStatusEnum.ERROR,
                ]
            ]

            if feeds_to_stop:
                # Stop all feeds without triggering _check_and_manage_sample_feed after each one
                for fid in feeds_to_stop:
                    try:
                        await self._stop_feed_internal(fid, skip_sample_mgmt=True)
                        # Broadcast update for each feed so frontend sees the state change
                        await self._broadcast_feed_update(fid)
                    except Exception as e:
                        logger.error(f"Error stopping feed {fid}: {e}")

        await self._broadcast_kpi_update()

    async def start_all_feeds(self):
        """Start all feeds currently in stopped or error state. Mirrors stop_all_feeds."""
        logger.info("Starting all stopped/error feeds.")
        async with self._lock:
            feeds_to_start = [
                fid for fid, entry in self.process_registry.items()
                if entry["status"] in [
                    FeedOperationalStatusEnum.STOPPED,
                    FeedOperationalStatusEnum.ERROR,
                ]
            ]

            if feeds_to_start:
                for fid in feeds_to_start:
                    try:
                        await self._start_feed_internal(fid)
                        # Broadcast update for each feed so frontend sees the state change
                        await self._broadcast_feed_update(fid)
                    except Exception as e:
                        logger.error(f"Error starting feed {fid}: {e}")

        await self._broadcast_kpi_update()

    async def request_snapshot(self, feed_id: str, incident_id: str):
        """Sends a command to the worker to save a high-res snapshot."""
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry or not entry.get("command_queue"):
                logger.warning(
                    f"Cannot request snapshot for {feed_id}: feed not running or no command queue."
                )
                return

            try:
                entry["command_queue"].put_nowait({"type": "save_snapshot", "incident_id": incident_id})
                logger.info(f"Requested snapshot for feed {feed_id}, incident {incident_id}")
            except Exception as e:
                logger.error(f"Failed to put snapshot command for {feed_id}: {e}")

    # --- Internal Process & Resource Management ---

    def _launch_worker(self, feed_id: str, source: str):
        """Spawn the ingestion process for a feed."""
        entry = self.process_registry.get(feed_id)
        if not entry:
            return

        # Slot routing for batched inference.
        #
        # Workers batch frames pulled from the slots they own. A worker owns
        # slot `s` when `s % pool_size == worker_id` (see InferencePoolManager.
        # scale_pool / _spawn_worker). A naive hash routes each feed to a
        # distinct slot, so a worker almost always sees exactly one feed per
        # slot and the configured batch_size (8) degenerates to a batch of 1.
        # That keeps the GPU at <50% util while the CPU pre/post work dominates.
        #
        # To let batches actually fill, we co-locate feeds onto the SAME
        # worker's slot set: feed N is placed on worker (N % pool_size), and
        # successive feeds on that worker are spread across that worker's
        # distinct slot indices. Consecutive feeds therefore share a worker
        # and get batched together -> real GPU utilisation.
        with self._route_lock:
            configured_pool = self._resolve_pool_size()
            pool_size = max(1, self.pool_manager.pool_size or configured_pool or 1)
            wid = self._feed_launch_seq % pool_size
            sub = self._per_worker_feed_count.get(wid, 0)
            # Worker `wid` owns slots { wid, wid + pool_size, wid + 2*pool_size, ... }.
            slot_id = (wid + sub * pool_size) % self.slot_count
            self._per_worker_feed_count[wid] = sub + 1
            self._feed_launch_seq += 1

        target_queue = self._inference_input_queues[slot_id]
        logger.info(
            f"Routing feed {feed_id} to slot {slot_id} (worker {wid}, pool_size {pool_size})"
        )

        worker_args = (
            source,
            feed_id,
            target_queue,
            None,   # stop_event (legacy parameter) — worker checks Redis keys
            self.config,
            entry.get("is_looped_feed", False),
            None,   # command_queue — handled via Redis
            None,   # frame_buffer — worker initialises its own handle
            None,   # pipeline_pressure — worker reads from Redis
            entry.get("video_writer_queue").name
                if entry.get("video_writer_queue") is not None
                else None,
            # New: per-feed Redis stop-key name. The parent already creates
            # entry["stop_event"] = RedisEvent('feed_stop_' + feed_id) at
            # start time (line ~1274). Before this plumbing, _terminate_resources
            # would call stop_event.set() but the worker never read the key
            # -- it was checking the GLOBAL signal:pipeline_stop only -- so
            # graceful stop was a no-op and every per-feed termination fell
            # through to SIGTERM after a full 1.0s join wait. Pass the key
            # name (not the RedisEvent handle, which isn't picklable across
            # multiprocessing.Process) and let the worker check it via
            # Redis EXISTS inside should_stop(), throttled to ~625ms cadence.
            (lambda ev: getattr(ev, "name", None))(entry.get("stop_event")),
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

        resources = {
            "feed_id": feed_id,
            "process": entry.get("process"),
            "stop_event": entry.get("stop_event"),
            "video_writer_queue": entry.get("video_writer_queue"),
            "video_writer": self.video_writers.pop(feed_id, None),
        }

        entry.update({
            "status": FeedOperationalStatusEnum.STOPPED,
            "error_message": None,
            "process": None,
            "stop_event": None,
            "video_writer_queue": None,
            "timer": None,
        })

        if feed_id in self._feed_running_events:
            self._feed_running_events[feed_id].clear()

        return resources

    async def _terminate_resources(self, resources: Dict[str, Any]):
        """Robust cleanup sequence to prevent zombie processes and deadlocks."""
        feed_id = resources.get("feed_id", "unknown")
        process = resources.get("process")
        stop_event = resources.get("stop_event")
        writer_queue = resources.get("video_writer_queue")
        video_writer = resources.get("video_writer")

        # 1. Signal stop
        if stop_event:
            stop_event.set()

        # 2. Stop video writer
        if video_writer:
            try:
                await asyncio.to_thread(video_writer.stop)
            except Exception as e:
                logger.error(f"Error stopping video writer for {feed_id}: {e}")

        # 3. Join or kill the process
        if process and process.is_alive():
            try:
                loop = asyncio.get_running_loop()
                # Dropped join timeout from 1.0s -> 0.2s. Once the per-feed
                # stop_event.set() above propagates to Redis (RedisEvent.set()
                # is a synchronous SET), the ingestion worker's should_stop()
                # exits its frame loop within ~150ms (observed in production
                # logs: child sent end-of-stream 136ms after SIGTERM, with
                # the Redis-key path being even faster). A 1.0s wait was
                # 5-7x longer than necessary per feed, accumulating to ~24s
                # of pure sleep across the 24-feed inference pool at every
                # shutdown. If the worker is genuinely stuck (deadlocked in
                # cv2 / frame_buffer), 200ms is still plenty for the SIGTERM
                # escalation path below to do its job.
                await loop.run_in_executor(None, process.join, 0.2)

                if process.is_alive():
                    # The per-feed stop_event was set but the worker didn't
                    # break its loop within 200ms. Most likely causes:
                    # blocked in cv2.VideoCapture.read() / SHM read / frame
                    # encode. Escalate to SIGTERM -- the signal handler is
                    # installed and will set the local flag immediately.
                    logger.warning(
                        f"Process {process.pid} for {feed_id} did not exit within "
                        f"200ms after stop_event.set(); escalating to SIGTERM."
                    )
                    process.terminate()
                    await asyncio.sleep(0.2)

                    if process.is_alive():
                        # SIGTERM didn't work either. Force-kill.
                        logger.warning(
                            f"Process {process.pid} for {feed_id} ignored SIGTERM. "
                            f"Force-killing."
                        )
                        process.kill()
            except Exception as e:
                logger.error(f"Error joining process for {feed_id}: {e}")

        # 4. Close queues
        if writer_queue:
            try:
                writer_queue.close()
                writer_queue.cancel_join_thread()
            except Exception:
                pass

    # --- Background Reader ---
    # Result processing is now handled by the ResultProcessor service.

    async def _handle_periodic_tasks(self):
        now = time.time()
        if now - self._last_kpi_broadcast_time >= self._kpi_broadcast_interval:
            await self._broadcast_kpi_update()
            self._last_kpi_broadcast_time = now

        if now - self._last_queue_log_time >= 30.0:
            self._last_queue_log_time = now
            try:
                self._check_resources()
                if self._has_active_feeds():
                    # Reclaim segments that are genuinely stale (abandoned by a
                    # crashed writer) but NOT ones still in flight. The timeout
                    # now comes from config (performance.shm_stale_timeout) so
                    # it always exceeds worst-case reader lag; reclaiming an
                    # in-flight segment would let ingestion recycle it under
                    # the result reader and produce feed_hash mismatches (the
                    # old ~14% drop + shutdown SHM-leak noise).
                    stale_timeout = float(
                        self.config.get("performance", {}).get("shm_stale_timeout", 60.0)
                    )
                    self.frame_buffer.prune_stale_segments(
                        timeout_seconds=stale_timeout, odd_timeout=stale_timeout / 6.0
                    )
                    
                    # Log SHM stats to detect throughput issues early
                    stats = self.frame_buffer.get_stats()
                    free_pct = (stats['free_pool_size'] / stats['pool_size'] * 100) if stats['pool_size'] > 0 else 0
                    drop_rate = stats['drop_count'] / max(1, stats['acquired_count'])
                    in_flight = stats.get('in_flight', 0)
                    orphan = stats.get('orphan_count', max(0, stats['acquired_count'] - stats['release_count'] - in_flight))
                    self.logger.info(
                        f"[SHM-STATS] free={stats['free_pool_size']}/{stats['pool_size']} ({free_pct:.1f}%), "
                        f"acq={stats['acquired_count']}, rel={stats['release_count']}, "
                        f"in_flight={in_flight}, orphan={orphan}, "
                        f"drops={stats['drop_count']} ({drop_rate:.1%})"
                    )

                    # Apply backpressure if pool is running low
                    if free_pct < 20:
                        self.logger.warning(f"[SHM-PRESSURE] Pool at {free_pct:.1f}% free - inference cannot keep up with ingestion")
                    elif orphan > stats['pool_size'] * 0.1:
                        self.logger.warning(
                            f"[SHM-PRESSURE] Orphan segment count {orphan} > 10% of pool. SHM recycling is leaking; "
                            f"check that release() is being called for every acquire()."
                        )
            except ResourceLimitError as e:
                logger.error(f"Resource limit exceeded during operation: {e}")

    def _has_active_feeds(self) -> bool:
        """Cheap check: is any feed currently in a running-lifecycle state?"""
        try:
            target = FeedOperationalStatusEnum.RUNNING
            for entry in self.process_registry.values():
                if entry.get("status") == target:
                    return True
            return False
        except Exception:
            # If registry isn't accessible for any reason, fall back to
            # the safer "yes, prune" so we don't silently leak segments.
            return True

    async def _compute_vehicle_deltas(
        self, feed_id: str, vehicles: List[Dict], frame_idx: int
    ) -> List[Dict]:
        """
        Computes delta updates for vehicles to reduce bandwidth.
        - KEYFRAME (every 30 frames): Send full data.
        - DELTA: Send only changed fields + mandatory (id, bbox, velocity).
        """
        KEYFRAME_INTERVAL = 30
        is_keyframe = (frame_idx % KEYFRAME_INTERVAL == 0)

        async with self._get_feed_lock(feed_id):
            entry = self.process_registry.get(feed_id)
            if not entry:
                return vehicles

            if "vehicle_states" not in entry:
                entry["vehicle_states"] = {}

            last_states = entry["vehicle_states"]
            current_ids: set = set()
            delta_vehicles = []

            static_fields = [
                "class_name", "class_id", "car_model",
                "license_plate", "color", "behavior",
                "car_model_confidence", "gallery_size",
            ]

            for v in vehicles:
                vid = v["vehicle_id"]
                current_ids.add(vid)

                if is_keyframe or vid not in last_states:
                    delta_vehicles.append(v)
                    last_states[vid] = v.copy()
                    continue

                last_v = last_states[vid]
                delta: Dict[str, Any] = {
                    "vehicle_id": vid,
                    "bbox": v["bbox"],
                    "vx": v.get("vx", 0),
                    "vy": v.get("vy", 0),
                }

                for field in static_fields:
                    val = v.get(field)
                    if val != last_v.get(field):
                        delta[field] = val

                if v.get("status") != last_v.get("status"):
                    delta["status"] = v.get("status")
                if v.get("is_occluded") != last_v.get("is_occluded"):
                    delta["is_occluded"] = v.get("is_occluded")

                delta_vehicles.append(delta)
                last_states[vid] = v.copy()

            # Remove stale vehicle states
            for missing_id in list(last_states.keys()):
                if missing_id not in current_ids:
                    del last_states[missing_id]

            return delta_vehicles

    # --- Broadcast Helpers ---

    async def _broadcast_feed_update(self, feed_id: str):
        if not self.broadcaster:
            return
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry:
                return
            status_data = self._entry_to_status_data(feed_id, entry)

        await self.broadcaster.broadcast_feed_update(status_data)

    async def trigger_kpi_push(self):
        """Public method to force a KPI update broadcast to all subscribed clients."""
        await self._broadcast_kpi_update()

    async def _broadcast_kpi_update(self):
        if not self.broadcaster:
            return

        total_vehicles_active = 0
        total_vehicles_cumulative = 0
        total_speed_sum = 0.0
        total_speed_count = 0
        total_congestion_sum = 0.0
        active_feeds_count = 0

        async with self._lock:
            for feed_id, entry in self.process_registry.items():
                status = entry.get("status")
                metrics = entry.get("latest_metrics")

                if status in [
                    FeedOperationalStatusEnum.RUNNING,
                    FeedOperationalStatusEnum.STARTING,
                ] and metrics:
                    v_active = metrics.get("total_vehicles", 0)
                    total_vehicles_active += v_active
                    total_vehicles_cumulative += metrics.get("total_vehicles_cumulative", v_active)

                    avg_speed = metrics.get("session_average_speed_kmh") or metrics.get(
                        "average_speed_kmh", 0.0
                    )
                    if avg_speed > 0:
                        total_speed_sum += avg_speed
                        total_speed_count += 1

                    congestion = metrics.get("session_average_congestion_score") or metrics.get(
                        "congestion_score", 0.0
                    )
                    total_congestion_sum += congestion
                    active_feeds_count += 1

        if active_feeds_count == 0:
            logger.debug(f"[BROADCAST_KPI] No active feeds (active={active_feeds_count}, total={len(self.process_registry)}). Broadcasting zeros.")
            
            kpi_data = GlobalRealtimeMetrics(
                timestamp=datetime.now(timezone.utc).isoformat(),
                metrics_source="aggregated_feeds",
                total_flow=0,
                average_speed_kmh=0.0,
                congestion_index=0.0,
                active_incidents_count=0,
                feed_statuses={"active": 0, "total": len(self.process_registry)},
                custom_metrics={"active_vehicles": 0},
            )
            await self.broadcaster.broadcast_kpi_update(kpi_data)
            return

        global_avg_speed = (total_speed_sum / total_speed_count) if total_speed_count > 0 else 0.0
        global_congestion_index = total_congestion_sum / active_feeds_count

        # Global distinct vehicle count (audit finding #2): summing per-feed
        # `total_vehicles_cumulative` double-counts any vehicle seen in more
        # than one feed (it owns one global_vehicle_id but is added to each
        # feed's tally). The ReID manager's gallery is the authoritative
        # system-wide unique-vehicle registry -- a vehicle seen across feeds
        # keeps a single global_id -- so its size is the true distinct count.
        # It is bounded by TTL + max_gallery_size, i.e. distinct vehicles within
        # the retention window (not all-time). Falls back to the per-feed
        # cumulative sum when the ReID manager has no entries (reid disabled).
        reid_mgr = getattr(self, "_reid_manager", None)
        global_distinct = reid_mgr.distinct_vehicle_count() if reid_mgr else 0
        total_flow = global_distinct if global_distinct > 0 else int(total_vehicles_cumulative)

        kpi_data = GlobalRealtimeMetrics(
            timestamp=datetime.now(timezone.utc).isoformat(),
            metrics_source="aggregated_feeds",
            total_flow=total_flow,
            average_speed_kmh=round(global_avg_speed, 1),
            congestion_index=round(global_congestion_index, 1),
            active_incidents_count=0,
            feed_statuses={"active": active_feeds_count, "total": len(self.process_registry)},
            custom_metrics={"active_vehicles": total_vehicles_active},
        )

        logger.info(f"[BROADCAST_KPI] Broadcasting KPI: feeds={active_feeds_count}, avg_speed={global_avg_speed:.1f}, congestion={global_congestion_index:.1f}")

        await self.broadcaster.broadcast_kpi_update(kpi_data)

    async def _perform_broadcasts(self, feeds_to_update, kpi_needed, sample_needed):
        for fid in feeds_to_update:
            await self._broadcast_feed_update(fid)

        now = time.time()
        if kpi_needed or (now - self._last_kpi_broadcast_time >= self._kpi_broadcast_interval):
            await self._broadcast_kpi_update()
            self._last_kpi_broadcast_time = now

        if sample_needed:
            await self._check_and_manage_sample_feed()

    # --- Frame Subscriptions (in-process consumers: VideoProcessor for recording) ---

    async def subscribe_to_frames(self, feed_id: str, maxsize: int = 30) -> asyncio.Queue:
        """
        Subscribe to decoded frames for a specific feed.

        Each subscriber receives a private bounded asyncio.Queue. The
        ResultProcessor pumps a copy of every deduped decoded frame into
        each queue. Backpressure: if a subscriber's queue is full, the
        oldest frame is dropped (we never block the main pipeline).

        Returns the queue the caller must read from and later pass to
        unsubscribe_from_frames() to release the slot.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        async with self._frame_subscribers_lock:
            self._frame_subscribers.setdefault(feed_id, []).append(q)
        return q

    async def unsubscribe_from_frames(self, feed_id: str, q: asyncio.Queue) -> None:
        """Release a previously-acquired frame subscriber queue."""
        async with self._frame_subscribers_lock:
            subs = self._frame_subscribers.get(feed_id)
            if subs and q in subs:
                subs.remove(q)
                if not subs:
                    del self._frame_subscribers[feed_id]

    async def deliver_to_subscribers(
        self, feed_id: str, frame_idx: int, frame_bytes: bytes, metrics: Dict, vehicles: list
    ) -> None:
        """
        Push a copy of one decoded frame into every active subscriber's queue.
        Called by ResultProcessor after broadcast. Drops oldest frame on overflow
        so the pipeline never blocks.
        """
        subs = self._frame_subscribers.get(feed_id)
        if not subs:
            return
        payload = {
            "feed_id": feed_id,
            "frame_index": frame_idx,
            "frame": frame_bytes,
            "metrics": metrics,
            "vehicles": vehicles,
        }
        for q in list(subs):  # copy because we may modify
            try:
                if q.full():
                    # Drop the oldest frame to keep the consumer close to real-time.
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Race: queue filled between full() check and put_nowait().
                # Skip this frame for this consumer rather than blocking.
                pass
            except Exception as e:
                self.logger.debug(
                    f"Subscriber delivery failed for {feed_id}: {e}"
                )

    # --- Shutdown ---

    async def shutdown(self):
        logger.info("Shutdown initiated.")
        self._stop_reader_flag = True
        self._is_shutting_down = True

        # Publish the global stop signal so every ingestion worker (which is
        # launched with stop_event=None and only checks this Redis key on
        # shutdown) breaks its produce loop promptly instead of draining the
        # SHM free pool after its consumers have exited. Consumers (inference
        # workers, result processor) honour this key too, so it guarantees a
        # coordinated, fast drain.
        try:
            rc = get_redis_client()
            rc.set("signal:pipeline_stop", "1")
            logger.info("Published global 'signal:pipeline_stop' for connected workers.")
        except Exception as e:
            logger.warning(f"Could not publish pipeline stop signal: {e}")

        # Immediately prevent any new workers from being spawned
        if self.pool_manager:
            self.pool_manager._is_shutting_down = True

        # Stop and cancel watchdog immediately to prevent respawning workers
        if self.watchdog:
            self.watchdog.stop()
        if self._watchdog_task:
            self._watchdog_task.cancel()

        await self.stop_processing()
        await self.stop_all_feeds()
        await self._stop_inference_pool()

        if self._reid_manager:
            await asyncio.to_thread(self._reid_manager.save_state)
            logger.info("ReID state saved during shutdown.")

        tasks = [
            t for t in (
                self._result_reader_task,
                self._watchdog_task,
                self._db_reader_task,
            )
            if t is not None
        ]

        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Shutdown complete.")