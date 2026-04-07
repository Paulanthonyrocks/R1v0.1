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

logger = logging.getLogger("Inference")

def inference_worker(
    worker_id: int,
    central_input_queue: MPQueue,
    central_output_queue: MPQueue,
    command_queue: MPQueue,
    stop_event: Event,
    config: Dict[str, Any],
    db_queue: Optional[MPQueue] = None,
    shared_skip_array: Optional[Any] = None
):
    from .metrics import WorkerMetrics, serialize_tracked_vehicles
    from .worker_utils import SharedFrameManager
    from ..core.core_module import CoreModule
    from ..utils.monitoring import TrafficMonitor
    from ..utils.process import start_parent_monitor
    import torch

    def _serialize_tracked_vehicles_with_map(tracked_vehicles, scale_x, scale_y):
        v_map = CoreModule.vehicle_type_map if CoreModule is not None else {}
        return serialize_tracked_vehicles(tracked_vehicles, scale_x, scale_y, v_map)

    from ..config import set_config
    set_config(config)
    start_parent_monitor(stop_event)
    import logging.config
    try:
        logging.config.dictConfig(config["logging"])
    except Exception:
        pass

    def signal_handler(signum, frame):
        stop_event.set()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    pid = os.getpid()
    logger.info(f"Inference process {pid} (Worker {worker_id}) started.")

    core_modules: Dict[str, CoreModule] = {}
    traffic_monitors: Dict[str, TrafficMonitor] = {}
    pending_configs: Dict[str, Dict] = {}
    metrics_map: Dict[str, WorkerMetrics] = {}
    shared_model = None
    shm_managers: Dict[str, SharedFrameManager] = {}

    from ..services.reid_manager import GlobalReIDManager
    local_reid_manager = GlobalReIDManager(config)

    vehicle_det_cfg = config.get("vehicle_detection", {})
    perf_cfg = config.get("performance", {})
    use_gpu = perf_cfg.get("gpu_acceleration", False)
    device = torch.device("cuda" if use_gpu and torch.cuda.is_available() else "cpu")
    logger.info(f"[Worker {worker_id}] Inference device: {device}")

    model_path = vehicle_det_cfg.get("model_path")
    shared_reid_embedder = None
    if model_path:
        try:
            from ultralytics import YOLO
            shared_model = YOLO(model_path)
            shared_model.to(device)
            logger.info(f"[Worker {worker_id}] Shared YOLO model loaded on {device}.")
            if vehicle_det_cfg.get("reid_enabled", True):
                from ..ml.reid_model import ReIDEmbedder
                shared_reid_embedder = ReIDEmbedder(config)
                logger.info(f"[Worker {worker_id}] ReID Embedder pre-loaded.")
        except Exception as e:
            logger.error(f"[Worker {worker_id}] Shared model load exception: {e}")

    def get_model_for_feed(f_id: str) -> YOLO:
        return shared_model

    def handle_command(cmd):
        pass # Simplified for this fix

    last_metrics_log = time.time()

    try:
        while not stop_event.is_set():
            try:
                while True:
                    cmd = command_queue.get_nowait()
                    handle_command(cmd)
            except queue.Empty:
                pass
            
            batch_tasks = []
            try:
                first_task = central_input_queue.get(timeout=0.1)
                if first_task:
                    batch_tasks.append(first_task)
                while len(batch_tasks) < perf_cfg.get("batch_size", 1):
                    t = central_input_queue.get_nowait()
                    if t: batch_tasks.append(t)
            except queue.Empty:
                if not batch_tasks: continue
            
            frames_to_infer = []
            inference_indices = []
            batch_meta = []

            for task in batch_tasks:
                feed_id, frame_index, frame_data, timestamp, fw, fh = task

                if feed_id not in metrics_map:
                    metrics_map[feed_id] = WorkerMetrics(feed_id)
                
                if frame_index == -999:
                    if feed_id in core_modules: core_modules.pop(feed_id).cleanup()
                    if feed_id in shm_managers: shm_managers.pop(feed_id).close()
                    continue

                if feed_id not in core_modules:
                    # BUG #19 FIX: Pass all required arguments to CoreModule constructor
                    core_modules[feed_id] = CoreModule(
                        feed_id=feed_id,
                        config=config,
                        model_path=model_path,
                        fps=config.get("video_processing", {}).get("target_fps", 15),
                        db_queue=db_queue,
                        preloaded_model=shared_model,
                        preloaded_reid=shared_reid_embedder
                    )
                    traffic_monitors[feed_id] = TrafficMonitor(config)

                core = core_modules[feed_id]
                metrics_obj = metrics_map[feed_id]
                frame = None
                
                if isinstance(frame_data, dict) and "shm_name" in frame_data:
                    shm_name = frame_data["shm_name"]
                    if shm_name not in shm_managers:
                        try:
                            shm_managers[shm_name] = SharedFrameManager(name=shm_name, frame_shape=frame_data["shape"], dtype=frame_data["dtype"], num_buffers=20, create=False)
                            logger.info(f"[{feed_id}] Attached to SHM ring buffer: {shm_name}")
                        except Exception as e:
                            logger.error(f"Failed to attach to SHM {shm_name}: {e}")
                            continue
                    
                    try:
                        frame = shm_managers[shm_name].get_frame(frame_data["shm_index"])
                    except Exception as e:
                        logger.error(f"Error reading from SHM {shm_name}: {e}")
                        continue
                elif isinstance(frame_data, dict) and "raw_bytes" in frame_data:
                    try:
                        frame = np.frombuffer(frame_data["raw_bytes"], dtype=frame_data["dtype"]).reshape(frame_data["shape"])
                    except Exception as e:
                        logger.error(f"Raw buffer reconstruction error: {e}")
                        continue
                
                if frame is None:
                    continue

                batch_meta.append({
                    "feed_id": feed_id, "frame_index": frame_index, "frame": frame, 
                    "frame_data": frame_data, "timestamp": timestamp,
                    "core": core, "metrics": metrics_obj
                })
                frames_to_infer.append(core._preprocess_frame(frame)[0])
                inference_indices.append(len(batch_meta) - 1)

            batch_detections_map = {}
            if frames_to_infer:
                try:
                    model = get_model_for_feed(batch_meta[0]["feed_id"])
                    results = model(frames_to_infer, verbose=False, stream=False, conf=0.1, half=(device.type=="cuda"))
                    for i, res in enumerate(results):
                        meta_idx = inference_indices[i]
                        formatted_dets = [((b[0], b[1], b[2], b[3]), b[4], b[5]) for b in res.boxes.data.cpu().numpy()]
                        batch_detections_map[meta_idx] = formatted_dets
                except Exception as e:
                    logger.error(f"[Worker {worker_id}] Batch inference failed: {e}")

            for i, meta in enumerate(batch_meta):
                core, metrics_obj = meta['core'], meta['metrics']
                frame, f_idx = meta['frame'], meta['frame_index']
                detections = batch_detections_map.get(i, [])
                
                vis_tracks, _, _, _ = core.detect_and_track(frame, f_idx, external_detections=detections, timestamp=meta["timestamp"])
                
                monitor = traffic_monitors[meta['feed_id']]
                monitor.update_vehicles(vis_tracks)
                metrics_result = monitor.get_metrics()
                metrics_obj.frames_processed += 1

                fh, fw = frame.shape[:2]
                scale_x, scale_y = 1.0 / fw, 1.0 / fh
                serialized_v = _serialize_tracked_vehicles_with_map(vis_tracks, scale_x, scale_y)

                try:
                    central_output_queue.put((
                        meta['feed_id'], f_idx, meta['frame_data'], metrics_result, serialized_v, {}
                    ))
                except queue.Full:
                    metrics_obj.frames_dropped += 1

            now = time.time()
            if now - last_metrics_log > 30.0:
                for fid, m in metrics_map.items():
                    logger.info(f"[Worker {worker_id}][{fid}] METRICS: {json.dumps(m.to_dict())}")
                last_metrics_log = now

    except Exception as e:
        logger.error(f"[Worker {worker_id}] Fatal error: {e}", exc_info=True)
    finally:
        for manager in shm_managers.values(): manager.close()
        for cm in core_modules.values(): cm.cleanup()
        logger.info(f"Inference process {os.getpid()} terminated.")
