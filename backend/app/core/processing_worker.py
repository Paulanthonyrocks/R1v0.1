import os
import cv2
import logging
import time
import numpy as np
import queue
import threading
import signal
from typing import Dict, Optional, Set, Tuple, TYPE_CHECKING, Any, List
from multiprocessing import Queue as MPQueue

if TYPE_CHECKING:
    pass

logger = logging.getLogger("Process")

"""
WORKER ARCHITECTURE:
- ingestion_worker.py: Capture frames from source → central_input_queue
  Use for: Multi-feed systems where AI is shared across feeds
  
- inference_worker.py: Process frames from central_input_queue → AI results
  Use for: GPU-bound scenarios where one GPU serves multiple feeds
  
- processing_worker.py: All-in-one (capture + AI + visualization)
  Use for: Single-feed systems or when each feed needs isolated processing
  
DO NOT MIX: Choose either (ingestion + inference) OR (processing) per deployment
"""

# Conditional Imports
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

from ..utils.process import start_parent_monitor
import shutil

# Fix #20: Disk space check helper
def check_disk_space(path, min_gb=1.0):
    try:
        stat = shutil.disk_usage(path)
        free_gb = stat.free / (1024**3)
        return free_gb >= min_gb
    except Exception:
        return True

# --- Helpers ---
def _make_serializable(obj):
    if isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    if isinstance(obj, (np.floating, np.float64, np.float32)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj

def _serialize_tracked_vehicles(tracked_vehicles: Dict[str, Dict], scale_x: float = 1.0, scale_y: float = 1.0) -> List[Dict[str, Any]]:
    serialized_list = []
    # Optimization: Pre-fetch map to avoid repeated attribute lookup
    v_map = CoreModule.vehicle_type_map if CoreModule else {}
    
    for vehicle_id, data in tracked_vehicles.items():
        try:
            c_id = data.get("class_id", -1)
            # Use local map reference
            c_name = v_map.get(c_id, "unknown")

            bbox = data.get("bbox")
            scaled_bbox = []
            if bbox and len(bbox) == 4:
                scaled_bbox = [
                    bbox[0] * scale_x,
                    bbox[1] * scale_y,
                    bbox[2] * scale_x,
                    bbox[3] * scale_y
                ]

            serialized_list.append({
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
                "status": str(data.get("status", "unknown")),
                "ground_centroid": [_make_serializable(x) for x in data.get("ground_centroid")] if "ground_centroid" in data else None,
            })
        except Exception as e:
            logger.warning(f"Failed to serialize vehicle {vehicle_id}: {e}")
            continue # Skip malformed tracks
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
    is_looped: bool = False,
    command_queue: Optional["MPQueue"] = None
) -> None:
    # Fix #24: Validate vis_options
    if vis_options is None:
        vis_options = set()
    
    pid = os.getpid()
    
    # --- Setup Logging (Process Safe) ---
    log_cfg = config.get("logging", {})
    log_level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    logger.setLevel(log_level)
    
    # Always ensure we have handlers (File + Stream)
    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s - %(process)d - %(levelname)s - %(message)s")
        
        # 1. Console Handler
        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        logger.addHandler(sh)
        
        # 2. File Handler (if configured)
        handlers_cfg = log_cfg.get("handlers", {})
        worker_handler_cfg = handlers_cfg.get("workerFileHandler")
        if worker_handler_cfg and worker_handler_cfg.get("filename"):
            try:
                fh = logging.FileHandler(worker_handler_cfg["filename"], mode='a')
                fh.setFormatter(formatter)
                logger.addHandler(fh)
            except Exception as e:
                # Fallback to console if file access fails
                print(f"Failed to setup worker file logging: {e}")

    # --- Signal Handling ---
    def signal_handler(signum, frame):
        logger.info(f"[{feed_id}] Received signal {signum}, stopping gracefully")
        stop_event.set()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    logger.info(f"Process {pid} started for {feed_id}")

    # --- Parent Monitoring (Anti-Zombie) ---
    start_parent_monitor(stop_event, feed_id)

    # --- Config Extraction (Optimization) ---
    vehicle_det_cfg = config.get("vehicle_detection", {})
    video_out_cfg = config.get("video_output", {})
    ocr_cfg = config.get("ocr_engine", {})
    
    target_fps = config.get("video_processing", {}).get("target_fps", 30)
    skip_interval = max(1, vehicle_det_cfg.get("skip_frames", 1))
    stream_res = tuple(video_out_cfg.get("stream_resolution", (640, 480)))
    processing_enabled = video_out_cfg.get("processing_enabled", True)
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), 80]

    # 1. Config Validation
    if target_fps <= 0 or target_fps > 120:
        logger.warning(f"[{feed_id}] Invalid target_fps {target_fps}, using 30")
        target_fps = 30
    if skip_interval < 1:
        logger.warning(f"[{feed_id}] Invalid skip_interval {skip_interval}, using 1")
        skip_interval = 1
    if len(stream_res) != 2 or stream_res[0] <= 0 or stream_res[1] <= 0:
        logger.warning(f"[{feed_id}] Invalid stream_resolution {stream_res}, using (640, 480)")
        stream_res = (640, 480)

    logger.info(f"[{feed_id}] Config: FPS={target_fps}, Skip={skip_interval}, Res={stream_res}")
    
    # 2. Performance Metrics Initialization
    frame_count = 0
    processed_count = 0
    skipped_count = 0
    dropped_frames = 0
    encoded_frames = 0
    last_log_time = time.time()
    
    # NOTE: Config updates (skip_interval, roi_polygon) take effect on next iteration
    # No locking needed due to GIL protection of simple assignments, but we keep them isolated
    config_lock = threading.Lock()

    # Pre-extract location data and ROI
    lat, lon = 0.0, 0.0
    roi_polygon = None
    if feed_config_info:
        if isinstance(feed_config_info, dict):
            lat = feed_config_info.get("latitude", 0.0)
            lon = feed_config_info.get("longitude", 0.0)
            roi = feed_config_info.get("roi")
        else:
            lat = getattr(feed_config_info, "latitude", 0.0)
            lon = getattr(feed_config_info, "longitude", 0.0)
            roi = getattr(feed_config_info, "roi", None)
        
        if roi and len(roi) >= 3:
            # ROI is stored as normalized coordinates [{'x': 0.1, 'y': 0.1}, ...]
            # We'll convert to pixel coordinates inside the loop once we know frame size
            roi_polygon = np.array([[p['x'], p['y']] for p in roi], dtype=np.float32)

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
        
        # 3. FrameReader Initialization with Retry Logic
        max_retries = 3
        retry_delay = 2.0
        
        for attempt in range(max_retries):
            try:
                reader = FrameReader(
                    source, 
                    max_queue_size=config["video_input"].get("max_queue_size", 1000),
                    is_looped=is_looped,
                    target_fps=target_fps
                )
                
                if reader.start():
                    logger.info(f"[{feed_id}] FrameReader started successfully on attempt {attempt+1}")
                    break
                else:
                    error_msg = getattr(reader, 'error_message', 'Unknown error')
                    logger.warning(f"[{feed_id}] Start attempt {attempt+1}/{max_retries} failed: {error_msg}")
                    reader = None
            except Exception as e:
                logger.error(f"[{feed_id}] Init attempt {attempt+1}/{max_retries} error: {e}")
                reader = None
            
            if attempt < max_retries - 1 and not stop_event.is_set():
                logger.info(f"[{feed_id}] Retrying in {retry_delay}s...")
                time.sleep(retry_delay)

        if reader is None:
             raise RuntimeError(f"[{feed_id}] Failed to initialize FrameReader after {max_retries} attempts")

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
            # Check for commands (e.g., Config Updates, Snapshots)
            snapshot_request = None
            if command_queue:
                try:
                    cmd = command_queue.get_nowait()
                    if cmd and cmd.get("type") == "config_update":
                        data = cmd.get("data", {})
                        logger.info(f"[{feed_id}] Received config update command.")
                        
                        # Update Local ROI Polygon
                        if "roi" in data:
                            roi = data["roi"]
                            if roi and len(roi) >= 3:
                                with config_lock:
                                    roi_polygon = np.array([[p['x'], p['y']] for p in roi], dtype=np.float32)
                                logger.info(f"[{feed_id}] Worker ROI polygon updated.")
                            else:
                                with config_lock:
                                    roi_polygon = None
                                logger.info(f"[{feed_id}] Worker ROI polygon cleared.")
                                
                        # Update CoreModule
                        if core_module:
                            # Pass normalized points to CoreModule
                            cm_update = data.copy()
                            if "roi" in data:
                                if "roi_processing" not in cm_update:
                                    cm_update["roi_processing"] = {}
                                
                                roi = data["roi"]
                                if roi and len(roi) >= 3:
                                    # Pass as list of [x, y] floats
                                    normalized_points = [[p['x'], p['y']] for p in roi]
                                    cm_update["roi_processing"]["roi_points_normalized"] = normalized_points
                                    cm_update["roi_processing"]["enabled"] = True
                                else:
                                    cm_update["roi_processing"]["roi_points_normalized"] = None
                                    cm_update["roi_processing"]["enabled"] = False
                            
                            core_module.update_config(cm_update)
                        
                        # Update Skip Interval (Adaptive FPS)
                        if "skip_frames" in data:
                            try:
                                new_skip = int(data["skip_frames"])
                                with config_lock:
                                    skip_interval = max(1, new_skip)
                                logger.info(f"[{feed_id}] Updated skip_interval to {skip_interval} frames.")
                            except (ValueError, TypeError):
                                logger.warning(f"[{feed_id}] Invalid skip_frames value in config update.")

                    elif cmd.get("type") == "save_snapshot":
                        # Fix #20: Check disk space before snapshot
                        snapshot_dir = config.get("snapshots_dir") or config.get("storage", {}).get("snapshot_output_dir", "backend/data/snapshots")
                        if not check_disk_space(snapshot_dir, min_gb=0.5):
                             logger.error(f"[{feed_id}] Insufficient disk space for snapshot")
                             continue
                        
                        # Store for processing after we have the frame and tracking data
                        snapshot_request = cmd

                except queue.Empty:
                    pass
                except Exception as e:
                    logger.error(f"[{feed_id}] Error processing command: {e}")

            frame_index, frame = _read_frame(feed_id, reader, stop_event)

            if frame is None:
                if stop_event.is_set():
                    break
                time.sleep(0.005) # Reduced sleep for better responsiveness
                continue

            try: # Entire frame processing block
                tracked_vehicles = {}
                metrics = {}
                serialized_vehicles = []
                
                if core_module and traffic_monitor:
                    # 1. Detection / Tracking with Robustness
                    try:
                        with config_lock:
                            current_skip = skip_interval
                        
                        if frame_index % current_skip == 0:
                            processed_count += 1
                            tracked_vehicles = core_module.detect_and_track(
                                frame, frame_index, confidence_threshold, proximity_threshold, track_timeout
                            )
                            # Only update stats on actual detection frames
                            traffic_monitor.update_vehicles(tracked_vehicles)
                        else:
                            skipped_count += 1
                            tracked_vehicles = core_module.predict_only(frame_index)
                            # Do NOT update traffic_monitor with predictions to avoid polluting stats
                    except Exception as e:
                        logger.error(f"[{feed_id}] Detection/Tracking failed on frame {frame_index}: {e}", exc_info=True)
                        tracked_vehicles = {}

                    # 2. Update Statistics with Robustness
                    try:
                        # traffic_monitor updated above inside condition
                        metrics = traffic_monitor.get_metrics()
                        metrics["latitude"] = lat
                        metrics["longitude"] = lon
                    except Exception as e:
                        logger.error(f"[{feed_id}] Metrics update failed: {e}")
                        metrics = {}
                    
                    # 3. Handle Snapshot Request (if any)
                    if snapshot_request:
                        try:
                            inc_id = snapshot_request.get("incident_id", "unknown")
                            snapshot_dir = config.get("snapshots_dir") or config.get("storage", {}).get("snapshot_output_dir", "backend/data/snapshots")
                            os.makedirs(snapshot_dir, exist_ok=True)
                            
                            filename = f"snapshot_{feed_id}_{inc_id}_{int(time.time())}.jpg"
                            filepath = os.path.join(snapshot_dir, filename)
                            
                            snap_frame = frame.copy()
                            # Draw visualizations if we have tracked vehicles to give context to snapshot
                            if tracked_vehicles:
                                snap_frame = visualize_data(snap_frame, tracked_vehicles, metrics, vis_options, config, feed_id)
                            
                            cv2.imwrite(filepath, snap_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                            
                            # Send back the snapshot path via the frame_queue but as a special type
                            frame_queue.put_nowait((feed_id, frame_index, None, {}, [], {"type": "snapshot", "incident_id": inc_id, "path": filepath}))
                            logger.info(f"[{feed_id}] Snapshot saved: {filepath}")
                        except Exception as e:
                            logger.error(f"[{feed_id}] Failed to save snapshot: {e}")
                        finally:
                            snapshot_request = None

                    # 4. Video Writer (Conditional)
                    if video_writer_queue:
                        try:
                            # Only copy and draw if we have a writer and it's not full
                            vis_frame = visualize_data(frame.copy(), tracked_vehicles, metrics, vis_options, config, feed_id)
                            video_writer_queue.put_nowait(vis_frame)
                        except queue.Full:
                            pass 
                        except Exception as e:
                            logger.error(f"[{feed_id}] Video writer error: {e}")

                    # 5. Stream Preparation with Backpressure Monitoring
                    try:
                        # Optimization: Use qsize heuristic to avoid unnecessary encoding
                        q_max = config.get("video_input", {}).get("max_queue_size", 500)
                        try:
                            q_current = frame_queue.qsize()
                        except NotImplementedError:
                            q_current = 0
                        
                        if q_current >= q_max - 5:
                            dropped_frames += 1
                        else:
                            # Resize raw frame for stream
                            stream_frame = cv2.resize(frame, stream_res, interpolation=cv2.INTER_LINEAR)
                            success, buffer = cv2.imencode(".jpg", stream_frame, encode_params)
                            
                            if success:
                                # Calculate scale factors for frontend drawing
                                orig_h, orig_w = frame.shape[:2]
                                target_w, target_h = stream_res
                                scale_x = target_w / orig_w if orig_w > 0 else 1.0
                                scale_y = target_h / orig_h if orig_h > 0 else 1.0
                                
                                serialized_vehicles = _serialize_tracked_vehicles(tracked_vehicles, scale_x, scale_y)
                                
                                try:
                                    frame_queue.put_nowait((feed_id, frame_index, buffer.tobytes(), metrics, serialized_vehicles, {}))
                                    encoded_frames += 1
                                except queue.Full:
                                     dropped_frames += 1
                            else:
                                logger.error(f"[{feed_id}] Frame encoding failed.")
                    except Exception as e:
                        logger.error(f"[{feed_id}] Stream preparation error: {e}")

                else:
                    # Pass-through mode (No processing)
                    try:
                        q_max = config.get("video_input", {}).get("max_queue_size", 500)
                        try:
                            q_current = frame_queue.qsize()
                        except NotImplementedError:
                            q_current = 0
                        
                        if q_current < q_max - 5:
                            stream_frame = cv2.resize(frame, stream_res, interpolation=cv2.INTER_LINEAR)
                            success, buffer = cv2.imencode(".jpg", stream_frame, encode_params)
                            if success:
                                try:
                                    frame_queue.put_nowait((feed_id, frame_index, buffer.tobytes(), {}, [], {}))
                                    encoded_frames += 1
                                except queue.Full:
                                    dropped_frames += 1
                    except Exception as e:
                        logger.error(f"[{feed_id}] Pass-through error: {e}")

                # --- Periodic Metrics Logging ---
                frame_count += 1
                now = time.time()
                if now - last_log_time > 10.0:
                    elapsed = now - last_log_time
                    fps = frame_count / elapsed
                    process_ratio = processed_count / max(1, frame_count)
                    drop_rate = (dropped_frames / max(1, dropped_frames + encoded_frames)) * 100
                    
                    logger.info(
                        f"[{feed_id}] FPS: {fps:.2f} | "
                        f"Processed: {process_ratio*100:.1f}% | "
                        f"Dropped: {drop_rate:.1f}% | "
                        f"Total: {frame_count}"
                    )
                    
                    frame_count = 0
                    processed_count = 0
                    skipped_count = 0
                    dropped_frames = 0
                    encoded_frames = 0
                    last_log_time = now

            except Exception as e:
                logger.error(f"[{feed_id}] Unhandled error processing frame {frame_index}: {e}", exc_info=True)
            finally:
                # Critical Memory Cleanup
                if 'frame' in locals(): del frame
                if 'stream_frame' in locals(): del stream_frame
                if 'vis_frame' in locals(): del vis_frame
                if 'snap_frame' in locals(): del snap_frame

    except Exception as e:
        logger.critical(f"[{feed_id}] FATAL Process Error: {e}", exc_info=True)
        if error_queue:
            error_queue.put(f"[{feed_id}] FATAL: {e}")
    finally:
        logger.info(f"[{feed_id}] Shutting down...")
        if reader:
            reader.stop()
        if core_module:
            core_module.cleanup()
        
        # Drain video writer queue to unblock writer process
        if video_writer_queue:
            try:
                # Send poison pill
                video_writer_queue.put(None, timeout=1.0)
                
                # Drain remaining items to prevent queue.Full errors in writer
                drained = 0
                while drained < 100:  # Limit to prevent infinite loop
                    try:
                        video_writer_queue.get_nowait()
                        drained += 1
                    except queue.Empty:
                        break
                
                if drained > 0:
                    logger.info(f"[{feed_id}] Drained {drained} frames from video writer queue")
            except Exception as e:
                logger.error(f"[{feed_id}] Error draining video writer queue: {e}")

        logger.info(f"[{feed_id}] Process terminated.")