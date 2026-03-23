import os
import cv2
import logging
import time
import numpy as np
import queue
import signal
import json
import base64
from pathlib import Path
from typing import Dict, Any, List, Optional
from multiprocessing import Queue as MPQueue, Event
import torch

from ..core.core_module import CoreModule
from ..utils.monitoring import TrafficMonitor
from ..utils.process import start_parent_monitor
from .worker_utils import WorkerMetrics, serialize_tracked_vehicles, SharedFrameManager

logger = logging.getLogger("Inference")


def _serialize_tracked_vehicles_with_map(
    tracked_vehicles: Dict[str, Dict], 
    scale_x: float = 1.0, 
    scale_y: float = 1.0
) -> List[Dict[str, Any]]:
    """Wrapper that uses CoreModule's vehicle_type_map."""
    v_map = CoreModule.vehicle_type_map if CoreModule is not None else {}
    return serialize_tracked_vehicles(tracked_vehicles, scale_x, scale_y, v_map)

def _extract_rois(frame: np.ndarray, tracked_vehicles: Any, scale: float = 1.0, device: Optional[torch.device] = None) -> List[Dict[str, Any]]:
    """
    Extracts high-res PNG patches for active vehicles (better for OCR).
    tracked_vehicles can be a list of dicts or an iterable of dicts.
    Assumes bbox coordinates in tracked_vehicles are absolute (pixels).
    """
    if frame is None or frame.size == 0: return []
    rois = []
    h, w = frame.shape[:2]
    
    # Optimization: Use GPU for cropping if available
    frame_tensor = None
    if device and device.type == "cuda":
        try:
            frame_tensor = torch.from_numpy(frame).to(device).permute(2, 0, 1) # (C, H, W)
        except Exception as e:
            logger.warning(f"Failed to move frame to GPU for ROI extraction: {e}")

    for v in tracked_vehicles:
        bbox = v.get("bbox")
        if not bbox or len(bbox) != 4: continue
        
        # Clamp coordinates
        x1, y1, x2, y2 = map(int, bbox)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        
        if x2 <= x1 or y2 <= y1: continue
        
        if frame_tensor is not None:
            # GPU Crop
            crop_tensor = frame_tensor[:, y1:y2, x1:x2]
            crop = crop_tensor.permute(1, 2, 0).byte().cpu().numpy()
        else:
            # CPU Crop
            crop = frame[y1:y2, x1:x2]
            
        _, crop_bytes = cv2.imencode(".png", crop) # Switched to PNG
        
        rois.append({
            "b": crop_bytes.tobytes(),
            "x": x1, "y": y1, "w": x2-x1, "h": y2-y1
        })
    return rois

def inference_worker(
    worker_id: int,
    central_input_queue: MPQueue,
    central_output_queue: MPQueue,
    command_queue: MPQueue,
    stop_event: Event,
    config: Dict[str, Any],
    db_queue: Optional[MPQueue] = None,
    shared_skip_array: Optional[Any] = None # Shared array for early skipping
):
    """
    Heavyweight AI process that processes frames from the central queue.
    Can handle frames from multiple feeds interleaved.
    """
    # Start parent monitor to avoid zombie processes
    start_parent_monitor(stop_event)
    # Initialize logging for the child process
    import logging.config
    try:
        logging.config.dictConfig(config["logging"])
    except Exception:
        # Cannot use logger here as it may not be configured
        pass  # Logging config failed, will use default

    # --- Signal Handling ---
    def signal_handler(signum, frame):
        logger.info(f"[Worker {worker_id}] Received signal {signum}, stopping gracefully")
        stop_event.set()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    pid = os.getpid()
    logger.debug(f"Inference process {pid} (Worker {worker_id}) entering initialization...")
    logger.info(f"Inference process {pid} (Worker {worker_id}) started.")
    
    logger.debug(f"[Worker {worker_id}] Initializing state containers...")
    # Per-feed CoreModules and Monitors (lazy initialized)
    core_modules: Dict[str, CoreModule] = {}
    traffic_monitors: Dict[str, TrafficMonitor] = {}
    pending_configs: Dict[str, Dict] = {}
    metrics_map: Dict[str, WorkerMetrics] = {} # Feed-specific metrics
    shared_model = None
    
    # Initialize a local ReID manager for visual matching across loops
    from ..services.reid_manager import GlobalReIDManager
    local_reid_manager = GlobalReIDManager(config)
    
    # Data Collection for Hard Negative Mining
    collection_cfg = config.get("data_collection", {})
    collect_hard_negatives = collection_cfg.get("enabled", False)
    collection_dir = Path(config.get("project_root_dir", "")) / "backend/data/hard_negatives"
    if collect_hard_negatives:
        collection_dir.mkdir(parents=True, exist_ok=True)
    last_collection_time = 0.0
    collection_cooldown = collection_cfg.get("cooldown_seconds", 60.0)
    collection_sample_counts: Dict[str, int] = {}
    collection_max_samples = config.get("hard_negative_max_samples_per_feed", 1000)
    collection_quality = config.get("hard_negative_quality", 70)

    # Pre-extract shared config
    vehicle_det_cfg = config.get("vehicle_detection", {})
    target_fps = config.get("video_processing", {}).get("target_fps", 15)
    ocr_cfg = config.get("ocr_engine", {})
    stream_res = tuple(config.get("video_output", {}).get("stream_resolution", (640, 480)))
    skip_frames = vehicle_det_cfg.get("skip_frames", 2)
    
    # Device setup for GPU acceleration
    perf_cfg = config.get("performance", {})
    use_gpu = perf_cfg.get("gpu_acceleration", False)
    device = torch.device("cuda" if use_gpu and torch.cuda.is_available() else "cpu")
    logger.info(f"[Worker {worker_id}] Inference device: {device}")

    model_path = vehicle_det_cfg.get("model_path")

    # Shared Model Loading Logic
    model_load_failed = False
    shared_reid_embedder = None
    if model_path:
        try:
            if stop_event.is_set(): return
            logger.info(f"[Worker {worker_id}] Loading shared models...")
            root_dir = config.get("project_root_dir", "")
            full_model_path = str(Path(root_dir) / model_path)
            
            # Load YOLO
            engine_path = Path(full_model_path).with_suffix(".engine")
            if engine_path.exists():
                logger.info(f"[Worker {worker_id}] Found TensorRT engine: {engine_path}")
                from ultralytics import YOLO
                if stop_event.is_set(): return
                shared_model = YOLO(str(engine_path))
                shared_model.to(device) 
                logger.info(f"[Worker {worker_id}] Shared TensorRT engine loaded on {device}.")
            else:
                from ultralytics import YOLO
                if stop_event.is_set(): return
                shared_model = YOLO(full_model_path)
                shared_model.to(device)
                logger.info(f"[Worker {worker_id}] Shared YOLO model loaded on {device}.")

            # Load ReID
            if vehicle_det_cfg.get("reid_enabled", True):
                try:
                    if stop_event.is_set(): return
                    from ..ml.reid_model import ReIDEmbedder
                    logger.info(f"[Worker {worker_id}] Pre-loading ReID Embedder...")
                    for h in logger.handlers: h.flush()
                    
                    shared_reid_embedder = ReIDEmbedder(config)
                    
                    logger.info(f"[Worker {worker_id}] ReID Embedder pre-loaded.")
                    for h in logger.handlers: h.flush()
                except Exception as e:
                    logger.error(f"[Worker {worker_id}] ReID pre-load failed: {e}", exc_info=True)
                    shared_reid_embedder = None
                    for h in logger.handlers: h.flush()

        except Exception as e:
            logger.error(f"[Worker {worker_id}] Shared model load exception: {e}")
            shared_model = None
            model_load_failed = True

    # Start parent monitor AFTER model loading to avoid premature triggers
    logger.debug(f"[Worker {worker_id}] Starting parent monitor...")
    start_parent_monitor(stop_event, f"Inference-{worker_id}")

    # Per-feed model overrides
    model_overrides: Dict[str, YOLO] = {}
    
    def get_model_for_feed(f_id: str) -> YOLO:
        return model_overrides.get(f_id, shared_model)

    def handle_command(cmd):
        if not cmd: return
        try:
            cmd_type = cmd.get("type")
            if cmd_type == "config_update":
                data = cmd.get("data", {})
                feed_id_cmd = data.get("feed_id") or cmd.get("feed_id")
                
                # Check for model path override
                if "model_path" in data and feed_id_cmd:
                    try:
                        logger.info(f"[Worker {worker_id}] Overriding model for feed {feed_id_cmd}: {data['model_path']}")
                        override_path = str(Path(config.get("project_root_dir", "")) / data["model_path"])
                        model_overrides[feed_id_cmd] = YOLO(override_path)
                        # Move to same device as shared
                        if shared_model:
                            model_overrides[feed_id_cmd].to(next(shared_model.parameters()).device)
                    except Exception as e:
                        logger.error(f"Failed to load model override for {feed_id_cmd}: {e}")

                if feed_id_cmd:
                    if feed_id_cmd not in core_modules:
                        if feed_id_cmd not in pending_configs:
                            pending_configs[feed_id_cmd] = {}
                        pending_configs[feed_id_cmd].update(data)
                    else:
                        core_modules[feed_id_cmd].update_config(data)
            elif cmd_type == "save_snapshot":
                data = cmd.get("data", {})
                feed_id_cmd = cmd.get("feed_id")
                if feed_id_cmd and feed_id_cmd in core_modules:
                    # We can't save it here immediately because we don't have the current frame
                    # Set a flag on the CoreModule to save the next processed frame
                    core_modules[feed_id_cmd]._pending_snapshot_incident_id = data.get("incident_id")
        except Exception as e:
            logger.error(f"[Worker {worker_id}] Command error: {e}")

    last_metrics_log = time.time()
    
    try:
        while not stop_event.is_set():
            # Handle command queue
            try:
                while True:
                    cmd = command_queue.get_nowait()
                    handle_command(cmd)
            except queue.Empty:
                pass

            try:
                # --- Adaptive Frame Skipping ---
                # Dynamically adjust skip_frames based on queue fullness
                q_size = central_input_queue.qsize()
                q_max = config.get("performance", {}).get("queue_max_size", 500)
                
                # If queue is more than 50% full, start increasing skip
                if q_size > q_max * 0.5:
                    # 1. HOL Blocking Prevention: If queue is dangerously full (>70%),
                    # drain it and only keep the LATEST frame for each feed.
                    if q_size > q_max * 0.7:
                        drain_map = {}
                        try:
                            # Drain up to 200 stale items at once
                            for _ in range(200):
                                item = central_input_queue.get_nowait()
                                # Key by feed_id to keep only the latest frame
                                if item and len(item) >= 2:
                                    feed_id = item[0]
                                    drain_map[feed_id] = item
                        except queue.Empty:
                            pass
                        
                        # Re-inject only the latest frame for each active feed
                        for feed_id, latest_item in drain_map.items():
                            try:
                                central_input_queue.put_nowait(latest_item)
                            except queue.Full: pass
                        
                        # Re-calculate size after drain
                        q_size = central_input_queue.qsize()

                    # Scale skip_frames up to 2x base value or 10 max
                    # load_factor is 0.0 at 50% full, 1.0 at 100% full
                    load_factor = (q_size - (q_max * 0.5)) / (q_max * 0.5)
                    # Use a more gradual scaling: base + (load_factor * 12) up to 15
                    adaptive_skip = int(skip_frames + (load_factor * 12))
                    current_skip = min(15, adaptive_skip)
                else:
                    current_skip = skip_frames

                # Update shared array for Ingestion Workers to see
                if shared_skip_array is not None:
                    try:
                        shared_skip_array[worker_id] = current_skip
                    except Exception as e:
                        logger.error(f"[Worker {worker_id}] Error updating shared skip: {e}")

                # Collect batch of frames
                batch_tasks = []
                batch_size = config.get("performance", {}).get("batch_size", 1)
                inference_timeout = config.get("performance", {}).get("inference_timeout", 0.005)

                try:
                    first_task = central_input_queue.get(timeout=0.1)
                    if first_task is not None:
                        batch_tasks.append(first_task)
                except queue.Empty:
                    pass

                if batch_tasks:
                    start_wait = time.time()
                    while len(batch_tasks) < batch_size and (time.time() - start_wait < inference_timeout):
                        try:
                            t = central_input_queue.get_nowait()
                            if t is not None:
                                batch_tasks.append(t)
                        except queue.Empty:
                            time.sleep(0.0005)
                            
                if not batch_tasks:
                   continue

                # Process Batch items
                frames_to_infer = []
                inference_indices = []
                batch_meta = []

                for task in batch_tasks:
                    feed_id, frame_index, frame_bytes, extra_payload = task
                    timestamp = extra_payload if isinstance(extra_payload, (int, float)) else time.time()
                    
                    if feed_id not in metrics_map:
                        metrics_map[feed_id] = WorkerMetrics(feed_id)
                    
                    if frame_index == -888:
                         if feed_id in core_modules: core_modules[feed_id]._first_detection_done = False
                         continue
                    if frame_index == -999:
                         if feed_id in core_modules:
                             core_modules[feed_id].cleanup(); del core_modules[feed_id]
                         if feed_id in traffic_monitors: del traffic_monitors[feed_id]
                         pending_configs.pop(feed_id, None)
                         if feed_id in metrics_map: del metrics_map[feed_id]
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
                    monitor = traffic_monitors[feed_id]
                    metrics_obj = metrics_map[feed_id]

                    # Use adaptive skip calculated from queue fullness
                    actual_skip = current_skip
                    first_detect = not getattr(core, '_first_detection_done', False)
                    should_detect = (frame_index % (actual_skip + 1) == 0) or (first_detect and not core.vehicle_data)

                    # --- Force Detection (Continuity Safety) ---
                    # If we have active tracks, but haven't detected in a while, force it
                    if not should_detect and core.vehicle_data:
                        # Find the oldest last_detection_time among active tracks
                        now = time.time()
                        # core.tracker.vehicle_data tracks might have 'last_detection_time'
                        # but CoreModule usually keeps state. 
                        # Let's use a simpler heuristic: if skip is large, and we have many tracks
                        # check if it's been more than 0.5s since ANY detection update
                        last_update = getattr(core, '_last_detection_time', 0)
                        if (now - last_update) > 0.5: # More than 500ms
                            should_detect = True
                            # logger.debug(f"[{feed_id}] Force detection triggered (latency: {now-last_update:.2f}s)")

                    lane_cfg = config.get("lane_detection", {})
                    is_lane_frame = (lane_cfg.get("dynamic_lane_detection_enabled", False) and 
                                     (frame_index % lane_cfg.get("lane_detection_interval", 10) == 0))
                    
                    needs_frame = should_detect or is_lane_frame
                    
                    frame = None
                    shm_to_cleanup = None
                    if needs_frame:
                        if isinstance(frame_bytes, dict) and "shm_name" in frame_bytes:
                            # ZERO-COPY PATH
                            try:
                                name = frame_bytes["shm_name"]
                                shape = frame_bytes["shape"]
                                dtype = frame_bytes["dtype"]
                                frame, shm_obj = SharedFrameManager.access_shm(name, shape, dtype)
                                shm_to_cleanup = name
                                # We MUST keep shm_obj alive until we are done processing the frame
                                # or copy it. Since we use it in batch_meta, we'll cleanup after inference.
                            except Exception as e:
                                logger.error(f"[Worker {worker_id}] SHM Access error: {e}")
                                metrics_obj.errors += 1
                                continue
                        else:
                            # FALLBACK PATH: Decode JPEG
                            try:
                                if isinstance(frame_bytes, str):
                                    try:
                                        frame_bytes = base64.b64decode(frame_bytes)
                                    except Exception as e:
                                        logger.error(f'Base64 decode error: {e}')
                                nparr = np.frombuffer(frame_bytes, np.uint8)
                                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                                if frame is None:
                                    metrics_obj.errors += 1
                                    continue
                            except Exception as e:
                                logger.error(f"[Worker {worker_id}] JPEG decode error: {e}")
                                metrics_obj.errors += 1
                                continue
                    
                    batch_meta.append({
                        "feed_id": feed_id, "frame_index": frame_index, "frame": frame, 
                        "frame_bytes": frame_bytes, "timestamp": timestamp,
                        "core": core, "monitor": monitor, "metrics": metrics_obj,
                        "should_detect": should_detect, "first_detect": first_detect,
                        "shm_name": shm_to_cleanup
                    })

                    if should_detect:
                        proc_frame, roi_enabled, x_off, y_off = core._preprocess_frame(frame)
                        frames_to_infer.append(proc_frame) 
                        inference_indices.append(len(batch_meta) - 1)
                        batch_meta[-1]["crop_offsets"] = (x_off, y_off) if roi_enabled else (0, 0)
                
                # Run Batch Inference
                batch_detections_map = {}
                if frames_to_infer:
                    try:
                        now = time.time()
                        # Optimization (Phase 12): Group frames by model instance so we can run multi-camera 
                        # batch inference natively on the GPU, even if feeds have overridden models.
                        model_groups = {} 
                        # Map: id(model_obj) -> {'model': obj, 'frames': [], 'meta_indices': []}
                        
                        for i, meta_idx in enumerate(inference_indices):
                            meta = batch_meta[meta_idx]
                            model_to_use = get_model_for_feed(meta['feed_id'])
                            if model_to_use is None:
                                continue
                            
                            m_id = id(model_to_use)
                            if m_id not in model_groups:
                                model_groups[m_id] = {
                                    'model': model_to_use, 
                                    'frames': [], 
                                    'meta_indices': []
                                }
                            
                            model_groups[m_id]['frames'].append(frames_to_infer[i])
                            model_groups[m_id]['meta_indices'].append(meta_idx)
                            
                        conf_min = vehicle_det_cfg.get("low_confidence_threshold", 0.15)
                        conf_max = vehicle_det_cfg.get("confidence_threshold", 0.30)
                        use_half = device.type == "cuda"
                        
                        # Process each grouped model explicitly as a massive batch tensor
                        for m_id, group in model_groups.items():
                            m = group['model']
                            g_frames = group['frames']
                            g_meta_indices = group['meta_indices']
                            
                            results = m(g_frames, verbose=False, stream=False, conf=conf_min, half=use_half)
                            
                            for i, res in enumerate(results):
                                meta_idx = g_meta_indices[i]
                                meta = batch_meta[meta_idx]
                                boxes_data = res.boxes.data.cpu().numpy()
                                formatted_dets = []
                                x_off, y_off = meta.get("crop_offsets", (0, 0))
                                
                                has_uncertain = False
                                for row in boxes_data:
                                    rx1, ry1, rx2, ry2, conf, cls_id = row
                                    fx1, fy1 = rx1 + x_off, ry1 + y_off
                                    fx2, fy2 = rx2 + x_off, ry2 + y_off
                                    formatted_dets.append(((fx1, fy1, fx2, fy2), conf, cls_id))
                                    
                                    if conf_min < conf < conf_max:
                                        has_uncertain = True
                                        
                                # Hard Negative Mining logic
                                if collect_hard_negatives and has_uncertain and (now - last_collection_time > collection_cooldown):
                                    feed_id_hn = meta['feed_id']
                                    current_count = collection_sample_counts.get(feed_id_hn, 0)
                                    if current_count < collection_max_samples:
                                        try:
                                            # Use WebP for better compression of collection samples
                                            fname = f"hard_neg_{feed_id_hn}_{meta['frame_index']}_{int(now)}.webp"
                                            fpath = collection_dir / fname
                                            cv2.imwrite(str(fpath), meta['frame'], [int(cv2.IMWRITE_WEBP_QUALITY), collection_quality])
                                            last_collection_time = now
                                            collection_sample_counts[feed_id_hn] = current_count + 1
                                            logger.info(f"[Worker {worker_id}] Saved hard negative sample for {feed_id_hn}: {fname} (Total: {current_count+1})")
                                        except Exception as e:
                                            logger.error(f"Failed to save hard negative: {e}")
                                    else:
                                        if current_count == collection_max_samples:
                                             logger.info(f"[Worker {worker_id}] Hard negative limit reached for {feed_id_hn} ({collection_max_samples})")
                                             collection_sample_counts[feed_id_hn] += 1 # Avoid repeated logging
                                             
                                batch_detections_map[meta_idx] = formatted_dets

                    except Exception as e:
                        logger.error(f"[Worker {worker_id}] Batch inference failed: {e}", exc_info=True)

                # Tracking & Output
                for i, meta in enumerate(batch_meta):
                    core, monitor, metrics_obj = meta['core'], meta['monitor'], meta['metrics']
                    frame, f_idx = meta['frame'], meta['frame_index']
                    
                    detections = batch_detections_map.get(i, []) if meta['should_detect'] else []
                    vis_tracks, lane_bounds, lane_lines, calib_status = core.detect_and_track(
                        frame, f_idx, external_detections=detections,
                        timestamp=meta.get("timestamp")
                    )
                    
                    if meta['should_detect']:
                        core._last_detection_time = time.time()
                        if vis_tracks and meta['first_detect']:
                            core._first_detection_done = True
                    
                    # Only assign global IDs in worker if Redis is enabled for distributed sync.
                    # Otherwise, let the central FeedManager handle it to avoid conflicts.
                    if config.get("redis", {}).get("enabled", False):
                        for vid, track in vis_tracks.items():
                            emb = track.get("embedding")
                            if emb is not None:
                                global_id = local_reid_manager.match_only(np.array(emb))
                                if global_id:
                                    track["global_vehicle_id"] = global_id
                                    if meta['feed_id'] not in local_reid_manager.local_to_global:
                                        local_reid_manager.local_to_global[meta['feed_id']] = {}
                                    local_reid_manager.local_to_global[meta['feed_id']][vid] = global_id
                            if not track.get("global_vehicle_id"):
                                mapped_id = local_reid_manager.get_global_id(meta['feed_id'], vid)
                                if mapped_id: track["global_vehicle_id"] = mapped_id
                        
                    monitor.update_vehicles(vis_tracks)
                    metrics_result = monitor.get_metrics()
                    metrics_obj.frames_processed += 1
                    
                    # --- NORMALIZE COORDINATES FOR FRONTEND ---
                    # Frontend expects 0.0 to 1.0 range
                    serialized_v = []
                    if frame is not None and frame.size > 0:
                        fh, fw = frame.shape[:2]
                        if fw > 0 and fh > 0:
                            scale_x, scale_y = 1.0 / fw, 1.0 / fh
                            serialized_v = _serialize_tracked_vehicles_with_map(vis_tracks, scale_x, scale_y)
                    
                    extra = {"calibration": calib_status}
                    if frame is not None and frame.size > 0:
                        v_proc_cfg = config.get("video_processing", {})
                        if v_proc_cfg.get("adaptive_streaming", False) and meta['should_detect']:
                             bg_scale = v_proc_cfg.get("roi_scale", 0.5)
                             bg_frame = cv2.resize(frame, (0, 0), fx=bg_scale, fy=bg_scale)
                             _, bg_bytes = cv2.imencode(".jpg", bg_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
                             extra["bg"] = bg_bytes.tobytes()
                             
                             # Throttle heavy ROI/embedding extraction 
                             # Only extract PNG patches on new detections or every 15th frame for EMA tracking stability
                             if meta['first_detect'] or f_idx % 15 == 0:
                                 extra["rois"] = _extract_rois(frame, vis_tracks.values(), device=device)
                             else:
                                 extra["rois"] = []
                    
                    try:
                        central_output_queue.put((
                            meta['feed_id'], f_idx, meta['frame_bytes'], metrics_result, serialized_v, extra
                        ))
                    except queue.Full:
                        metrics_obj.frames_dropped += 1
                
                # Post-Processing Cleanup (including SHM)
                for meta in batch_meta:
                    if meta.get("shm_name"):
                        SharedFrameManager.cleanup_shm(meta["shm_name"])

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