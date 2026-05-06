from __future__ import annotations

import base64
import asyncio
import hashlib
import logging
import multiprocessing
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

logger = logging.getLogger("app.services.feed_manager")

# Constants
PROCESS_JOIN_TIMEOUT = 3.0
QUEUE_MAX_SIZE = 500
QUEUE_DRAIN_LIMIT = 100
MAX_METRICS_HISTORY_LENGTH = 1000

# Scaling Constants
SLOT_COUNT = 16
MIN_WORKERS = 1
MAX_WORKERS = 2
SCALE_UP_THRESHOLD = 150
SCALE_DOWN_THRESHOLD = 20
SCALE_COOLDOWN = 30


class FeedManager:
    def __init__(self, config: Dict[str, Any]):
        self.config = config

        # Emergency SHM cleanup before initializing anything to prevent restart failures
        SharedFrameBuffer.force_cleanup()

        self.process_registry: Dict[str, Dict[str, Any]] = {}
        self.video_writers: Dict[str, VideoWriter] = {}
        self._lock = asyncio.Lock()
        self._feed_locks: Dict[str, asyncio.Lock] = {}

        self._global_fps = None
        self._feed_id_counter = 1
        self._stop_reader_flag = False
        self._result_reader_task: Optional[asyncio.Task] = None
        self.frame_subscriber_queues: Dict[str, List[asyncio.Queue]] = {}
        self._active_broadcast_tasks: Dict[str, asyncio.Task] = {}

        self._connection_manager: Optional[ConnectionManager] = None
        self._prediction_scheduler: Optional[PredictionScheduler] = None
        self._analytics_service: Optional[AnalyticsService] = None
        self._reid_manager = GlobalReIDManager(config)
        self.frame_buffer = SharedFrameBuffer(pool_size=100, owner=True)
        self.pipeline_pressure = RedisValue('f', 0.0, 'pipeline_pressure')
        self._is_processing_active: bool = False

        # Executor for CPU-bound result processing (SHM read -> bytes)
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="FeedMgr-Proc")

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
        self.persistence_path = Path(
            self.config.get("feeds_config_path", "backend/data/feeds_config.json")
        )

        # Database processing
        self._db_queue: Optional[RedisQueue] = RedisQueue('db_writes', maxsize=100000)
        self._db_reader_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None

        # Virtual Slot Architecture for Dynamic Scaling
        self.slot_count = SLOT_COUNT
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
            self._central_output_queue = RedisQueue('central_output', maxsize=QUEUE_MAX_SIZE)

        self._inference_pool: Dict[int, Process] = {}
        self._inference_command_queues: Dict[int, RedisQueue] = {}
        self._slot_to_worker: Dict[int, int] = {}
        self._inference_stop_event = RedisEvent('inference_stop')

        self._initial_inference_pool_size = (
            self.config.get("performance", {}).get("inference_pool_size", 2)
        )

        self.initialize_shared_values()
        self._initialize_available_feeds()

        # Register cleanup on exit
        atexit.register(self._atexit_cleanup)

    def _purge_stale_streams(self):
        """Purge stale Redis stream data from previous sessions.

        Stale messages reference dead SHM segments (zeroed on restart) and
        cause 'Invalid size 0' error spam if the result reader processes them.
        Must be called BEFORE the result reader task starts.
        """
        try:
            rc = self._central_output_queue.redis
            central_key = self._central_output_queue.key
            if rc.exists(central_key):
                rc.delete(central_key)
                self.logger.info(f"Deleted stale stream {central_key} for clean startup")
            self._central_output_queue._ensure_group()

            for slot_q in self._inference_input_queues:
                if hasattr(slot_q, 'key') and hasattr(slot_q, 'redis'):
                    try:
                        slot_key = slot_q.key
                        if slot_q.redis.exists(slot_key):
                            pending = slot_q.redis.xreadgroup(
                                slot_q.group_name, slot_q.consumer_id,
                                {slot_key: "0"}, count=500
                            )
                            if pending and pending[0][1]:
                                for msg_id, _ in pending[0][1]:
                                    slot_q.redis.xack(slot_key, slot_q.group_name, msg_id)
                                self.logger.info(
                                    f"Purged {len(pending[0][1])} stale pending from {slot_key}"
                                )
                    except Exception as slot_err:
                        self.logger.debug(
                            f"Slot stream purge failed for {getattr(slot_q, 'key', '?')}: {slot_err}"
                        )
        except Exception as e:
            self.logger.warning(f"Could not purge stale stream data: {e}")

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

        self._result_reader_task = asyncio.create_task(self._read_result_queues())
        self._pressure_task = asyncio.create_task(self._update_pipeline_pressure())
        self._scaling_task = asyncio.create_task(self._scaling_monitor())
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        self.logger.info(
            "FeedManager initialized. Reader, Watchdog, Pressure and Scaling tasks started."
        )

    def _get_feed_lock(self, feed_id: str) -> asyncio.Lock:
        """Returns a per-feed lock to ensure atomic operations like restart."""
        return self._feed_locks.setdefault(feed_id, asyncio.Lock())

    def _spawn_worker(self, worker_id: int):
        """Spawns a single inference worker assigned to specific slots."""
        slots = [s for s, w in self._slot_to_worker.items() if w == worker_id]
        cmd_q = RedisQueue(f'worker_cmd_{worker_id}', maxsize=100)
        self._inference_command_queues[worker_id] = cmd_q

        p = Process(
            target=inference_worker,
            args=(
                worker_id,
                self._inference_input_queues,
                cmd_q,
                self._inference_stop_event,
                dict(self.config) if hasattr(self.config, 'model_dump') else self.config,
                self._db_queue,
                None,   # frame_buffer — handled inside worker
                None,   # pipeline_pressure — worker reads from Redis
                slots,
            ),
            daemon=False,
            name=f"InferenceWorker-{worker_id}",
        )
        p.start()
        self._inference_pool[worker_id] = p
        logger.info(f"Launched InferenceWorker-{worker_id} handling slots {slots}")

        # Brief liveness check after spawn
        time.sleep(0.5)
        if p.is_alive():
            logger.info(f"InferenceWorker-{worker_id} (PID {p.pid}) is alive after spawn")
        else:
            logger.error(
                f"InferenceWorker-{worker_id} (PID {p.pid}) DIED immediately "
                f"with exitcode={p.exitcode}"
            )

    def scale_pool(self, target_size: int):
        """Dynamically adjusts the number of active workers and rebalances slots."""
        target_size = max(MIN_WORKERS, min(target_size, MAX_WORKERS))
        current_size = len(self._inference_pool)

        if target_size == current_size:
            return

        logger.info(f"Scaling inference pool: {current_size} -> {target_size}")

        # 1. Rebalance slots
        old_slot_to_worker = self._slot_to_worker.copy()
        self._slot_to_worker = {slot: (slot % target_size) for slot in range(self.slot_count)}

        # 2. Terminate excess workers
        if target_size < current_size:
            for wid in sorted(self._inference_pool.keys(), reverse=True):
                if wid >= target_size:
                    p = self._inference_pool.pop(wid)
                    p.terminate()
                    self._inference_command_queues.pop(wid, None)

        # 3. Restart workers whose slot assignments have changed
        for wid in range(target_size):
            needs_restart = False
            if wid not in self._inference_pool:
                needs_restart = True
            else:
                assigned_slots = [s for s, w in self._slot_to_worker.items() if w == wid]
                old_assigned_slots = [s for s, w in old_slot_to_worker.items() if w == wid]
                if assigned_slots != old_assigned_slots:
                    needs_restart = True

            if needs_restart:
                if wid in self._inference_pool:
                    self._inference_pool[wid].terminate()
                self._spawn_worker(wid)

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
        for p in self._inference_pool.values():
            if p.is_alive():
                logger.info(f"Terminating inference process {p.pid} in atexit")
                p.terminate()
                p.join(timeout=0.5)
                if p.is_alive():
                    p.kill()

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

        if self._reid_manager and hasattr(service, "_db_manager"):
            self._reid_manager.set_db_manager(service._db_manager)
            self.logger.info("ReIDManager connected to DatabaseManager.")

        if self._db_reader_task is None:
            self._db_reader_task = asyncio.create_task(self._read_db_queue())

    def set_connection_manager(self, manager: ConnectionManager):
        self._connection_manager = manager
        logger.info("WebSocket ConnectionManager set in FeedManager.")

    async def _scaling_monitor(self):
        """Monitors queue depth and scales the worker pool dynamically."""
        while not self._stop_reader_flag:
            try:
                total_depth = sum(
                    q.qsize() for q in self._inference_input_queues if hasattr(q, 'qsize')
                )
                avg_depth = total_depth / self.slot_count
                current_size = len(self._inference_pool)

                if avg_depth > SCALE_UP_THRESHOLD and current_size < MAX_WORKERS:
                    logger.info(f"High load detected (avg depth {avg_depth:.1f}). Scaling up...")
                    self.scale_pool(current_size + 1)
                elif avg_depth < SCALE_DOWN_THRESHOLD and current_size > MIN_WORKERS:
                    logger.info(f"Low load detected (avg depth {avg_depth:.1f}). Scaling down...")
                    self.scale_pool(current_size - 1)

                await asyncio.sleep(SCALE_COOLDOWN)
            except Exception as e:
                logger.error(f"Error in scaling monitor: {e}")
                await asyncio.sleep(5.0)

    async def _update_pipeline_pressure(self):
        """Updates the global pressure signal based on ConnectionManager queue depths."""
        while not self._stop_reader_flag:
            try:
                if self._connection_manager:
                    queues = self._connection_manager.client_queues.values()
                    if queues:
                        total_fill = sum(q.qsize() / q.maxsize for q in queues)
                        self.pipeline_pressure.value = total_fill / len(queues)
                    else:
                        self.pipeline_pressure.value = 0.0
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
            for fid, entry in self.process_registry.items():
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
        for entry in self.process_registry.values():
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
                    status = self.process_registry.get(fid, {}).get("status")
                    if status in [FeedOperationalStatusEnum.RUNNING, FeedOperationalStatusEnum.STARTING]:
                        to_stop.append(fid)
            else:
                active_count = sum(
                    1 for entry in self.process_registry.values()
                    if entry["status"] in [
                        FeedOperationalStatusEnum.RUNNING,
                        FeedOperationalStatusEnum.STARTING,
                    ]
                )
                max_feeds = self.config.get("feed_manager", {}).get("max_concurrent_feeds", 10)

                for fid in self._sample_feed_ids:
                    status = self.process_registry.get(fid, {}).get("status")
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

    async def start_processing(self):
        """Starts the overall video processing and prediction scheduling."""
        if self._is_processing_active:
            return

        self.logger.info("Starting overall video processing.")
        await self.initialize()
        self._is_processing_active = True

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
        self.logger.info("Cleared stale inference stop event from Redis.")
    except Exception as e:
        self.logger.warning(f"Could not clear inference stop event: {e}")

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

        pool_size = self.config.get("performance", {}).get("inference_pool_size", 2)
        self.scale_pool(pool_size)

        await self._check_and_manage_sample_feed()

        if self._prediction_scheduler:
            if self.config.get("prediction_scheduler", {}).get("enabled", True):
                await self._prediction_scheduler.start()
                self.logger.info("Prediction scheduler started.")
            else:
                self.logger.debug("PredictionScheduler disabled in config.")

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

    async def _stop_inference_pool(self):
        logger.info("Stopping Inference Pool...")
        self._inference_stop_event.set()

        for _ in range(50):
            try:
                self._central_output_queue.get_nowait()
            except Exception:
                pass

            if all(not p.is_alive() for p in self._inference_pool.values()):
                break
            await asyncio.sleep(0.1)

        for p in self._inference_pool.values():
            if p.is_alive():
                logger.warning(f"Forcing termination of Inference Worker {p.name}")
                p.terminate()
                await asyncio.sleep(0.1)
                if p.is_alive():
                    p.kill()

        self._inference_pool = {}
        self._inference_command_queues = {}

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
                    data = config_info.model_dump()
                    data["_is_looped_feed"] = entry.get("is_looped_feed", True)
                    data["_is_sample_feed"] = entry.get("is_sample_feed", False)
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
                    is_looped = feed_data.pop("_is_looped_feed", True)
                    is_sample = feed_data.pop("_is_sample_feed", False)
                    config_info = FeedConfigInfo(**feed_data)

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
                        "is_sample_feed": is_sample,
                        "is_looped_feed": is_looped,
                        "config_info": config_info,
                        "last_broadcast_time": 0.0,
                    }

                    if is_sample and feed_id not in self._sample_feed_ids:
                        self._sample_feed_ids.append(feed_id)

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

            try:
                resources = self._detach_resources(feed_id)
                if resources:
                    await self._terminate_resources(resources)
            except Exception as e:
                logger.error(f"Error stopping feed {feed_id} during removal: {e}")

            del self.process_registry[feed_id]
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
        for worker_id, q in self._inference_command_queues.items():
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
                    "metrics_history": deque(maxlen=MAX_METRICS_HISTORY_LENGTH),
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
        failed_resources_to_cleanup = None
        is_sample = False
        started_real_feed = False

        async with self._lock:
            entry = self.process_registry.get(feed_id)
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
            if not self._inference_pool:
                pool_size = self.config.get("performance", {}).get("inference_pool_size", 2)
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

            entry["command_queue"] = RedisQueue('feed_cmd_' + feed_id, maxsize=50)

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
            entry["metrics_history"] = deque(maxlen=MAX_METRICS_HISTORY_LENGTH)
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

    async def _stop_feed_internal(self, feed_id: str, skip_sample_mgmt: bool = False):
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
        if not skip_sample_mgmt:
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

        slot_id = int(hashlib.md5(feed_id.encode()).hexdigest(), 16) % self.slot_count
        target_queue = self._inference_input_queues[slot_id]
        logger.info(f"Routing feed {feed_id} to slot {slot_id}")

        worker_args = (
            source,
            feed_id,
            target_queue,
            None,   # stop_event — worker checks Redis
            self.config,
            entry.get("is_looped_feed", False),
            None,   # command_queue — handled via Redis
            None,   # frame_buffer — worker initialises its own handle
            None,   # pipeline_pressure — worker reads from Redis
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
                await loop.run_in_executor(None, process.join, 1.0)

                if process.is_alive():
                    logger.warning(f"Process {process.pid} for {feed_id} hung. Terminating.")
                    process.terminate()
                    await asyncio.sleep(0.5)

                    if process.is_alive():
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

    def _process_single_result(self, item):
        """CPU-bound processing of a single result item (SHM read -> bytes)."""
        try:
            feed_id, frame_idx, shm_ref, metrics, vehicles, extra = item
            raw_jpg_view, dims = self.frame_buffer.read(shm_ref)
            frame_bytes = raw_jpg_view.tobytes()
            
            # Explicitly release the memoryview to avoid BufferError during SHM cleanup
            if hasattr(raw_jpg_view, 'release'):
                raw_jpg_view.release()
                
            self.frame_buffer.release(shm_ref)
            return feed_id, frame_idx, frame_bytes, metrics, vehicles, extra
        except Exception as e:
            now = time.time()
            last_ts = getattr(self, '_stale_err_last_ts', 0)
            count = getattr(self, '_stale_err_count', 0)
            if now - last_ts > 5.0:
                if count > 1:
                    logger.warning(f"Stale SHM result errors: {count} suppressed in last 5s")
                logger.error(
                    f"Error processing result for "
                    f"{item[0] if len(item) > 0 else 'unknown'}: {e}"
                )
                self._stale_err_count = 1
                self._stale_err_last_ts = now
            else:
                self._stale_err_count = count + 1
                self._stale_err_last_ts = now
            return None

    async def _read_result_queues(self):
        logger.info("Result reader task started (Decoupled Mode).")
        last_heartbeat = time.time()
        # Cross-batch dedup: track last-processed timestamp per feed to suppress
        # rapid-fire duplicate frames (e.g., frame_idx=0 on video loop).
        _feed_last_processed_ts: Dict[str, float] = {}

        while not self._stop_reader_flag:
            try:
                if time.time() - last_heartbeat > 10.0:
                    logger.debug("Result reader heartbeat: loop is active")
                    last_heartbeat = time.time()

                items_buffer = []
                try:
                    for _ in range(200):
                        res = self._central_output_queue.get(block=False)
                        if isinstance(res, tuple) and len(res) == 2:
                            msg_id, item = res
                            items_buffer.append((msg_id, item))
                        else:
                            items_buffer.append((None, res))
                except queue.Empty:
                    pass

                if not items_buffer:
                    await asyncio.sleep(0.001)
                    await self._handle_periodic_tasks()
                    continue

                logger.info(f"Result reader: Popped {len(items_buffer)} items from central output queue")

                processed_items = await asyncio.gather(*[
                    asyncio.get_running_loop().run_in_executor(
                        self._executor,
                        self._process_single_result,
                        item,
                    )
                    for msg_id, item in items_buffer
                ])

                feed_ids_to_update: set = set()

                # Per-feed dedup: keep only the latest frame per feed in this batch.
                # Uses list index (arrival order) for recency — NOT frame_idx, because
                # frame_idx resets to 0 on video loop.
                latest_per_feed: Dict[str, int] = {}
                for i, result in enumerate(processed_items):
                    if result is None:
                        continue
                    latest_per_feed[result[0]] = i

                latest_indices = set(latest_per_feed.values())
                skipped_count = len(processed_items) - len(latest_indices)

                # ACK all messages (including dedup'd ones) to prevent pending buildup
                for i, result in enumerate(processed_items):
                    msg_id, _ = items_buffer[i]
                    if result is None:
                        if msg_id:
                            self._central_output_queue.ack(msg_id)
                        continue
                    if msg_id:
                        try:
                            self._central_output_queue.ack(msg_id)
                        except Exception as e:
                            logger.error(f"Failed to ack message {msg_id}: {e}")

                if skipped_count > 0:
                    logger.info(
                        f"[RESULT_READER] Dedup: {skipped_count} stale frames skipped, "
                        f"{len(latest_indices)} latest kept"
                    )

                for i, result in enumerate(processed_items):
                    if result is None or i not in latest_indices:
                        continue

                    feed_id, frame_idx, frame_bytes, metrics, vehicles, extra = result

                    # Cross-batch dedup: skip frames arriving within the broadcast window
                    now_dedup = time.time()
                    target_fps_dedup = self.config.get("video_output", {}).get("fps", 10)
                    dedup_window = 1.0 / target_fps_dedup
                    if now_dedup - _feed_last_processed_ts.get(feed_id, 0.0) < dedup_window:
                        logger.debug(
                            f"[RESULT_READER] Cross-batch dedup: skipping frame {frame_idx} "
                            f"for feed={feed_id}"
                        )
                        continue
                    _feed_last_processed_ts[feed_id] = now_dedup

                    entry = self.process_registry.get(feed_id)
                    if not entry:
                        logger.error(
                            f"[RESULT_READER] Feed {feed_id} not in process_registry, skipping"
                        )
                        continue

                    logger.info(
                        f"[RESULT_READER] Received frame {frame_idx} for feed={feed_id}, "
                        f"size={len(frame_bytes)} bytes, vehicles={len(vehicles)}"
                    )

                    if i % 10 == 0:
                        await asyncio.sleep(0)

                    # Transition STARTING -> RUNNING
                    if entry["status"] == FeedOperationalStatusEnum.STARTING:
                        async with self._lock:
                            if (
                                self.process_registry.get(feed_id, {}).get("status")
                                == FeedOperationalStatusEnum.STARTING
                            ):
                                self.process_registry[feed_id]["status"] = (
                                    FeedOperationalStatusEnum.RUNNING
                                )
                                feed_ids_to_update.add(feed_id)
                                if feed_id in self._feed_running_events:
                                    self._feed_running_events[feed_id].set()
                        logger.info(f"[RESULT_READER] Feed {feed_id} transitioned to RUNNING")

                    now = time.time()
                    metrics["timestamp"] = datetime.now(timezone.utc)

                    if entry.get("config_info"):
                        metrics["latitude"] = entry["config_info"].latitude
                        metrics["longitude"] = entry["config_info"].longitude
                        metrics["location_name"] = entry["config_info"].name

                    entry["latest_metrics"] = metrics
                    entry["last_frame_time"] = now
                    if entry.get("timer"):
                        entry["timer"].tick()

                    if extra and extra.get("type") == "snapshot":
                        inc_id = extra.get("incident_id")
                        path = extra.get("path")
                        if self._analytics_service and inc_id:
                            asyncio.create_task(
                                self._analytics_service.update_incident_snapshot(inc_id, path)
                            )
                        continue

                    if entry.get("video_writer_queue"):
                        try:
                            entry["video_writer_queue"].put_nowait((frame_bytes, metrics))
                        except queue.Full:
                            pass

                    if "metrics_history" not in entry or not isinstance(
                        entry["metrics_history"], deque
                    ):
                        entry["metrics_history"] = deque(maxlen=MAX_METRICS_HISTORY_LENGTH)
                    entry["metrics_history"].append((now, metrics.copy()))

                    while (
                        entry["metrics_history"]
                        and entry["metrics_history"][0][0] < now - self._metrics_averaging_window
                    ):
                        entry["metrics_history"].popleft()

                    # Broadcast logic (FPS-limited)
                    target_fps = self.config.get("video_output", {}).get("fps", 10)
                    min_interval = 1.0 / target_fps
                    last_broadcast = entry.get("last_broadcast_time", 0.0)

                    logger.info(
                        f"[BROADCAST] Frame {frame_idx}: now={now:.3f}, "
                        f"last_broadcast={last_broadcast:.3f}, min_interval={min_interval:.3f}"
                    )

                    if now - last_broadcast >= min_interval:
                        entry["last_broadcast_time"] = now
                        logger.info(
                            f"[BROADCAST] Scheduling broadcast for {feed_id} frame {frame_idx}"
                        )
                        task = asyncio.create_task(
                            self._broadcast_video_frame(
                                feed_id, frame_idx, frame_bytes, metrics, vehicles, extra
                            )
                        )
                        self._active_broadcast_tasks[feed_id] = task
                    else:
                        logger.debug(
                            f"[BROADCAST] Skipping frame {frame_idx} (elapsed: "
                            f"{now - last_broadcast:.3f}s, need: {min_interval:.3f}s)"
                        )

                    if self._analytics_service:
                        asyncio.create_task(
                            self._analytics_service.process_feed_metrics(feed_id, metrics, vehicles)
                        )

                await self._perform_broadcasts(feed_ids_to_update, False, False)
                await self._handle_periodic_tasks()

                # Yield to the event loop to prevent starving the _client_sender
                # and other coroutines on a constrained CPU.
                # If the buffer is large, we still yield for a meaningful amount of time.
                await asyncio.sleep(0.01 if len(items_buffer) < 200 else 0.02)

            except Exception as e:
                logger.error(f"Error in result reader loop: {e}", exc_info=True)
                await asyncio.sleep(1.0)

    async def _handle_periodic_tasks(self):
        now = time.time()
        if now - self._last_kpi_broadcast_time >= self._kpi_broadcast_interval:
            await self._broadcast_kpi_update()
            self._last_kpi_broadcast_time = now

        if now - self._last_queue_log_time >= 30.0:
            self._last_queue_log_time = now
            try:
                self._check_resources()
                self.frame_buffer.prune_stale_segments(timeout_seconds=300)
            except ResourceLimitError as e:
                logger.error(f"Resource limit exceeded during operation: {e}")

    def _compute_vehicle_deltas(
        self, feed_id: str, vehicles: List[Dict], frame_idx: int
    ) -> List[Dict]:
        """
        Computes delta updates for vehicles to reduce bandwidth.
        - KEYFRAME (every 30 frames): Send full data.
        - DELTA: Send only changed fields + mandatory (id, bbox, velocity).
        """
        KEYFRAME_INTERVAL = 30
        is_keyframe = (frame_idx % KEYFRAME_INTERVAL == 0)

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

    async def _broadcast_video_frame(
        self, feed_id, frame_idx, frame_bytes, metrics, vehicles, extra_payload=None
    ):
        logger.info(
            f"[BROADCAST] >>>>>> START frame={frame_idx} feed={feed_id} "
            f"bytes={len(frame_bytes) if frame_bytes else 0}"
        )

        if not self._connection_manager:
            logger.error(f"[BROADCAST] FAIL: ConnectionManager not initialized for feed={feed_id}")
            return

        if not frame_bytes:
            logger.error(f"[BROADCAST] FAIL: frame_bytes is empty for feed={feed_id} frame={frame_idx}")
            return

        subscribers = self._connection_manager.get_clients_for_feed(feed_id)
        if not subscribers:
            logger.info(
                f"[BROADCAST] No subscribers for feed={feed_id}, "
                f"skipping broadcast (saved ~{len(frame_bytes)}B serialization)"
            )
            return

        active_conns = len(self._connection_manager.active_connections or [])
        feed_subs = list(self._connection_manager.feed_subscriptions.keys() or [])
        logger.info(
            f"[BROADCAST] ConnectionManager: active_connections={active_conns}, "
            f"feed_subscriptions={feed_subs}, subscribers_for_feed={len(subscribers)}"
        )

        try:
            logger.info(f"[BROADCAST] Computing vehicle deltas for {len(vehicles) if vehicles else 0} vehicles")
            optimized_vehicles = self._compute_vehicle_deltas(feed_id, vehicles, frame_idx)
            logger.info(f"[BROADCAST] Optimized to {len(optimized_vehicles)} vehicles")

            payload: Dict[str, Any] = {
                "t": WebSocketMessageTypeEnum.VIDEO_FRAME,
                "f": feed_id,
                "i": frame_idx,
                "ts": time.time(),
                "v": optimized_vehicles,
                "m": metrics,
            }

            if extra_payload and "bg" in extra_payload:
                payload["bg"] = extra_payload["bg"]
                payload["rois"] = extra_payload.get("rois", [])
                logger.info("[BROADCAST] Using adaptive streaming with ROIs")
            else:
                payload["frame"] = frame_bytes
                logger.info(f"[BROADCAST] Standard frame broadcast, size={len(frame_bytes)}")

            def msgpack_default(obj):
                if isinstance(obj, datetime):
                    return obj.isoformat()
                if isinstance(obj, (np.integer, np.int64, np.int32)):
                    return int(obj)
                if isinstance(obj, (np.floating, np.float64, np.float32)):
                    return float(obj)
                if isinstance(obj, np.ndarray):
                    return obj.tolist()
                return str(obj)

            logger.info("[BROADCAST] Serializing payload...")
            msg_bytes = msgpack.packb(payload, default=msgpack_default, use_bin_type=True)
            logger.info(f"[BROADCAST] Serialized to {len(msg_bytes)} bytes")

            logger.info(
                f"[BROADCAST] Broadcasting to feed={feed_id} frame={frame_idx} "
                f"size={len(msg_bytes)} bytes"
            )
            await self._connection_manager.broadcast_to_feed_realtime_bytes(
                feed_id, msg_bytes, frame_index=frame_idx
            )
            logger.info(f"[BROADCAST] <<<<<< SUCCESS frame={frame_idx} feed={feed_id}")

        except Exception as e:
            import traceback
            logger.error(f"[BROADCAST] EXCEPTION: {e}")
            logger.error(f"[BROADCAST] Traceback: {traceback.format_exc()}")

    async def _watchdog_loop(self):
        """Periodically checks if processing workers are alive and responsive."""
        logger.info("Watchdog task started.")
        while not self._stop_reader_flag:
            try:
                await asyncio.sleep(5.0)

                feeds_to_restart = []
                async with self._lock:
                    for feed_id, entry in self.process_registry.items():
                        if entry["status"] not in [
                            FeedOperationalStatusEnum.RUNNING,
                            FeedOperationalStatusEnum.STARTING,
                        ]:
                            continue

                        process = entry.get("process")
                        if process and not process.is_alive():
                            exit_code = process.exitcode if process else "N/A"
                            if exit_code is not None and exit_code != 0:
                                logger.warning(
                                    f"Video process {feed_id} exited with error code: {exit_code}"
                                )
                            else:
                                logger.info(f"Video process {feed_id} ended (likely reached EOF).")
                            feeds_to_restart.append(feed_id)

                for feed_id in feeds_to_restart:
                    try:
                        logger.info(f"Watchdog: Restarting video feed: {feed_id}")
                        await self.restart_feed(feed_id)
                        logger.info(f"Watchdog: Video feed restarted successfully: {feed_id}")
                    except Exception as e:
                        logger.error(f"Watchdog: Failed to restart video feed {feed_id}: {e}")

                # Check inference pool workers
                dead_workers = []
                for wid, p in list(self._inference_pool.items()):
                    if not p.is_alive():
                        exit_code = p.exitcode
                        logger.warning(
                            f"Watchdog: InferenceWorker-{wid} (PID {p.pid}) is dead. "
                            f"Exit code: {exit_code}"
                        )
                        dead_workers.append(wid)

                if dead_workers:
                    # Clear stop signals before respawning to prevent the same race
                    try:
                        self._inference_stop_event.clear()
                        rc = get_redis_client()
                        rc.delete("signal:pipeline_stop")
                    except Exception:
                        pass
                    for wid in dead_workers:
                        logger.info(f"Watchdog: Respawning InferenceWorker-{wid}")
                        self._inference_pool.pop(wid, None)
                        self._spawn_worker(wid)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in watchdog loop: {e}", exc_info=True)
                await asyncio.sleep(10.0)

    # --- Broadcast Helpers ---

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
            data=FeedStatusUpdate(feed_status_data=data).model_dump(),
        )
        # HIGH priority so status updates are never dropped by video-frame back-pressure
        await self._connection_manager.broadcast(msg.model_dump_json(), priority=MessagePriority.HIGH)

    async def _broadcast_kpi_update(self):
        if not self._connection_manager:
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
            return

        global_avg_speed = (total_speed_sum / total_speed_count) if total_speed_count > 0 else 0.0
        global_congestion_index = total_congestion_sum / active_feeds_count

        kpi_data = GlobalRealtimeMetrics(
            timestamp=datetime.now(timezone.utc).isoformat(),
            metrics_source="aggregated_feeds",
            total_flow=total_vehicles_cumulative,
            average_speed_kmh=round(global_avg_speed, 1),
            congestion_index=round(global_congestion_index, 1),
            active_incidents_count=0,
            feed_statuses={"active": active_feeds_count, "total": len(self.process_registry)},
            custom_metrics={"active_vehicles": total_vehicles_active},
        )

        message = WebSocketMessage(
            type=WebSocketMessageTypeEnum.KPI_UPDATE,
            data=kpi_data.model_dump(),
        )
        await self._connection_manager.broadcast_to_topic(
            message.model_dump_json(), topic='kpi', priority=MessagePriority.NORMAL
        )

    async def _perform_broadcasts(self, feeds_to_update, kpi_needed, sample_needed):
        for fid in feeds_to_update:
            await self._broadcast_feed_update(fid)

        now = time.time()
        if kpi_needed or (now - self._last_kpi_broadcast_time >= self._kpi_broadcast_interval):
            await self._broadcast_kpi_update()
            self._last_kpi_broadcast_time = now

        if sample_needed:
            await self._check_and_manage_sample_feed()

    # --- Frame Subscriptions ---

    async def subscribe_to_frames(self, feed_id: str) -> asyncio.Queue:
        async with self._lock:
            if feed_id not in self.frame_subscriber_queues:
                self.frame_subscriber_queues[feed_id] = []
            q: asyncio.Queue = asyncio.Queue(maxsize=30)
            self.frame_subscriber_queues[feed_id].append(q)
            return q

    async def unsubscribe_from_frames(self, feed_id: str, q: asyncio.Queue):
        async with self._lock:
            if feed_id in self.frame_subscriber_queues:
                if q in self.frame_subscriber_queues[feed_id]:
                    self.frame_subscriber_queues[feed_id].remove(q)
                if not self.frame_subscriber_queues[feed_id]:
                    del self.frame_subscriber_queues[feed_id]

    # --- Shutdown ---

    async def shutdown(self):
        logger.info("Shutdown initiated.")
        self._stop_reader_flag = True

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