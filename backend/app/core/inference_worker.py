import os
import cv2
import logging
import time
import numpy as np
import queue
import signal
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from multiprocessing import Queue as MPQueue, Event

from ..core.core_module import CoreModule
from ..utils.monitoring import TrafficMonitor
from ..utils.process import start_parent_monitor
from .worker_utils import WorkerMetrics, make_serializable, serialize_tracked_vehicles

logger = logging.getLogger("Inference")


def _serialize_tracked_vehicles_with_map(
    tracked_vehicles: Dict[str, Dict], 
    scale_x: float = 1.0, 
    scale_y: float = 1.0
) -> List[Dict[str, Any]]:
    """Wrapper that uses CoreModule's vehicle_type_map."""
    v_map = CoreModule.vehicle_type_map if CoreModule else {}
    return serialize_tracked_vehicles(tracked_vehicles, scale_x, scale_y, v_map)

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
    
    # Start parent monitor to avoid zombies
    logger.debug(f"[Worker {worker_id}] Starting parent monitor...")
    start_parent_monitor(stop_event, f"Inference-{worker_id}")
    
    logger.debug(f"[Worker {worker_id}] Initializing state containers...")
    # Per-feed CoreModules and Monitors (lazy initialized)
    core_modules: Dict[str, CoreModule] = {}
    traffic_monitors: Dict[str, TrafficMonitor] = {}
    pending_configs: Dict[str, Dict] = {}
    metrics_map: Dict[str, WorkerMetrics] = {} # Feed-specific metrics
    shared_model = None
    
    # Pre-extract shared config
    vehicle_det_cfg = config.get("vehicle_detection", {})
    target_fps = config.get("video_processing", {}).get("target_fps", 15)
    ocr_cfg = config.get("ocr_engine", {})
    stream_res = tuple(config.get("video_output", {}).get("stream_resolution", (640, 480)))
    skip_frames = vehicle_det_cfg.get("skip_frames", 2)
    
    model_path = vehicle_det_cfg.get("model_path")

    # Shared Model Loading Logic ...
    model_load_failed = False
    if model_path:
        try:
            logger.info(f"[Worker {worker_id}] Loading shared model from {model_path}...")
            # Resolve absolute path
            root_dir = config.get("project_root_dir", "")
            full_model_path = str(Path(root_dir) / model_path)
            
            use_gpu = config.get("performance", {}).get("gpu_acceleration", False)
            
            if full_model_path.endswith(".onnx") or full_model_path.endswith("_quant.onnx"):
                import onnxruntime as ort
                providers = ["CPUExecutionProvider"]
                if use_gpu and "CUDAExecutionProvider" in ort.get_available_providers():
                    providers.insert(0, "CUDAExecutionProvider")
                shared_model = ort.InferenceSession(full_model_path, providers=providers)
                logger.info(f"[Worker {worker_id}] Shared ONNX model loaded.")
            else:
                # Lazy import to avoid startup overhead if not used
                from ultralytics import YOLO
                import torch
                
                device = "cpu"
                if use_gpu:
                    if torch.cuda.is_available():
                        device = "cuda:0"
                    else:
                        logger.warning(f"[Worker {worker_id}] GPU requested but not available.")
                
                shared_model = YOLO(full_model_path)
                shared_model.to(device)
                logger.info(f"[Worker {worker_id}] Shared YOLO model loaded on {device}.")
                
        except Exception as e:
            logger.error(f"[Worker {worker_id}] Shared model load exception: {e}")
            shared_model = None

        # After loading:
        if shared_model is None:
             logger.error(f"[Worker {worker_id}] Failed to load shared model. Will attempt per-feed load.")
             model_load_failed = True
    
    # ... handle command ...
    def handle_command(cmd):
        if not cmd: return
        try:
            cmd_type = cmd.get("type")
            if cmd_type == "config_update":
                data = cmd.get("data", {})
                # Some commands might wrap data, others might be flat. Adjust as needed.
                feed_id_cmd = data.get("feed_id") or cmd.get("feed_id")
                
                if feed_id_cmd:
                    # Fix #27: Limit pending configs
                    if feed_id_cmd not in core_modules:
                        if feed_id_cmd not in pending_configs:
                            pending_configs[feed_id_cmd] = {}
                        pending_configs[feed_id_cmd].update(data)
                        
                        MAX_PENDING_CONFIGS = 100
                        if len(pending_configs) > MAX_PENDING_CONFIGS:
                            oldest = next(iter(pending_configs))
                            pending_configs.pop(oldest)
                            logger.warning(f"[Worker {worker_id}] Dropped pending config for {oldest} (limit reached)")
                    else:
                        core_modules[feed_id_cmd].update_config(data)
                        
        except Exception as e:
            logger.error(f"[Worker {worker_id}] Command error: {e}")

    last_detection_mode = {} # feed_id -> bool
    last_metrics_log = time.time()
    
    logger.debug(f"[Worker {worker_id}] Entering main loop...")

    try:
        while not stop_event.is_set():
            # ... handle command queue ...
            try:
                while True:
                    cmd = command_queue.get_nowait()
                    handle_command(cmd)
            except queue.Empty:
                pass

            try:
                # 1. Get a frame task from the queue
                # print(f"DEBUG: [Worker {worker_id}] Waiting for frame...") # Commented out to avoid spam
                task = central_input_queue.get(timeout=0.1) 
                if task is None: continue
                
                feed_id, frame_index, frame_bytes, extra_payload = task
                # print(f"DEBUG: [Worker {worker_id}] Got frame {frame_index} from {feed_id}")

                # Initialize metrics for feed if needed
                if feed_id not in metrics_map:
                    metrics_map[feed_id] = WorkerMetrics(feed_id)

                # Handle Lifecycle Signals
                if frame_index == -888:  # Feed started
                    logger.info(f"[Worker {worker_id}] Feed {feed_id} started signal received")
                    if feed_id in core_modules:
                         core_modules[feed_id]._first_detection_done = False
                    continue
                    
                if frame_index == -999: # End of Stream
                    logger.info(f"[Worker {worker_id}] Received EOS for {feed_id}")
                    if feed_id in core_modules:
                        core_modules[feed_id].cleanup()
                        del core_modules[feed_id]
                    if feed_id in traffic_monitors:
                        del traffic_monitors[feed_id]
                    # Clean pending configs
                    pending_configs.pop(feed_id, None)
                    # Clean metrics
                    if feed_id in metrics_map:
                         logger.info(f"[Worker {worker_id}] Final Metrics for {feed_id}: {json.dumps(metrics_map[feed_id].to_dict())}")
                         del metrics_map[feed_id]
                    continue

                if frame_index == -1: # Control Message
                    continue

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
                        preloaded_model=shared_model if not model_load_failed else None
                    )
                    core_modules[feed_id]._first_detection_done = False # Initialize flag
                    
                    if model_load_failed:
                        logger.warning(f"[Worker {worker_id}] Feed {feed_id} loaded model independently (shared model failed)")

                    traffic_monitors[feed_id] = TrafficMonitor(config)
                    
                    # Apply any pending configs
                    if feed_id in pending_configs:
                        core_modules[feed_id].update_config(pending_configs.pop(feed_id))
                
                core = core_modules[feed_id]
                monitor = traffic_monitors[feed_id]
                metrics_obj = metrics_map[feed_id]
                
                # 4. Process AI with Frame Skipping
                # Fix #22: Ensure first detection runs
                first_detect = not getattr(core, '_first_detection_done', False)
                should_detect = (frame_index % (skip_frames + 1) == 0) or (first_detect and not core.vehicle_data)
                
                if should_detect:
                    # 2. Decode frame only if we need to detect
                    nparr = np.frombuffer(frame_bytes, np.uint8)
                    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if frame is None: 
                        metrics_obj.errors += 1
                        continue
                    
                    tracked_vehicles = core.detect_and_track(
                        frame, frame_index
                    )
                    
                    if tracked_vehicles and first_detect:
                        core._first_detection_done = True
                        
                    # Update stats on detection
                    monitor.update_vehicles(tracked_vehicles)
                else:
                    # Skip detection, use Kalman Filter prediction
                    tracked_vehicles = core.predict_only(frame_index)
                    # DON'T update monitor with pure predictions to avoid stats pollution
                    # Or update only active/confirmed ones if needed. For now, strict separation.
                
                metrics = monitor.get_metrics()
                metrics_obj.frames_processed += 1
                
                # 5. Serialize and push results
                serialized_vehicles = _serialize_tracked_vehicles(tracked_vehicles, 1.0, 1.0)
                
                try:
                    central_output_queue.put_nowait((
                        feed_id, frame_index, frame_bytes, metrics, serialized_vehicles, {}
                    ))
                except queue.Full:
                    metrics_obj.frames_dropped += 1
                
                # Periodic Metrics Logging
                now = time.time()
                if now - last_metrics_log > 30.0:
                     for fid, m in metrics_map.items():
                         logger.info(f"[Worker {worker_id}][{fid}] METRICS: {json.dumps(m.to_dict())}")
                     last_metrics_log = now

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"[Worker {worker_id}] Error: {e}", exc_info=True)
                if 'metrics_obj' in locals(): metrics_obj.errors += 1

    except KeyboardInterrupt:
        logger.info(f"[Worker {worker_id}] Received keyboard interrupt")
    except Exception as e:
        logger.error(f"[Worker {worker_id}] Fatal error in main loop: {e}", exc_info=True)
    finally:
        logger.info(f"[Worker {worker_id}] Shutting down, cleaning up {len(core_modules)} feeds...")
        for feed_id, cm in core_modules.items():
            try:
                cm.cleanup()
                logger.debug(f"[Worker {worker_id}] Cleaned up {feed_id}")
            except Exception as e:
                logger.error(f"[Worker {worker_id}] Cleanup failed for {feed_id}: {e}")
        
        # Clear shared model reference
        if shared_model is not None:
            del shared_model
            
            # If using PyTorch, explicitly clear cache
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    logger.debug(f"[Worker {worker_id}] Cleared CUDA cache")
            except Exception:
                pass  # PyTorch may not be installed or CUDA unavailable
        
        logger.info(f"Inference process {os.getpid()} terminated.")
