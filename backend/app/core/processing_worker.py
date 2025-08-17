import os
import cv2
import gc
import logging
import time
import numpy as np
import queue
from typing import Dict, Optional, Set, Tuple, Union, TYPE_CHECKING, Any
from multiprocessing import Queue as MPQueue
import multiprocessing
from datetime import datetime, timezone

if TYPE_CHECKING:
    import numpy as np
from pathlib import Path

logger = logging.getLogger("Process") # Define logger at module level

try:
    # Import necessary components from their respective modules
    from ..utils.video import FrameTimer, FrameReader
    from ..utils.monitoring import TrafficMonitor
    from ..utils.visualization import visualize_data
    from ..utils.config import ConfigError
    # from ..utils import DEFAULT_CONFIG # Not directly used here, but might be in other parts of the worker
    # LOG_PATH is defined in app.py, get it via config if needed or handle logging differently
    # For simplicity, we might re-fetch the path from config inside the function if needed
except ImportError as e:
    # Use print as logger might not be configured yet
    print(
        f"Error importing from utils.py in processing_worker: {e}. Ensure utils.py is in the Python path."
    )

    # Define dummy classes/functions if import fails
    class FrameTimer:
        def __init__(self, *args, **kwargs):
            self.timings = {}

        def log_time(self, *args, **kwargs):
            pass

        def get_avg(self, *args, **kwargs):
            return 0

        def get_fps(self, *args, **kwargs):
            return 0

        def update_from_dict(self, *args, **kwargs):
            pass

    class TrafficMonitor:
        def __init__(self, *args, **kwargs):
            pass

        def update_vehicles(self, *args, **kwargs):
            pass

        def get_metrics(self, *args, **kwargs):
            return {}

    def visualize_data(*args, **kwargs):
        return args[0]  # Return original frame

    class FrameReader:
        def __init__(self, *args, **kwargs):
            self.cap = None
            self.end_of_video = True

        def read(self):
            return None, None

        def stop(self):
            pass

        def isOpened(self):
            return False  # Simulate failure if import failed

    class ConfigError(Exception):
        pass

    # Fallback log path if config doesn't provide one
    LOG_PATH = "./logs/worker.log"


try:
    from ..core.core_module import CoreModule
except ImportError as e:
    print(
        f"Error importing CoreModule in processing_worker: {e}. Ensure core_module.py is present."
    )
    CoreModule = None


# --- Helper Functions for process_video ---
def _read_frame(feed_id: str, reader: Any, stop_event: Any, logger: logging.Logger) -> Tuple[Optional[int], Optional[np.ndarray]]:
    try:
        result = reader.read()
        if result is None or not isinstance(result, tuple) or len(result) != 2:
            if reader.end_of_video:
                logger.info(f"[{feed_id}] End of video/stream detected by reader, or reader stopped. Exiting worker loop.")
                stop_event.set()
            else:
                logger.debug(f"[{feed_id}] FrameReader.read() returned no frame (queue empty). Retrying...")
                if stop_event.is_set():
                    logger.info(f"[{feed_id}] Stop event set while waiting for frame. Exiting worker loop.")
            return None, None

        current_frame_index, frame = result

        if not isinstance(current_frame_index, int) or not isinstance(frame, np.ndarray):
            logger.warning(f"[{feed_id}] Invalid frame data received from reader. Index type: {type(current_frame_index)}, Frame type: {type(frame)}. Skipping.")
            return None, None

        return current_frame_index, frame
    except Exception as e:
        logger.error(f"[{feed_id}] Exception in _read_frame: {e}", exc_info=True)
        stop_event.set()
        return None, None

def _preprocess_frame_for_detection(feed_id: str, frame: np.ndarray, current_frame_index: int, target_resolution: Tuple[int, int], logger: logging.Logger, frame_queue: Any, max_queue_size: int, dynamic_skip_interval: int, base_frame_skip_interval: int, performance_config: Dict[str, Any]) -> Optional[np.ndarray]:
    if not isinstance(frame, np.ndarray) or frame.size == 0:
        logger.warning(f"[{feed_id}] Invalid frame provided for preprocessing at index {current_frame_index}. Type: {type(frame)}. Skipping.")
        return None
 
    if not np.isscalar(current_frame_index):
        logger.warning(f"[{feed_id}] Non-scalar frame index: current_frame_index={current_frame_index}. Skipping frame.")
        return None

    # Dynamic frame skipping based on frame_queue fullness
    queue_fill_ratio = frame_queue.qsize() / max_queue_size if max_queue_size > 0 else 0
    dynamic_skip_interval = _adjust_skip_interval(dynamic_skip_interval, base_frame_skip_interval, queue_fill_ratio, performance_config)

    try:
        # Ensure frame is 3 channels (BGR) before resizing
        if len(frame.shape) == 3 and frame.shape[2] == 4:
            logger.debug(f"[{feed_id}] Frame {current_frame_index} has 4 channels, converting to BGR.")
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        elif len(frame.shape) == 2:
            logger.debug(f"[{feed_id}] Frame {current_frame_index} is grayscale, converting to BGR.")
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

        if frame.shape[1] != target_resolution[0] or frame.shape[0] != target_resolution[1]:
            processing_frame = cv2.resize(frame, target_resolution, interpolation=cv2.INTER_LINEAR)
        else:
            processing_frame = frame
        return processing_frame
    except cv2.error as e:
        logger.error(f"[{feed_id}] OpenCV error preparing/resizing frame {current_frame_index}: {e}. Shape: {frame.shape}. Skip.")
        return None
    except Exception as e:
        logger.error(f"[{feed_id}] Generic error preparing/resizing frame {current_frame_index}: {e}. Shape: {frame.shape}. Skip.")
        return None

def _adjust_skip_interval(current_interval: int, base_interval: int, queue_fill_ratio: float, performance_config: Dict[str, Any]) -> int:
    """Adjusts the frame skip interval based on queue fullness and performance config."""
    min_skip = performance_config.get("min_global_skip_factor", 1)
    max_skip = performance_config.get("max_global_skip_factor", 10)
    increase_step = performance_config.get("skip_factor_increase_step", 1)
    decrease_step = performance_config.get("skip_factor_decrease_step", 1)
    queue_fullness_threshold = performance_config.get("queue_fullness_threshold_for_skip_increase", 0.8)

    if queue_fill_ratio > queue_fullness_threshold: # If queue is over the threshold
        return min(current_interval + increase_step, max_skip) # Increase skip
    elif queue_fill_ratio < 0.5 and current_interval > base_interval: # If queue is below 50% and we are skipping
        return max(current_interval - decrease_step, min_skip) # Decrease skip
    else:
        return current_interval # No change


def _process_detection_and_tracking(feed_id: str, core_module: Any, processing_frame: np.ndarray, current_frame_index: int, confidence_threshold: float, proximity_threshold: int, track_timeout: int, error_queue: Optional[MPQueue], logger: logging.Logger, log_level: int) -> Tuple[Dict, bool]:
    tracked_vehicles_raw = {}
    core_error_occurred = False
    try:
        if core_module and hasattr(core_module, 'detect_and_track'):
            tracked_vehicles_raw = core_module.detect_and_track(
                processing_frame,
                frame_index=current_frame_index,
                confidence_threshold=confidence_threshold,
                proximity_threshold=proximity_threshold,
                track_timeout=track_timeout,
            )
        else:
            logger.error(f"[{feed_id}] CoreModule not initialized or missing 'detect_and_track' method, cannot process frame {current_frame_index}.")
            if error_queue:
                error_queue.put(f"[{feed_id}] CoreModule not initialized or method missing.")
            core_error_occurred = True
    except Exception as core_err:
        logger.error(f"[{feed_id}] Core Error frame {current_frame_index}: {core_err}", exc_info=(log_level <= logging.DEBUG))
        if error_queue:
            error_queue.put(f"[{feed_id}] Core Error: {core_err}")
        core_error_occurred = True
    return tracked_vehicles_raw, core_error_occurred

def _update_metrics_and_visualize(feed_id: str, processing_frame: np.ndarray, tracked_vehicles_raw: Dict, traffic_monitor: Any, current_frame_index: int, feed_config_info: Optional[Dict], vis_options: Set[str], config: Dict, core_module: Any, logger: logging.Logger) -> Tuple[Optional[np.ndarray], Dict]:
    traffic_monitor.update_vehicles(tracked_vehicles_raw)
    metrics = traffic_monitor.get_metrics()
    metrics["frame_index"] = current_frame_index
    metrics["timestamp"] = datetime.now(timezone.utc)

    detections_for_vis = []
    for vehicle_id, data in tracked_vehicles_raw.items():
        detections_for_vis.append({
            "bbox": data["bbox"],
            "label": core_module._get_vehicle_type(data["class_id"]),
            "confidence": data["confidence"],
            "speed": data["speed"],
            "behavior": data["behavior"],
            "vehicle_id": vehicle_id
        })
    metrics["detections"] = detections_for_vis

    if feed_config_info:
        if hasattr(feed_config_info, "latitude") and feed_config_info.latitude is not None:
            metrics["latitude"] = feed_config_info.latitude
        if hasattr(feed_config_info, "longitude") and feed_config_info.longitude is not None:
            metrics["longitude"] = feed_config_info.longitude
    
    combined_frame = visualize_data(
        frame=processing_frame,
        tracked_vehicles=tracked_vehicles_raw,
        traffic_metrics=metrics,
        visualization_options=vis_options,
        config=config,
        feed_id=feed_id,
    )
    if combined_frame is None:
        logger.warning(f"[{feed_id}] Visualization returned None for frame {current_frame_index}. Using processing frame.")
        combined_frame = processing_frame
    return combined_frame, metrics

def _handle_output_queue(feed_id: str, frame_queue: MPQueue, current_frame_index: int, combined_frame: np.ndarray, metrics: Dict, tracked_vehicles_raw: Dict, timings: Dict, logger: logging.Logger):
    # Encode the frame as JPEG
    is_success, buffer = cv2.imencode(".jpg", combined_frame)
    if not is_success:
        logger.warning(f"[{feed_id}] Failed to encode frame {current_frame_index} to JPEG.")
        return

    output_data = (
        feed_id,
        current_frame_index,
        buffer.tobytes(),
        metrics,
        tracked_vehicles_raw,
        timings,
    )
    try:
        logger.debug(f"[{feed_id}] Attempting to put frame {current_frame_index} onto queue.")
        if frame_queue.full():
            try:
                frame_queue.get_nowait()
            except queue.Empty:
                pass
        frame_queue.put_nowait(output_data)
        logger.debug(f"[{feed_id}] Successfully put frame {current_frame_index} onto queue.")
    except queue.Full:
        logger.error(f"[{feed_id}] Output frame queue STILL FULL after drop attempt! Frame {current_frame_index} lost.")
    except Exception as q_put_err:
        logger.error(f"[{feed_id}] Error putting frame {current_frame_index} onto queue: {q_put_err}")

def _log_periodic_stats(feed_id: str, timer: Any, frame_queue: MPQueue, current_frame_index: int, dynamic_skip_interval: int, consecutive_core_errors: int, last_log_time: float, logger: logging.Logger) -> float:
    current_time = time.time()
    if current_time - last_log_time > 10.0:
        qsize_approx = -1
        try:
            qsize_approx = frame_queue.qsize()
        except NotImplementedError:
            pass

        logger.info(
            f"[{feed_id}] Frame ~{current_frame_index}. "
            f"Avg Loop: {timer.get_avg('loop_total') * 1000:.1f}ms (~{timer.get_fps('loop_total'):.1f} FPS). "
            f"Read={timer.get_avg('read') * 1000:.1f}, Detect={timer.get_avg('detect_track') * 1000:.1f}, "
            f"Vis={timer.get_avg('visualize') * 1000:.1f}, Put={timer.get_avg('queue_put') * 1000:.1f} (ms). "
            f"OutQueue: ~{qsize_approx}. Skip: {dynamic_skip_interval}. CoreErrs: {consecutive_core_errors}"
        )
        return current_time
    return last_log_time


# --- Process Video Function ---
def process_video(
    video_path: str,
    frame_queue: "MPQueue[Tuple[str, int, 'np.ndarray', Dict, Dict, Dict]]",
    stop_event: Any,  # Event
    alerts_queue: "MPQueue[Dict]",
    config: Dict,
    feed_id: str,
    confidence_threshold: float,
    proximity_threshold: int,
    track_timeout: int,
    vis_options: Set[str],
    reduce_frame_rate_event: Any,  # multiprocessing.Event
    global_fps: Any,  # multiprocessing.Value
    
    db_queue: Optional["multiprocessing.Queue[Dict]"] = None,
    error_queue: Optional["multiprocessing.Queue[str]"] = None,
    feed_config_info: Optional[Dict] = None,  # New argument for feed-specific config
) -> None:
    # Load the latest configuration within the worker process
    from app.config import initialize_config, get_current_config
    try:
        # Assuming config.yaml is in backend/configs/
        config_file_path_obj = Path(__file__).parent.parent.parent / "configs" / "config.yaml"
        initialize_config(str(config_file_path_obj.resolve()))
        config = get_current_config()
    except Exception as e:
        error_msg = f"[{feed_id}] FATAL: Failed to load configuration in worker: {e}"
        logger.critical(error_msg, exc_info=True)
        if error_queue:
            error_queue.put(error_msg)
        stop_event.set()
        return

    # Configure logging specific to this process
    log_level_str = config.get("logging", {}).get("level", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    # Get log path from config, fallback to local if not found
    log_path_worker = config.get("logging", {}).get("log_path", "./logs/worker.log")
    Path(log_path_worker).parent.mkdir(parents=True, exist_ok=True)

    # Configure the module-level logger for this process
    # This ensures the logger is set up correctly without redefining the 'logger' variable
    # that might be used before this block.
    logger.setLevel(log_level)
    # Avoid adding handlers if they already exist (can happen in some envs)
    # This check is important to prevent duplicate log entries if the worker process is reused
    # or if handlers are added by other means.
    if not logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s - %(process)d - %(levelname)s - %(message)s"
        )
        # Stream Handler (console)
        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        logger.addHandler(sh)
        # File Handler
        try:
            fh = logging.FileHandler(log_path_worker)
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        except Exception as log_e:
            logger.error(
                f"Failed to create file handler for worker log {log_path_worker}: {log_e}"
            )

    logger.propagate = False  # Prevent duplication if root logger also has handlers

    logger.info(
        f"Process {os.getpid()} started for {feed_id} ({video_path}) with log level {log_level_str}"
    )
    logger.info(f"Worker received config: {config}")

    video_output_config = config.get("video_output", {})
    vehicle_detection_config = config.get("vehicle_detection", {})
    processing_enabled = video_output_config.get("processing_enabled", True) and vehicle_detection_config.get("enabled", True)


    reader = None
    core_module = None
    timer = FrameTimer()  # Local timer
    consecutive_core_errors = 0  # Counter for core module errors
    MAX_CONSECUTIVE_CORE_ERRORS = 10  # Threshold to stop worker

    reader = None
    video_writer = None
    
    source_type = "unknown" # Initialize source_type with a default value
    try:
        # Code for initial setup (config loading, reader, writer, core_module init)
        # --- Webcam Index Parsing ---
        source_location: Union[str, int] = video_path  # Default to video path

        if isinstance(video_path, str) and video_path.startswith("webcam:"):
            source_type = "webcam"
            try:
                # Extract index after "webcam:"
                source_location = int(video_path.split(":")[1])
                logger.info(f"Identified webcam source with index: {source_location}")
            except (IndexError, ValueError) as e:
                logger.error(
                    f"[{feed_id}] Invalid webcam source format '{video_path}'. Using default index 0. Error: {e}"
                )
                source_location = 0  # Fallback to default index
        elif (
            video_path == "webcam"
        ):  # Handle legacy "webcam" string if needed (optional fallback)
            source_type = "webcam"
            source_location = config["video_input"].get("webcam_index", 0)
            logger.warning(
                f"[{feed_id}] Received legacy 'webcam' source string. Using index {source_location} from config."
            )
        # --- End Webcam Index Parsing ---

        is_looped_feed = feed_config_info.is_looped_feed if feed_config_info and hasattr(feed_config_info, 'is_looped_feed') else False

        logger.info(f"Initializing FrameReader for {source_type}: {source_location}")
        # FrameReader.__init__ now raises RuntimeError if capture fails to open
        reader = FrameReader(
            source_location,
            buffer_size=config["video_input"].get("webcam_buffer_size", 1),
            target_fps=config.get("fps"),
            max_queue_size=config["video_input"].get(
                "max_queue_size", 1000
            ),  # Pass max_queue_size from config
            queue_put_timeout=config["video_input"].get("queue_put_timeout_ms", 1000) / 1000.0, # Convert ms to seconds
            is_looped=is_looped_feed,
        )
        # Add a small delay for camera/reader thread to initialize
        time.sleep(config["interface"].get("camera_warmup_time", 0.5))

        if not reader.isOpened:
            error_msg = f"[{feed_id}] FrameReader failed to open source: {source_location}. Worker stopping."
            logger.error(error_msg)
            if error_queue:
                error_queue.put(error_msg)
            raise RuntimeError(error_msg)

        logger.info(f"FrameReader successfully opened source for {feed_id}")

        if video_output_config.get("enabled", False):
            output_dir = Path(video_output_config.get("output_directory", "./data/processed_videos"))
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"{feed_id}.mp4"
            fourcc = cv2.VideoWriter_fourcc(*video_output_config.get("codec", "mp4v"))
            
            fps = config.get("fps", 30)
            resolution = tuple(config["vehicle_detection"].get("frame_resolution", (640, 480)))

            video_writer = cv2.VideoWriter(str(output_path), fourcc, fps, resolution)
            logger.info(f"Video writer initialized for {output_path} with resolution {resolution} and FPS {fps}")
        
        if processing_enabled:
            target_resolution = tuple(config["vehicle_detection"].get("frame_resolution", (640, 480)))

            if CoreModule is None:
                raise ImportError("CoreModule could not be imported.")
            logger.info(f"Initializing CoreModule for {feed_id}")
            # Pass db_queue to CoreModule for data saving
            core_module = CoreModule(
                feed_id=feed_id,  # Pass feed_id for unique vehicle IDs
                gemini_api_key=config["ocr_engine"].get("gemini_api_key"),
                model_path=config["vehicle_detection"].get("model_path"),
                config=config,
                fps=config.get("fps", 30),  # Pass FPS from config
                db_queue=db_queue,
            )
            logger.info(f"CoreModule initialized for {feed_id}")
            # TrafficMonitor is now also imported from utils
            traffic_monitor = TrafficMonitor(config)
        else:
            core_module = None
            traffic_monitor = None

        frame_count_processed = 0
        last_log_time = time.time()
        base_frame_skip_interval = max(
            1, config["vehicle_detection"].get("skip_frames", 1)
        )
        dynamic_skip_interval = base_frame_skip_interval

        max_queue_size = config["video_input"].get("max_queue_size", 1000)

        # Main processing loop would go here
        logger.info(f"[{feed_id}] Starting main processing loop...")
        
        # Inner try block for the main processing loop
        try:
            while not stop_event.is_set():
                loop_start_time = time.time()

                # 1. Read Frame
                read_start_time = time.time()
                current_frame_index, frame = _read_frame(feed_id, reader, stop_event, logger)
                timer.log_time("read", time.time() - read_start_time)

                if frame is None:
                    if stop_event.is_set():
                        logger.info(f"[{feed_id}] Stop event set, exiting loop after read attempt.")
                        break
                    logger.debug(f"[{feed_id}] _read_frame returned None, waiting...")
                    time.sleep(0.01)  # Small sleep to prevent busy-waiting
                    continue

                logger.debug(f"[{feed_id}] Successfully read frame {current_frame_index}. Shape: {frame.shape}")

                if not processing_enabled:
                    # If processing is disabled, just put the raw frame on the queue
                    _handle_output_queue(
                        feed_id, frame_queue, current_frame_index, frame,
                        {}, {}, timer.timings, logger
                    )
                    time.sleep(1 / config.get("fps", 30)) # Approximate frame rate
                    continue

                # Frame Skipping Logic (based on base_frame_skip_interval)
                # The dynamic skipping adjusts the interval used here
                if current_frame_index % dynamic_skip_interval != 0:
                    logger.debug(f"[{feed_id}] Skipping frame {current_frame_index} based on dynamic interval {dynamic_skip_interval}")
                    continue

                # 2. Preprocess Frame
                performance_config = config.get("performance", {})
                processing_frame = _preprocess_frame_for_detection(feed_id, frame, current_frame_index, target_resolution, logger, frame_queue, max_queue_size, dynamic_skip_interval, base_frame_skip_interval, performance_config)
                if processing_frame is None:
                    logger.warning(f"[{feed_id}] Preprocessing returned None for frame {current_frame_index}. Skipping frame.")
                    continue
                logger.debug(f"[{feed_id}] Successfully preprocessed frame {current_frame_index}. Shape: {processing_frame.shape}")

                # 3. Detect and Track
                detect_start_time = time.time()
                logger.debug(f"[{feed_id}] Calling detect_and_track for frame {current_frame_index}...")
                tracked_vehicles_raw, core_error_occurred = _process_detection_and_tracking(
                    feed_id, core_module, processing_frame, current_frame_index,
                    confidence_threshold, proximity_threshold, track_timeout,
                    error_queue, logger, log_level
                )
                timer.log_time("detect_track", time.time() - detect_start_time)

                if core_error_occurred:
                    consecutive_core_errors += 1
                    if consecutive_core_errors >= MAX_CONSECUTIVE_CORE_ERRORS:
                        logger.critical(f"[{feed_id}] Exceeded max core errors. Stopping worker.")
                        stop_event.set()
                    continue
                else:
                    consecutive_core_errors = 0
                logger.debug(f"[{feed_id}] detect_and_track completed for frame {current_frame_index}. Found {len(tracked_vehicles_raw)} vehicles.")

                # 4. Update Metrics and Visualize
                vis_start_time = time.time()
                combined_frame, metrics = _update_metrics_and_visualize(
                    feed_id, processing_frame, tracked_vehicles_raw, traffic_monitor,
                    current_frame_index, feed_config_info, vis_options, config, core_module, logger
                )
                timer.log_time("visualize", time.time() - vis_start_time)

                # 5. Handle Output Queue
                put_start_time = time.time()
                _handle_output_queue(
                    feed_id, frame_queue, current_frame_index, combined_frame,
                    metrics, tracked_vehicles_raw, timer.timings, logger
                )
                timer.log_time("queue_put", time.time() - put_start_time)

                if video_writer and combined_frame is not None:
                    # Ensure the frame is 3-channel BGR before writing
                    if len(combined_frame.shape) == 3 and combined_frame.shape[2] == 4:
                        combined_frame_bgr = cv2.cvtColor(combined_frame, cv2.COLOR_BGRA2BGR)
                    else:
                        combined_frame_bgr = combined_frame
                    video_writer.write(combined_frame_bgr)

                frame_count_processed += 1
                timer.log_time("loop_total", time.time() - loop_start_time)

                # 6. Log Periodic Stats
                last_log_time = _log_periodic_stats(
                    feed_id, timer, frame_queue, current_frame_index,
                    dynamic_skip_interval, consecutive_core_errors, last_log_time, logger
                )

                # 7. Explicit Garbage Collection
                if frame_count_processed % 100 == 0:
                    gc.collect()
                    logger.debug(f"[{feed_id}] Explicit garbage collection run at frame {current_frame_index}")

            # End of the processing loop (exit while loop)

        # Exception handling specifically for the processing loop (inner try)
        except KeyboardInterrupt:
            logger.warning(f"[{feed_id}] KeyboardInterrupt received. Stopping worker.")
            stop_event.set()
        except RuntimeError:
            # Catch runtime errors (like FrameReader init failure)
            # Error message is already logged where the exception is raised
            if not stop_event.is_set():
                stop_event.set() # Ensure stop is signaled
        except ImportError as e:
            # Catch import errors during setup (CoreModule)
            error_msg = f"[{feed_id}] FATAL Import Error: {e}. Worker cannot run."
            logger.critical(error_msg, exc_info=True)
            if error_queue:
                error_queue.put(error_msg)
            if not stop_event.is_set():
                stop_event.set()
        except Exception as e:
            # Catch any other unexpected exception during the main loop
            error_msg = f"[{feed_id}] FATAL Unhandled Error in process loop: {e}"
            logger.critical(error_msg, exc_info=True) # Log as critical
            if error_queue:
                error_queue.put(error_msg)
            if not stop_event.is_set():
                stop_event.set() # Ensure stop is signaled
    finally:
        # --- Enhanced Cleanup ---
        pid = os.getpid()
        logger.info(f"[{feed_id}] Cleaning up process {pid}...")
        if not stop_event.is_set():
            logger.warning(
                f"[{feed_id}] Stop event not set during cleanup initiation, setting now.\n"
            )
            stop_event.set()

        if video_writer:
            try:
                video_writer.release()
                logger.info(f"[{feed_id}] Video writer released.")
            except Exception as vw_release_err:
                logger.error(f"[{feed_id}] Error releasing video writer: {vw_release_err}\n", exc_info=True)

        # Stop FrameReader safely
        if reader:
            try:
                logger.info(f"[{feed_id}] Stopping FrameReader...")
                reader.stop()
                logger.info(f"[{feed_id}] FrameReader stopped.")
            except Exception as read_stop_err: # Catch exceptions during reader stop as well
                logger.error(
                    f"[{feed_id}] Error stopping FrameReader: {read_stop_err}",
                    exc_info=True,
                )
        else:
            logger.info(f"[{feed_id}] FrameReader was not initialized, skipping stop.")

        # Cleanup CoreModule safely
        if core_module:
            try:
                logger.info(f"[{feed_id}] Cleaning up CoreModule...")
                core_module.cleanup()
                logger.info(f"[{feed_id}] CoreModule cleaned up.")
            except Exception as core_clean_err:
                logger.error(
                    f"[{feed_id}] Error cleaning up CoreModule: {core_clean_err}",
                    exc_info=True,
                )
        else:
            logger.info(
                f"[{feed_id}] CoreModule was not initialized, skipping cleanup."
            )

        # Drain output queue (optional, helps release memory faster)
        drained_count = 0
        if frame_queue:
            try:
                while not frame_queue.empty():
                    frame_queue.get_nowait()
                    drained_count += 1
            except queue.Empty:
                pass
            except Exception as q_drain_err:
                logger.warning(
                    f"[{feed_id}] Error draining output queue during cleanup: {q_drain_err}"
                )
            if drained_count > 0:
                logger.debug(
                    f"[{feed_id}] Drained {drained_count} items from output queue during cleanup."
                )

        # Close queue from worker side (optional, main process usually manages queue lifetime)
        # try: frame_queue.close()
        # except Exception as q_close_err: logger.warning(f"[{feed_id}] Error closing output queue: {q_close_err}")

        frame_count_processed = (
            frame_count_processed if "frame_count_processed" in locals() else 0
        )
        logger.info(
            f"[{feed_id}] Process {pid} terminated. Processed ~{frame_count_processed} frames."
        )
        # Ensure all handlers are flushed and closed (helps with file logging)
        logging.shutdown()
