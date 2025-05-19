# backend/app/services/feed_manager.py

import asyncio
import logging
import time
import numpy as np
import psutil
import re
from multiprocessing import Process, Queue as MPQueue, Event, Lock, Value, set_start_method, get_start_method
from typing import Dict, Any, Optional, List, Tuple, Type

# Create type alias for Event to use in type hints
from enum import Enum

class FeedStatus(Enum):
    """Enum to represent the possible states of a feed."""

    RUNNING = 'running'
    STOPPED = 'stopped'
    STARTING = 'starting'
    ERROR = 'error'


MPEvent = Type[Event]
from pathlib import Path
import queue # For queue.Empty exception
import json # For potentially logging complex objects
from datetime import datetime # For alert timestamps

# Import custom exceptions
from .exceptions import FeedNotFoundError, FeedOperationError, ResourceLimitError

# Import Pydantic models
from app.models.feeds import FeedStatusData, FeedConfigInfo, FeedOperationalStatusEnum # Updated import for FeedStatusData
from app.models.alerts import Alert, AlertSeverityEnum # Updated import for Alert
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum, FeedStatusUpdate, NewAlertNotification, GeneralNotification, GlobalRealtimeMetrics # New imports

# Import core worker and utilities (adjust path as needed)
from app.core.processing_worker import process_video
from app.utils.utils import check_system_resources, FrameTimer # Assuming these are in utils

# Import WebSocket Manager type for hinting (will be implemented later)
from app.websocket.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)

# Ensure start method is set (important for multiprocessing)
try:
    if get_start_method(allow_none=True) is None:
        set_start_method('spawn')
    logger.info(f"Multiprocessing start method: {get_start_method()}")
except Exception as e:
    logger.warning(f"Could not set multiprocessing start method ('spawn'): {e}")
    
class FeedManager:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        # Process registry structure:
        # { feed_id: {
        #       'process': Process | None,
        #       'result_queue': MPQueue | None,
        #       'stop_event': MPEvent | None,
        #       'reduce_fps_event': MPEvent | None, # If dynamic scaling used
        #       'status': str ("stopped", "starting", "running", "error"),
        #       'source': str,
        #       'start_time': float | None,
        #       'error_message': str | None,
        #       'latest_metrics': Dict | None,
        #       'timer': FrameTimer | None,
        #       'is_sample_feed': bool, # Added flag
        #       'is_looped_feed': bool # New flag
        #   }
        # }
        self.process_registry: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock() # Use asyncio lock for async methods managing the registry
        self._global_fps = Value('i', config.get('fps', 30)) # Shared value for workers
        self._feed_id_counter = 1 # Simple counter for unique IDs
        self._stop_reader_flag = False
        self._result_reader_task: Optional[asyncio.Task] = None
        self._connection_manager: Optional[ConnectionManager] = None # Added type hint
        self._last_kpi_broadcast_time = 0.0
        self._kpi_broadcast_interval = 1.0 # Seconds
        self._sample_feed_id: Optional[str] = None # Store the ID of the sample feed

        # Load available feeds from config if needed (or assume they are added dynamically)
        self._initialize_available_feeds()

        # Start the background task to read results
        self._result_reader_task = asyncio.create_task(self._read_result_queues())
        logger.info("FeedManager initialized and result reader task started.")

    def set_connection_manager(self, manager): # manager: ConnectionManager): # Add type hint later
        """Inject the WebSocket ConnectionManager."""
        self._connection_manager = manager
        logger.info("WebSocket ConnectionManager set in FeedManager.")

    def _initialize_available_feeds(self):
        # Example: Add sample feed from config if it exists
        sample_path_str = self.config.get('video_input',{}).get('sample_video')
        if sample_path_str:
            resolved_path = Path(sample_path_str) # Assuming load_config resolved it
            if resolved_path.exists():
                feed_id = self._generate_feed_id(str(resolved_path), "Sample")
                # Add to registry with 'stopped' status initially
                self.process_registry[feed_id] = {
                    'process': None, 'result_queue': None, 'stop_event': None,
                    'reduce_fps_event': None, 'status': FeedOperationalStatusEnum.STOPPED, 'source': str(resolved_path),
                    'start_time': None, 'error_message': None, 'latest_metrics': None, 'timer': None,
                    'is_sample_feed': True, # Mark as sample feed
                    'is_looped_feed': True,
                    'config_info': FeedConfigInfo(source=str(resolved_path), name_hint="Sample Video", is_sample=True, is_looped=True) # Store config info
                }
                self._sample_feed_id = feed_id # Store the sample feed ID
                logger.info(f"Initialized sample feed '{feed_id}' as {FeedOperationalStatusEnum.STOPPED}.")
            else:
                logger.warning(f"Sample video path configured but not found: {resolved_path}")

    def _generate_feed_id(self, source: str, name_hint: Optional[str] = None) -> str:
        """Generates a unique Feed ID."""
        # Simple generation logic, enhance as needed
        if name_hint:
            base_name = re.sub(r'[^\w\-]+', '_', name_hint)
        elif source.startswith("webcam:"):
            base_name = f"Webcam_{source.split(':')[1]}"
        else:
            base_name = re.sub(r'[^\w\-]+', '_', Path(source).stem)

        feed_id = f"Feed_{self._feed_id_counter}_{base_name}"
        while feed_id in self.process_registry:
            self._feed_id_counter += 1
            feed_id = f"Feed_{self._feed_id_counter}_{base_name}"
        self._feed_id_counter += 1
        return feed_id

    def _check_resources(self):
        """Checks if system resources allow starting a new feed."""
        limit = self.config.get('performance', {}).get('memory_limit_percent', 80)
        cpu, mem = check_system_resources() # Assumes check_system_resources is available
        if mem >= limit:
            logger.warning(f"Resource limit reached: Memory Usage {mem:.1f}% >= Limit {limit}%.")
            raise ResourceLimitError(f"Memory usage ({mem:.1f}%) exceeds limit ({limit}%). Cannot start new feed.")
        # Add CPU check if desired
        logger.debug(f"Resource check passed: CPU={cpu:.1f}%, Memory={mem:.1f}% (Limit={limit}%)")

    async def _broadcast(self, message_type: str, data: Dict):
        """Helper to broadcast safely."""
        if self._connection_manager:
            await self._connection_manager.broadcast({"type": message_type, "data": data})
        else:
            logger.debug(f"Broadcast skipped (No WS Manager): Type={message_type}")

    async def get_all_statuses(self) -> List[FeedStatusData]:
        """Retrieves the status of all feeds."""
        async with self._lock:
            statuses = []
            for feed_id, entry in self.process_registry.items():
                try:
                    # Construct FeedStatusData directly
                    # Assuming 'status' in entry is already FeedOperationalStatusEnum or string equivalent
                    op_status = entry['status']
                    if isinstance(op_status, str):
                        try:
                            op_status = FeedOperationalStatusEnum(op_status.lower())
                        except ValueError:
                            logger.warning(f"Invalid status string '{op_status}' for feed {feed_id}, defaulting to ERROR")
                            op_status = FeedOperationalStatusEnum.ERROR

                    status_data = FeedStatusData(
                        feed_id=feed_id,
                        config=entry.get('config_info', FeedConfigInfo(source=entry['source'])), # Use stored or default config_info
                        status=op_status,
                        current_fps=entry['timer'].get_fps('loop_total')
                        if entry.get('timer') and op_status == FeedOperationalStatusEnum.RUNNING
                        else None,
                        last_error=entry.get('error_message'),
                        # Add other fields as available/necessary, e.g., uptime, processed_frames
                        latest_metrics=entry.get('latest_metrics') # Assuming this is a dict of relevant metrics
                    )
                    statuses.append(status_data)
                except Exception as e:
                    logger.error(
                        f"Error creating FeedStatusData for feed '{feed_id}': {e}",
                        exc_info=True,
                    )
                    # Consider how to handle errors (e.g., return a default status or skip)
                    # For now, we skip to avoid crashing the entire request

        return statuses


    async def _broadcast_feed_update(self, feed_id: str):
        """Sends feed status update via WebSocket manager."""
        if not self._connection_manager:
            logger.debug(f"Skipping feed update broadcast for {feed_id}: ConnectionManager not available.")
            return

        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry:
                logger.warning(f"Feed {feed_id} not found in registry for status update broadcast.")
                return

            op_status = entry['status']
            if isinstance(op_status, str):
                try:
                    op_status = FeedOperationalStatusEnum(op_status.lower())
                except ValueError:
                    op_status = FeedOperationalStatusEnum.ERROR

            feed_status_data = FeedStatusData(
                feed_id=feed_id,
                config=entry.get('config_info', FeedConfigInfo(source=entry['source'])),
                status=op_status,
                current_fps=entry['timer'].get_fps('loop_total')
                if entry.get('timer') and op_status == FeedOperationalStatusEnum.RUNNING
                else None,
                last_error=entry.get('error_message'),
                latest_metrics=entry.get('latest_metrics')
            )
            
            ws_payload = FeedStatusUpdate(feed_data=feed_status_data)
            message = WebSocketMessage(
                event_type=WebSocketMessageTypeEnum.FEED_STATUS_UPDATE,
                payload=ws_payload
            )
            
        # Broadcast to a specific topic for this feed
        topic = f"feed:{feed_id}"
        await self._connection_manager.broadcast_message_model(message, specific_topic=topic)
        # Also broadcast a general version to a generic "feeds" topic for overview listeners
        # This might be too noisy if many feeds update frequently. Consider if needed.
        # await self._connection_manager.broadcast_message_model(message, specific_topic="feeds_all")
        logger.debug(f"Broadcasted feed status update for {feed_id} to topic {topic}. Status: {op_status}")

    async def _broadcast_alert(self, feed_id: Optional[str], severity: AlertSeverityEnum, message_text: str, details: Optional[Dict[str, Any]] = None):
        """Sends a new alert via WebSocket manager."""
        if not self._connection_manager:
            logger.debug(f"Skipping alert broadcast: ConnectionManager not available.")
            return

        alert_model = Alert(
             timestamp=datetime.utcnow(),
             severity=severity,
             feed_id=feed_id,
             message=message_text,
             details=details or {}
        )
        
        ws_payload = NewAlertNotification(alert_data=alert_model)
        message = WebSocketMessage(
            event_type=WebSocketMessageTypeEnum.NEW_ALERT_NOTIFICATION,
            payload=ws_payload
        )

        # Broadcast to a general alerts topic, and potentially a feed-specific alert topic
        await self._connection_manager.broadcast_message_model(message, specific_topic="alerts")
        if feed_id:
            await self._connection_manager.broadcast_message_model(message, specific_topic=f"feed_alerts:{feed_id}")

        logger.info(f"Broadcasted alert (Severity: {severity.value}, Feed: {feed_id or 'N/A'}): {message_text}")

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
             active_incidents_kpi = 0 # Placeholder

             for entry in self.process_registry.values(): 
                 current_status_val = entry['status']
                 # Ensure status is an enum for consistent comparison and keying
                 current_status_enum: FeedOperationalStatusEnum
                 if isinstance(current_status_val, FeedOperationalStatusEnum):
                     current_status_enum = current_status_val
                 elif isinstance(current_status_val, str):
                     try:
                         current_status_enum = FeedOperationalStatusEnum(current_status_val.lower())
                     except ValueError:
                         logger.warning(f"Invalid status string '{current_status_val}' in KPI calculation, treating as ERROR.")
                         current_status_enum = FeedOperationalStatusEnum.ERROR
                 else:
                     logger.warning(f"Unknown status type '{type(current_status_val)}' in KPI calculation, treating as ERROR.")
                     current_status_enum = FeedOperationalStatusEnum.ERROR

                 if current_status_enum == FeedOperationalStatusEnum.RUNNING: 
                     running_feeds += 1 
                     metrics = entry.get('latest_metrics') 
                     if metrics and isinstance(metrics.get('avg_speed'), (int, float)): 
                         all_speeds.append(float(metrics['avg_speed'])) 
                 elif current_status_enum == FeedOperationalStatusEnum.ERROR: 
                     error_feeds += 1 
                 elif current_status_enum == FeedOperationalStatusEnum.STOPPED: 
                     idle_feeds += 1 
             
             avg_speed_kpi = round(float(np.median(all_speeds)), 1) if all_speeds else 0.0 
             speed_limit_kpi = self.config.get('speed_limit', 60) 
             congestion_thresh = self.config.get('incident_detection', {}).get('congestion_speed_threshold', 20) 

             if avg_speed_kpi < congestion_thresh and running_feeds > 0: 
                 congestion_index = round(max(0, min(100, 100 * (1 - (avg_speed_kpi / congestion_thresh)))), 1) 
             elif speed_limit_kpi > 0 and running_feeds > 0: 
                 congestion_index = round(max(0, min(100, 100 * (1 - (avg_speed_kpi / speed_limit_kpi)))), 1) 
            
            metrics_payload = GlobalRealtimeMetrics(
                metrics_source="FeedManagerGlobalKPIs",
                congestion_index=congestion_index,
                average_speed_kmh=avg_speed_kpi,
                active_incidents_count=active_incidents_kpi, 
                feed_statuses={
                    FeedOperationalStatusEnum.RUNNING.value: running_feeds,
                    FeedOperationalStatusEnum.ERROR.value: error_feeds,
                    FeedOperationalStatusEnum.STOPPED.value: idle_feeds 
                }
            )
        
        message = WebSocketMessage(
            event_type=WebSocketMessageTypeEnum.GLOBAL_REALTIME_METRICS_UPDATE,
            payload=metrics_payload
        )
        await self._connection_manager.broadcast_message_model(message, specific_topic="kpis")
        logger.debug(f"Broadcasted KPI update: {metrics_payload.model_dump_json(indent=2)}")

    async def handle_start_feed(self, feed_id: str):
        """Handles a request to start a feed."""
        try:
            await self.start_feed(feed_id)
            logger.info(f"Started feed via WS request: {feed_id}")
        except FeedNotFoundError as e:
            logger.error(f"Feed not found: {feed_id}")
        except FeedOperationError as e:
            logger.error(f"Could not start feed {feed_id}: {e}")

    async def handle_stop_feed(self, feed_id: str):
        """Handles a request to stop a feed."""
        await self.stop_feed(feed_id)
        logger.info(f"Stopped feed via WS request: {feed_id}")

    async def _read_result_queues(self):
        """Background task to read from worker result queues."""
        logger.info("Result queue reader task started.")
        while not self._stop_reader_flag:
            active_queues: List[Tuple[str, MPQueue]] = []
            feed_ids_to_update = set() # Track feeds needing a status broadcast
            kpi_update_needed = False
            sample_feed_check_needed = False # Flag to check sample feed status later

            async with self._lock: # Lock briefly to get active queues
                for feed_id, entry in self.process_registry.items():
                    if entry['status'] in ['running', 'starting'] and entry.get('result_queue'):
                        active_queues.append((feed_id, entry['result_queue']))

            for feed_id, q in active_queues:
                try:
                    last_item = None
                    while True: # Drain
                         item = q.get_nowait()
                         last_item = item
                except queue.Empty:
                    # Check if process died while queue empty
                    async with self._lock:
                        entry = self.process_registry.get(feed_id)
                        if entry and entry.get('process'):
                            process = entry['process']
                            # Check if process is alive without blocking
                            if not process.is_alive():
                                exitcode = process.exitcode
                                logger.warning(f"Process for feed '{feed_id}' found dead (is_alive=False, exitcode={exitcode}). Marking as error.")
                                if entry['status'] != 'error': # Avoid redundant updates/checks
                                    entry['status'] = 'error'
                                    entry['error_message'] = f"Process terminated unexpectedly (exitcode: {exitcode})."
                                    entry['process'] = None # Clear process handle
                                    feed_ids_to_update.add(feed_id)
                                    kpi_update_needed = True
                                    if not entry.get('is_sample_feed'):
                                        sample_feed_check_needed = True # Real feed died, check sample
                            # Optional: Check via wait(0) if is_alive() is unreliable on some platforms
                            # elif entry['status'] == 'running' and process.wait(0) is not None:
                            #     logger.warning(f"Process for feed '{feed_id}' found dead (confirmed by wait(0)). Marking as error.")
                            #     entry['status'] = 'error'
                            #     entry['error_message'] = "Process terminated unexpectedly."
                            #     entry['process'] = None
                            #     feed_ids_to_update.add(feed_id)
                            #     kpi_update_needed = True # Feed status count changed
                    continue # Go to next queue if this one is empty
                except Exception as e:
                    logger.error(f"Error reading queue for feed '{feed_id}': {e}")
                    continue

                if last_item:
                    try:
                        _feed_id, frame_idx, _frame, metrics, _raw_vehicles, timings = last_item
                        if _feed_id == feed_id:
                            async with self._lock:
                                entry = self.process_registry.get(feed_id)
                                if entry:
                                    if 'timer' not in entry or not entry['timer']:
                                         entry['timer'] = FrameTimer()
                                    entry['timer'].update_from_dict(timings)
                                    entry['latest_metrics'] = metrics
                                    if entry['status'] == 'starting':
                                        logger.info(f"Feed '{feed_id}' transitioned to 'running'.")
                                        entry['status'] = 'running'
                                        feed_ids_to_update.add(feed_id)
                                        kpi_update_needed = True # Feed status count changed
                                        # If a real feed just started, check sample feed status
                                        if not entry.get('is_sample_feed'):
                                            sample_feed_check_needed = True

                            await self._broadcast("feed_metrics", {"feed_id": feed_id, "metrics": metrics})

                        else:
                            logger.warning(f"Queue item feed_id mismatch for {feed_id}")
                    except Exception as e:
                        logger.error(f"Error processing item from queue for feed '{feed_id}': {e}")

            # --- Broadcast Updates (outside the queue read loop) ---
            # Broadcast individual feed updates if status changed
            for feed_id_to_update in feed_ids_to_update:
                await self._broadcast_feed_update(feed_id_to_update)

            # Broadcast global KPIs periodically or if status changed
            current_time = time.time()
            if kpi_update_needed or (current_time - self._last_kpi_broadcast_time >= self._kpi_broadcast_interval):
                 await self._broadcast_kpi_update()
                 self._last_kpi_broadcast_time = current_time

            # Check if sample feed needs starting/stopping
            if sample_feed_check_needed:
                await self._check_and_manage_sample_feed()

            await asyncio.sleep(0.1) # Prevent busy-waiting

        logger.info("Result queue reader task stopped.")

    async def add_and_start_feed(self, source: str, name_hint: Optional[str] = None, is_looped: bool = True) -> Dict[str, Any]:
        """Adds a new feed and attempts to start it. Returns feed_id and initial status."""
        async with self._lock:
            self._check_resources() # Raises ResourceLimitError if limits exceeded

            feed_id = self._generate_feed_id(source, name_hint)
            logger.info(f"Adding new feed: {feed_id} for source: {source}")
            
            # Initial config for the feed
            feed_config = FeedConfigInfo(
                source=source,
                name_hint=name_hint,
                is_sample=False, # Manually added feeds are not sample feeds by default
                is_looped=is_looped,
                # other config params like resolution_preference, inference_mode can be added here
            )

            self.process_registry[feed_id] = {
                'process': None, 
                'result_queue': None, 
                'stop_event': None,
                'reduce_fps_event': None, 
                'status': FeedOperationalStatusEnum.STARTING, # Initial status
                'source': source, 
                'start_time': None, 
                'error_message': None,
                'latest_metrics': None,
                'timer': FrameTimer(),
                'is_sample_feed': False, 
                'is_looped_feed': is_looped,
                'config_info': feed_config
            }
        
        await self._broadcast_feed_update(feed_id) # Broadcast initial 'starting' status

        try:
            await self.start_feed(feed_id) # This will further update status and broadcast
            # Status after attempting start_feed:
            async with self._lock:
                current_status = self.process_registry[feed_id]['status']
                error_msg = self.process_registry[feed_id]['error_message']
            return {"feed_id": feed_id, "status": current_status.value, "error": error_msg}
        except Exception as e:
            logger.error(f"Failed to start feed {feed_id} immediately after adding: {e}")
            async with self._lock:
                self.process_registry[feed_id]['status'] = FeedOperationalStatusEnum.ERROR
                self.process_registry[feed_id]['error_message'] = str(e)
            await self._broadcast_feed_update(feed_id) # Broadcast error status
            # Re-raise or return error status
            # raise FeedOperationError(f"Failed to start feed {feed_id}: {e}") from e
            return {"feed_id": feed_id, "status": FeedOperationalStatusEnum.ERROR.value, "error": str(e)}

    async def start_feed(self, feed_id: str):
        """Starts a specific feed if it is stopped."""
        is_sample = False
        started_real_feed = False
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry:
                raise FeedNotFoundError(feed_id)
            if entry['status'] != 'stopped':
                raise FeedOperationError(f"Cannot start feed '{feed_id}': Status is '{entry['status']}' (must be 'stopped').")

            # Check resources only if it's NOT the sample feed OR if other feeds are running
            is_sample = entry.get('is_sample_feed', False)
            if not is_sample or self._any_real_feeds_active_unsafe():
                self._check_resources()

            logger.info(f"Starting existing feed: '{feed_id}'")

            # Re-create communication primitives
            entry['result_queue'] = MPQueue(maxsize=self.config.get('video_input', {}).get('max_queue_size', 500))
            entry['stop_event'] = MPEvent()
            entry['reduce_fps_event'] = MPEvent()
            entry['status'] = FeedOperationalStatusEnum.STARTING
            entry['start_time'] = time.time()
            entry['error_message'] = None
            entry['latest_metrics'] = None
            entry['timer'] = FrameTimer()

            try:
                self._launch_worker(feed_id, entry['source'])
                logger.info(f"Worker process launched for restarting feed '{feed_id}'.")
                if not is_sample:
                    started_real_feed = True # Mark that a real feed was started
            except Exception as e:
                logger.error(f"Failed to launch worker for restarting '{feed_id}': {e}", exc_info=True)
                entry['status'] = 'error'
                entry['error_message'] = f"Failed to launch process on restart: {e}"
                if entry['result_queue']: entry['result_queue'].close()
                entry['result_queue'] = None
                entry['stop_event'] = None
                # Don't remove from registry
                await self._broadcast_feed_update(feed_id) # Broadcast error status
                raise FeedOperationError(f"Failed to launch worker for restarting '{feed_id}'.") from e

        # Broadcast updates and check sample feed outside the lock
        await self._broadcast_feed_update(feed_id) # Broadcast 'starting' status
        await self._broadcast_kpi_update() # Update counts
        if started_real_feed:
             await self._check_and_manage_sample_feed() # Check if sample needs stopping

    async def stop_feed(self, feed_id: str):
        """Stops a specific feed if it is running."""
        stopped_real_feed = False
        is_sample = False
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry:
                raise FeedNotFoundError(feed_id)

            is_sample = entry.get('is_sample_feed', False)
            current_status = entry['status']

            if current_status not in ['running', 'starting', 'error']:
                # Allow stopping feeds in error state for cleanup
                if current_status != 'error':
                    raise FeedOperationError(f"Cannot stop feed '{feed_id}': Status is '{current_status}'.")
                else:
                    logger.warning(f"Stopping feed '{feed_id}' already in error state for cleanup.")

            logger.info(f"Stopping feed: '{feed_id}' (Status: {current_status})")
            await self._cleanup_process(feed_id) # Updates status to 'stopped' in registry

            # Check if a real feed was stopped
            if current_status in ['running', 'starting'] and not is_sample:
                stopped_real_feed = True

        # Broadcast updates and check sample feed outside the lock
        await self._broadcast_feed_update(feed_id) # Broadcast 'stopped' status
        await self._broadcast_kpi_update() # Update counts
        if stopped_real_feed:
            await self._check_and_manage_sample_feed() # Check if sample needs starting

    async def restart_feed(self, feed_id: str):
        """Restarts a feed by stopping and then starting it."""
        logger.info(f"Restart requested for feed: '{feed_id}'")
        original_source = None
        is_sample = False
        async with self._lock:
             entry = self.process_registry.get(feed_id)
             if not entry:
                 raise FeedNotFoundError(feed_id)
             original_source = entry['source'] # Store source before stopping
             is_sample = entry.get('is_sample_feed', False)

        if not original_source: # Should not happen if entry exists
             raise FeedOperationError(f"Cannot restart {feed_id}, source not found.")

        try:
            logger.debug(f"Stopping '{feed_id}' for restart...")
            await self.stop_feed(feed_id) # This will handle broadcasts and sample check if it was a real feed
            # Wait briefly for resources to release (optional but can help)
            await asyncio.sleep(1.0)
            logger.debug(f"Starting '{feed_id}' after stop...")
            await self.start_feed(feed_id) # This handles resource check, broadcasts, and sample check if it's a real feed
            logger.info(f"Feed '{feed_id}' restart sequence initiated.")
        except Exception as e:
            logger.error(f"Error during restart sequence for '{feed_id}': {e}", exc_info=True)
            # Mark as error if restart failed midway
            async with self._lock:
                entry = self.process_registry.get(feed_id)
                if entry and entry['status'] != 'stopped': # Avoid marking as error if stop succeeded but start failed
                    entry['status'] = 'error'
                    entry['error_message'] = f"Restart failed: {e}"
            await self._broadcast_feed_update(feed_id)
            # No need to check sample feed here, start/stop handles it
            raise FeedOperationError(f"Restart failed for '{feed_id}': {e}") from e

    async def stop_all_feeds(self):
        logger.info("Stopping all active feeds requested.")
        feed_ids_stopped = []
        stopped_real_feed = False
        async with self._lock:
            # Stop only non-sample feeds first
            feed_ids_to_stop = [ fid for fid, entry in self.process_registry.items()
                                 if entry['status'] in ['running', 'starting', 'error'] and not entry.get('is_sample_feed') ]
            logger.info(f"Found {len(feed_ids_to_stop)} non-sample feeds to stop: {feed_ids_to_stop}")
            if feed_ids_to_stop:
                stopped_real_feed = True
                tasks = [self._cleanup_process(feed_id) for feed_id in feed_ids_to_stop]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                feed_ids_stopped.extend(feed_ids_to_stop) # Store IDs that were attempted to stop

                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        feed_id = feed_ids_to_stop[i]
                        logger.error(f"Error stopping feed {feed_id}: {result}", exc_info=True)
                        # Status is likely already 'error' or cleanup failed, broadcast happens below

            # Now check if sample feed needs stopping (it might have been running)
            if self._sample_feed_id and self.process_registry[self._sample_feed_id]['status'] in ['running', 'starting', 'error']:
                 logger.info(f"Stopping sample feed '{self._sample_feed_id}' as part of stop_all.")
                 try:
                     await self._cleanup_process(self._sample_feed_id)
                     feed_ids_stopped.append(self._sample_feed_id)
                 except Exception as e:
                     logger.error(f"Error stopping sample feed {self._sample_feed_id}: {e}", exc_info=True)


        # Broadcast updates outside the lock
        for feed_id in feed_ids_stopped:
             await self._broadcast_feed_update(feed_id) # Broadcast stopped status for each

        if feed_ids_stopped: # If any feeds were stopped, update KPIs
            await self._broadcast_kpi_update()

        # After stopping real feeds, check if sample needs starting (it should)
        if stopped_real_feed:
             await self._check_and_manage_sample_feed()

        logger.info("Finished stopping all active feeds.")

    def _launch_worker(self, feed_id: str, source: str):
        """Launches the worker process (synchronous part)."""
        # This part MUST remain synchronous as it deals with multiprocessing primitives
        # It's called from within async methods holding the lock

        entry = self.process_registry.get(feed_id)
        if not entry:
            logger.error(f"_launch_worker: No registry entry found for {feed_id}")
            return # Should not happen if called correctly

        result_queue = entry['result_queue']
        stop_event = entry['stop_event']
        reduce_event = entry['reduce_fps_event']
        vis_options = self.config.get('vis_options_default', {"Tracked Vehicles"}) # Get default vis options

        # Placeholder for error queue (if used, pass it)
        error_queue = None # Example: MPQueue() if you want workers to report errors separately

        # Worker arguments
        worker_args = (
            source, result_queue, stop_event, None, # Pass None for alerts_queue, FeedManager handles alerts via results
            self.config, feed_id,
            self.config['vehicle_detection']['confidence_threshold'],
            self.config['vehicle_detection']['proximity_threshold'],
            self.config['vehicle_detection']['track_timeout'],
            vis_options, # Pass default or dynamically configured options
            reduce_event, self._global_fps,
            None, # Pass None for db_queue, DB handled centrally if needed or via results
            error_queue,
        )

        process = Process(
            target=process_video,
            args=worker_args,
            daemon=True,
            name=f"Worker-{feed_id}"
        )
        process.start()
        entry['process'] = process
        entry['start_time'] = time.time() # Update start time
        logger.info(f"Launched process PID {process.pid} for feed '{feed_id}'.")


    def _signal_stop_event(self, feed_id: str, stop_event: Optional[MPEvent]):
        """Signals the stop event for a feed."""
        if stop_event and not stop_event.is_set():
            try:
                stop_event.set()
                logger.debug(f"Stop event set for {feed_id}")
            except Exception as e:
                logger.error(f"Error setting stop event for {feed_id}: {e}", exc_info=True)


    async def _join_process(self, feed_id: str, process: Optional[Process]):
        """Joins a process with a timeout, terminating it if needed."""
        if process and process.is_alive():
            pid = process.pid
            logger.debug(f"Joining process {pid} for feed '{feed_id}'...")
            try:
                # Run the blocking join in a thread pool executor
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, process.join, 1.5)  # Timeout 1.5s

                if process.is_alive():
                    logger.warning(
                        f"Process {pid} for '{feed_id}' did not exit gracefully after join timeout. Terminating.")
                    await loop.run_in_executor(None, process.terminate)
                    await asyncio.sleep(0.2)  # Give terminate time
                    if process.is_alive():
                        logger.error(f"Process {pid} for '{feed_id}' FAILED TO TERMINATE.")
                    else:
                        logger.info(f"Process {pid} terminated.")
                else:
                    logger.info(f"Process {pid} for '{feed_id}' joined successfully.")
            except Exception as e:
                logger.error(f"Error joining/terminating process {pid} for '{feed_id}': {e}", exc_info=True)
                # Try terminate again if join failed?
                if process.is_alive():
                    process.terminate()

    def _close_queue(self, feed_id: str, result_queue: Optional[MPQueue]):
        """Drains and closes a queue."""
        if result_queue:
            drained_count = 0
            while True:
                try:
                    result_queue.get_nowait();
                    drained_count += 1
                except queue.Empty:
                    break
                except Exception:
                    break  # Error reading queue
            if drained_count > 0:
                logger.debug(
                    f"Drained {drained_count} items from result queue for {feed_id} during cleanup.")
            try:
                result_queue.close();
                result_queue.join_thread()
            except Exception as e:
                logger.error(f"Error closing result queue for {feed_id}: {e}", exc_info=True)

    def _update_registry_status(self, entry, feed_id: str):
        """Update registry status based on process state, called after process joins."""
        # This method is called when a process has finished (either normally or with error)
        # Lock should be acquired by caller if modifying shared state
        if entry['process'] and entry['process'].exitcode is not None:
            if entry['process'].exitcode == 0:
                if entry['status'] not in [FeedOperationalStatusEnum.STOPPED, FeedOperationalStatusEnum.ERROR]: # Avoid overwriting explicit stop/error
                    entry['status'] = FeedOperationalStatusEnum.STOPPED # Or 'COMPLETED' if that state exists
                    entry['error_message'] = entry.get('error_message') # Keep error if worker set one before clean exit
                    logger.info(f"Process for feed '{feed_id}' exited cleanly (exitcode 0). Status set to STOPPED.")
            else:
                error_msg = f"Process for feed '{feed_id}' exited with error code: {entry['process'].exitcode}."
                logger.error(error_msg)
                if entry['status'] != FeedOperationalStatusEnum.ERROR: # Avoid overwriting more specific error
                    entry['status'] = FeedOperationalStatusEnum.ERROR
                    entry['error_message'] = entry.get('error_message', error_msg) # Prefer existing error message if worker set one

            # Broadcast final status after process termination
            await self._broadcast_feed_update(feed_id)
        elif entry['status'] == FeedOperationalStatusEnum.RUNNING: # Process still alive but stop was requested
             # This case might be covered by stop_feed logic itself.
             # If stop_event was set and process is still alive here, it means it hasn't exited yet.
             # The status should be STOPPING or already STOPPED by the stop_feed method.
             pass

    async def _cleanup_process(self, feed_id: str):
        #Stops, joins, and cleans up resources for a specific feed_id. Assumes lock is held.\"\"\"
        # This method needs to be async if joining the process might block event loop
        # But process.join() itself is blocking. Running in executor?
        needs_sample_check = False # Flag to check sample feed after releasing lock
        try:
            entry = self.process_registry.get(feed_id)
            if not entry:
                logger.warning(f"Cleanup requested for non-existent feed_id: {feed_id}")
                return

            # Separate declaration and assignment for type checking
            process: Optional[Process] = entry.get('process')
            stop_event = entry.get('stop_event')
            result_queue = entry.get('result_queue')
            status = entry.get('status')
            is_sample = entry.get('is_sample_feed', False)

            logger.debug(f"Starting cleanup for {feed_id} (Process: {process.pid if process else 'None'}, Status: {status})")

            self._signal_stop_event(feed_id, stop_event)
            await self._join_process(feed_id, process)

            # Close Process Handle (if supported and process exists)
            if process:
                try:
                    process.close()
                except Exception as e:
                    logger.error(f"Error closing process handle for {feed_id}: {e}", exc_info=True)

            self._close_queue(feed_id, result_queue)

            # 5. Update Registry Status (Only if not already stopped - avoid overwriting error state if cleanup failed)
            if entry['status'] != 'stopped':
                await self._update_registry_status(entry, feed_id)

            # Check if a real feed was cleaned up (even from error state)
            # Need to trigger sample feed check if the last real feed is now stopped
            if status in ['running', 'starting', 'error'] and not is_sample:
                 needs_sample_check = True # Set flag to check after lock release

        except Exception as e:    
            logger.error(f"Unexpected error during cleanup for feed {feed_id}: {e}", exc_info=True)
            # Ensure status is error if cleanup fails badly
            entry = self.process_registry.get(feed_id)
            if entry and entry['status'] != 'error':
                 entry['status'] = 'error'
                 entry['error_message'] = f"Cleanup failed: {e}"
                 # Attempt to broadcast this error state
                 loop = asyncio.get_running_loop()
                 loop.call_soon(asyncio.create_task, self._broadcast_feed_update(feed_id))

        # Perform sample check outside the lock if needed
        if needs_sample_check:
            loop = asyncio.get_running_loop()
            loop.call_soon(asyncio.create_task, self._check_and_manage_sample_feed())


    async def shutdown(self):
        """Shuts down the FeedManager and all active feeds."""
        logger.info("FeedManager shutdown initiated.")
        self._stop_reader_flag = True # Signal reader task to stop

        # Stop all running feeds (including sample)
        await self.stop_all_feeds() # stop_all now handles sample feed too

        # Wait for the reader task to finish
        if self._result_reader_task:
            try:
                logger.debug("Waiting for result reader task to finish...")
                await asyncio.wait_for(self._result_reader_task, timeout=5.0)
                logger.info("Result reader task finished.")
            except asyncio.TimeoutError:
                logger.warning("Result reader task did not finish within timeout during shutdown.")
            except Exception as e:
                logger.error(f"Error waiting for result reader task: {e}")

        logger.info("FeedManager shutdown complete.")


    # --- Sample Feed Management ---

    def _any_real_feeds_active_unsafe(self) -> bool:
        """Checks if any non-sample feeds are running/starting. Assumes lock is held."""
        for feed_id, entry in self.process_registry.items():
            if not entry.get('is_sample_feed', False) and entry['status'] in ['running', 'starting']:
                return True
        return False

    async def _check_and_manage_sample_feed(self):
        """Starts or stops the sample feed based on the status of real feeds."""
        if not self._sample_feed_id:
            logger.debug("Sample feed management check: No sample feed configured.")
            return

        feed_id_to_stop = None
        feed_id_to_start = None

        async with self._lock:
            sample_entry = self.process_registry.get(self._sample_feed_id)
            if not sample_entry:
                logger.error(f"Sample feed ID {self._sample_feed_id} not found in registry during check.")
                return

            sample_status = sample_entry['status']
            real_feeds_active = any(
                entry['status'] in ['running', 'starting'] and not entry.get('is_sample_feed', False)
                for entry in self.process_registry.values()
            )

            if real_feeds_active and sample_status == 'running':
                logger.info(f"Stopping sample feed '{self._sample_feed_id}' as real feeds are active.")
                feed_id_to_stop = self._sample_feed_id

            elif not real_feeds_active and sample_status == 'stopped':
                logger.info(f"Starting sample feed '{self._sample_feed_id}' as no real feeds are active.")
                feed_id_to_start = self._sample_feed_id

        if feed_id_to_stop:
            try:
                await self.stop_feed(feed_id_to_stop)
            except Exception as e:
                logger.error(f"Error stopping sample feed: {e}", exc_info=True)

        if feed_id_to_start:
            try:
                await self.start_feed(feed_id_to_start)
            except Exception as e:
                logger.error(f"Error starting sample feed: {e}", exc_info=True)