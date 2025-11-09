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
    from ..utils.video import FrameTimer, FrameReader
    from ..utils.monitoring import TrafficMonitor
    from ..utils.visualization import visualize_data
    from ..utils.config import ConfigError
except ImportError as e:
    print(f"Error importing from utils.py in processing_worker: {e}.")

try:
    from ..core.core_module import CoreModule
except ImportError as e:
    logger.error(f"Error importing CoreModule in processing_worker: {e}.")
    CoreModule = None

def _read_frame(feed_id: str, reader: Any, stop_event: Any, logger: logging.Logger) -> Tuple[Optional[int], Optional[np.ndarray]]:
    try:
        result = reader.read()
        if result is None:
            if reader.end_of_video:
                logger.info(f"[{feed_id}] End of stream, stopping.")
                stop_event.set()
            return None, None
        return result
    except Exception as e:
        logger.error(f"[{feed_id}] Exception in _read_frame: {e}", exc_info=True)
        stop_event.set()
        return None, None

def process_video(
    video_path: str, frame_queue: "MPQueue", stop_event: Any, alerts_queue: "MPQueue",
    config: Dict, feed_id: str, confidence_threshold: float, proximity_threshold: int,
    track_timeout: int, vis_options: Set[str], reduce_frame_rate_event: Any, 
    global_fps: Any, db_queue: Optional["MPQueue"] = None, 
    error_queue: Optional["MPQueue"] = None, feed_config_info: Optional[Dict] = None,
    video_writer_queue: Optional["MPQueue"] = None, is_looped: bool = False
) -> None:
    pid = os.getpid()
    reader = None
    core_module = None
    timer = FrameTimer()
    try:
        log_cfg = config.get("logging", {})
        log_level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
        logger.setLevel(log_level)
        if not logger.handlers:
            formatter = logging.Formatter("%(asctime)s - %(process)d - %(levelname)s - %(message)s")
            sh = logging.StreamHandler()
            sh.setFormatter(formatter)
            try:
                log_path = Path(log_cfg.get("log_path", "./logs/worker.log"))
                log_path.parent.mkdir(parents=True, exist_ok=True)
                fh = logging.FileHandler(log_path)
                fh.setFormatter(formatter)
                logger.addHandler(fh)
            except Exception as e:
                logger.error(f"Failed to create file handler: {e}")
        logger.propagate = False
        logger.info(f"Process {pid} started for {feed_id} ({video_path})")

        source = video_path
        if isinstance(video_path, str) and video_path.startswith("webcam:"):
            try: source = int(video_path.split(":")[1])
            except (IndexError, ValueError): source = 0
        
        target_fps = config.get("video_processing", {}).get("target_fps", 10)
        reader = FrameReader(
            source, 
            max_queue_size=config["video_input"].get("max_queue_size", 1000),
            is_looped=is_looped,
            target_fps=target_fps
        )
        if not reader.start():
            raise RuntimeError(f"FrameReader failed to start: {reader.error_message}")

        processing_enabled = config.get("video_output", {}).get("processing_enabled", True)
        if processing_enabled and CoreModule:
            core_module = CoreModule(
                feed_id=feed_id,
                model_path=config["vehicle_detection"].get("model_path"),
                config=config,
                fps=config.get("fps", 30),
                db_queue=db_queue,
                gemini_api_key=config["ocr_engine"].get("gemini_api_key"),
                model_type=config["vehicle_detection"].get("model_type", "yolo"),
            )
            traffic_monitor = TrafficMonitor(config)
        else:
            core_module, traffic_monitor = None, None

        frame_count, last_log_time, core_errors = 0, time.time(), 0
        skip_interval = max(1, config["vehicle_detection"].get("skip_frames", 1))
        
        while not stop_event.is_set():
            loop_start = time.time()
            frame_index, frame = _read_frame(feed_id, reader, stop_event, logger)

            if frame is None:
                if stop_event.is_set(): break
                time.sleep(0.01)
                continue

            if core_module and traffic_monitor:
                if frame_index % skip_interval == 0:
                    # Perform detection on the original frame
                    tracked_vehicles = core_module.detect_and_track(frame, frame_index, confidence_threshold, proximity_threshold, track_timeout)
                    traffic_monitor.update_vehicles(tracked_vehicles)
                    metrics = traffic_monitor.get_metrics()

                    # Visualize data on the original frame
                    vis_frame = visualize_data(frame.copy(), tracked_vehicles, metrics, vis_options, config, feed_id)

                    # Write full-resolution frame with visualizations to video_writer_queue
                    if video_writer_queue:
                        video_writer_queue.put(vis_frame)

                    # For streaming, resize the visualized frame to a smaller resolution
                    stream_frame_resolution = tuple(config.get("video_output", {}).get("stream_resolution", (640, 480)))
                    stream_frame = cv2.resize(vis_frame, stream_frame_resolution)
                    _, buffer = cv2.imencode(".jpg", stream_frame)
                    frame_queue.put((feed_id, frame_index, buffer.tobytes(), metrics, tracked_vehicles, {}))
            else:
                 _, buffer = cv2.imencode(".jpg", frame)
                 frame_queue.put((feed_id, frame_index, buffer.tobytes(), {}, {}, {}))
            
            frame_count += 1
            if time.time() - last_log_time > 10.0:
                logger.info(f"[{feed_id}] Processed {frame_count} frames.")
                last_log_time = time.time()

    except Exception as e:
        logger.critical(f"[{feed_id}] FATAL Error: {e}", exc_info=True)
        if error_queue: error_queue.put(f"[{feed_id}] FATAL: {e}")
    finally:
        logger.info(f"[{feed_id}] Cleaning up process {pid}...")
        if reader: reader.stop()
        if core_module: core_module.cleanup()
        if video_writer_queue:
            video_writer_queue.put(None) # Sentinel to stop writer
        logging.shutdown()
        logger.info(f"[{feed_id}] Process {pid} terminated.")
