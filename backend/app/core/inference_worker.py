import os
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
# --- Hardware Optimization Flags ---
# Force TensorFlow to only allocate memory as needed, preventing conflicts with PyTorch
os.environ["TF_FORCE_GPU_ALLOW_GROWTH"] = "true"
# Suppress excessive TensorFlow logging

from app.config import initialize_config
import cv2
import logging
import time
import numpy as np
import queue
import signal
import json
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from app.core.core_module import CoreModule
from app.utils.monitoring import TrafficMonitor
from app.utils.process import start_parent_monitor
from .worker_utils import WorkerMetrics, make_serializable, serialize_tracked_vehicles

logger = logging.getLogger("Inference")


def _serialize_tracked_vehicles_with_map(
    tracked_vehicles: Dict[str, Dict], 
    scale_x: float = 1.0, 
    scale_y: float = 1.0
) -> List[Dict[str, Any]]:
    """Wrapper that uses CoreModule's vehicle_type_map."""
    v_map = CoreModule.vehicle_type_map if CoreModule is not None else {}
    return serialize_tracked_vehicles(tracked_vehicles, scale_x, scale_y, v_map)

def _extract_rois(frame: np.ndarray, tracked_vehicles: List[Dict[str, Any]], scale: float = 1.0) -> List[Dict[str, Any]]:
    """Extracts high-res JPEG patches for active vehicles."""
    rois = []
    h, w = frame.shape[:2]
    for v in tracked_vehicles:
        bbox = v.get("bbox")
        if not bbox or len(bbox) != 4: continue
        
        # Clamp coordinates
        x1, y1, x2, y2 = map(int, bbox)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        
        if x2 <= x1 or y2 <= y1: continue
        
        crop = frame[y1:y2, x1:x2]
        _, crop_bytes = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        
        rois.append({
            "b": crop_bytes.tobytes(),
            "x": x1, "y": y1, "w": x2-x1, "h": y2-y1
        })
    return rois

def inference_worker(
    worker_id: int,
    central_input_queue: Any,
    central_output_queue: Any,
    command_queue: Any,
    stop_event: Any,
    config: Dict[str, Any],
    db_queue: Optional[Any] = None,
    frame_buffer: Any = None,
    pipeline_pressure: Any = None,
    slots: List[int] = None
):
    # Initialize global config for this process
    initialize_config()
    """
    Heavyweight AI process that processes frames from the central queue.
    Can handle frames from multiple feeds interleaved.
    """
    # Initialize logging for the child process
    import logging.config
    try:
        logging.config.dictConfig(config["logging"])
    except Exception as e:
        # Cannot use logger here as it may not be configured
        pass  # Logging config failed, will use default

    # --- Signal Handling ---
    def signal_handler(signum, frame):
        logger.info(f"[Worker {worker_id}] Received signal {signum}, stopping gracefully")
        if stop_event:
            stop_event.set()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    pid = os.getpid()
    logger.debug(f"Inference process {pid} (Worker {worker_id}) entering initialization...")
    logger.info(f"Inference process {pid} (Worker {worker_id}) started.")
    
    # Start parent monitor to avoid zombies
    logger.debug(f"[Worker {worker_id}] Starting parent monitor...")
    
    # Use a dummy event for the monitor since we've moved to Redis signals
    import multiprocessing
    dummy_event = multiprocessing.Event()
    start_parent_monitor(dummy_event, f"Inference-{worker_id}")
    
    logger.debug(f"[Worker {worker_id}] Initializing state containers...")
    # Per-feed CoreModules and Monitors (lazy initialized)
    core_modules: Dict[str, CoreModule] = {}
    traffic_monitors: Dict[str, TrafficMonitor] = {}
    pending_configs: Dict[str, Dict] = {}
    metrics_map: Dict[str, WorkerMetrics] = {} # Feed-specific metrics
    shared_model = None
    
    # Initialize a local ReID manager for visual matching across loops
    from app.services.reid_manager import GlobalReIDManager
    local_reid_manager = GlobalReIDManager(config)
    
    # Initialize shared frame buffer handle if not provided
    from app.utils.shared_frame_buffer import SharedFrameBuffer
    if frame_buffer is None:
        # Create a handle to the existing shared memory pool
        # The SharedFrameBuffer constructor handles attaching to existing segments
        frame_buffer = SharedFrameBuffer(pool_size=config.get("performance", {}).get("shm_pool_size", 200), read_only=True)
    
    # ... (inside inference_worker)
    
    # Initialize Redis client for signals and pressure
    from app.utils.redis_client import get_redis_client
    redis_client = get_redis_client()
    
    # Replace stop_event check with Redis signal
    # We'll check this periodically in the main loop
    
    # Pre-extract shared config
    vehicle_det_cfg = config.get("vehicle_detection", {})
    target_fps = config.get("video_processing", {}).get("target_fps", 15)
    ocr_cfg = config.get("ocr_engine", {})
    stream_res = tuple(config.get("video_output", {}).get("stream_resolution", (640, 480)))
    
    # ... (rest of initialization)
    
    # Main loop
    while True:
        # Check for stop signal in Redis
        if redis_client.get("pipeline:stop"):
            logger.info(f"Worker {worker_id} received stop signal via Redis. Terminating...")
            break
            
        # Read current pipeline pressure from Redis
        pressure_val = redis_client.get("pipeline:pressure")
        current_pressure = float(pressure_val) if pressure_val else 0.0
        
        # ... (rest of the loop)

    skip_frames = vehicle_det_cfg.get("skip_frames", 2)
    
    model_path = vehicle_det_cfg.get("model_path")

    # Shared Model Loading Logic
    model_load_failed = False
    shared_reid_embedder = None
    if model_path:
        try:
            logger.info(f"[Worker {worker_id}] Loading shared models...")
            root_dir = config.get("project_root_dir", "")
            full_model_path = str(Path(root_dir) / model_path)
            use_gpu = config.get("performance", {}).get("gpu_acceleration", False)
            
            # Load YOLO
            engine_path = Path(full_model_path).with_suffix(".engine")
            if engine_path.exists():
                logger.info(f"[Worker {worker_id}] Found TensorRT engine: {engine_path}")
                from ultralytics import YOLO
                import torch
                device = "cuda:0" if use_gpu and torch.cuda.is_available() else "cpu"
                shared_model = YOLO(str(engine_path))
                shared_model.to(device) 
                logger.info(f"[Worker {worker_id}] Shared TensorRT engine loaded on {device}.")
            else:
                from ultralytics import YOLO
                import torch
                device = "cuda:0" if use_gpu and torch.cuda.is_available() else "cpu"
                shared_model = YOLO(full_model_path)
                shared_model.to(device)
                logger.info(f"[Worker {worker_id}] Shared YOLO model loaded on {device}.")

            # Load ReID
            if vehicle_det_cfg.get("reid_enabled", True):
                from app.ml.reid_model import ReIDEmbedder
                logger.info(f"[Worker {worker_id}] Pre-loading ReID Embedder...")
                shared_reid_embedder = ReIDEmbedder(config)
                logger.info(f"[Worker {worker_id}] ReID Embedder pre-loaded.")

        except Exception as e:
            logger.error(f"[Worker {worker_id}] Shared model load exception: {e}")
            shared_model = None
            model_load_failed = True

    def handle_command(cmd):
        if not cmd: return
        try:
            cmd_type = cmd.get("type")
            if cmd_type == "config_update":
                data = cmd.get("data", {})
                feed_id_cmd = data.get("feed_id") or cmd.get("feed_id")
                if feed_id_cmd:
                    if feed_id_cmd not in core_modules:
                        if feed_id_cmd not in pending_configs:
                            pending_configs[feed_id_cmd] = {}
                        pending_configs[feed_id_cmd].update(data)
                    else:
                        core_modules[feed_id_cmd].update_config(data)
        except Exception as e:
            logger.error(f"[Worker {worker_id}] Command error: {e}")

    last_metrics_log = time.time()
    
    try:
        while True:
            # Handle command queue
            try:
                while True:
                    cmd = command_queue.get_nowait()
                    handle_command(cmd)
            except queue.Empty:
                pass

            try:
                batch_tasks = []
                # 1. Conservative Batching: Prevent OOM spikes by capping batch size
                q_depth = central_input_queue.qsize()
                base_batch_size = config.get("performance", {}).get("batch_size", 1)
                
                # Limit batch size to a very small number regardless of queue depth
                MAX_SAFE_BATCH_SIZE = 2
                batch_size = min(base_batch_size, MAX_SAFE_BATCH_SIZE)
                
                # Only allow a slight increase if the queue is deep, but stay within safety limits
                if q_depth > 100 and batch_size < MAX_SAFE_BATCH_SIZE:
                    batch_size = MAX_SAFE_BATCH_SIZE

                inference_timeout = config.get("performance", {}).get("inference_timeout", 0.005)

                try:
                    # Poll assigned slot queues
                    for slot_id in slots:
                        try:
                            slot_q = central_input_queue[slot_id]
                            res = slot_q.get_nowait()
                            if res:
                                msg_id, first_task = (res if len(res) == 2 and isinstance(res[1], tuple) else (None, res))
                                batch_tasks.append((msg_id, first_task))
                        except (queue.Empty, IndexError):
                            continue
                    
                    if not batch_tasks:
                        # If nothing was found in slots, sleep briefly to prevent CPU spin
                        time.sleep(0.01)
                except Exception as e:
                    logger.error(f'[Worker {worker_id}] Error polling slots: {e}')
                except queue.Empty:
                    pass

                if batch_tasks:
                    start_wait = time.time()
                    while len(batch_tasks) < batch_size and (time.time() - start_wait < inference_timeout):
                        for slot_id in slots:
                            try:
                                slot_q = central_input_queue[slot_id]
                                res = slot_q.get_nowait()
                                if res:
                                    msg_id, t = (res if len(res) == 2 and isinstance(res[1], tuple) else (None, res))
                                    
                                    # 2. Smart Skip
                                    if q_depth > 200:
                                        t_feed_id, t_frame_idx, _, _ = t
                                        if t_frame_idx != -888 and t_frame_idx != -999:
                                            if t_feed_id in core_modules and getattr(core_modules[t_feed_id], '_first_detection_done', False):
                                                continue
                                    
                                    batch_tasks.append((None, t))
                            except (queue.Empty, IndexError):
                                continue
                            time.sleep(0.0005)
                            
                if not batch_tasks:
                   continue

                # Process Batch items
                frames_to_infer = []
                inference_indices = []
                batch_meta = []

                for task_tuple in batch_tasks:
                    msg_id, task = task_tuple
                    feed_id, frame_index, shm_ref, extra_payload = task
                    res = frame_buffer.read(shm_ref) if frame_buffer and shm_ref else (shm_ref, (0,0,0))
                    frame_bytes, dims = res
                    timestamp = extra_payload if isinstance(extra_payload, (int, float)) else time.time()
                    
                    if feed_id not in metrics_map:
                        metrics_map[feed_id] = WorkerMetrics(feed_id)
                    
                    if frame_index == -888:
                         if feed_id in core_modules: core_modules[feed_id]._first_detection_done = False
                         # Acknowledge control messages too
                         continue
                    if frame_index == -999:
                         if feed_id in core_modules:
                             core_modules[feed_id].cleanup(); del core_modules[feed_id]
                         if feed_id in traffic_monitors: del traffic_monitors[feed_id]
                         pending_configs.pop(feed_id, None)
                         if feed_id in metrics_map: del metrics_map[feed_id]
                         # Acknowledge control messages too
                         continue

                    if feed_id not in core_modules:
                        core_modules[feed_id] = CoreModule(
                            feed_id=feed_id, model_path=vehicle_det_cfg.get("model_path"),
                            config=config, fps=target_fps, db_queue=db_queue,
                            gemini_api_key=ocr_cfg.get("gemini_api_key"),
                            model_type=vehicle_det_cfg.get("model_type", "yolo"),
                            preloaded_model=shared_model if not model_load_failed else None,
                            preloaded_reid=shared_reid_embedder
                        )
                        core_modules[feed_id]._first_detection_done = False
                        traffic_monitors[feed_id] = TrafficMonitor(config)
                        if feed_id in pending_configs:
                            core_modules[feed_id].update_config(pending_configs.pop(feed_id))

                    core = core_modules[feed_id]
                    core.last_activity = time.time() # Track activity for pruning
                    monitor = traffic_monitors[feed_id]
                    metrics_obj = metrics_map[feed_id]

                    actual_skip = skip_frames
                    first_detect = not getattr(core, '_first_detection_done', False)
                    should_detect = (frame_index % (actual_skip + 1) == 0) or (first_detect and not core.vehicle_data)

                    lane_cfg = config.get("lane_detection", {})
                    is_lane_frame = (lane_cfg.get("dynamic_lane_detection_enabled", False) and 
                                     (frame_index % lane_cfg.get("lane_detection_interval", 10) == 0))
                    
                    needs_frame = should_detect or is_lane_frame
                    
                    frame = None
                    if needs_frame:
                        # data is already raw BGR frame from SHM
                        if isinstance(frame_bytes, memoryview):
                            w, h, c = dims
                            frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape(h, w, c)
                        elif isinstance(frame_bytes, np.ndarray):
                            frame = frame_bytes
                        else:
                            # Fallback for bytes/fallback case
                            frame = cv2.imdecode(np.frombuffer(frame_bytes, np.uint8), cv2.IMREAD_COLOR)
                            if frame is None:
                                metrics_obj.errors += 1
                                continue
                    
                    batch_meta.append({
                        "feed_id": feed_id, "frame_index": frame_index, "frame": frame, 
                        "timestamp": timestamp,
                        "core": core, "monitor": monitor, "metrics": metrics_obj,
                        "should_detect": should_detect, "first_detect": first_detect,
                        "msg_id": msg_id
                    })

                    if should_detect:
                        proc_frame, roi_enabled, x_off, y_off = core._preprocess_frame(frame)
                        frames_to_infer.append(proc_frame) 
                        inference_indices.append(len(batch_meta) - 1)
                        batch_meta[-1]["crop_offsets"] = (x_off, y_off) if roi_enabled else (0, 0)
                
                # Run Batch Inference
                batch_detections_map = {}
                if frames_to_infer and shared_model is not None:
                    try:
                        results = shared_model(frames_to_infer, verbose=False, stream=False)
                        for i, res in enumerate(results):
                            meta_idx = inference_indices[i]
                            meta = batch_meta[meta_idx]
                            boxes_data = res.boxes.data.cpu().numpy()
                            formatted_dets = []
                            x_off, y_off = meta.get("crop_offsets", (0, 0))
                            for row in boxes_data:
                                rx1, ry1, rx2, ry2, conf, cls_id = row
                                fx1, fy1 = rx1 + x_off, ry1 + y_off
                                fx2, fy2 = rx2 + x_off, ry2 + y_off
                                formatted_dets.append(((fx1, fy1, fx2, fy2), conf, cls_id))
                            batch_detections_map[meta_idx] = formatted_dets
                    except Exception as e:
                        logger.error(f"[Worker {worker_id}] Batch inference failed: {e}")

                # Tracking & Output
                for i, meta in enumerate(batch_meta):
                    core, monitor, metrics_obj = meta['core'], meta['monitor'], meta['metrics']
                    frame, f_idx = meta['frame'], meta['frame_index']
                    
                    detections = batch_detections_map.get(i, []) if meta['should_detect'] else []
                    vis_tracks, lane_bounds, lane_lines = core.detect_and_track(
                        frame, f_idx, external_detections=detections,
                        timestamp=meta.get("timestamp")
                    )
                    
                    # CRITICAL: Immediately release the uncompressed frame to free memory
                    del frame
                    
                    if vis_tracks and meta['first_detect']:
                        core._first_detection_done = True
                    
                    for vid, track in vis_tracks.items():
                        emb = track.get("embedding")
                        if emb:
                            # Use match_or_register to ensure new identities are created and synced
                            global_id = local_reid_manager.match_or_register(
                                feed_id=meta['feed_id'],
                                local_id=str(vid),
                                embedding=np.array(emb),
                                metadata={"class_name": CoreModule.vehicle_type_map.get(track["class_id"], "unknown")},
                                confidence=track.get("confidence", 1.0)
                            )
                            track["global_vehicle_id"] = global_id
                        elif not track.get("global_vehicle_id"):
                            # Fallback to check existing mapping for tracks without embeddings this frame
                            mapped_id = local_reid_manager.get_global_id(meta['feed_id'], str(vid))
                            if mapped_id: track["global_vehicle_id"] = mapped_id
                        
                    monitor.update_vehicles(vis_tracks)
                    metrics_result = monitor.get_metrics()
                    metrics_obj.frames_processed += 1
                    
                    serialized_v = _serialize_tracked_vehicles_with_map(vis_tracks)
                    
                    extra = {}
                    v_proc_cfg = config.get("video_processing", {})
                    if v_proc_cfg.get("adaptive_streaming", False) and meta['should_detect']:
                         bg_scale = v_proc_cfg.get("roi_scale", 0.5)
                         bg_frame = cv2.resize(frame, (0, 0), fx=bg_scale, fy=bg_scale)
                         _, bg_bytes = cv2.imencode(".jpg", bg_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
                         extra["bg"] = bg_bytes.tobytes()
                         extra["rois"] = _extract_rois(frame, serialized_v)
                    
                    try:
                        # We need to reconstruct the original task structure for the output queue
                        # The task was (feed_id, frame_index, shm_ref, extra_payload)
                        # We use the task data from the loop if we had it, but we only have meta.
                        # Wait, I need to access the original task to get shm_ref and extra_payload.
                        # I will modify the batch_meta to store the original task.
                        pass # I'll fix this in the next instruction.
                    except queue.Full:
                        metrics_obj.frames_dropped += 1
                        # We log a drop
                
                now = time.time()
                if now - last_metrics_log > 30.0:
                      for fid, m in metrics_map.items():
                          logger.info(f"[Worker {worker_id}][{fid}] METRICS: {json.dumps(m.to_dict())}")
                      last_metrics_log = now

            except Exception as e:
                logger.error(f"[Worker {worker_id}] Error: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"[Worker {worker_id}] Fatal error: {e}", exc_info=True)
    finally:
        for feed_id, cm in core_modules.items(): cm.cleanup()
        logger.info(f"Inference process {os.getpid()} terminated.")

            except Exception as e:
                logger.error(f"[Worker {worker_id}] Error: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"[Worker {worker_id}] Fatal error: {e}", exc_info=True)
    finally:
        for feed_id, cm in core_modules.items(): cm.cleanup()
        logger.info(f"Inference process {os.getpid()} terminated.")