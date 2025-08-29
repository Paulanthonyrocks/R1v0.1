from __future__ import annotations
import base64
import asyncio
import logging
import time
import numpy as np
import psutil
import re
from multiprocessing import (
    Process,
    Queue as MPQueue,
    Event,
    set_start_method,
    get_start_method,
)
from typing import Dict, Any, Optional, List, Tuple
from enum import Enum
from pathlib import Path
import queue  # For queue.Empty exception
from datetime import datetime, timezone  # For alert timestamps

# Import custom exceptions
from .exceptions import FeedNotFoundError, FeedOperationError, ResourceLimitError

# Import Pydantic models
from app.models.feeds import (
    FeedStatusData,
    FeedConfigInfo,
    FeedOperationalStatusEnum,
)  # Updated import for FeedStatusData
from app.models.alerts import Alert, AlertSeverityEnum  # Updated import for Alert
from app.models.websocket import (
    WebSocketMessage,
    WebSocketMessageTypeEnum,
    FeedStatusUpdate,
    NewAlertNotification,
    GlobalRealtimeMetrics,
    VideoFrameData,
)  # New imports

# Import core worker and utilities (adjust path as needed)
from app.core.processing_worker import process_video
from app.utils.monitoring import check_system_resources
from app.utils.video import FrameTimer  # FrameTimer moved to video.py
from app.websocket.connection_manager import ConnectionManager
from app.services.analytics_service import AnalyticsService
from app.tasks.prediction_scheduler import PredictionScheduler


class FeedStatus(Enum):
    """Enum to represent the possible states of a feed."""

    RUNNING = "running"
    STOPPED = "stopped"
    STARTING = "starting"
    ERROR = "error"


logger = logging.getLogger("app.services.feed_manager")

# Ensure start method is set (important for multiprocessing)
try:
    if get_start_method(allow_none=True) is None:
        set_start_method("spawn")
    logger.info(f"Multiprocessing start method: {get_start_method()}")
except Exception as e:
    logger.warning(f"Could not set multiprocessing start method ('spawn'): {e}")


# Add this helper at the top of the file (or before _read_result_queues)
async def queue_get_task():
    await asyncio.sleep(0.1)
    return None


class FeedManager:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.process_registry: Dict[str, Dict[str, Any]] = {}
        self._lock = (
            asyncio.Lock()
        )  # Use asyncio lock for async methods managing the registry
        self._global_fps = None  # Will be initialized after app startup
        self._feed_id_counter = 1  # Simple counter for unique IDs
        self._stop_reader_flag = False
        self._result_reader_task: Optional[asyncio.Task] = None
        
        self._connection_manager = None  # Added type hint
        self._prediction_scheduler = None  # New: Reference to PredictionScheduler
        self._analytics_service = None  # New: Reference to AnalyticsService
        self._is_processing_active: bool = (
            False  # New: Flag to control overall processing
        )
        self._last_kpi_broadcast_time = 0.0
        self._kpi_broadcast_interval = self.config.get(
            "kpi_broadcast_interval", 1.0
        )  # Seconds, configurable
        self._sample_feed_ids: List[str] = []  # Store IDs of all sample feeds
        self._feed_running_events: Dict[
            str, asyncio.Event
        ] = {}  # New: Events to signal when a feed is running
        self.logger = logger  # Assign the module-level logger to the instance

        # Adaptive delay for result queue reading
        self._min_read_delay = self.config.get("min_frame_read_delay_ms", 1) / 1000.0  # Minimum delay (e.g., 1ms)
        self._max_read_delay = self.config.get("max_frame_read_delay_ms", 100) / 1000.0 # Maximum delay (e.g., 100ms)
        self._current_read_delay = self._min_read_delay
        self._delay_adjustment_factor = self.config.get("delay_adjustment_factor", 1.1) # Factor to increase/decrease delay
        self._last_queue_log_time = 0.0
        self._queue_log_interval = self.config.get("queue_log_interval", 15.0) # Log queue size every 15 seconds

        # Load available feeds from config if needed (or assume they are added dynamically)
        self._initialize_available_feeds()

        # Start the background task to read results
        self._result_reader_task = asyncio.create_task(self._read_result_queues())
        self.logger.info("FeedManager initialized and result reader task started.")

    def set_prediction_scheduler(self, scheduler: "PredictionScheduler"):  # type: ignore [name-defined]
        """Inject the PredictionScheduler instance."""
        self._prediction_scheduler = scheduler
        self.logger.info("PredictionScheduler set in FeedManager.")

    def set_analytics_service(self, service: AnalyticsService):  # type: ignore [name-defined]
        """Inject the AnalyticsService instance."""
        self._analytics_service = service
        self.logger.info("AnalyticsService set in FeedManager.")

    async def start_processing(self):
        """Starts the overall video processing and prediction scheduling."""
        if self._is_processing_active:
            self.logger.info("Processing is already active. Skipping start.")
            return

        self.logger.info("Starting overall video processing and prediction scheduling.")
        self._is_processing_active = True

        # Start the sample feed if no real feeds are active
        await self._check_and_manage_sample_feed()

        # Start the prediction scheduler
        if self._prediction_scheduler:
            if self.config.get("prediction_scheduler", {}).get("enabled", True):
                await self._prediction_scheduler.start()
                self.logger.info("Prediction scheduler started by FeedManager.")
            else:
                self.logger.info("Prediction scheduler is disabled in config. Skipping startup by FeedManager.")
        else:
            self.logger.warning(
                "PredictionScheduler not set in FeedManager. Cannot start it."
            )

    async def stop_processing(self):
        """Stops the overall video processing and prediction scheduling."""
        if not self._is_processing_active:
            self.logger.info("Processing is already inactive. Skipping stop.")
            return

        self.logger.info("Stopping overall video processing and prediction scheduling.")
        self._is_processing_active = False

        # Stop the sample feed
        await self._check_and_manage_sample_feed()

        # Stop the prediction scheduler
        if self._prediction_scheduler:
            await self._prediction_scheduler.stop()
            self.logger.info("Prediction scheduler stopped by FeedManager.")
        else:
            self.logger.warning(
                "PredictionScheduler not set in FeedManager. Cannot stop it."
            )

    def initialize_shared_values(self):
        import multiprocessing

        if self._global_fps is None:
            manager = multiprocessing.Manager()
            self._global_fps = manager.Value("i", self.config.get("fps", 30))
            
            logger.info(
                "FeedManager shared values initialized using multiprocessing.Manager().Value."
            )

    def set_connection_manager(self, manager: "ConnectionManager"):  # type: ignore [name-defined]
        """Inject the WebSocket ConnectionManager."""
        self._connection_manager = manager
        logger.info("WebSocket ConnectionManager set in FeedManager.")

    def _initialize_available_feeds(self):
        sample_video_paths = self.config.get("video_input", {}).get("sample_videos", [])
        singular_sample_path = self.config.get("video_input", {}).get("sample_video")

        # Ensure singular_sample_path is always considered if it exists
        if singular_sample_path:
            if isinstance(sample_video_paths, list):
                if singular_sample_path not in sample_video_paths:
                    sample_video_paths.append(singular_sample_path)
            else: # If sample_videos was not a list, or not present, create a new list
                sample_video_paths = [singular_sample_path]

        for i, sample_path_str in enumerate(sample_video_paths):
            resolved_path = Path(self.config.get("project_root_dir"), sample_path_str)
            logger.info(f"Attempting to resolve path: {sample_path_str}")
            logger.info(f"Resolved path: {resolved_path.absolute()}")
            logger.info(f"Current working directory: {Path.cwd()}")
            if not resolved_path.exists():
                logger.warning(f"Sample video path configured but not found: {resolved_path}")
                # Do not raise an exception here, just log a warning and skip this path
                continue # Skip to the next sample path
            # If it exists, proceed as normal (this block was incorrectly indented)
            feed_name = f"Sample Video {i+1}" if len(sample_video_paths) > 1 else "Sample Video"
            feed_id = self._generate_feed_id(str(resolved_path), feed_name)
            self.process_registry[feed_id] = {
                "process": None,
                "result_queue": None,
                "stop_event": None,
                "reduce_fps_event": None,
                "status": FeedOperationalStatusEnum.STOPPED,
                "source": str(resolved_path),
                "start_time": None,
                "error_message": None,
                "latest_metrics": None,
                "timer": None,
                "is_sample_feed": True,
                'is_looped_feed': True,
                "config_info": FeedConfigInfo(
                    name=feed_name,
                    source_type="video_file",
                    source_identifier=str(resolved_path),
                    latitude=34.0522,
                    longitude=-118.2437, # Default coordinates
                ),
            }
            self._sample_feed_ids.append(feed_id)
            logger.info(
                f"Initialized sample feed '{feed_id}' ({feed_name}) as {FeedOperationalStatusEnum.STOPPED}."
            )
        if not self._sample_feed_ids:
            logger.info("No sample video paths configured or found.")

    def _generate_feed_id(self, source: str, name_hint: Optional[str] = None) -> str:
        """Generates a unique Feed ID."""
        # Simple generation logic, enhance as needed
        if name_hint:
            base_name = re.sub(r"[^\w\-.]+", "_", name_hint)
        elif source.startswith("webcam:"):
            base_name = f"Webcam_{source.split(':')[1]}"
        else:
            base_name = re.sub(r"[^\w\-.]+", "_", Path(source).stem)

        feed_id = f"Feed_{self._feed_id_counter}_{base_name}"
        while feed_id in self.process_registry:
            self._feed_id_counter += 1
            feed_id = f"Feed_{self._feed_id_counter}_{base_name}"
        self._feed_id_counter += 1
        return feed_id

    def _check_resources(self):
        """Checks if system resources allow starting a new feed."""
        limit = self.config.get("performance", {}).get("memory_limit_percent", 80)
        cpu, mem = (
            check_system_resources()
        )  # Assumes check_system_resources is available
        if mem >= limit:
            logger.warning(
                f"Resource limit reached: Memory Usage {mem:.1f}% >= Limit {limit}%."
            )
            raise ResourceLimitError(
                f"Memory usage ({mem:.1f}%) exceeds limit ({limit}%). Cannot start new feed."
            )
        # Add CPU check if desired
        logger.debug(
            f"Resource check passed: CPU={cpu:.1f}%, Memory={mem:.1f}% (Limit={limit}%)"
        )

    async def _broadcast(self, message_type: WebSocketMessageTypeEnum, data: Dict):
        """Helper to broadcast safely."""
        if self._connection_manager:
            # Assuming data is already a dictionary that can be directly used as payload
            # or wrapped into a WebSocketMessage model if needed.
            message = WebSocketMessage(
                type=message_type, data=data
            )
            await self._connection_manager.broadcast(message)
        else:
            logger.debug(f"Broadcast skipped (No WS Manager): Type={message_type}")

    async def get_all_statuses(self) -> List[FeedStatusData]:
        """Retrieves the status of all feeds."""
        logger.info("Getting all feed statuses.")
        statuses = []
        async with self._lock:
            for feed_id, entry in self.process_registry.items():
                try:
                    op_status = entry["status"]
                    config_info_entry = entry.get("config_info")
                    if not isinstance(config_info_entry, FeedConfigInfo):
                        source_val = entry.get("source", "Unknown Source")
                        # Attempt to infer name and source_type for fallback
                        name_val = (
                            Path(source_val).name
                            if Path(source_val).is_file()
                            or "/" in source_val
                            or "\\" in source_val
                            else "Unknown Feed Name"
                        )
                        source_type_val = (
                            "video_file" if Path(source_val).suffix else "unknown"
                        )
                        config_info_entry = FeedConfigInfo(
                            name=name_val,
                            source_type=source_type_val,
                            source_identifier=source_val,
                        )

                    status_data = FeedStatusData(
                        feed_id=feed_id,
                        config=config_info_entry,
                        source=entry.get("source"),
                        status=op_status,
                        current_fps=entry["timer"].get_fps("loop_total")
                        if entry.get("timer")
                        and op_status == FeedOperationalStatusEnum.RUNNING
                        else None,
                        last_error=entry.get("error_message"),
                        latest_metrics=entry.get("latest_metrics"),
                    )
                    statuses.append(status_data)
                except Exception as e:
                    logger.error(
                        f"Error creating FeedStatusData for feed '{feed_id}': {e}",
                        exc_info=True,
                    )
        logger.info(f"Found {len(statuses)} feed statuses.")
        logger.debug(f"Prepared feed statuses: {statuses}")
        return statuses

    async def get_feed_status(self, feed_id: str) -> Optional[FeedStatusData]:
        """Retrieves the status of a specific feed by feed_id."""
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry:
                return None
            op_status = entry["status"]
            config_info_entry = entry.get("config_info")
            if not isinstance(config_info_entry, FeedConfigInfo):
                source_val = entry.get("source", "Unknown Source")
                name_val = (
                    Path(source_val).name
                    if Path(source_val).is_file()
                    or "/" in source_val
                    or "\\" in source_val
                    else "Unknown Feed Name"
                )
                source_type_val = "video_file" if Path(source_val).suffix else "unknown"
                config_info_entry = FeedConfigInfo(
                    name=name_val,
                    source_type=source_type_val,
                    source_identifier=source_val,
                )
            return FeedStatusData(
                feed_id=feed_id,
                config=config_info_entry,
                source=entry.get("source"),
                status=op_status,
                current_fps=entry["timer"].get_fps("loop_total")
                if entry.get("timer") and op_status == FeedOperationalStatusEnum.RUNNING
                else None,
                status_message=None,
                start_time=entry.get("start_time"),
                last_data_timestamp=None,
                processed_items_count=None,
                items_per_second_current=None,
                error_details=entry.get("error_message"),
                latest_metrics=entry.get("latest_metrics"),
            )

    async def get_feed_entry(self, feed_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves the raw registry entry for a specific feed by feed_id."""
        async with self._lock:
            return self.process_registry.get(feed_id)

    async def _broadcast_feed_update(self, feed_id: str):
        """Sends feed status update via WebSocket manager."""
        try:
            if not self._connection_manager:
                logger.debug(
                    f"Skipping feed update broadcast for {feed_id}: ConnectionManager not available."
                )
                return

            async with self._lock:
                entry = self.process_registry.get(feed_id)
            if not entry:
                logger.warning(
                    f"Feed {feed_id} not found in registry for status update broadcast."
                )
                return

            op_status = entry["status"]
            if isinstance(op_status, str):
                try:
                    op_status = FeedOperationalStatusEnum(op_status.lower())
                except ValueError:
                    op_status = FeedOperationalStatusEnum.ERROR
            feed_status_data = FeedStatusData(
                feed_id=feed_id,
                config=entry.get("config_info")
                or FeedConfigInfo(
                    name=Path(entry["source"]).name
                    if Path(entry["source"]).is_file()
                    or "/" in entry["source"]
                    or "\\" in entry["source"]
                    else "Unknown Feed Name",
                    source_type="video_file"
                    if Path(entry["source"]).suffix
                    else "unknown",
                    source_identifier=entry["source"],
                ),
                source=entry["source"],  # Add the source directly here
                status=op_status,
                current_fps=entry["timer"].get_fps("loop_total")
                if entry.get("timer") and op_status == FeedOperationalStatusEnum.RUNNING
                else None,
                last_error=entry.get("error_message"),
                latest_metrics=entry.get("latest_metrics"),
            )

            ws_payload = FeedStatusUpdate(feed_status_data=feed_status_data)
            message = WebSocketMessage(
                type=WebSocketMessageTypeEnum.FEED_STATUS_UPDATE, data=ws_payload
            )

            # Broadcast to a specific topic for this feed
            topic = f"feed:{feed_id}"
            await self._connection_manager.broadcast_to_topic(message, topic)
            # Also broadcast a general version to a generic "feeds" topic for overview listeners
            # This might be too noisy if many feeds update frequently. Consider if needed.
            # await self._connection_manager.broadcast_message_model(message, specific_topic="feeds_all")
            logger.debug(
                f"Broadcasted feed status update for {feed_id} to topic {topic}. Status: {op_status}"
            )
        except Exception as e:
            logger.error(
                f"Error broadcasting feed update for feed '{feed_id}': {e}",
                exc_info=True,
            )

    async def _broadcast_alert(
        self,
        feed_id: Optional[str],
        severity: AlertSeverityEnum,
        message_text: str,
        details: Optional[Dict[str, Any]] = None,
    ):
        """Sends a new alert via WebSocket manager."""
        if not self._connection_manager:
            logger.debug("Skipping alert broadcast: ConnectionManager not available.")
            return

        alert_model = Alert(
            timestamp=datetime.utcnow(),
            severity=severity,
            feed_id=feed_id,
            message=message_text,
            details=details or {},
        )

        ws_payload = NewAlertNotification(alert_data=alert_model)
        message = WebSocketMessage(
            type=WebSocketMessageTypeEnum.NEW_ALERT_NOTIFICATION, data=ws_payload
        )

        # Broadcast to a general alerts topic, and potentially a feed-specific alert topic
        await self._connection_manager.broadcast_to_topic(
            message, topic="alerts"
        )
        if feed_id:
            await self._connection_manager.broadcast_to_topic(
                message, topic=f"feed_alerts:{feed_id}"
            )

        logger.info(
            f"Broadcasted alert (Severity: {severity.value}, Feed: {feed_id or 'N/A'}): {message_text}"
        )

    async def _broadcast_kpi_update(self):
        """Calculates and broadcasts aggregated KPIs using GlobalRealtimeMetrics."""
        if not self._connection_manager:
            logger.debug("Skipping KPI broadcast: ConnectionManager not available.")
            return

        async with self._lock:
            running_feeds = 0
            error_feeds = 0
            idle_feeds = 0
            all_speeds = []
            congestion_index = 0.0
            active_incidents_kpi = 0  # Placeholder
            total_flow_accumulator = 0  # Initialize total flow accumulator

            for entry in self.process_registry.values():
                current_status_val = entry["status"]
                # Ensure status is an enum for consistent comparison and keying
                current_status_enum: FeedOperationalStatusEnum
                if isinstance(current_status_val, FeedOperationalStatusEnum):
                    current_status_enum = current_status_val
                elif isinstance(current_status_val, str):
                    try:
                        current_status_enum = FeedOperationalStatusEnum(
                            current_status_val.lower()
                        )
                    except ValueError:
                        logger.warning(
                            f"Invalid status string '{current_status_val}' in KPI calculation, treating as ERROR."
                        )
                        current_status_enum = FeedOperationalStatusEnum.ERROR
                else:
                    logger.warning(
                        f"Unknown status type '{type(current_status_val)}' in KPI calculation, treating as ERROR."
                    )
                    current_status_enum = FeedOperationalStatusEnum.ERROR

                if current_status_enum == FeedOperationalStatusEnum.RUNNING:
                    running_feeds += 1
                    metrics = entry.get("latest_metrics")
                    if metrics:
                        if isinstance(metrics.get("avg_speed"), (int, float)):
                            all_speeds.append(float(metrics["avg_speed"]))
                        # Accumulate total_flow from 'vehicle_count' in latest_metrics
                        # This assumes 'vehicle_count' represents the flow for the interval for that feed
                        if isinstance(metrics.get("vehicle_count"), (int, float)):
                            total_flow_accumulator += int(metrics["vehicle_count"])
                elif current_status_enum == FeedOperationalStatusEnum.ERROR:
                    error_feeds += 1
                elif current_status_enum == FeedOperationalStatusEnum.STOPPED:
                    idle_feeds += 1

            avg_speed_kpi = (
                round(float(np.median(all_speeds)), 1) if all_speeds else 0.0
            )
            speed_limit_kpi = self.config.get("speed_limit", 60)
            congestion_thresh = self.config.get("incident_detection", {}).get(
                "congestion_speed_threshold", 20
            )

            if avg_speed_kpi < congestion_thresh and running_feeds > 0:
                congestion_index = round(
                    max(0, min(100, 100 * (1 - (avg_speed_kpi / congestion_thresh)))), 1
                )
            elif speed_limit_kpi > 0 and running_feeds > 0:
                congestion_index = round(
                    max(0, min(100, 100 * (1 - (avg_speed_kpi / speed_limit_kpi)))), 1
                )

            metrics_payload = GlobalRealtimeMetrics(
                metrics_source="FeedManagerGlobalKPIs",
                congestion_index=congestion_index,
                average_speed_kmh=avg_speed_kpi,
                active_incidents_count=active_incidents_kpi,  # Remains placeholder
                total_flow=total_flow_accumulator,  # Add accumulated total flow
                feed_statuses={
                    FeedOperationalStatusEnum.RUNNING.value: running_feeds,
                    FeedOperationalStatusEnum.ERROR.value: error_feeds,
                    FeedOperationalStatusEnum.STOPPED.value: idle_feeds,
                },
            )

        message = WebSocketMessage(
            type=WebSocketMessageTypeEnum.GLOBAL_REALTIME_METRICS_UPDATE,
            data=metrics_payload,
        )
        await self._connection_manager.broadcast_to_topic(
            message, topic="kpis"
        )
        logger.debug(
            f"Broadcasted KPI update: {metrics_payload.model_dump_json(indent=2)}"
        )

    async def handle_start_feed(self, feed_id: str):
        """Handles a request to start a feed."""
        try:
            await self.start_feed(feed_id)
            logger.info(f"Started feed via WS request: {feed_id}")
        except FeedNotFoundError:
            logger.error(f"Feed not found: {feed_id}")
        except FeedOperationError as e:
            logger.error(f"Could not start feed {feed_id}: {e}")

    async def handle_stop_feed(self, feed_id: str):
        """Handles a request to stop a feed."""
        await self.stop_feed(feed_id)
        logger.info(f"Stopped feed via WS request: {feed_id}")

    async def refresh_feed(self, feed_id: str):
        """Refreshes a feed by stopping and then attempting to start it."""
        logger.info(f"Refresh requested for feed: '{feed_id}'")
        original_source = None
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry:
                raise FeedNotFoundError(feed_id)
            original_source = entry["source"]  # Store source before stopping

        if not original_source:  # Should not happen if entry exists
            raise FeedOperationError(f"Cannot refresh {feed_id}, source not found.")

        try:
            logger.debug(f"Stopping '{feed_id}' for refresh...")
            await self.stop_feed(
                feed_id
            )  # This will handle broadcasts and sample check if it was a real feed
            # Wait briefly for resources to release (optional but can help)
            await asyncio.sleep(1.0)
            logger.debug(f"Starting '{feed_id}' after stop for refresh...")
            await self.start_feed(
                feed_id
            )  # This handles resource check, broadcasts, and sample check if it's a real feed
            logger.info(f"Feed '{feed_id}' refresh sequence initiated.")
        except Exception as e:
            logger.error(
                f"Error during refresh sequence for '{feed_id}': {e}", exc_info=True
            )
            # Error status and broadcast handled by start_feed
            raise FeedOperationError(f"Refresh failed for '{feed_id}': {e}") from e

    async def _read_result_queues(self):
        """Background task to read from worker result queues."""
        logger.info("Result queue reader task started.")
        while not self._stop_reader_flag:
            feed_ids_to_update = set()
            kpi_update_needed = False
            sample_feed_check_needed = False
            processed_any_item_in_cycle = False

            active_queues_info = await self._get_active_queues_info()

            # Process Result Queues
            for feed_id, result_q in active_queues_info["result_queues"]:
                (
                    processed_result_queue,
                    kpi_update_needed_local,
                    sample_feed_check_needed_local,
                ) = await self._process_result_queue(
                    feed_id, result_q, feed_ids_to_update
                )
                processed_any_item_in_cycle |= processed_result_queue
                kpi_update_needed |= kpi_update_needed_local
                sample_feed_check_needed |= sample_feed_check_needed_local

            # Process DB Queues
            for feed_id, db_q in active_queues_info["db_queues"]:
                processed_any_item_in_cycle |= await self._process_db_queue(
                    feed_id, db_q
                )

            # Broadcast Updates
            await self._perform_broadcasts(
                feed_ids_to_update, kpi_update_needed, sample_feed_check_needed
            )

            if not processed_any_item_in_cycle:
                # If no items were processed, it means queues were empty or we hit queue.Empty
                # Decrease delay, but not below minimum
                self._current_read_delay = max(self._min_read_delay, self._current_read_delay / self._delay_adjustment_factor)
                await asyncio.sleep(self._current_read_delay)
            else:
                # If items were processed, increase delay slightly to prevent overwhelming
                # This is a simple heuristic; more advanced might consider queue fullness
                self._current_read_delay = min(self._max_read_delay, self._current_read_delay * self._delay_adjustment_factor)
                # Still yield control to event loop briefly
                await asyncio.sleep(0.001) # Smallest possible sleep to yield control

            # Log queue sizes periodically
            current_time = time.time()
            if current_time - self._last_queue_log_time >= self._queue_log_interval:
                for feed_id, result_q in active_queues_info["result_queues"]:
                    try:
                        qsize = result_q.qsize()
                        logger.info(f"Feed '{feed_id}' result queue size: {qsize}. Current read delay: {self._current_read_delay:.4f}s")
                    except NotImplementedError:
                        logger.debug(f"Queue.qsize() not implemented for {feed_id} result queue.")
                    except Exception as e:
                        logger.warning(f"Error getting queue size for {feed_id} result queue: {e}")
                for feed_id, db_q in active_queues_info["db_queues"]:
                    try:
                        qsize = db_q.qsize()
                        logger.info(f"Feed '{feed_id}' DB queue size: {qsize}")
                    except NotImplementedError:
                        logger.debug(f"Queue.qsize() not implemented for {feed_id} DB queue.")
                    except Exception as e:
                        logger.warning(f"Error getting queue size for {feed_id} DB queue: {e}")
                self._last_queue_log_time = current_time

        logger.info("Result queue reader task stopped.")

    async def _get_active_queues_info(self) -> Dict[str, List[Tuple[str, MPQueue]]]:
        """Helper to gather active result and DB queues."""
        active_result_queues: List[Tuple[str, MPQueue]] = []
        active_db_queues: List[Tuple[str, MPQueue]] = []
        async with self._lock:
            for feed_id, entry in self.process_registry.items():
                if entry["status"] in ["running", "starting"]:
                    if entry.get("result_queue"):
                        active_result_queues.append((feed_id, entry["result_queue"]))
                    if entry.get("db_queue"):
                        active_db_queues.append((feed_id, entry["db_queue"]))
        return {"result_queues": active_result_queues, "db_queues": active_db_queues}

    async def _process_result_queue(
        self, feed_id: str, q: MPQueue, feed_ids_to_update: set
    ) -> Tuple[bool, bool, bool]:
        """Helper to process items from a single result queue."""
        processed_items = False
        kpi_update_needed_local = False
        sample_feed_check_needed_local = False
        while True:
            try:
                item = q.get_nowait()
                processed_items = True

                _feed_id, frame_idx, frame_bytes, metrics, _raw_vehicles, timings = item
                if _feed_id == feed_id:
                    async with self._lock:
                        entry = self.process_registry.get(feed_id)
                        if entry:
                            if "timer" not in entry or not entry["timer"]:
                                entry["timer"] = FrameTimer()
                            entry["timer"].update_from_dict(timings)
                            # Ensure timestamp in metrics is timezone-aware (UTC)
                            if "timestamp" in metrics and isinstance(
                                metrics["timestamp"], (int, float)
                            ):
                                metrics["timestamp"] = datetime.fromtimestamp(
                                    metrics["timestamp"], tz=datetime.timezone.utc
                                )
                            elif "timestamp" not in metrics:
                                metrics["timestamp"] = datetime.now(timezone.utc)
                            entry["latest_metrics"] = metrics
                            entry["latest_frame_bytes"] = (
                                frame_bytes  # Store the frame bytes
                            )
                            if entry["status"] == "starting":
                                logger.info(
                                    f"Feed '{feed_id}' transitioned to 'running'."
                                )
                                entry["status"] = "running"
                                feed_ids_to_update.add(feed_id)
                                kpi_update_needed_local = True
                                if not entry.get("is_sample_feed"):
                                    sample_feed_check_needed_local = True
                                    kpi_update_needed_local = True
                                if not entry.get("is_sample_feed"):
                                    sample_feed_check_needed_local = True
                                # Signal the feed running event
                                if feed_id in self._feed_running_events:
                                    self._feed_running_events[feed_id].set()

                    # Broadcast metrics and frame separately with corrected message types
                    await self._broadcast(
                        WebSocketMessageTypeEnum.METRICS_UPDATE,
                        {"feed_id": feed_id, "metrics": metrics},
                    )
                    # Base64 encode the frame for WebSocket transport
                    frame_b64 = base64.b64encode(frame_bytes).decode('utf-8')
                    logger.info(f"Broadcasting VIDEO_FRAME for feed {feed_id}. Frame size: {len(frame_b64)} bytes.")
                    await self._broadcast(
                        WebSocketMessageTypeEnum.VIDEO_FRAME,
                        VideoFrameData(feed_id=feed_id, frame=frame_b64),
                    )

                    if self._analytics_service:
                        try:
                            # Get location info from feed's config_info
                            config_info = entry.get("config_info")
                            if (
                                config_info
                                and config_info.latitude is not None
                                and config_info.longitude is not None
                            ):
                                metrics["latitude"] = config_info.latitude
                                metrics["longitude"] = config_info.longitude
                            else:
                                logger.warning(
                                    f"Feed {feed_id} missing latitude/longitude in config_info. Cannot update cache with location."
                                )

                            await self._analytics_service.process_feed_metrics(
                                feed_id, metrics
                            )
                        except Exception as e:
                            logger.error(
                                f"Error passing latest_metrics to AnalyticsService for feed '{feed_id}': {e}"
                            )
                else:
                    logger.warning(f"Result queue item feed_id mismatch for {feed_id}")

                await asyncio.sleep(self._current_read_delay)
            except queue.Empty:
                break
            except Exception as e:
                logger.error(
                    f"Error processing item from result queue for feed '{feed_id}': {e}",
                    exc_info=True,
                )
                break

        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if entry and entry.get("process"):
                process = entry["process"]
                if not process.is_alive():
                    exitcode = process.exitcode
                    if exitcode == 0:
                        logger.info(
                            f"Process for feed '{feed_id}' exited cleanly (exitcode 0). Status set to STOPPED."
                        )
                        if entry["status"] != FeedOperationalStatusEnum.STOPPED:
                            entry["status"] = FeedOperationalStatusEnum.STOPPED
                            entry["error_message"] = None
                            entry["process"] = None
                            feed_ids_to_update.add(feed_id)
                            kpi_update_needed_local = True
                    else:
                        logger.warning(
                            f"Process for feed '{feed_id}' found dead (is_alive=False, exitcode={exitcode}). Marking as error."
                        )
                        if entry["status"] != FeedOperationalStatusEnum.ERROR:
                            entry["status"] = FeedOperationalStatusEnum.ERROR
                            entry["error_message"] = (
                                f"Process terminated unexpectedly (exitcode: {exitcode})."
                            )
                            entry["process"] = None
                            feed_ids_to_update.add(feed_id)
                            kpi_update_needed_local = True
                            if not entry.get("is_sample_feed"):
                                sample_feed_check_needed_local = True
        return processed_items, kpi_update_needed_local, sample_feed_check_needed_local
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if entry and entry.get("process"):
                process = entry["process"]
                if not process.is_alive():
                    exitcode = process.exitcode
                    if exitcode == 0:
                        logger.info(
                            f"Process for feed '{feed_id}' exited cleanly (exitcode 0). Status set to STOPPED."
                        )
                        if entry["status"] != FeedOperationalStatusEnum.STOPPED:
                            entry["status"] = FeedOperationalStatusEnum.STOPPED
                            entry["error_message"] = None
                            entry["process"] = None
                            feed_ids_to_update.add(feed_id)
                            kpi_update_needed_local = True
                    else:
                        logger.warning(
                            f"Process for feed '{feed_id}' found dead (is_alive=False, exitcode={exitcode}). Marking as error."
                        )
                        if entry["status"] != FeedOperationalStatusEnum.ERROR:
                            entry["status"] = FeedOperationalStatusEnum.ERROR
                            entry["error_message"] = (
                                f"Process terminated unexpectedly (exitcode: {exitcode})."
                            )
                            entry["process"] = None
                            feed_ids_to_update.add(feed_id)
                            kpi_update_needed_local = True
                            if not entry.get("is_sample_feed"):
                                sample_feed_check_needed_local = True
        return processed_items, kpi_update_needed_local, sample_feed_check_needed_local

    async def _process_db_queue(self, feed_id: str, q: MPQueue) -> bool:
        """Helper to process items from a single DB queue."""
        processed_items = False
        while True:
            try:
                vehicle_data = q.get_nowait()
                processed_items = True
                if self._analytics_service:
                    await self._analytics_service.save_vehicle_data(vehicle_data)
                await asyncio.sleep(self._current_read_delay)
                break
            except Exception as e:
                logger.error(
                    f"Error processing item from db_queue for feed '{feed_id}': {e}",
                    exc_info=True,
                )
                break
        return processed_items

    async def _perform_broadcasts(
        self,
        feed_ids_to_update: set,
        kpi_update_needed: bool,
        sample_feed_check_needed: bool,
    ):
        """Helper to perform all necessary broadcasts and sample feed checks."""
        for feed_id_to_update in feed_ids_to_update:
            await self._broadcast_feed_update(feed_id_to_update)

        current_time = time.time()
        if kpi_update_needed or (
            current_time - self._last_kpi_broadcast_time >= self._kpi_broadcast_interval
        ):
            await self._broadcast_kpi_update()
            self._last_kpi_broadcast_time = current_time

        if sample_feed_check_needed:
            await self._check_and_manage_sample_feed()

    async def add_and_start_feed(
        self,
        source: str,
        latitude: float,
        longitude: float,
        name_hint: Optional[str] = None,
        is_looped: bool = True,
    ) -> Dict[str, Any]:
        """Adds a new feed and attempts to start it. Returns feed_id and initial status."""
        async with self._lock:
            self._check_resources()  # Raises ResourceLimitError if limits exceeded

            feed_id = self._generate_feed_id(source, name_hint)
            logger.info(f"Adding new feed: {feed_id} for source: {source}")

            # Initial config for the feed
            feed_config = FeedConfigInfo(
                source=source,
                name_hint=name_hint,
                is_sample=False,  # Manually added feeds are not sample feeds by default
                is_looped=is_looped,
                latitude=latitude
                if latitude is not None
                else 0.0,  # Default to 0.0 if not provided
                longitude=longitude
                if longitude is not None
                else 0.0,  # Default to 0.0 if not provided
                # other config params like resolution_preference, inference_mode can be added here
            )

            self.process_registry[feed_id] = {
                "process": None,
                "result_queue": None,
                "stop_event": None,
                "reduce_fps_event": None,
                "status": FeedOperationalStatusEnum.STARTING,  # Initial status
                "source": source,
                "start_time": None,
                "error_message": None,
                "latest_metrics": None,
                "timer": FrameTimer(),
                "is_sample_feed": False,
                "is_looped_feed": is_looped,
                "config_info": feed_config,
            }

        await self._broadcast_feed_update(
            feed_id
        )  # Broadcast initial 'starting' status

        try:
            await self.start_feed(
                feed_id
            )  # This will further update status and broadcast
            # Status after attempting to start_feed:
            async with self._lock:
                current_status = self.process_registry[feed_id]["status"]
                error_msg = self.process_registry[feed_id]["error_message"]
            return {
                "feed_id": feed_id,
                "status": current_status.value,
                "error": error_msg,
            }
        except Exception as e:
            logger.error(
                f"Failed to start feed {feed_id} immediately after adding: {e}"
            )
            async with self._lock:
                self.process_registry[feed_id]["status"] = (
                    FeedOperationalStatusEnum.ERROR
                )
                self.process_registry[feed_id]["error_message"] = str(e)
            await self._broadcast_feed_update(feed_id)  # Broadcast error status
            # Re-raise or return error status
            # raise FeedOperationError(f"Failed to start feed {feed_id}: {e}") from e
            return {
                "feed_id": feed_id,
                "status": FeedOperationalStatusEnum.ERROR.value,
                "error": str(e),
            }

    async def start_feed(self, feed_id: str):
        """Starts a specific feed if it is stopped."""
        is_sample = False
        started_real_feed = False
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry:
                raise FeedNotFoundError(feed_id)
            if entry["status"] != "stopped":
                raise FeedOperationError(
                    f"Cannot start feed '{feed_id}': Status is '{entry['status']}' (must be 'stopped')."
                )

            # Check resources only if it's NOT the sample feed OR if other feeds are running
            is_sample = entry.get("is_sample_feed", False)
            if not is_sample or self._any_real_feeds_active_unsafe():
                self._check_resources()

            logger.info(f"Starting existing feed: '{feed_id}'")

            # Re-create communication primitives
            entry["result_queue"] = MPQueue(
                maxsize=self.config.get("video_input", {}).get("max_queue_size", 500)
            )
            entry["stop_event"] = Event()
            entry["reduce_fps_event"] = Event()
            entry["status"] = FeedOperationalStatusEnum.STARTING
            entry["start_time"] = time.time()
            entry["error_message"] = None
            entry["latest_metrics"] = None
            entry["timer"] = FrameTimer()

            try:
                self._launch_worker(feed_id, entry["source"])
                logger.info(f"Worker process launched for restarting feed '{feed_id}'.")
                if not is_sample:
                    started_real_feed = True  # Mark that a real feed was started
            except Exception as e:
                logger.error(
                    f"Failed to launch worker for restarting '{feed_id}': {e}",
                    exc_info=True,
                )
                entry["status"] = "error"
                entry["error_message"] = f"Failed to launch process on restart: {e}"
                if entry["result_queue"]:
                    entry["result_queue"].close()
                entry["result_queue"] = None
                entry["stop_event"] = None
                # Don't remove from registry
                await self._broadcast_feed_update(feed_id)  # Broadcast error status
                raise FeedOperationError(
                    f"Failed to launch worker for restarting '{feed_id}'."
                ) from e

        # Broadcast updates and check sample feed outside the lock
        await self._broadcast_feed_update(feed_id)  # Broadcast 'starting' status
        await self._broadcast_kpi_update()  # Update counts
        if started_real_feed:
            await self._check_and_manage_sample_feed()  # Check if sample needs stopping

    async def stop_feed(self, feed_id: str):
        """Stops a specific feed if it is running."""
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry:
                raise FeedNotFoundError(feed_id)

            current_status = entry["status"]

            if current_status not in ["running", "starting", "error"]:
                # Allow stopping feeds in error state for cleanup
                if current_status != "error":
                    raise FeedOperationError(
                        f"Cannot stop feed '{feed_id}': Status is '{current_status}'."
                    )
                else:
                    logger.warning(
                        f"Stopping feed '{feed_id}' already in error state for cleanup."
                    )

            logger.info(f"Stopping feed: '{feed_id}' (Status: {current_status})")
            await self._cleanup_process(
                feed_id
            )  # Updates status to 'stopped' in registry

        # Broadcast updates and check sample feed outside the lock
        await self._broadcast_feed_update(feed_id)  # Broadcast 'stopped' status
        await self._broadcast_kpi_update()  # Update counts
        await self._check_and_manage_sample_feed()

    async def restart_feed(self, feed_id: str):
        """Restarts a feed by stopping and then starting it."""
        logger.info(f"Restart requested for feed: '{feed_id}'")
        original_source = None
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry:
                raise FeedNotFoundError(feed_id)
            original_source = entry["source"]  # Store source before stopping

        if not original_source:  # Should not happen if entry exists
            raise FeedOperationError(f"Cannot restart {feed_id}, source not found.")

        try:
            logger.debug(f"Stopping '{feed_id}' for restart...")
            await self.stop_feed(
                feed_id
            )  # This will handle broadcasts and sample check if it was a real feed
            # Wait briefly for resources to release (optional but can help)
            await asyncio.sleep(1.0)
            logger.debug(f"Starting '{feed_id}' after stop...")
            await self.start_feed(
                feed_id
            )  # This handles resource check, broadcasts, and sample check if it's a real feed
            logger.info(f"Feed '{feed_id}' restart sequence initiated.")
        except Exception as e:
            logger.error(
                f"Error during restart sequence for '{feed_id}': {e}", exc_info=True
            )
            # Mark as error if restart failed midway
            async with self._lock:
                entry = self.process_registry.get(feed_id)
                if (
                    entry and entry["status"] != "stopped"
                ):  # Avoid marking as error if stop succeeded but start failed
                    entry["status"] = "error"
                    entry["error_message"] = f"Restart failed: {e}"
            await self._broadcast_feed_update(feed_id)
            # No need to check sample feed here, start/stop handles it
            raise FeedOperationError(f"Restart failed for '{feed_id}': {e}") from e

    async def stop_all_feeds(self):
        """Stops all active feeds, including sample and real feeds, for a clean shutdown."""
        logger.info("Stopping all active feeds requested.")
        
        feeds_to_stop = []
        async with self._lock:
            # Identify all feeds that are in a stoppable state
            feeds_to_stop = [
                fid for fid, entry in self.process_registry.items()
                if entry["status"] in ["running", "starting", "error"]
            ]
            logger.info(f"Found {len(feeds_to_stop)} feeds to stop: {feeds_to_stop}")

        # Perform cleanup outside the main lock to avoid deadlocks
        if feeds_to_stop:
            # Create a list of coroutine tasks to run them concurrently
            tasks = [self.stop_feed(feed_id) for feed_id in feeds_to_stop]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, result in enumerate(results):
                feed_id = feeds_to_stop[i]
                if isinstance(result, Exception):
                    logger.error(f"Error stopping feed {feed_id} during stop_all: {result}", exc_info=True)
                else:
                    logger.info(f"Successfully stopped feed {feed_id} as part of stop_all.")

        # After all feeds are stopped, a final KPI update might be useful
        await self._broadcast_kpi_update()
        logger.info("Finished stopping all active feeds.")

    def _launch_worker(self, feed_id: str, source: str):
        """Launches the worker process (synchronous part)."""
        # This part MUST remain synchronous as it deals with multiprocessing primitives
        # It's called from within async methods holding the lock

        entry = self.process_registry.get(feed_id)
        if not entry:
            logger.error(f"_launch_worker: No registry entry found for {feed_id}")
            return  # Should not happen if called correctly

        result_queue = entry["result_queue"]
        stop_event = entry["stop_event"]
        reduce_event = entry["reduce_fps_event"]
        vis_options = self.config.get(
            "vis_options_default", {"Tracked Vehicles"}
        )  # Get default vis options

        # Placeholder for error queue (if used, pass it)
        error_queue = (
            None  # Example: MPQueue() if you want workers to report errors separately
        )

        # Worker arguments
        worker_args = (
            source,
            result_queue,
            stop_event,
            None,  # Pass None for alerts_queue, FeedManager handles alerts via results
            self.config,
            feed_id,
            self.config["vehicle_detection"]["confidence_threshold"],
            self.config["vehicle_detection"]["proximity_threshold"],
            self.config["vehicle_detection"]["track_timeout"],
            vis_options,  # Pass default or dynamically configured options
            reduce_event,
            self._global_fps,
            None,  # Pass None for db_queue, DB handled centrally if needed or via results
            error_queue,
            entry.get("config_info"),  # Pass the feed's specific config_info
        )

        process = Process(
            target=process_video,
            args=worker_args,
            daemon=True,
            name=f"Worker-{feed_id}",
        )
        process.start()
        entry["process"] = process
        entry["start_time"] = time.time()  # Update start time
        logger.info(f"Launched process PID {process.pid} for feed '{feed_id}'.")

    def _signal_stop_event(self, feed_id: str, stop_event: Optional[Any]):
        """Signals the stop event for a feed."""
        if stop_event and not stop_event.is_set():
            try:
                stop_event.set()
                logger.debug(f"Stop event set for {feed_id}")
            except Exception as e:
                logger.error(
                    f"Error setting stop event for {feed_id}: {e}", exc_info=True
                )

    async def _join_process(self, feed_id: str, process: Optional[Process]):
        """Joins a process with a timeout, terminating it if needed."""
        if process and process.is_alive():
            pid = process.pid
            logger.debug(f"Joining process {pid} for feed '{feed_id}'...")
            try:
                # Run the blocking join in a thread pool executor
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, process.join, 5.0)  # Timeout 5.0s

                if process.is_alive():
                    self.logger.warning(
                        f"Process {pid} for '{feed_id}' did not exit gracefully after join timeout. Terminating."
                    )
                    await loop.run_in_executor(None, process.terminate)
                    await asyncio.sleep(0.5)  # Give terminate more time
                    if process.is_alive():
                        self.logger.error(
                            f"Process {pid} for '{feed_id}' FAILED TO TERMINATE. Attempting to kill."
                        )
                        # Force kill if terminate failed
                        try:
                            # Use psutil to kill the process tree to ensure all subprocesses are also terminated
                            parent = psutil.Process(pid)
                            for child in parent.children(recursive=True):
                                child.kill()
                            parent.kill()
                            self.logger.info(
                                f"Process {pid} for '{feed_id}' and its children force killed."
                            )
                        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                            self.logger.error(
                                f"Failed to force kill process {pid} for '{feed_id}': {e}"
                            )
                    else:
                        self.logger.info(f"Process {pid} terminated.")
                else:
                    logger.info(f"Process {pid} for '{feed_id}' joined successfully.")
                if process:
                    process.close()
            except Exception as e:
                logger.error(
                    f"Error joining/terminating process {pid} for '{feed_id}': {e}",
                    exc_info=True,
                )
                # Try terminate again if join failed?
                if process:
                    if process.is_alive():
                        process.terminate()
                    process.close()

    def _close_queue(self, feed_id: str, result_queue: Optional[MPQueue]):
        """Drains and closes a queue."""
        if result_queue:
            drained_count = 0
            while True:
                try:
                    result_queue.get_nowait()
                    drained_count += 1
                except queue.Empty:
                    break
                except Exception:
                    break  # Error reading queue
            if drained_count > 0:
                logger.debug(
                    f"Drained {drained_count} items from result queue for {feed_id} during cleanup."
                )
            try:
                result_queue.close()
                result_queue.join_thread()
            except Exception as e:
                logger.error(
                    f"Error closing result queue for {feed_id}: {e}", exc_info=True
                )

    async def _update_registry_status(self, entry, feed_id: str):
        """Update registry status based on process state, called after process joins."""
        # This method is called when a process has finished (either normally or with error)
        # Lock should be acquired by caller if modifying shared state
        process = entry.get("process")
        exitcode = None
        is_alive_after_join = False
        
        if process:
            is_alive_after_join = process.is_alive()
            if not is_alive_after_join:
                exitcode = process.exitcode

        if not is_alive_after_join and exitcode is not None:
            if exitcode == 0:
                if entry["status"] not in [
                    FeedOperationalStatusEnum.STOPPED,
                    FeedOperationalStatusEnum.ERROR,
                ]:  # Avoid overwriting explicit stop/error
                    entry["status"] = (
                        FeedOperationalStatusEnum.STOPPED
                    )  # Or 'COMPLETED' if that state exists
                    entry["error_message"] = entry.get(
                        "error_message"
                    )  # Keep error if worker set one before clean exit
                    self.logger.info(
                        f"Process for feed '{feed_id}' exited cleanly (exitcode 0). Status set to STOPPED."
                    )
            else:
                error_msg = f"Process for feed '{feed_id}' exited with error code: {exitcode}."
                self.logger.error(error_msg)
                if (
                    entry["status"] != FeedOperationalStatusEnum.ERROR
                ):  # Avoid overwriting more specific error
                    entry["status"] = FeedOperationalStatusEnum.ERROR
                    entry["error_message"] = entry.get(
                        "error_message", error_msg
                    )  # Set error message if not already set
        elif is_alive_after_join:
            # Process still alive after join attempt
            self.logger.debug(
                f"Process {feed_id} still alive after join attempt. Status remains {entry['status']}."
            )
        else:
            # Process not alive, but exitcode is None (e.g., terminated by signal)
            error_msg = f"Process for feed '{feed_id}' terminated without explicit exit code."
            self.logger.warning(error_msg)
            if entry["status"] != FeedOperationalStatusEnum.ERROR:
                entry["status"] = FeedOperationalStatusEnum.ERROR
                entry["error_message"] = entry.get("error_message", error_msg)

        # Broadcast final status after process termination
        await self._broadcast_feed_update(feed_id)

    async def _cleanup_process(self, feed_id: str):
        """Stops, joins, and cleans up resources for a specific feed_id. Assumes lock is held."""
        # This method needs to be async if joining the process might block event loop
        # But process.join() itself is blocking. Running in executor?
        needs_sample_check = False  # Flag to check sample feed after releasing lock
        try:
            entry = self.process_registry.get(feed_id)
            if not entry:
                logger.warning(f"Cleanup requested for non-existent feed_id: {feed_id}")
                return

            # Separate declaration and assignment for type checking
            process: Optional[Process] = entry.get("process")
            stop_event = entry.get("stop_event")
            result_queue = entry.get("result_queue")
            status = entry.get("status")
            is_sample = entry.get("is_sample_feed", False)

            logger.debug(
                f"Starting cleanup for {feed_id} (Process: {process.pid if process else 'None'}, Status: {status})"
            )

            self._signal_stop_event(feed_id, stop_event)
            await self._join_process(feed_id, process)

            # Close Process Handle (if supported and process exists)
            self._close_queue(feed_id, result_queue)

            # Update Registry Status (Only if not already stopped - avoid overwriting error state if cleanup failed)
            if entry["status"] != "stopped":
                await self._update_registry_status(entry, feed_id)

            # Clear the running event for this feed
            if feed_id in self._feed_running_events:
                self._feed_running_events[feed_id].clear()

            # Check if a real feed was cleaned up (even from error state)
            # Need to trigger sample feed check if the last real feed is now stopped
            if status in ["running", "starting", "error"] and not is_sample:
                needs_sample_check = True  # Set flag to check after lock release

        except Exception as e:
            logger.error(
                f"Unexpected error during cleanup for feed {feed_id}: {e}",
                exc_info=True,
            )
            # Ensure status is error if cleanup fails badly
            entry = self.process_registry.get(feed_id)
            if entry and entry["status"] != "error":
                entry["status"] = "error"
                entry["error_message"] = f"Cleanup failed: {e}"
                # Attempt to broadcast this error state
                loop = asyncio.get_running_loop()
                loop.call_soon(
                    asyncio.create_task, self._broadcast_feed_update(feed_id)
                )

        # Perform sample check outside the lock if needed
        if needs_sample_check:
            loop = asyncio.get_running_loop()
            loop.call_soon(asyncio.create_task, self._check_and_manage_sample_feed())

    async def shutdown(self):
        """Shuts down the FeedManager and all active feeds."""
        logger.info("FeedManager shutdown initiated.")
        self._stop_reader_flag = True  # Signal reader task to stop

        # Stop all running feeds (including sample)
        await self.stop_all_feeds()  # stop_all now handles sample feed too

        # Wait for the reader task to finish
        if self._result_reader_task:
            try:
                logger.debug("Waiting for result reader task to finish...")
                await asyncio.wait_for(self._result_reader_task, timeout=5.0)
                logger.info("Result reader task finished.")
            except asyncio.TimeoutError:
                logger.warning(
                    "Result reader task did not finish within timeout during shutdown."
                )
            except Exception as e:
                logger.error(f"Error waiting for result reader task: {e}")

        logger.info("FeedManager shutdown complete.")

    async def start_background_tasks(self):
        """Starts the FeedManager's background tasks."""
        self.logger.info("FeedManager background tasks started.")

    # --- Sample Feed Management ---

    def _any_real_feeds_active_unsafe(self) -> bool:
        """Checks if any non-sample feeds are running/starting. Assumes lock is held."""
        for feed_id, entry in self.process_registry.items():
            if not entry.get("is_sample_feed", False) and entry["status"] in [
                "running",
                "starting",
            ]:
                return True
        return False

    async def _check_and_manage_sample_feed(self):
        """
        Determines which sample feeds to start or stop, then executes the actions
        outside of the main lock to prevent deadlocks.
        """
        self.logger.debug(f"_check_and_manage_sample_feed called. Sample feed IDs: {self._sample_feed_ids}")
        if not self._sample_feed_ids:
            logger.debug("Sample feed management check: No sample feeds configured.")
            return

        feeds_to_start = []
        feeds_to_stop = []

        async with self._lock:
            real_feeds_active = self._any_real_feeds_active_unsafe()
            
            running_sample_feeds = [
                feed_id for feed_id in self._sample_feed_ids
                if self.process_registry.get(feed_id, {}).get("status") == FeedOperationalStatusEnum.RUNNING
            ]
            stopped_sample_feeds = [
                feed_id for feed_id in self._sample_feed_ids
                if self.process_registry.get(feed_id, {}).get("status") == FeedOperationalStatusEnum.STOPPED
                or self.process_registry.get(feed_id, {}).get("status") == FeedOperationalStatusEnum.ERROR # Also consider error state as stoppable/startable
            ]

            if real_feeds_active:
                # Explicitly check if there's at least one non-sample feed in the registry
                # This prevents stopping sample feeds if real_feeds_active is true
                # due to some transient state or bug, but no real feeds are actually configured.
                if not any(not entry.get("is_sample_feed", False) for entry in self.process_registry.values()):
                     self.logger.debug("Real feeds reported active, but no non-sample feeds found in registry. Skipping sample feed stop.")
                if running_sample_feeds:
                    self.logger.info(f"Identified {len(running_sample_feeds)} sample feeds to stop as real feeds are active.")
                    feeds_to_stop.extend(running_sample_feeds)
            else:
                current_active_feeds = len([
                    fid for fid, entry in self.process_registry.items()
                    if entry["status"] in [FeedOperationalStatusEnum.RUNNING, FeedOperationalStatusEnum.STARTING]
                ])
                max_feeds = self.config.get("feed_manager", {}).get("max_concurrent_feeds", 10)
                
                # Ensure we only consider non-running sample feeds to start
                feeds_can_start_count = max_feeds - current_active_feeds
                
                if feeds_can_start_count > 0 and stopped_sample_feeds:
                    num_to_start = min(len(stopped_sample_feeds), feeds_can_start_count)
                    self.logger.info(f"Identified {num_to_start} sample feeds to start as no real feeds are active.")
                    feeds_to_start.extend(stopped_sample_feeds[:num_to_start])

        # --- Perform actions outside the lock ---

        if feeds_to_stop:
            self.logger.info(f"Executing stop for {len(feeds_to_stop)} sample feeds.")
            for feed_id in feeds_to_stop:
                try:
                    await self.stop_feed(feed_id)
                except Exception as e:
                    logger.error(f"Error stopping sample feed {feed_id}: {e}", exc_info=True)

        if feeds_to_start:
            self.logger.info(f"Executing start for {len(feeds_to_start)} sample feeds.")
            for feed_id in feeds_to_start:
                try:
                    # Resource check is inside start_feed, which is now called without holding the lock.
                    await self.start_feed(feed_id)
                except ResourceLimitError as e:
                    logger.warning(f"Could not start sample feed {feed_id} due to resource limits: {e}")
                    break  # Stop trying to start more if resource limit is hit
                except Exception as e:
                    logger.error(f"Error starting sample feed {feed_id}: {e}", exc_info=True)

    async def get_feed_running_event(self, feed_id: str) -> asyncio.Event:
        """Returns the asyncio.Event for a given feed_id, creating it if it doesn't exist."""
        async with self._lock:
            if feed_id not in self._feed_running_events:
                self._feed_running_events[feed_id] = asyncio.Event()
            return self._feed_running_events[feed_id]

    async def add_dynamic_sample_feed(self, video_path: str):
        """Dynamically adds a new sample feed to the registry."""
        resolved_path = Path(video_path)
        if not resolved_path.exists():
            self.logger.warning(f"Attempted to add non-existent dynamic sample video: {video_path}")
            return

        async with self._lock:
            # Check if already registered
            for feed_id, entry in self.process_registry.items():
                if entry.get("source") == str(resolved_path) and entry.get("is_sample_feed"):
                    self.logger.info(f"Dynamic sample feed {video_path} already registered as {feed_id}.")
                    return

            feed_name = f"Dynamic Sample Video {len(self._sample_feed_ids) + 1}"
            feed_id = self._generate_feed_id(str(resolved_path), feed_name)
            self.process_registry[feed_id] = {
                "process": None,
                "result_queue": None,
                "stop_event": None,
                "reduce_fps_event": None,
                "status": FeedOperationalStatusEnum.STOPPED,
                "source": str(resolved_path),
                "start_time": None,
                "error_message": None,
                "latest_metrics": None,
                "timer": None,
                "is_sample_feed": True,
                'is_looped_feed': True,
                "config_info": FeedConfigInfo(
                    name=feed_name,
                    source_type="video_file",
                    source_identifier=str(resolved_path),
                    latitude=34.0522,
                    longitude=-118.2437, # Default coordinates
                ),
            }
            self._sample_feed_ids.append(feed_id)
            self.logger.info(f"Dynamically added sample feed '{feed_id}' ({feed_name}).")

        # Trigger a check to potentially start the new feed if conditions are met
        await self._check_and_manage_sample_feed()

    async def remove_feed(self, feed_id: str):
        """Removes a feed from the registry, stopping it first if running."""
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry:
                raise FeedNotFoundError(feed_id)

            # Stop the feed if it's running or starting
            if entry["status"] in ["running", "starting", "error"]:
                logger.info(f"Stopping feed {feed_id} before removal.")
                await self.stop_feed(feed_id)  # This will handle cleanup and broadcast

            del self.process_registry[feed_id]
            # Also remove from _sample_feed_ids if it was a sample feed
            if feed_id in self._sample_feed_ids:
                self._sample_feed_ids.remove(feed_id)
            logger.info(f"Feed {feed_id} removed from registry.")
            # No need to broadcast status update for a removed feed, as stop_feed already broadcasted "stopped"
            # If a "removed" status is needed on the frontend, a new WebSocketMessageTypeEnum would be required.

    async def get_active_incidents(self) -> List[Alert]:
        """Retrieves active incidents from the AnalyticsService."""
        if self._analytics_service:
            return (
                await self._analytics_service.get_active_alerts()
            )  # Assuming AnalyticsService has this method
        else:
            logger.warning(
                "AnalyticsService not set. Cannot retrieve active incidents."
            )
            return []

feed_manager_instance: Optional[FeedManager] = None


async def initialize_feed_manager(config: dict):
    global feed_manager_instance
    if feed_manager_instance is None:
        try:
            feed_manager_instance = FeedManager(config)
            logger.info("FeedManager initialized successfully.")
        except Exception as e:
            logger.critical(f"Failed to initialize FeedManager: {e}", exc_info=True)
            feed_manager_instance = None
            raise RuntimeError(f"FeedManager Initialization Failed: {e}") from e
    return feed_manager_instance


def get_feed_manager() -> FeedManager:
    if feed_manager_instance is None:
        logger.error("FeedManager accessed before initialization!")
        raise RuntimeError("FeedManager not initialized.")
    return feed_manager_instance


async def close_feed_manager():
    global feed_manager_instance
    if feed_manager_instance:
        try:
            logger.info("Closing FeedManager...")
            await feed_manager_instance.shutdown()
            feed_manager_instance = None
        except Exception as e:
            logger.error(f"Error closing FeedManager: {e}")
    else:
        logger.info("FeedManager already closed or not initialized.")