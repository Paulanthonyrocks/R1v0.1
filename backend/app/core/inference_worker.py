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
from .worker_utils import WorkerMetrics, serialize_tracked_vehicles

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
    """
    Heavyweight AI process that processes frames from the central queue.
    Can handle frames from multiple feeds interleaved.
    """
    # CRITICAL: Print to stderr BEFORE any imports/initialization
    # so we can confirm the process actually starts with spawn method
    import sys, os
    print(f"[INFERENCE-BOOT] Worker {worker_id} PID={os.getpid()} slots={slots} starting...", file=sys.stderr, flush=True)

    # Initialize global config for this process
    initialize_config()

    # Initialize logging for the child process
    import logging.config
    import sys
    try:
        logging.config.dictConfig(config["logging"])
    except Exception as e:
        print(f"Logging configuration failed: {e}", file=sys.stderr)
        logging.basicConfig(level=logging.INFO)

    logger.info(f"[Worker {worker_id}] Process initialized. PID={os.getpid()}")
    logger.info(f"[Worker {worker_id}] Queue list type: {type(central_input_queue)}, length: {len(central_input_queue) if hasattr(central_input_queue, '__len__') else 'N/A'}")
    logger.info(f"[Worker {worker_id}] Slots assigned: {slots}")
    logger.info(f"[Worker {worker_id}] Will read from slot-specific inference_input streams, write to central_output")

    # Initialize Redis client for signals and pressure
    from app.utils.redis_client import get_redis_client
    redis_client = get_redis_client()

    # Ensure slots is a list
    slots = slots or []

    def should_stop():
        """Check if a stop signal has been received via event or Redis."""
        if stop_event and getattr(stop_event, 'is_set', lambda: False)():
            return True
        if redis_client and redis_client.exists("signal:pipeline_stop"):
            return True
        return False

    # --- Signal Handling ---
    def signal_handler(signum, frame):
        logger.info(f"[Worker {worker_id}] Received signal {signum}, stopping gracefully")
        if stop_event:
            stop_event.set()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    pid = os.getpid()
    # Write worker PID and status to Redis for parent-process observability
    try:
        redis_client.set(f"worker:{worker_id}:pid", pid)
        redis_client.set(f"worker:{worker_id}:status", "initializing")
    except Exception:
        pass

    # Start parent monitor to avoid zombies
    logger.debug(f"[Worker {worker_id}] Starting parent monitor...")

    # Use the actual stop_event for the monitor
    start_parent_monitor(stop_event, f"Inference-{worker_id}")

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
        frame_buffer = SharedFrameBuffer(pool_size=config.get("performance", {}).get("shm_pool_size", 100), read_only=False)

    # Pre-extract shared config
    vehicle_det_cfg = config.get("vehicle_detection", {})
    target_fps = config.get("video_processing", {}).get("target_fps", 15)
    ocr_cfg = config.get("ocr_engine", {})
    stream_res = tuple(config.get("video_output", {}).get("stream_resolution", (640, 480)))

    skip_frames = vehicle_det_cfg.get("skip_frames", 2)

    model_path = vehicle_det_cfg.get("model_path")

    # Shared Model Loading Logic
    model_load_failed = False
    shared_reid_embedder = None
    if model_path:
        if should_stop():
            logger.info(f"[Worker {worker_id}] Stop signal received before model loading. Skipping.")
            return

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

            if should_stop():
                logger.info(f"[Worker {worker_id}] Stop signal received after YOLO load. Skipping ReID.")
                return

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
            logger.critical(f"[Worker {worker_id}] Shared model load failed. Exiting worker to prevent silent failure.")
            return

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
            sent_shm_refs = set()
            if should_stop():
                logger.info(f"[Worker {worker_id}] Stop signal received. Exiting main loop.")
                break

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
                q_depth = sum(central_input_queue[slot_id].qsize() for slot_id in slots)
                
                # Base batch size from config
                batch_size = config.get("performance", {}).get("batch_size", 1)

                # Apply backpressure: reduce batch size if pipeline pressure is high
                if pipeline_pressure and pipeline_pressure.get("value", 0) > 0.7:
                    batch_size = max(1, batch_size // 2)
                    logger.debug(f"[Worker {worker_id}] High pipeline pressure ({pipeline_pressure.get('value'):.2f}). Reducing batch size to {batch_size}")
                
                # Ensure a reasonable upper bound for safety
                # We use a constant here, but it could be moved to config
                batch_size = min(batch_size, 8) 

                inference_timeout = config.get("performance", {}).get("inference_timeout", 0.005)

                try:
                    # Poll assigned slot queues
                    for slot_id in slots:
                        try:
                            slot_q = central_input_queue[slot_id]
                            res = slot_q.get_nowait()
                            if res:
                                # RedisStreamQueue returns (msg_id, item)
                                # RedisQueue returns just item
                                if isinstance(res, tuple) and len(res) == 2 and not isinstance(res[1], (tuple, list)):
                                    msg_id, task = res
                                elif isinstance(res, tuple) and len(res) == 2 and isinstance(res[1], (tuple, list)):
                                    # Handle (msg_id, (feed_id, frame_idx, ...))
                                    msg_id, task = res
                                else:
                                    msg_id, task = None, res
                                # Include slot_q reference so we can ACK on the correct stream
                                batch_tasks.append((msg_id, task, slot_q))
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
                    logger.info(f'[Worker {worker_id}] Received {len(batch_tasks)} tasks from inference queue')
                    start_wait = time.time()
                    while len(batch_tasks) < batch_size and (time.time() - start_wait < inference_timeout):
                        for slot_id in slots:
                            try:
                                slot_q = central_input_queue[slot_id]
                                res = slot_q.get_nowait()
                                if res:
                                    if isinstance(res, tuple) and len(res) == 2 and isinstance(res[1], (tuple, list)):
                                        msg_id, t = res
                                    else:
                                        msg_id, t = None, res
                                    
                                    # 2. Smart Skip
                                    if q_depth > 200:
                                        if isinstance(t, (tuple, list)) and len(t) >= 4:
                                            t_feed_id, t_frame_idx, _, _ = t[:4]
                                            if t_frame_idx != -888 and t_frame_idx != -999:
                                                if t_feed_id in core_modules and getattr(core_modules[t_feed_id], '_first_detection_done', False):
                                                    # ACK skipped messages immediately to prevent pending buildup
                                                    if msg_id and hasattr(slot_q, 'ack'):
                                                        slot_q.ack(msg_id)
                                                    continue
                                    else:
                                        # Malformed task, skip it or handle it
                                        continue

                                    # Include slot_q reference for ACK
                                    batch_tasks.append((msg_id, t, slot_q))
                            except (queue.Empty, IndexError):
                                continue
                            time.sleep(0.0005)
                            
                if not batch_tasks:
                    continue

                # Process Batch items
                frames_to_infer = []
                inference_indices = []
                batch_meta = []

                try:
                    for task_tuple in batch_tasks:
                        msg_id, task, slot_q_ref = task_tuple
                        feed_id, frame_index, shm_ref, extra_payload = task

                        # Handle control messages BEFORE attempting SHM read.
                        if frame_index == -888:
                            if feed_id not in metrics_map:
                                metrics_map[feed_id] = WorkerMetrics(feed_id)
                            if feed_id in core_modules:
                                core_modules[feed_id]._first_detection_done = False
                            # ACK control messages immediately
                            if msg_id and hasattr(slot_q_ref, 'ack'):
                                slot_q_ref.ack(msg_id)
                            continue
                        if frame_index == -999:
                            if feed_id in core_modules:
                                core_modules[feed_id].cleanup(); del core_modules[feed_id]
                            if feed_id in traffic_monitors:
                                del traffic_monitors[feed_id]
                            pending_configs.pop(feed_id, None)
                            if feed_id in metrics_map:
                                del metrics_map[feed_id]
                            # ACK control messages immediately
                            if msg_id and hasattr(slot_q_ref, 'ack'):
                                slot_q_ref.ack(msg_id)
                            continue

                        # TRACK SHM REF FOR RELEASE
                        batch_meta.append({
                            "msg_id": msg_id,
                            "slot_q": slot_q_ref,
                            "shm_ref": shm_ref,
                            "feed_id": feed_id,
                            "frame_index": frame_index
                        })
                        if frame_buffer:
                            res = frame_buffer.read(shm_ref)
                            if res is None:
                                logger.error(f"[Worker {worker_id}] SHM read returned None for ref {shm_ref}")
                                metrics_obj.errors += 1
                                continue
                            
                            if isinstance(res, tuple) and len(res) == 2:
                                frame_bytes, dims = res
                            else:
                                logger.error(f"[Worker {worker_id}] SHM read returned unexpected format: {type(res)}")
                                metrics_obj.errors += 1
                                continue
                        else:
                            frame_bytes, dims = (shm_ref, (0, 0, 0))
                        
                        timestamp = extra_payload if isinstance(extra_payload, (int, float)) else time.time()
                        
                        if feed_id not in metrics_map:
                            metrics_map[feed_id] = WorkerMetrics(feed_id)
                        
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
                        
                        actual_skip = skip_frames
                        first_detect = not getattr(core, '_first_detection_done', False)
                        should_detect = (frame_index % (actual_skip + 1) == 0) or (first_detect and not core.tracker.vehicle_data)
                        
                        lane_cfg = config.get("lane_detection", {})
                        is_lane_frame = (lane_cfg.get("dynamic_lane_detection_enabled", False) and 
                                         (frame_index % lane_cfg.get("lane_detection_interval", 10) == 0))
                        
                        needs_frame = should_detect or is_lane_frame
                        
                        frame = None
                        if needs_frame:
                            if isinstance(frame_bytes, memoryview):
                                frame = cv2.imdecode(np.frombuffer(frame_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
                            elif isinstance(frame_bytes, np.ndarray):
                                frame = frame_bytes
                            else:
                                frame = cv2.imdecode(np.frombuffer(frame_bytes, np.uint8), cv2.IMREAD_COLOR)
                            
                            if frame is None:
                                metrics_obj.errors += 1
                                continue

                            # Update activity ONLY after successful frame acquisition/skip
                            core.last_activity = time.time()                        
                        # Update meta with runtime info
                        meta_entry = {
                            "feed_id": feed_id, "frame_index": frame_index, "frame": frame, 
                            "timestamp": timestamp,
                            "core": core, "monitor": monitor, "metrics": metrics_obj,
                            "should_detect": should_detect, "first_detect": first_detect,
                            "msg_id": msg_id, "shm_ref": shm_ref
                        }
                        
                        if should_detect and frame is not None:
                            proc_frame, roi_enabled, x_off, y_off = core._preprocess_frame(frame)
                            frames_to_infer.append(proc_frame) 
                            inference_indices.append(len(batch_meta) - 1)
                            meta_entry["crop_offsets"] = (x_off, y_off) if roi_enabled else (0, 0)
                        
                        batch_meta.append(meta_entry)

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
                        # Skip if this is just a tracking metadata entry (from the first loop)
                        if "core" not in meta:
                            continue

                        core, monitor, metrics_obj = meta['core'], meta['monitor'], meta['metrics']
                        frame, f_idx = meta['frame'], meta['frame_index']
                        
                        if frame is None:
                            continue

                        detections = batch_detections_map.get(i, []) if meta['should_detect'] else []
                        vis_tracks, lane_bounds, lane_lines = core.detect_and_track(
                            frame, f_idx, external_detections=detections,
                            timestamp=meta.get("timestamp")
                        )
                        
                        if vis_tracks and meta['first_detect']:
                            core._first_detection_done = True
                        
                        for vid, track in vis_tracks.items():
                            emb = track.get("embedding")
                            if emb:
                                global_id = local_reid_manager.match_or_register(
                                    feed_id=meta['feed_id'],
                                    local_id=str(vid),
                                    embedding=np.array(emb),
                                    metadata={"class_name": CoreModule.vehicle_type_map.get(track["class_id"], "unknown")},
                                    confidence=track.get("confidence", 1.0)
                                )
                                track["global_vehicle_id"] = global_id
                            elif not track.get("global_vehicle_id"):
                                mapped_id = local_reid_manager.get_global_id(meta['feed_id'], str(vid))
                                if mapped_id: track["global_vehicle_id"] = mapped_id
                            
                        monitor.update_vehicles(vis_tracks)
                        metrics_obj.frames_processed += 1
                        
                        serialized_v = _serialize_tracked_vehicles_with_map(vis_tracks)
                        
                        extra = {}
                        v_proc_cfg = config.get("video_processing", {})
                        if v_proc_cfg.get("adaptive_streaming", False) and meta['should_detect']:
                             bg_scale = v_proc_cfg.get("roi_scale", 0.5)
                             if frame is not None:
                                 bg_frame = cv2.resize(frame, (0, 0), fx=bg_scale, fy=bg_scale)
                                 _, bg_bytes = cv2.imencode(".jpg", bg_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
                                 extra["bg"] = bg_bytes.tobytes()
                                 # Pass stream_res for proper ROI scaling
                                 extra["rois"] = _extract_rois(frame, serialized_v, scale=stream_res[0]/640.0 if stream_res[0] != 0 else 1.0)
                        try:
                            if central_output_queue:
                                central_output_queue.put_nowait((meta['feed_id'], f_idx, shm_ref, metrics_obj.to_dict(), serialized_v, extra))
                                sent_shm_refs.add(shm_ref)
                                logger.info(f"[Worker {worker_id}] Pushed result for {meta['feed_id']} frame {f_idx} to central_output queue")
                        except queue.Full:
                            metrics_obj.frames_dropped += 1

                        now = time.time()
                        if now - last_metrics_log > 30.0:
                            for fid, m in metrics_map.items():
                                logger.info(f"[Worker {worker_id}][{fid}] METRICS: {json.dumps(m.to_dict())}")
                            last_metrics_log = now

                except Exception as e:
                    logger.error(f"[Worker {worker_id}] Error processing batch: {e}", exc_info=True)
                finally:
                    # SAFE RELEASE: Release all SHM segments in the batch that were NOT sent to the manager.
                    # If they were sent, the manager is now responsible for releasing them.
                    # Also ACK all processed messages to prevent pending buildup in Redis Streams.
                    for meta_item in batch_meta:
                        # ACK the message on the slot queue it came from
                        msg_id = meta_item.get("msg_id")
                        slot_q_ref = meta_item.get("slot_q")
                        if msg_id and slot_q_ref and hasattr(slot_q_ref, 'ack'):
                            try:
                                slot_q_ref.ack(msg_id)
                            except Exception:
                                pass
                        
                        # Release SHM segments not sent to the output queue
                        shm_ref = meta_item.get("shm_ref")
                        if shm_ref and frame_buffer and shm_ref not in sent_shm_refs:
                            try:
                                frame_buffer.release(shm_ref)
                            except Exception:
                                pass
            except Exception as e:
                logger.error(f"[Worker {worker_id}] Error: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"[Worker {worker_id}] Fatal error: {e}", exc_info=True)
    finally:
        if local_reid_manager:
            logger.debug(f"[Worker {worker_id}] Cleaning up local ReID manager...")
            try:
                # Assuming GlobalReIDManager might have a cleanup or close method
                if hasattr(local_reid_manager, 'cleanup'):
                    local_reid_manager.cleanup()
                elif hasattr(local_reid_manager, 'close'):
                    local_reid_manager.close()
            except Exception as e:
                logger.error(f"[Worker {worker_id}] Error cleaning up ReID manager: {e}")

        for feed_id, cm in core_modules.items():
            try:
                cm.cleanup()
            except Exception as e:
                logger.error(f"[Worker {worker_id}] Error cleaning up CoreModule for {feed_id}: {e}")
        
        logger.info(f"Inference process {os.getpid()} terminated.")