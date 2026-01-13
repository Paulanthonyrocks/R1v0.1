import os
import cv2
import logging
import time
import numpy as np
import queue
from pathlib import Path
from typing import Dict, Any, List, Optional
from multiprocessing import Queue as MPQueue, Event

from ..core.core_module import CoreModule
from ..utils.monitoring import TrafficMonitor
from ..utils.process import start_parent_monitor

logger = logging.getLogger("Inference")

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
    v_map = CoreModule.vehicle_type_map if CoreModule else {}
    
    for vehicle_id, data in tracked_vehicles.items():
        try:
            c_id = data.get("class_id", -1)
            c_name = v_map.get(c_id, "unknown")
            bbox = data.get("bbox")
            scaled_bbox = []
            if bbox and len(bbox) == 4:
                scaled_bbox = [bbox[0] * scale_x, bbox[1] * scale_y, bbox[2] * scale_x, bbox[3] * scale_y]

            serialized_list.append({
                "vehicle_id": str(vehicle_id),
                "bbox": [_make_serializable(x) for x in scaled_bbox],
                "speed": _make_serializable(data.get("speed", 0)),
                "license_plate": str(data.get("license_plate", "Unknown")),
                "class_id": int(c_id),
                "class_name": c_name,
                "behavior": str(data.get("behavior", "unknown")),
                "confidence": _make_serializable(data.get("confidence", 0)),
                "lane": int(data.get("lane", -1)),
                "status": str(data.get("status", "unknown")),
                "car_model": data.get("car_model"),
                "car_model_confidence": _make_serializable(data.get("car_model_confidence", 0)),
            })
        except Exception:
            continue
    return serialized_list

def inference_worker(
    worker_id: int,
    central_input_queue: MPQueue,
    central_output_queue: MPQueue,
    command_queue: MPQueue,
    stop_event: Event,
    config: Dict[str, Any],
    db_queue: Optional[MPQueue] = None
):
    """
    Heavyweight AI process that processes frames from the central queue.
    Can handle frames from multiple feeds interleaved.
    """
    # Initialize logging for the child process
    import logging.config
    try:
        logging.config.dictConfig(config["logging"])
    except Exception as e:
        print(f"DEBUG: Worker {worker_id} failed to init logging: {e}")

    pid = os.getpid()
    print(f"DEBUG: Inference process {pid} (Worker {worker_id}) entering initialization...")
    logger.info(f"Inference process {pid} (Worker {worker_id}) started.")
    
    # Start parent monitor to avoid zombies
    print(f"DEBUG: [Worker {worker_id}] Starting parent monitor...")
    start_parent_monitor(stop_event, f"Inference-{worker_id}")
    
    print(f"DEBUG: [Worker {worker_id}] Initializing state containers...")
    # Per-feed CoreModules and Monitors (lazy initialized)
    core_modules: Dict[str, CoreModule] = {}
    traffic_monitors: Dict[str, TrafficMonitor] = {}
    pending_configs: Dict[str, Dict] = {}
    shared_model = None
    
    # Pre-extract shared config
    vehicle_det_cfg = config.get("vehicle_detection", {})
    target_fps = config.get("video_processing", {}).get("target_fps", 15)
    ocr_cfg = config.get("ocr_engine", {})
    stream_res = tuple(config.get("video_output", {}).get("stream_resolution", (640, 480)))
    skip_frames = vehicle_det_cfg.get("skip_frames", 2)

    # Pre-load shared model if possible to save memory
    model_path = vehicle_det_cfg.get("model_path")
    gpu_enabled = config.get("performance", {}).get("gpu_acceleration", False)
    
    if model_path:
        print(f"DEBUG: [Worker {worker_id}] Pre-loading shared model: {model_path} (GPU: {gpu_enabled})")
        try:
            # Resolve path relative to project root
            resolved_path = Path(config.get("project_root_dir", "")) / model_path
            
            if str(resolved_path).endswith((".onnx", "_quant.onnx")):
                import onnxruntime as ort
                providers = ["CPUExecutionProvider"]
                if gpu_enabled:
                    if "CUDAExecutionProvider" in ort.get_available_providers():
                        providers.insert(0, "CUDAExecutionProvider")
                        print(f"DEBUG: [Worker {worker_id}] Enabled CUDA for ONNX shared model")
                    else:
                         print(f"DEBUG: [Worker {worker_id}] CUDA requested but not available for ONNX")
                
                shared_model = ort.InferenceSession(str(resolved_path), providers=providers)
            else:
                from ultralytics import YOLO
                import torch
                shared_model = YOLO(str(resolved_path))
                
                target_device = "cpu"
                if gpu_enabled and torch.cuda.is_available():
                    target_device = "cuda"
                    print(f"DEBUG: [Worker {worker_id}] Moving YOLO shared model to CUDA")
                
                shared_model.to(target_device)

            print(f"DEBUG: [Worker {worker_id}] Shared model loaded successfully.")
        except Exception as e:
            print(f"DEBUG: [Worker {worker_id}] Failed to preload model: {e}")

    print(f"DEBUG: [Worker {worker_id}] Entering main processing loop.")

    def handle_command(cmd):
        nonlocal skip_frames
        if not cmd: return
        feed_id = cmd.get("feed_id")
        
        if cmd.get("type") == "config_update":
            data = cmd.get("data", {})
            if "skip_frames" in data:
                try:
                    skip_frames = int(data["skip_frames"])
                    logger.info(f"[Worker {worker_id}] Updated skip_frames to {skip_frames}")
                except: pass
            
            if not feed_id: return
            logger.info(f"[Worker {worker_id}] Received config update command for {feed_id}")
            
            cm_update = data.copy()
            
            # Transform ROI for CoreModule
            if "roi" in data:
                if "roi_processing" not in cm_update:
                    cm_update["roi_processing"] = {}
                
                roi = data["roi"]
                if roi and isinstance(roi, list) and len(roi) >= 3:
                    # Pass as list of [x, y] floats
                    try:
                        normalized_points = [[p['x'], p['y']] for p in roi]
                        cm_update["roi_processing"]["roi_points_normalized"] = normalized_points
                        cm_update["roi_processing"]["enabled"] = True
                        logger.info(f"[Worker {worker_id}] Parsed ROI update for {feed_id}")
                    except Exception as e:
                        logger.error(f"[Worker {worker_id}] Failed to parse ROI: {e}")
                else:
                    cm_update["roi_processing"]["roi_points_normalized"] = None
                    cm_update["roi_processing"]["enabled"] = False
                    logger.info(f"[Worker {worker_id}] ROI disabled in update for {feed_id}")

            if feed_id in core_modules:
                core_modules[feed_id].update_config(cm_update)
                logger.info(f"[Worker {worker_id}] Applied config update to active CoreModule for {feed_id}")
            else:
                # Store in pending to be applied upon initialization
                if feed_id not in pending_configs:
                    pending_configs[feed_id] = {}
                pending_configs[feed_id].update(cm_update)
                logger.info(f"[Worker {worker_id}] Stored pending config update for {feed_id}")

    try:
        while not stop_event.is_set():
            # 0. Check for commands first (broadcast channel)
            try:
                while True:
                    cmd = command_queue.get_nowait()
                    handle_command(cmd)
            except queue.Empty:
                pass

            try:
                # 1. Get a frame task from the queue
                # Format: (feed_id, frame_index, frame_bytes, timestamp_or_payload)
                task = central_input_queue.get(timeout=0.1) # Shorter timeout to check commands more often
                if task is None: continue
                
                feed_id, frame_index, frame_bytes, extra_payload = task

                # --- OLD CONTROL MESSAGE HANDLING REMOVED ---
                if frame_index == -1:
                    continue
                # --------------------------------

                # 3. Lazy initialize feed logic
                if feed_id not in core_modules:
                    logger.info(f"[Worker {worker_id}] Initializing core for feed {feed_id}")
                    core_modules[feed_id] = CoreModule(
                        feed_id=feed_id,
                        model_path=vehicle_det_cfg.get("model_path"),
                        config=config,
                        fps=target_fps,
                        db_queue=db_queue,
                        gemini_api_key=ocr_cfg.get("gemini_api_key"),
                        model_type=vehicle_det_cfg.get("model_type", "yolo"),
                        preloaded_model=shared_model
                    )
                    traffic_monitors[feed_id] = TrafficMonitor(config)
                    
                    # Apply any pending configs
                    if feed_id in pending_configs:
                        logger.info(f"[Worker {worker_id}] Applying pending config to new CoreModule for {feed_id}")
                        core_modules[feed_id].update_config(pending_configs.pop(feed_id))
                
                core = core_modules[feed_id]
                monitor = traffic_monitors[feed_id]
                
                # 4. Process AI with Frame Skipping
                # Logic: Detect every (skip_frames + 1) frames, predict on others.
                # Always detect if no tracks exist.
                should_detect = (frame_index % (skip_frames + 1) == 0) or (not core.vehicle_data)
                
                if should_detect:
                    # 2. Decode frame only if we need to detect
                    nparr = np.frombuffer(frame_bytes, np.uint8)
                    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if frame is None: continue
                    
                    tracked_vehicles = core.detect_and_track(
                        frame, frame_index
                    )
                else:
                    # Skip detection, use Kalman Filter prediction
                    tracked_vehicles = core.predict_only(frame_index)
                
                monitor.update_vehicles(tracked_vehicles)
                metrics = monitor.get_metrics()
                
                # 5. Serialize and push results
                # Scale factors are 1.0 since ingestion already resized to stream_res
                serialized_vehicles = _serialize_tracked_vehicles(tracked_vehicles, 1.0, 1.0)
                
                try:
                    # Format: (feed_id, frame_index, frame_bytes, metrics, vehicles, extra)
                    central_output_queue.put_nowait((
                        feed_id, frame_index, frame_bytes, metrics, serialized_vehicles, {}
                    ))
                except queue.Full:
                    pass

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"[Worker {worker_id}] Error: {e}", exc_info=True)

    finally:
        for cm in core_modules.values():
            cm.cleanup()
        logger.info(f"Inference process {pid} terminated.")
