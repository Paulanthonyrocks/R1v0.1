import os
import cv2
import gc
import logging
import time
import numpy as np
import queue
from typing import Dict, Optional, Set, Tuple, Union, TYPE_CHECKING, Any, List
from multiprocessing import Queue as MPQueue
from pathlib import Path

if TYPE_CHECKING:
    from ..core.core_module import CoreModule as CoreModuleType

logger = logging.getLogger("Process")

# Conditional Imports to prevent worker crash on startup
try:
    from ..utils.video import FrameReader
    from ..utils.monitoring import TrafficMonitor
    from ..utils.visualization import visualize_data
except ImportError as e:
    logger.error(f"Error importing utils in processing_worker: {e}")

try:
    from ..core.core_module import CoreModule
except ImportError as e:
    logger.error(f"Error importing CoreModule in processing_worker: {e}")
    CoreModule = None

# --- Helper: Safe Serialization for NumPy Types ---
def _make_serializable(obj):
    if isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    if isinstance(obj, (np.floating, np.float64, np.float32)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj

def _serialize_tracked_vehicles(tracked_vehicles: Dict[str, Dict], scale_x: float = 1.0, scale_y: float = 1.0) -> List[Dict[str, Any]]:
    """
    Serializes tracking data, ensuring all NumPy types are converted to Python native types
    to prevent JSON serialization errors in the API layer.
    """
    serialized_list = []
    for vehicle_id, data in tracked_vehicles.items():
        try:
            # Map class ID to Name safely
            c_id = data.get("class_id", -1)
            c_name = CoreModule.vehicle_type_map.get(c_id, "unknown") if CoreModule else "unknown"

            # Apply scaling to bbox
            bbox = data.get("bbox", [])
            scaled_bbox = []
            if bbox and len(bbox) == 4:
                scaled_bbox = [
                    bbox[0] * scale_x,
                    bbox[1] * scale_y,
                    bbox[2] * scale_x,
                    bbox[3] * scale_y
                ]

            serializable_data = {
                "vehicle_id": str(vehicle_id),
                "bbox": [_make_serializable(x) for x in scaled_bbox],
                "speed": _make_serializable(data.get("speed", 0)),
                "license_plate": str(data.get("license_plate", "Unknown")),
                "class_id": int(c_id),
                "class_name": c_name,
                "behavior": str(data.get("behavior", "unknown")),
                "confidence": _make_serializable(data.get("confidence", 0)),
                "is_occluded": bool(data.get("is_occluded", False)),
                "lane": int(data.get("lane", -1)),
            }
            serialized_list.append(serializable_data)
        except Exception as e:
            logger.warning(f"Error serializing vehicle {vehicle_id}: {e}")
            continue
    return serialized_list

def _read_frame(feed_id: str, reader: Any, stop_event: Any) -> Tuple[Optional[int], Optional[np.ndarray]]:
    try:
        result = reader.read()
        if result is None:
            if reader.end_of_video:
                logger.info(f"[{feed_id}] End of stream reached.")
                stop_event.set()
            return None, None
        return result
    except Exception as e:
        logger.error(f"[{feed_id}] Error reading frame: {e}")
        stop_event.set()
        return None, None

def process_video(
    video_path: str, 
    frame_queue: "MPQueue", 
    stop_event: Any, 
    alerts_queue: "MPQueue",
    config: Dict, 
    feed_id: str, 
    confidence_threshold: float, 
    proximity_threshold: int,
    track_timeout: int, 
    vis_options: Set[str], 
    reduce_frame_rate_event: Any, 
    global_fps: Any, 
    db_queue: Optional["MPQueue"] = None, 
    error_queue: Optional["MPQueue"] = None, 
    feed_config_info: Optional[Dict] = None,
    video_writer_queue: Optional["MPQueue"] = None, 
    is_looped: bool = False
) -> None:
    pid = os.getpid()
    
    # --- Setup Logging ---
    log_cfg = config.get("logging", {})
    log_level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    logger.setLevel(log_level)
    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s - %(process)d - %(levelname)s - %(message)s")
        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        logger.addHandler(sh)
    
    logger.info(f"Process {pid} started for {feed_id}")

    # --- Configuration Caching (Optimization) ---
    # Extract configs once to avoid dictionary lookups inside the loop
    vehicle_det_cfg = config.get("vehicle_detection", {})
    video_out_cfg = config.get("video_output", {})
    ocr_cfg = config.get("ocr_engine", {})
    
    target_fps = config.get("video_processing", {}).get("target_fps", 30)
    skip_interval = max(1, vehicle_det_cfg.get("skip_frames", 1))
    
    # Stream resolution (width, height)
    stream_res = tuple(video_out_cfg.get("stream_resolution", (640, 480)))
    processing_enabled = video_out_cfg.get("processing_enabled", True)
    
    # JPEG Compression: Quality 80 offers good speed/size balance
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), 80]

    # --- Component Initialization ---
    reader = None
    core_module = None
    traffic_monitor = None

    try:
        # Determine Source
        source = video_path
        if isinstance(video_path, str) and video_path.startswith("webcam:"):
            try: 
                source = int(video_path.split(":")[1])
            except (IndexError, ValueError): 
                source = 0
        
        reader = FrameReader(
            source, 
            max_queue_size=config["video_input"].get("max_queue_size", 1000),
            is_looped=is_looped,
            target_fps=target_fps
        )
        
        if not reader.start():
            raise RuntimeError(f"FrameReader failed: {reader.error_message}")

        if processing_enabled and CoreModule:
            core_module = CoreModule(
                feed_id=feed_id,
                model_path=vehicle_det_cfg.get("model_path"),
                config=config,
                fps=target_fps,
                db_queue=db_queue,
                gemini_api_key=ocr_cfg.get("gemini_api_key"),
                model_type=vehicle_det_cfg.get("model_type", "yolo"),
            )
            traffic_monitor = TrafficMonitor(config)

        frame_count = 0
        last_log_time = time.time()
        
        # --- Main Loop ---
        while not stop_event.is_set():
            frame_index, frame = _read_frame(feed_id, reader, stop_event)

            if frame is None:
                if stop_event.is_set(): break
                time.sleep(0.01) # Avoid busy wait
                continue

            # --- Processing Pipeline ---
            tracked_vehicles = {}
            metrics = {}
            serialized_vehicles = []
            
            if core_module and traffic_monitor:
                # 1. Detection / Tracking
                if frame_index % skip_interval == 0:
                    # Heavy Detection
                    tracked_vehicles = core_module.detect_and_track(
                        frame, frame_index, confidence_threshold, proximity_threshold, track_timeout
                    )
                else:
                    # Lightweight Prediction
                    tracked_vehicles = core_module.predict_only(frame_index)

                # 2. Update Statistics
                traffic_monitor.update_vehicles(tracked_vehicles)
                metrics = traffic_monitor.get_metrics()
                
                # Inject Feed Configuration Data (Location) into metrics
                if feed_config_info:
                    if isinstance(feed_config_info, dict):
                        metrics["latitude"] = feed_config_info.get("latitude", 0.0)
                        metrics["longitude"] = feed_config_info.get("longitude", 0.0)
                    else:
                        metrics["latitude"] = getattr(feed_config_info, "latitude", 0.0)
                        metrics["longitude"] = getattr(feed_config_info, "longitude", 0.0)
                
                # 3. Visualization
                # Note: We visualize on a copy to keep the original frame clean if needed for other pipes
                vis_frame = visualize_data(frame.copy(), tracked_vehicles, metrics, vis_options, config, feed_id)

                # 4. Video Writer (Full Resolution)
                if video_writer_queue:
                    # Only put if queue isn't full to prevent blocking the processing loop
                    if not video_writer_queue.full():
                        video_writer_queue.put(vis_frame)

                # 5. Prepare for Stream (Resized)
                try:
                    # Use raw frame for stream to allow client-side rendering (better performance/flexibility)
                    stream_frame = cv2.resize(frame, stream_res, interpolation=cv2.INTER_LINEAR)
                    _, buffer = cv2.imencode(".jpg", stream_frame, encode_params)
                    
                    # Calculate scale factors
                    orig_h, orig_w = frame.shape[:2]
                    target_w, target_h = stream_res
                    scale_x = target_w / orig_w if orig_w > 0 else 1.0
                    scale_y = target_h / orig_h if orig_h > 0 else 1.0
                    
                    serialized_vehicles = _serialize_tracked_vehicles(tracked_vehicles, scale_x, scale_y)
                    
                    # Push to Frontend
                    frame_queue.put((feed_id, frame_index, buffer.tobytes(), metrics, serialized_vehicles, {}))
                except Exception as e:
                    logger.error(f"[{feed_id}] Encoding/Streaming error: {e}")

            else:
                # Pass-through mode (No ML)
                try:
                    stream_frame = cv2.resize(frame, stream_res, interpolation=cv2.INTER_LINEAR)
                    _, buffer = cv2.imencode(".jpg", stream_frame, encode_params)
                    frame_queue.put((feed_id, frame_index, buffer.tobytes(), {}, [], {}))
                except Exception as e:
                    logger.error(f"[{feed_id}] Pass-through encoding error: {e}")

            # --- Loop Maintenance ---
            frame_count += 1
            
            # periodic logging
            now = time.time()
            if now - last_log_time > 10.0:
                fps = frame_count / (now - last_log_time)
                logger.info(f"[{feed_id}] Processing Speed: {fps:.2f} FPS | Frames: {frame_count}")
                if reader:
                    reader_stats = reader.get_stats()
                    # Calculate actual FPS from frames_read_count in reader
                    reader_actual_fps = reader_stats["frames_read"] / (now - reader.start_time) if reader.start_time else 0
                    logger.info(f"[{feed_id}] FrameReader Stats: {reader_stats['target_fps']} Target FPS, "
                                f"{reader_actual_fps:.2f} Actual Read FPS, "
                                f"{reader_stats['frames_queued']} Frames in Queue, "
                                f"Read Count: {reader_stats['frames_read']}, Processed Count: {reader_stats['frames_processed_count']}")
                last_log_time = now
                frame_count = 0

            # Optional: Periodic GC to prevent memory fragmentation in long-running processes
            if frame_count % 1000 == 0:
                gc.collect()

    except Exception as e:
        logger.critical(f"[{feed_id}] FATAL Process Error: {e}", exc_info=True)
        if error_queue:
            error_queue.put(f"[{feed_id}] FATAL: {e}")
    finally:
        logger.info(f"[{feed_id}] Shutting down...")
        if reader: reader.stop()
        if core_module: core_module.cleanup()
        if video_writer_queue: video_writer_queue.put(None)
        logger.info(f"[{feed_id}] Process terminated.")