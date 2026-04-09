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
    from .metrics import WorkerMetrics, prepare_vehicles_for_transport
    from .worker_utils import SharedFrameManager
    from ..core.core_module import CoreModule
    from ..utils.monitoring import TrafficMonitor
    from ..utils.process import start_parent_monitor
    import torch

    def _prepare_vehicles_for_transport_with_map(tracked_vehicles, scale_x, scale_y):
        v_map = CoreModule.vehicle_type_map if CoreModule is not None else {}
        return prepare_vehicles_for_transport(tracked_vehicles, scale_x, scale_y, v_map)

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
    shm_managers: Dict[str, SharedFrameManager] = {}

    shared_model = None
    shared_reid_embedder = None
    models_loaded = False

    perf_cfg = config.get("performance", {})
    use_gpu = perf_cfg.get("gpu_acceleration", False)
    device = torch.device("cuda" if use_gpu and torch.cuda.is_available() else "cpu")
    logger.info(f"[Worker {worker_id}] Inference device: {device}")

    def _lazy_load_models():
        nonlocal shared_model, shared_reid_embedder, models_loaded
        if models_loaded: return
        
        logger.info(f"[Worker {worker_id}] First feed received. Lazily loading models...")
        model_path = config.get("vehicle_detection", {}).get("model_path")
        if not model_path: 
            logger.error(f"[Worker {worker_id}] Model path not configured."); return
        try:
            from ultralytics import YOLO
            shared_model = YOLO(model_path)
            shared_model.to(device)
            logger.info(f"[Worker {worker_id}] Shared YOLO model loaded on {device}.")
            
            if config.get("vehicle_detection", {}).get("reid_enabled", True):
                from ..ml.reid_model import ReIDEmbedder
                shared_reid_embedder = ReIDEmbedder(config)
                logger.info(f"[Worker {worker_id}] ReID Embedder pre-loaded.")
            models_loaded = True
        except Exception as e:
            logger.error(f"[Worker {worker_id}] Shared model load exception: {e}", exc_info=True)

    def get_model_for_feed(f_id: str):
        if not models_loaded: _lazy_load_models()
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
            except queue.Empty: pass
            
            try:
                task = central_input_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            feed_id, frame_index, frame_data, timestamp, fw, fh = task

            if feed_id not in metrics_map: metrics_map[feed_id] = WorkerMetrics(feed_id)
            if frame_index == -999: # End of stream signal
                if feed_id in core_modules: core_modules.pop(feed_id).cleanup()
                if feed_id in shm_managers: shm_managers.pop(feed_id).close()
                if feed_id in traffic_monitors: traffic_monitors.pop(feed_id)
                if feed_id in metrics_map: metrics_map.pop(feed_id)
                continue

            if feed_id not in core_modules:
                _lazy_load_models()
                if not models_loaded: 
                    logger.warning(f"[{feed_id}] Models not loaded, skipping frame.")
                    continue
                core_modules[feed_id] = CoreModule(
                    feed_id=feed_id, config=config, db_queue=db_queue,
                    model_path=config.get("vehicle_detection", {}).get("model_path"),
                    fps=config.get("video_processing", {}).get("target_fps", 15),
                    preloaded_model=shared_model, preloaded_reid=shared_reid_embedder
                )
                traffic_monitors[feed_id] = TrafficMonitor(config, feed_id)

            core, metrics_obj = core_modules[feed_id], metrics_map[feed_id]
            frame = None
            
            if isinstance(frame_data, dict) and "shm_name" in frame_data:
                shm_name = frame_data["shm_name"]
                if shm_name not in shm_managers:
                    try:
                        shm_managers[shm_name] = SharedFrameManager(
                            name=shm_name, 
                            frame_shape=frame_data["shape"], 
                            dtype=frame_data["dtype"],
                            # CRITICAL #2 FIX: Use num_buffers from producer
                            num_buffers=frame_data['num_buffers'],
                            create=False # Consumer attaches
                        )
                        logger.info(f"[{feed_id}] Attached to SHM ring buffer: {shm_name}")
                    except Exception as e:
                        logger.error(f"Failed to attach to SHM {shm_name}: {e}", exc_info=True)
                        continue
                
                # CRITICAL #2 FIX: Use synchronized get_frame
                frame = shm_managers[shm_name].get_frame(frame_data["shm_index"])
            
            elif isinstance(frame_data, dict) and "raw_bytes" in frame_data:
                try:
                    frame = np.frombuffer(frame_data["raw_bytes"], dtype=frame_data["dtype"]).reshape(frame_data["shape"])
                except Exception as e:
                    logger.error(f"Raw buffer reconstruction error: {e}", exc_info=True)
                    continue
            
            if frame is None:
                metrics_obj.frames_dropped += 1
                continue

            vis_tracks, _, _, _ = core.detect_and_track(frame, frame_index, timestamp=timestamp)
            
            monitor = traffic_monitors[feed_id]
            monitor.update_vehicles(vis_tracks)
            metrics_result = monitor.get_metrics()
            metrics_obj.frames_processed += 1

            fh, fw = frame.shape[:2]
            scale_x, scale_y = 1.0 / fw, 1.0 / fh
            vehicles_for_transport = _prepare_vehicles_for_transport_with_map(vis_tracks, scale_x, scale_y)

            # Encode frame as JPEG for frontend broadcast
            encoded_frame = cv2.imencode('.jpg', frame)[1].tobytes()

            try:
                central_output_queue.put((
                    feed_id, frame_index, encoded_frame, metrics_result, vehicles_for_transport, {}
                ), timeout=0.01)
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
