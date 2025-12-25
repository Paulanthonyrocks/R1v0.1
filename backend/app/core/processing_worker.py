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
            })
        except Exception:
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
    is_looped: bool = False
) -> None:
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

    logger.info(f"Process {pid} started for {feed_id}")

    # --- Parent Monitoring (Anti-Zombie) ---
    def _monitor_parent(stop_evt, orig_ppid):
        # Allow brief startup grace period
        time.sleep(2.0)
        
        while not stop_evt.is_set():
            try:
                curr_ppid = os.getppid()
                parent_dead = False
                
                if curr_ppid != orig_ppid:
                    parent_dead = True
                else:
                    try:
                         # Signal 0 checks if process exists and we can send signals
                         os.kill(orig_ppid, 0)
                    except OSError:
                         parent_dead = True
                
                if parent_dead:
                    # Parent is gone. We must die.
                    stop_evt.set()
                    
                    # Log attempt (might fail if pipes broken)
                    try:
                        logger.warning(f"[{feed_id}] Parent {orig_ppid} gone (curr: {curr_ppid}). Initiating shutdown.")
                    except:  # noqa: E722
                        pass
                    
                    # Give the main loop a moment to see stop_evt and exit cleanly
                    time.sleep(3.0)
                    
                    # If we are still here, force exit
                    try:
                        logger.warning(f"[{feed_id}] Force terminating self (parent gone).")
                    except:  # noqa: E722
                        pass
                        
                    # 1. Try SIGTERM first
                    os.kill(os.getpid(), signal.SIGTERM)
                    time.sleep(1.0)
                    
                    # 2. SIGKILL if still alive
                    os.kill(os.getpid(), signal.SIGKILL)
                    break
                    
            except (Exception) as e: # Catch all exceptions to prevent the monitor thread from crashing
                # Should not happen, but if monitor fails, we default to safety
                try:
                    logger.error(f"[{feed_id}] Monitor thread error: {e}")
                except:  # noqa: E722
                    pass
                # Don't kill immediately on glitch, but sleep and retry or exit?
                # For safety, if monitoring is broken, maybe we should exit.
                # But let's just log for now to avoid premature death on transient errors.
                
            time.sleep(1.0)

    parent_monitor = threading.Thread(target=_monitor_parent, args=(stop_event, os.getppid()), daemon=True)
    parent_monitor.start()

    # --- Config Extraction (Optimization) ---
    vehicle_det_cfg = config.get("vehicle_detection", {})
    video_out_cfg = config.get("video_output", {})
    ocr_cfg = config.get("ocr_engine", {})
    
    target_fps = config.get("video_processing", {}).get("target_fps", 30)
    skip_interval = max(1, vehicle_det_cfg.get("skip_frames", 1))
    stream_res = tuple(video_out_cfg.get("stream_resolution", (640, 480)))
    processing_enabled = video_out_cfg.get("processing_enabled", True)
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), 80]

    # Pre-extract location data
    lat, lon = 0.0, 0.0
    if feed_config_info:
        if isinstance(feed_config_info, dict):
            lat = feed_config_info.get("latitude", 0.0)
            lon = feed_config_info.get("longitude", 0.0)
        else:
            lat = getattr(feed_config_info, "latitude", 0.0)
            lon = getattr(feed_config_info, "longitude", 0.0)

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
                if stop_event.is_set():
                    break
                time.sleep(0.005) # Reduced sleep for better responsiveness
                continue

            tracked_vehicles = {}
            metrics = {}
            serialized_vehicles = []
            
            if core_module and traffic_monitor:
                # 1. Detection / Tracking
                if frame_index % skip_interval == 0:
                    tracked_vehicles = core_module.detect_and_track(
                        frame, frame_index, confidence_threshold, proximity_threshold, track_timeout
                    )
                else:
                    tracked_vehicles = core_module.predict_only(frame_index)

                # 2. Update Statistics
                traffic_monitor.update_vehicles(tracked_vehicles)
                metrics = traffic_monitor.get_metrics()
                metrics["latitude"] = lat
                metrics["longitude"] = lon
                
                # 3. Video Writer (Conditional)
                # OPTIMIZATION: Only visualize if someone is listening (writer enabled)
                if video_writer_queue:
                    try:
                        # Only copy and draw if we have a writer
                        vis_frame = visualize_data(frame.copy(), tracked_vehicles, metrics, vis_options, config, feed_id)
                        video_writer_queue.put_nowait(vis_frame)
                    except queue.Full:
                        pass 
                    except Exception as e:
                        logger.error(f"[{feed_id}] Video writer error: {e}")

                # 4. Stream Preparation
                # OPTIMIZATION: Check queue size BEFORE expensive encoding
                # On Linux, qsize() is reliable. If queue is full, skipping encoding saves significant CPU.
                q_max = config.get("video_input", {}).get("max_queue_size", 500)
                q_current = 0
                try:
                    q_current = frame_queue.qsize()
                except NotImplementedError:
                    # Fallback for OS where qsize is not available (macOS)
                    pass
                
                if q_current >= q_max - 5:
                    # Queue is effectively full. Drop frame early.
                    now = time.time() # Define 'now' before use in this block
                    if now - last_log_time > 5.0: # Reuse last_log_time or add new throttler
                         pass # Don't spam logs for every dropped frame
                else:
                    try:
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
                            except queue.Full:
                                 # Should be rare due to qsize check, but possible due to race condition
                                 pass
                        else:
                            logger.error(f"[{feed_id}] Frame encoding failed.")
                    except Exception as e:
                        logger.error(f"[{feed_id}] Encoding error: {e}")

            else:
                # Pass-through mode
                # Apply same optimization for pass-through
                q_max = config.get("video_input", {}).get("max_queue_size", 500)
                q_current = 0
                try:
                    q_current = frame_queue.qsize()
                except NotImplementedError:
                    pass
                
                if q_current < q_max - 5:
                    try:
                        stream_frame = cv2.resize(frame, stream_res, interpolation=cv2.INTER_LINEAR)
                        success, buffer = cv2.imencode(".jpg", stream_frame, encode_params)
                        if success:
                            try:
                                frame_queue.put_nowait((feed_id, frame_index, buffer.tobytes(), {}, [], {}))
                            except queue.Full:
                                pass
                        else:
                            logger.error(f"[{feed_id}] Frame encoding failed in pass-through mode.")
                    except Exception as e:
                        logger.error(f"[{feed_id}] Pass-through error: {e}")

            # --- Loop Maintenance ---
            frame_count += 1
            
            # Periodic logging
            now = time.time()
            if now - last_log_time > 10.0:
                fps = frame_count / (now - last_log_time)
                logger.info(f"[{feed_id}] Speed: {fps:.2f} FPS | Frames: {frame_count}")
                last_log_time = now
                frame_count = 0
            
            # REMOVED: gc.collect() - Reliance on Python's ref counting is better for real-time video

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
        if video_writer_queue:
            video_writer_queue.put(None)
        logger.info(f"[{feed_id}] Process terminated.")