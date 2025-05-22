import os
import cv2
import logging
import time
# import numpy as np # F401: Unused import
import queue
from multiprocessing import Queue as MPQueue, Event as MPEvent, Value
# Ensure Union is imported, Tuple might not be needed if already handled
from typing import Dict, Optional, Set, Union # Removed Tuple if not used
from pathlib import Path

try:
    # Import necessary components from utils.py
    from ..utils.utils import FrameTimer, TrafficMonitor, visualize_data, FrameReader, ConfigError
    # LOG_PATH is defined in app.py, get it via config if needed or handle logging differently
    # For simplicity, we might re-fetch the path from config inside the function if needed
except ImportError as e:
    # Use print as logger might not be configured yet
    print(
        f"Error importing from utils.py in processing_worker: {e}. Ensure utils.py is in the Python path.")
    # Define dummy classes/functions if import fails

    class FrameTimer:
        def __init__(self, *args, **kwargs): self.timings = {}
        def log_time(self, *args, **kwargs): pass
        def get_avg(self, *args, **kwargs): return 0
        def get_fps(self, *args, **kwargs): return 0
        def update_from_dict(self, *args, **kwargs): pass

    class TrafficMonitor:
        def __init__(self, *args, **kwargs): pass
        def update_vehicles(self, *args, **kwargs): pass
        def get_metrics(self, *args, **kwargs): return {}

    # Return original frame
    def visualize_data(*args, **kwargs): return args[0]

    class FrameReader:
        def __init__(self, *args, **kwargs): # Added proper __init__
            self.cap = None
            self.end_of_video = True
            # Placeholder for actual initialization if needed for dummy
            if args and kwargs: # Basic check to avoid unused var warning
                pass


        def read(self): return None, None
        def stop(self): pass
        def isOpened(self): return False

    class ConfigError(Exception):
        pass
    # Fallback log path if config doesn't provide one
    LOG_PATH = './logs/worker.log'


try:
    from ..core.core_module import CoreModule
except ImportError as e:
    print(
        f"Error importing CoreModule in processing_worker: {e}. Ensure core_module.py is present.")
    CoreModule = None


# --- Process Video Function ---
def process_video(
    video_path: str, frame_queue: MPQueue, stop_event: MPEvent, alerts_queue: MPQueue,
    config: Dict, feed_id: str, confidence_threshold: float, proximity_threshold: int,
    track_timeout: int, vis_options: Set[str], reduce_frame_rate_event: MPEvent,
    global_fps: Value, db_queue: Optional[MPQueue] = None, error_queue: Optional[MPQueue] = None,
) -> None:
    # Configure logging specific to this process
    log_level_str = config.get('logging', {}).get('level', 'INFO').upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    # Get log path from config, fallback to local if not found
    log_path_worker = config.get('logging', {}).get('log_path', './logs/worker.log')
    Path(log_path_worker).parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"Process-{feed_id}")
    logger.setLevel(log_level)
    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s - %(process)d - %(levelname)s - %(message)s")
        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        logger.addHandler(sh)
        try:
            fh = logging.FileHandler(log_path_worker)
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        except Exception as log_e:
            logger.error(f"Failed to create file handler for worker log {log_path_worker}: {log_e}")
    logger.propagate = False
    logger.info(f"Process {os.getpid()} started for {feed_id} ({video_path}) with log level {log_level_str}")

    reader = None
    core_module = None
    timer = FrameTimer()
    consecutive_core_errors = 0
    MAX_CONSECUTIVE_CORE_ERRORS = 10

    try:
        target_resolution = tuple(config['vehicle_detection']['frame_resolution'])
        source_type = 'video'
        source_location: Union[str, int] = video_path

        if isinstance(video_path, str) and video_path.startswith("webcam:"):
            source_type = 'webcam'
            try:
                source_location = int(video_path.split(':')[1])
                logger.info(f"Identified webcam source with index: {source_location}")
            except (IndexError, ValueError) as e:
                logger.error(f"[{feed_id}] Invalid webcam format '{video_path}'. Defaulting to 0. Error: {e}")
                source_location = 0
        elif video_path == "webcam": # Legacy support
            source_type = 'webcam'
            source_location = config['video_input'].get('webcam_index', 0)
            logger.warning(f"[{feed_id}] Legacy 'webcam' source. Using index {source_location}.")

        logger.info(f"Initializing FrameReader for {source_type}: {source_location}")
        reader = FrameReader(source_location, fps=config.get('fps'),
                             buffer_size=config['video_input'].get('webcam_buffer_size', 1))
        time.sleep(config['interface'].get('camera_warmup_time', 0.5))

        if not reader or not reader.cap or not reader.cap.isOpened():
            error_msg = f"[{feed_id}] FrameReader failed for source: {source_location}. Worker stopping."
            logger.error(error_msg)
            if error_queue: error_queue.put(error_msg)
            raise RuntimeError(error_msg)

        logger.info(f"FrameReader initialized for {feed_id}")

        if CoreModule is None: raise ImportError("CoreModule could not be imported.")
        logger.info(f"Initializing CoreModule for {feed_id}")
        core_module = CoreModule(
            feed_id=feed_id,
            gemini_api_key=config['ocr_engine'].get('gemini_api_key'),
            model_path=config['vehicle_detection']['model_path'], config=config,
            fps=config.get('fps', 30),
            db_queue=db_queue
        )
        logger.info(f"CoreModule initialized for {feed_id}")
        traffic_monitor = TrafficMonitor(config)

        frame_count_processed = 0
        last_log_time = time.time()
        base_frame_skip_interval = max(1, config['vehicle_detection'].get('skip_frames', 1))
        dynamic_skip_interval = base_frame_skip_interval

        while not stop_event.is_set():
            loop_start_time = time.time()
            read_start_time = time.time()
            frame, current_frame_index = reader.read()
            timer.log_time('read', time.time() - read_start_time)

            if frame is None:
                if reader.end_of_video:
                    logger.info(f"[{feed_id}] End of video/stream detected.")
                    break
                time.sleep(0.01)
                continue

            if reduce_frame_rate_event.is_set():
                dynamic_skip_interval = min(base_frame_skip_interval * 3, 90)
            elif dynamic_skip_interval != base_frame_skip_interval:
                dynamic_skip_interval = base_frame_skip_interval

            if current_frame_index is not None and current_frame_index % dynamic_skip_interval != 0:
                timer.log_time('loop_total', time.time() - loop_start_time)
                continue

            frame_count_processed += 1
            try:
                if frame.shape[1] != target_resolution[0] or frame.shape[0] != target_resolution[1]:
                    processing_frame = cv2.resize(frame, target_resolution, interpolation=cv2.INTER_LINEAR)
                else:
                    processing_frame = frame
            except Exception as e:
                logger.error(f"[{feed_id}] Error resizing frame {current_frame_index}: {e}. Skip.")
                continue

            detect_start_time = time.time()
            tracked_vehicles_raw = {}
            try:
                if core_module:
                    tracked_vehicles_raw = core_module.detect_and_track(
                        processing_frame, frame_index=current_frame_index,
                        confidence_threshold=confidence_threshold,
                        proximity_threshold=proximity_threshold,
                        track_timeout=track_timeout
                    )
                    consecutive_core_errors = 0
                else:
                    logger.error(f"[{feed_id}] CoreModule not init for frame {current_frame_index}.")
                    if error_queue: error_queue.put(f"[{feed_id}] CoreModule not initialized.")
                    time.sleep(1)
                    continue
            except Exception as core_err:
                logger.error(f"[{feed_id}] Core Error frame {current_frame_index}: {core_err}",
                             exc_info=(log_level <= logging.DEBUG))
                if error_queue: error_queue.put(f"[{feed_id}] Core Error: {core_err}")
                consecutive_core_errors += 1
                if consecutive_core_errors >= MAX_CONSECUTIVE_CORE_ERRORS:
                    crit_msg = f"[{feed_id}] Max core errors ({MAX_CONSECUTIVE_CORE_ERRORS}). Stop."
                    logger.critical(crit_msg)
                    if error_queue: error_queue.put(crit_msg)
                    stop_event.set()
                    break
                continue
            timer.log_time('detect_track', time.time() - detect_start_time)

            if consecutive_core_errors == 0:
                traffic_monitor.update_vehicles(tracked_vehicles_raw)

            metrics = traffic_monitor.get_metrics()
            metrics['frame_index'] = current_frame_index
            density = metrics.get('vehicles_per_lane', {})

            vis_start_time = time.time()
            combined_frame = visualize_data(
                frame=processing_frame, tracked_vehicles=tracked_vehicles_raw,
                density=density, alerts_queue=alerts_queue,
                visualization_options=vis_options, config=config,
                debug_mode=(log_level <= logging.DEBUG), feed_id=feed_id
            )
            timer.log_time('visualize', time.time() - vis_start_time)

            if combined_frame is None:
                logger.warning(f"[{feed_id}] Vis returned None for frame {current_frame_index}.")
                combined_frame = processing_frame

            queue_put_start_time = time.time()
            output_data = (feed_id, current_frame_index, combined_frame,
                           metrics, tracked_vehicles_raw, timer.timings.copy())
            try:
                if frame_queue.full(): # Non-blocking check
                    try: frame_queue.get_nowait() # Drop oldest
                    except queue.Empty: pass
                frame_queue.put_nowait(output_data)
            except queue.Full:
                logger.error(f"[{feed_id}] Output Q FULL after drop! Frame {current_frame_index} lost.")
            except Exception as q_put_err:
                logger.error(f"[{feed_id}] Error putting frame {current_frame_index} to Q: {q_put_err}")
            timer.log_time('queue_put', time.time() - queue_put_start_time)

            timer.log_time('loop_total', time.time() - loop_start_time)

            current_time_log = time.time() # Renamed to avoid conflict
            if current_time_log - last_log_time > 10.0:
                qsize_approx = -1
                try: qsize_approx = frame_queue.qsize()
                except NotImplementedError: pass
                log_msg = (
                    f"[{feed_id}] Frame ~{current_frame_index}. "
                    f"Avg Loop: {timer.get_avg('loop_total')*1000:.1f}ms "
                    f"(~{timer.get_fps('loop_total'):.1f} FPS). "
                    f"Read={timer.get_avg('read')*1000:.1f}, "
                    f"Detect={timer.get_avg('detect_track')*1000:.1f}, "
                    f"Vis={timer.get_avg('visualize')*1000:.1f}, "
                    f"Put={timer.get_avg('queue_put')*1000:.1f} (ms). "
                    f"OutQ: ~{qsize_approx}. Skip: {dynamic_skip_interval}. "
                    f"CoreErrs: {consecutive_core_errors}"
                )
                logger.info(log_msg)
                last_log_time = current_time_log

    except KeyboardInterrupt:
        logger.warning(f"[{feed_id}] KeyboardInterrupt. Stopping worker.")
        stop_event.set()
    except RuntimeError as e:
        if not stop_event.is_set(): stop_event.set()
    except ImportError as e:
        error_msg = f"[{feed_id}] FATAL Import Error: {e}. Worker cannot run."
        logger.critical(error_msg, exc_info=True)
        if error_queue: error_queue.put(error_msg)
        if not stop_event.is_set(): stop_event.set()
    except Exception as e:
        error_msg = f"[{feed_id}] FATAL Unhandled Error in process loop: {e}"
        logger.critical(error_msg, exc_info=True)
        if error_queue: error_queue.put(error_msg)
        if not stop_event.is_set(): stop_event.set()
    finally:
        pid = os.getpid()
        logger.info(f"[{feed_id}] Cleaning up process {pid}...")
        if not stop_event.is_set():
            logger.warning(f"[{feed_id}] Stop event not set during cleanup, setting now.")
            stop_event.set()
        if reader:
            try:
                logger.info(f"[{feed_id}] Stopping FrameReader...")
                reader.stop()
                logger.info(f"[{feed_id}] FrameReader stopped.")
            except Exception as read_stop_err:
                logger.error(f"[{feed_id}] Error stopping FrameReader: {read_stop_err}", exc_info=True)
        else: logger.info(f"[{feed_id}] FrameReader not initialized, skipping stop.")

        if core_module:
            try:
                logger.info(f"[{feed_id}] Cleaning up CoreModule...")
                core_module.cleanup()
                logger.info(f"[{feed_id}] CoreModule cleaned up.")
            except Exception as core_clean_err:
                logger.error(f"[{feed_id}] Error cleaning up CoreModule: {core_clean_err}", exc_info=True)
        else: logger.info(f"[{feed_id}] CoreModule not initialized, skipping cleanup.")

        drained_count = 0
        try:
            while not frame_queue.empty():
                frame_queue.get_nowait()
                drained_count += 1
        except queue.Empty: pass
        except Exception as q_drain_err:
            logger.warning(f"[{feed_id}] Error draining output queue: {q_drain_err}")
        if drained_count > 0: logger.debug(f"[{feed_id}] Drained {drained_count} items from output Q.")

        logger.info(f"[{feed_id}] Process {pid} terminated. Processed ~{frame_count_processed} frames.")
        logging.shutdown()
