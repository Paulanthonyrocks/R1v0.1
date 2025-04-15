# backend/app/services/feed_manager.py

import asyncio
import logging
import time
import numpy as np
import psutil
import re
from multiprocessing import Process, Queue as MPQueue, Event as MPEvent, Lock, Value, set_start_method, get_start_method
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path
import queue # For queue.Empty exception
import json # For potentially logging complex objects
from datetime import datetime # For alert timestamps

# Import custom exceptions
from .exceptions import FeedNotFoundError, FeedOperationError, ResourceLimitError

# Import Pydantic models (or define similar structure if not using Pydantic internally)
from app.models.feeds import FeedStatus
from app.models.alerts import AlertItem # Import Alert model

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
        #       'timer': FrameTimer | None
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
                    'reduce_fps_event': None, 'status': 'stopped', 'source': str(resolved_path),
                    'start_time': None, 'error_message': None, 'latest_metrics': None, 'timer': None
                }
                logger.info(f"Initialized sample feed '{feed_id}' as stopped.")
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

    async def get_all_statuses(self) -> List[FeedStatus]:
        """Retrieves the status of all feeds."""
        async with self._lock:
            statuses = []
            for feed_id, entry in self.process_registry.items():
                try:
                    status = FeedStatus(
                        id=feed_id,
                        source=entry['source'],
                        status=entry['status'],
                        fps=entry['timer'].get_fps('loop_total')
                        if entry.get('timer') and entry['status'] == 'running'
                        else None,
                        error_message=entry.get('error_message'),
                    )
                    statuses.append(status)
                except Exception as e:
                    logger.error(
                        f"Error creating FeedStatus for feed '{feed_id}': {e}",
                        exc_info=True,
                    )
                    # Consider how to handle errors (e.g., return a default status or skip)
                    # For now, we skip to avoid crashing the entire request

        return statuses


    async def _broadcast_feed_update(self, feed_id: str):
        """Sends feed status update via WebSocket manager."""
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry:
                return
            status_model = FeedStatus(
                id=feed_id,
                source=entry['source'],
                status=entry['status'],
                fps=entry['timer'].get_fps('loop_total') if entry.get('timer') and entry['status'] == 'running' else None,
                error_message=entry.get('error_message')
            )
            data_dict = status_model.dict() # Use .model_dump() in Pydantic v2

        await self._broadcast("feed_update", data_dict) # Use helper
        logger.debug(f"Broadcasted feed update for {feed_id}, Status: {data_dict.get('status')}")

    async def _broadcast_alert(self, feed_id: Optional[str], severity: str, message: str):
        """Sends a new alert via WebSocket manager."""
        # Create AlertItem model for structure and validation
        alert_item = AlertItem(
             timestamp=datetime.utcnow(), # Use UTC time
             severity=severity.upper(),
             feed_id=feed_id,
             message=message
        )
        await self._broadcast("new_alert", alert_item.dict()) # Use helper, broadcast model dict
        logger.debug(f"Broadcasted alert (Severity: {severity}): {message}")

    async def _broadcast_kpi_update(self):
        """Calculates and broadcasts aggregated KPIs."""
        if not self._connection_manager: return

        # Calculate KPIs (similar logic to app.py, but based on FeedManager state)
        async with self._lock: # Lock needed to read registry safely
             running_feeds = 0
             error_feeds = 0
             idle_feeds = 0
             all_speeds = []
             congestion_index = 0.0 # Default

             for entry in self.process_registry.values():
                 if entry['status'] == 'running':
                     running_feeds += 1
                     metrics = entry.get('latest_metrics')
                     if metrics and isinstance(metrics.get('avg_speed'), (int, float)):
                         all_speeds.append(float(metrics['avg_speed']))
                 elif entry['status'] == 'error':
                     error_feeds += 1
                 elif entry['status'] == 'stopped':
                     idle_feeds += 1
                 # 'starting' feeds aren't counted as running or idle for KPIs yet

             # Calculate avg speed (median?) and congestion
             avg_speed_kpi = round(float(np.median(all_speeds)), 1) if all_speeds else 0.0
             speed_limit_kpi = self.config.get('speed_limit', 60)
             congestion_thresh = self.config.get('incident_detection', {}).get('congestion_speed_threshold', 20)

             if avg_speed_kpi < congestion_thresh and running_feeds > 0:
                  congestion_index = round(max(0, min(100, 100 * (1 - (avg_speed_kpi / congestion_thresh)))), 1)
             elif speed_limit_kpi > 0 and running_feeds > 0:
                  congestion_index = round(max(0, min(100, 100 * (1 - (avg_speed_kpi / speed_limit_kpi)))), 1)

             # TODO: Get active incident count (e.g., count recent critical/error alerts from DB or internal deque?)
             active_incidents_kpi = 0 # Placeholder

        kpi_data = {
            "congestion_index": congestion_index,
            "avg_speed": avg_speed_kpi,
            "active_incidents": active_incidents_kpi,
            "feed_status_counts": {
                "running": running_feeds,
                "error": error_feeds,
                "idle": idle_feeds
             }
        }
        await self._broadcast("kpi_update", kpi_data)
        logger.debug(f"Broadcasted KPI update: {kpi_data}")

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
                        if entry and entry.get('process') and not entry['process'].is_alive() and entry['status'] == 'running':
                            logger.warning(f"Process for feed '{feed_id}' found dead. Marking as error.")
                            entry['status'] = 'error'
                            entry['error_message'] = "Process terminated unexpectedly."
                            entry['process'] = None
                            feed_ids_to_update.add(feed_id)
                            kpi_update_needed = True # Feed status count changed
                            # Consider broadcasting an ERROR alert immediately
                            # await self._broadcast_alert(feed_id, "ERROR", entry['error_message']) # Requires moving broadcast call
                    continue
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

                            # TODO: Optionally broadcast per-feed metrics if needed by UI
                            # await self._broadcast("feed_metrics", {"feed_id": feed_id, "metrics": metrics})

                        else:
                            logger.warning(f"Queue item feed_id mismatch for {feed_id}")
                    except Exception as e:
                        logger.error(f"Error processing item from queue for feed '{feed_id}': {e}")

            # --- Broadcast Updates (outside the queue read loop) ---
            # Broadcast individual feed updates if status changed
            for feed_id in feed_ids_to_update:
                await self._broadcast_feed_update(feed_id)

            # Broadcast global KPIs periodically or if status changed
            current_time = time.time()
            if kpi_update_needed or (current_time - self._last_kpi_broadcast_time >= self._kpi_broadcast_interval):
                 await self._broadcast_kpi_update()
                 self._last_kpi_broadcast_time = current_time

            await asyncio.sleep(0.1) # Prevent busy-waiting

        logger.info("Result queue reader task stopped.")

    async def add_and_start_feed(self, source: str, name_hint: Optional[str]) -> str:
        """Adds a new feed source and starts processing."""
        async with self._lock:
            self._check_resources() # Raises ResourceLimitError if limits exceeded

            # Validate source format if needed (e.g., check file exists, webcam format)
            is_webcam = source.startswith("webcam:")
            if not is_webcam and not Path(source).exists():
                 raise ValueError(f"Source path does not exist: {source}")
            # Could add more validation

            feed_id = self._generate_feed_id(source, name_hint)
            logger.info(f"Adding and starting new feed: '{feed_id}' for source: {source}")

            # Prepare resources before launching
            result_queue = MPQueue(maxsize=self.config.get('video_input', {}).get('max_queue_size', 500))
            stop_event = MPEvent()
            reduce_event = MPEvent() # Placeholder

            self.process_registry[feed_id] = {
                'process': None, # Will be set by _launch_worker
                'result_queue': result_queue,
                'stop_event': stop_event,
                'reduce_fps_event': reduce_event,
                'status': 'starting',
                'source': source,
                'start_time': time.time(),
                'error_message': None,
                'latest_metrics': None,
                'timer': FrameTimer() # Init timer immediately
            }

            try:
                 self._launch_worker(feed_id, source)
                 logger.info(f"Worker process launched for feed '{feed_id}'.")
            except Exception as e:
                logger.error(f"Failed to launch worker for '{feed_id}': {e}", exc_info=True)
                # Clean up registry entry if launch failed
                self.process_registry[feed_id]['status'] = 'error'
                self.process_registry[feed_id]['error_message'] = f"Failed to launch process: {e}"
                # Close queues/events?
                if result_queue: result_queue.close()
                raise FeedOperationError(f"Failed to launch worker for '{feed_id}'.") from e
                entry['status'] = 'error' # Update status before broadcast
                await self._broadcast_feed_update(feed_id) # Broadcast error status
                raise FeedOperationError(f"Failed to launch worker for '{feed_id}'.") from e

        await self._broadcast_feed_update(feed_id) # Broadcast 'starting' status
        await self._broadcast_kpi_update() # Update counts
        return feed_id

    async def start_feed(self, feed_id: str):
        """Starts an existing feed that is currently stopped."""
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry:
                raise FeedNotFoundError(feed_id)
            if entry['status'] != 'stopped':
                raise FeedOperationError(f"Cannot start feed '{feed_id}': Status is '{entry['status']}' (must be 'stopped').")

            self._check_resources()
            logger.info(f"Starting existing feed: '{feed_id}'")

            # Re-create communication primitives
            entry['result_queue'] = MPQueue(maxsize=self.config.get('video_input', {}).get('max_queue_size', 500))
            entry['stop_event'] = MPEvent()
            entry['reduce_fps_event'] = MPEvent()
            entry['status'] = 'starting'
            entry['start_time'] = time.time()
            entry['error_message'] = None
            entry['latest_metrics'] = None
            entry['timer'] = FrameTimer()

            try:
                self._launch_worker(feed_id, entry['source'])
                logger.info(f"Worker process launched for restarting feed '{feed_id}'.")
            except Exception as e:
                logger.error(f"Failed to launch worker for restarting '{feed_id}': {e}", exc_info=True)
                entry['status'] = 'error'
                entry['error_message'] = f"Failed to launch process on restart: {e}"
                if entry['result_queue']: entry['result_queue'].close()
                entry['result_queue'] = None
                entry['stop_event'] = None
                raise FeedOperationError(f"Failed to launch worker for restarting '{feed_id}'.") from e
                await self._broadcast_feed_update(feed_id) # Broadcast error status
                raise FeedOperationError(f"Failed to launch worker for restarting '{feed_id}'.") from e

        await self._broadcast_feed_update(feed_id) # Broadcast 'starting' status
        await self._broadcast_kpi_update() # Update counts

    async def stop_feed(self, feed_id: str):
        """Stops a running or starting feed."""
        async with self._lock:
            entry = self.process_registry.get(feed_id)
            if not entry:
                raise FeedNotFoundError(feed_id)
            if entry['status'] not in ['running', 'starting', 'error']:
                # Allow stopping feeds in error state for cleanup
                if entry['status'] != 'error':
                    raise FeedOperationError(f"Cannot stop feed '{feed_id}': Status is '{entry['status']}'.")
                else:
                    logger.warning(f"Stopping feed '{feed_id}' already in error state for cleanup.")

            logger.info(f"Stopping feed: '{feed_id}' (Status: {entry['status']})")
            await self._cleanup_process(feed_id) # Updates status to 'stopped' in registry

        await self._broadcast_feed_update(feed_id) # Broadcast 'stopped' status
        await self._broadcast_kpi_update() # Update counts


    async def restart_feed(self, feed_id: str):
        """Restarts a feed by stopping and then starting it."""
        logger.info(f"Restart requested for feed: '{feed_id}'")
        original_source = None
        async with self._lock:
             entry = self.process_registry.get(feed_id)
             if not entry:
                 raise FeedNotFoundError(feed_id)
             original_source = entry['source'] # Store source before stopping

        if not original_source: # Should not happen if entry exists
             raise FeedOperationError(f"Cannot restart {feed_id}, source not found.")

        try:
            logger.debug(f"Stopping '{feed_id}' for restart...")
            await self.stop_feed(feed_id)
            # Wait briefly for resources to release (optional but can help)
            await asyncio.sleep(1.0)
            logger.debug(f"Starting '{feed_id}' after stop...")
            await self.start_feed(feed_id) # start_feed handles resource check again
            logger.info(f"Feed '{feed_id}' restart sequence initiated.")
        except Exception as e:
            logger.error(f"Error during restart sequence for '{feed_id}': {e}", exc_info=True)
            # Mark as error if restart failed midway
            async with self._lock:
                entry = self.process_registry.get(feed_id)
                if entry:
                    entry['status'] = 'error'
                    entry['error_message'] = f"Restart failed: {e}"
            await self._broadcast_feed_update(feed_id)
            raise FeedOperationError(f"Restart failed for '{feed_id}': {e}") from e


    async def stop_all_feeds(self):
        logger.info("Stopping all active feeds requested.")
        feed_ids_stopped = []
        async with self._lock:
            feed_ids_to_stop = [ fid for fid, entry in self.process_registry.items()
                                 if entry['status'] in ['running', 'starting', 'error'] ]
            logger.info(f"Found {len(feed_ids_to_stop)} feeds to stop: {feed_ids_to_stop}")
            tasks = [self._cleanup_process(feed_id) for feed_id in feed_ids_to_stop]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            feed_ids_stopped = feed_ids_to_stop # Store IDs that were attempted to stop

        # Broadcast updates outside the lock
        for feed_id in feed_ids_stopped:
             await self._broadcast_feed_update(feed_id) # Broadcast stopped status for each
        if feed_ids_stopped: # If any feeds were stopped, update KPIs
            await self._broadcast_kpi_update()
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


    async def _cleanup_process(self, feed_id: str):
        """Stops, joins, and cleans up resources for a specific feed_id. Assumes lock is held."""
        # This method needs to be async if joining the process might block event loop
        # But process.join() itself is blocking. Running in executor?

        entry = self.process_registry.get(feed_id)
        if not entry:
            logger.warning(f"Cleanup requested for non-existent feed_id: {feed_id}")
            return

        process: Optional[Process] = entry.get('process')
        stop_event: Optional[MPEvent] = entry.get('stop_event')
        result_queue: Optional[MPQueue] = entry.get('result_queue')
        status = entry.get('status')

        logger.debug(f"Starting cleanup for {feed_id} (Process: {process.pid if process else 'None'}, Status: {status})")

        # 1. Signal Stop Event
        if stop_event and not stop_event.is_set():
            try:
                stop_event.set()
                logger.debug(f"Stop event set for {feed_id}")
            except Exception as e:
                logger.error(f"Error setting stop event for {feed_id}: {e}")

        # 2. Join Process (potentially blocking - run in executor)
        if process and process.is_alive():
            pid = process.pid
            logger.debug(f"Joining process {pid} for feed '{feed_id}'...")
            try:
                # Run the blocking join in a thread pool executor
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, process.join, 1.5) # Timeout 1.5s

                if process.is_alive():
                    logger.warning(f"Process {pid} for '{feed_id}' did not exit gracefully after join timeout. Terminating.")
                    await loop.run_in_executor(None, process.terminate)
                    await asyncio.sleep(0.2) # Give terminate time
                    if process.is_alive():
                        logger.error(f"Process {pid} for '{feed_id}' FAILED TO TERMINATE.")
                    else:
                         logger.info(f"Process {pid} terminated.")
                else:
                    logger.info(f"Process {pid} for '{feed_id}' joined successfully.")
            except Exception as e:
                logger.error(f"Error joining/terminating process {pid} for '{feed_id}': {e}")
                # Try terminate again if join failed?
                if process.is_alive(): process.terminate()


        # 3. Close Process Handle (if supported and process exists)
        if process:
             try: process.close()
             except Exception as e: logger.warning(f"Error closing process handle for {feed_id}: {e}")

        # 4. Drain and Close Queue
        if result_queue:
            drained_count = 0
            while True:
                try: result_queue.get_nowait(); drained_count += 1
                except queue.Empty: break
                except Exception: break # Error reading queue
            if drained_count > 0: logger.debug(f"Drained {drained_count} items from result queue for {feed_id} during cleanup.")
            try: result_queue.close(); result_queue.join_thread()
            except Exception as e: logger.warning(f"Error closing result queue for {feed_id}: {e}")

        # 5. Update Registry Status
        entry['status'] = 'stopped'
        entry['process'] = None
        entry['result_queue'] = None
        entry['stop_event'] = None
        entry['reduce_fps_event'] = None
        entry['start_time'] = None
        entry['latest_metrics'] = None
        # Keep 'source', 'error_message' (if any previous error occurred)
        # Keep 'timer' object? Or reset it? Resetting seems cleaner.
        entry['timer'] = None
        logger.debug(f"Registry updated to 'stopped' for {feed_id}")


    async def shutdown(self):
        """Shuts down the FeedManager and all active feeds."""
        logger.info("FeedManager shutdown initiated.")
        self._stop_reader_flag = True # Signal reader task to stop

        # Stop all running feeds
        await self.stop_all_feeds()

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