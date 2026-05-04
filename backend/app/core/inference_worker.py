import os
import sys
import cv2
import logging
import time
import numpy as np
import queue
import signal
import json
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["TF_FORCE_GPU_ALLOW_GROWTH"] = "true"

from app.config import initialize_config
from app.core.core_module import CoreModule
from app.utils.monitoring import TrafficMonitor
from app.utils.process import start_parent_monitor
from .worker_utils import WorkerMetrics, serialize_tracked_vehicles

logger = logging.getLogger("Inference")


def _serialize_tracked_vehicles_with_map(
    tracked_vehicles: Dict[str, Dict],
    scale_x: float = 1.0,
    scale_y: float = 1.0,
) -> List[Dict[str, Any]]:
    """Wrapper that uses CoreModule's vehicle_type_map."""
    v_map = CoreModule.vehicle_type_map if CoreModule is not None else {}
    return serialize_tracked_vehicles(tracked_vehicles, scale_x, scale_y, v_map)


def _extract_rois(
    frame: np.ndarray,
    tracked_vehicles: List[Dict[str, Any]],
    scale: float = 1.0,
) -> List[Dict[str, Any]]:
    """Extracts high-res JPEG patches for active vehicles."""
    rois = []
    h, w = frame.shape[:2]
    for v in tracked_vehicles:
        bbox = v.get("bbox")
        if not bbox or len(bbox) != 4:
            continue

        x1, y1, x2, y2 = map(int, bbox)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)

        if x2 <= x1 or y2 <= y1:
            continue

        crop = frame[y1:y2, x1:x2]
        _, crop_bytes = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        rois.append({"b": crop_bytes.tobytes(), "x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1})
    return rois


def _unpack_queue_result(res) -> Tuple[Optional[Any], Any]:
    """Normalise the (msg_id, task) pair returned by different queue backends."""
    if isinstance(res, tuple) and len(res) == 2:
        return res  # Works for both RedisStreamQueue and plain tuples
    return None, res


def inference_worker(
    worker_id: int,
    central_input_queue: Any,
    command_queue: Any,
    stop_event: Any,
    config: Dict[str, Any],
    db_queue: Optional[Any] = None,
    frame_buffer: Any = None,
    pipeline_pressure: Any = None,
    slots: List[int] = None,
):
    """
    Heavyweight AI process that processes frames from the central queue.
    Can handle frames from multiple feeds interleaved.
    """
    print(
        f"[INFERENCE-BOOT] Worker {worker_id} PID={os.getpid()} slots={slots} starting...",
        file=sys.stderr,
        flush=True,
    )

    initialize_config()

    import logging.config as logging_config

    try:
        logging_config.dictConfig(config["logging"])
    except Exception as e:
        print(f"Logging configuration failed: {e}", file=sys.stderr)
        logging.basicConfig(level=logging.INFO)

    logger.info(f"[Worker {worker_id}] Process initialized. PID={os.getpid()}")
    logger.info(
        f"[Worker {worker_id}] Queue list type: {type(central_input_queue)}, "
        f"length: {len(central_input_queue) if hasattr(central_input_queue, '__len__') else 'N/A'}"
    )
    logger.info(f"[Worker {worker_id}] Slots assigned: {slots}")

    from app.utils.redis_client import get_redis_client
    from app.utils.distributed_queue import RedisStreamQueue

    redis_client = get_redis_client()
    central_output_queue = RedisStreamQueue("central_output", group_name="output-readers")

    slots = slots or []

    def should_stop() -> bool:
        if stop_event and getattr(stop_event, "is_set", lambda: False)():
            return True
        if redis_client and redis_client.exists("signal:pipeline_stop"):
            return True
        return False

    def signal_handler(signum, frame):
        logger.info(f"[Worker {worker_id}] Received signal {signum}, stopping gracefully")
        if stop_event:
            stop_event.set()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    pid = os.getpid()
    try:
        redis_client.set(f"worker:{worker_id}:pid", pid)
        redis_client.set(f"worker:{worker_id}:status", "initializing")
    except Exception:
        pass

    logger.debug(f"[Worker {worker_id}] Starting parent monitor...")
    start_parent_monitor(stop_event, f"Inference-{worker_id}")

    # Per-feed state (lazy-initialised)
    core_modules: Dict[str, CoreModule] = {}
    traffic_monitors: Dict[str, TrafficMonitor] = {}
    pending_configs: Dict[str, Dict] = {}
    metrics_map: Dict[str, WorkerMetrics] = {}
    shared_model = None

    from app.services.reid_manager import GlobalReIDManager
    local_reid_manager = GlobalReIDManager(config)

    from app.utils.shared_frame_buffer import SharedFrameBuffer

    if frame_buffer is None:
        frame_buffer = SharedFrameBuffer(
            pool_size=config.get("performance", {}).get("shm_pool_size", 100),
            read_only=False,
        )

    vehicle_det_cfg = config.get("vehicle_detection", {})
    target_fps = config.get("video_processing", {}).get("target_fps", 15)
    ocr_cfg = config.get("ocr_engine", {})
    stream_res = tuple(config.get("video_output", {}).get("stream_resolution", (640, 480)))
    skip_frames = vehicle_det_cfg.get("skip_frames", 2)
    model_path = vehicle_det_cfg.get("model_path")

    # --- Shared model loading ---
    model_load_failed = False
    shared_reid_embedder = None

    if model_path:
        if should_stop():
            logger.info(f"[Worker {worker_id}] Stop signal before model load. Exiting.")
            return

        try:
            logger.info(f"[Worker {worker_id}] Loading shared models...")
            root_dir = config.get("project_root_dir", "")
            full_model_path = str(Path(root_dir) / model_path)
            use_gpu = config.get("performance", {}).get("gpu_acceleration", False)

            from ultralytics import YOLO
            import torch

            device = "cuda:0" if use_gpu and torch.cuda.is_available() else "cpu"
            engine_path = Path(full_model_path).with_suffix(".engine")

            if engine_path.exists():
                logger.info(f"[Worker {worker_id}] Found TensorRT engine: {engine_path}")
                shared_model = YOLO(str(engine_path))
            else:
                shared_model = YOLO(full_model_path)

            shared_model.to(device)
            logger.info(f"[Worker {worker_id}] Shared model loaded on {device}.")

            if should_stop():
                logger.info(f"[Worker {worker_id}] Stop signal after YOLO load. Exiting.")
                return

            if vehicle_det_cfg.get("reid_enabled", True):
                from app.ml.reid_model import ReIDEmbedder

                logger.info(f"[Worker {worker_id}] Pre-loading ReID Embedder...")
                shared_reid_embedder = ReIDEmbedder(config)
                logger.info(f"[Worker {worker_id}] ReID Embedder pre-loaded.")

        except Exception as e:
            logger.error(f"[Worker {worker_id}] Shared model load exception: {e}")
            logger.critical(f"[Worker {worker_id}] Shared model load failed. Exiting.")
            return

    # --- Command handler ---
    def handle_command(cmd: Dict) -> None:
        if not cmd:
            return
        try:
            cmd_type = cmd.get("type")
            if cmd_type == "config_update":
                data = cmd.get("data", {})
                feed_id_cmd = data.get("feed_id") or cmd.get("feed_id")
                if feed_id_cmd:
                    if feed_id_cmd not in core_modules:
                        pending_configs.setdefault(feed_id_cmd, {}).update(data)
                    else:
                        core_modules[feed_id_cmd].update_config(data)
        except Exception as e:
            logger.error(f"[Worker {worker_id}] Command error: {e}")

    last_metrics_log = time.time()

    try:
        while True:
            if should_stop():
                logger.info(f"[Worker {worker_id}] Stop signal received. Exiting main loop.")
                break

            # Drain command queue
            try:
                while True:
                    cmd = command_queue.get_nowait()
                    handle_command(cmd)
            except queue.Empty:
                pass

            # --- Batching ---
            batch_tasks = []
            q_depth = sum(central_input_queue[s].qsize() for s in slots)

            batch_size = config.get("performance", {}).get("batch_size", 1)
            if pipeline_pressure and pipeline_pressure.get("value", 0) > 0.7:
                batch_size = max(1, batch_size // 2)
                logger.debug(
                    f"[Worker {worker_id}] High pipeline pressure "
                    f"({pipeline_pressure.get('value'):.2f}). Batch size -> {batch_size}"
                )
            batch_size = min(batch_size, 8)
            inference_timeout = config.get("performance", {}).get("inference_timeout", 0.005)

            # Initial poll of each slot
            for slot_id in slots:
                try:
                    res = central_input_queue[slot_id].get_nowait()
                    if res:
                        msg_id, task = _unpack_queue_result(res)
                        batch_tasks.append((msg_id, task, central_input_queue[slot_id]))
                except (queue.Empty, IndexError):
                    continue

            if not batch_tasks:
                time.sleep(0.01)
                continue

            logger.info(f"[Worker {worker_id}] Received {len(batch_tasks)} tasks from inference queue")

            # Fill batch up to batch_size within timeout
            start_wait = time.time()
            while len(batch_tasks) < batch_size and (time.time() - start_wait < inference_timeout):
                for slot_id in slots:
                    try:
                        slot_q = central_input_queue[slot_id]
                        res = slot_q.get_nowait()
                        if not res:
                            continue
                        msg_id, task = _unpack_queue_result(res)

                        # Smart skip: drop non-control frames under heavy queue pressure
                        if q_depth > 200 and isinstance(task, (tuple, list)) and len(task) >= 4:
                            t_feed_id, t_frame_idx = task[0], task[1]
                            if t_frame_idx not in (-888, -999):
                                if t_feed_id in core_modules and getattr(
                                    core_modules[t_feed_id], "_first_detection_done", False
                                ):
                                    if msg_id and hasattr(slot_q, "ack"):
                                        slot_q.ack(msg_id)
                                    continue

                        batch_tasks.append((msg_id, task, slot_q))
                    except (queue.Empty, IndexError):
                        continue
                time.sleep(0.0005)

            # --- Process batch ---
            sent_shm_refs: set = set()
            batch_meta: List[Dict] = []
            frames_to_infer: List[np.ndarray] = []
            inference_indices: List[int] = []

            try:
                for msg_id, task, slot_q_ref in batch_tasks:
                    feed_id, frame_index, shm_ref, extra_payload = task

                    # Control messages
                    if frame_index == -888:
                        metrics_map.setdefault(feed_id, WorkerMetrics(feed_id))
                        if feed_id in core_modules:
                            core_modules[feed_id]._first_detection_done = False
                        if msg_id and hasattr(slot_q_ref, "ack"):
                            slot_q_ref.ack(msg_id)
                        continue

                    if frame_index == -999:
                        if feed_id in core_modules:
                            core_modules[feed_id].cleanup()
                            del core_modules[feed_id]
                        traffic_monitors.pop(feed_id, None)
                        pending_configs.pop(feed_id, None)
                        metrics_map.pop(feed_id, None)
                        if msg_id and hasattr(slot_q_ref, "ack"):
                            slot_q_ref.ack(msg_id)
                        continue

                    # Read frame from shared memory
                    if frame_buffer:
                        res = frame_buffer.read(shm_ref)
                        if res is None:
                            logger.error(f"[Worker {worker_id}] SHM read returned None for ref {shm_ref}")
                            if msg_id and hasattr(slot_q_ref, "ack"):
                                slot_q_ref.ack(msg_id)
                            continue
                        if isinstance(res, tuple) and len(res) == 2:
                            frame_bytes, dims = res
                        else:
                            logger.error(
                                f"[Worker {worker_id}] SHM read unexpected format: {type(res)}"
                            )
                            if msg_id and hasattr(slot_q_ref, "ack"):
                                slot_q_ref.ack(msg_id)
                            continue
                    else:
                        frame_bytes, dims = shm_ref, (0, 0, 0)

                    timestamp = extra_payload if isinstance(extra_payload, (int, float)) else time.time()

                    metrics_map.setdefault(feed_id, WorkerMetrics(feed_id))

                    if feed_id not in core_modules:
                        core_modules[feed_id] = CoreModule(
                            feed_id=feed_id,
                            model_path=vehicle_det_cfg.get("model_path"),
                            config=config,
                            fps=target_fps,
                            db_queue=db_queue,
                            gemini_api_key=ocr_cfg.get("gemini_api_key"),
                            model_type=vehicle_det_cfg.get("model_type", "yolo"),
                            preloaded_model=shared_model if not model_load_failed else None,
                            preloaded_reid=shared_reid_embedder,
                        )
                        core_modules[feed_id]._first_detection_done = False
                        traffic_monitors[feed_id] = TrafficMonitor(config)
                        if feed_id in pending_configs:
                            core_modules[feed_id].update_config(pending_configs.pop(feed_id))

                    core = core_modules[feed_id]
                    monitor = traffic_monitors[feed_id]
                    metrics_obj = metrics_map[feed_id]

                    first_detect = not getattr(core, "_first_detection_done", False)
                    should_detect = (frame_index % (skip_frames + 1) == 0) or (
                        first_detect and not core.tracker.vehicle_data
                    )

                    lane_cfg = config.get("lane_detection", {})
                    is_lane_frame = lane_cfg.get("dynamic_lane_detection_enabled", False) and (
                        frame_index % lane_cfg.get("lane_detection_interval", 10) == 0
                    )

                    frame = None
                    if should_detect or is_lane_frame:
                        if isinstance(frame_bytes, memoryview):
                            frame = cv2.imdecode(
                                np.frombuffer(frame_bytes, dtype=np.uint8), cv2.IMREAD_COLOR
                            )
                        elif isinstance(frame_bytes, np.ndarray):
                            frame = frame_bytes
                        else:
                            frame = cv2.imdecode(
                                np.frombuffer(frame_bytes, np.uint8), cv2.IMREAD_COLOR
                            )

                        if frame is None:
                            metrics_obj.errors += 1
                            if msg_id and hasattr(slot_q_ref, "ack"):
                                slot_q_ref.ack(msg_id)
                            continue

                        core.last_activity = time.time()

                    meta_entry = {
                        "msg_id": msg_id,
                        "slot_q": slot_q_ref,
                        "shm_ref": shm_ref,
                        "feed_id": feed_id,
                        "frame_index": frame_index,
                        "frame": frame,
                        "timestamp": timestamp,
                        "core": core,
                        "monitor": monitor,
                        "metrics": metrics_obj,
                        "should_detect": should_detect,
                        "first_detect": first_detect,
                    }

                    if should_detect and frame is not None:
                        proc_frame, roi_enabled, x_off, y_off = core._preprocess_frame(frame)
                        frames_to_infer.append(proc_frame)
                        inference_indices.append(len(batch_meta))
                        meta_entry["crop_offsets"] = (x_off, y_off) if roi_enabled else (0, 0)

                    batch_meta.append(meta_entry)

                # Batch inference
                batch_detections_map: Dict[int, List] = {}
                if frames_to_infer and shared_model is not None:
                    try:
                        results = shared_model(frames_to_infer, verbose=False, stream=False)
                        for i, result in enumerate(results):
                            meta_idx = inference_indices[i]
                            meta = batch_meta[meta_idx]
                            boxes_data = result.boxes.data.cpu().numpy()
                            x_off, y_off = meta.get("crop_offsets", (0, 0))
                            formatted_dets = []
                            for row in boxes_data:
                                rx1, ry1, rx2, ry2, conf, cls_id = row
                                formatted_dets.append(
                                    ((rx1 + x_off, ry1 + y_off, rx2 + x_off, ry2 + y_off), conf, cls_id)
                                )
                            batch_detections_map[meta_idx] = formatted_dets
                    except Exception as e:
                        logger.error(f"[Worker {worker_id}] Batch inference failed: {e}")

                # Tracking & output
                for i, meta in enumerate(batch_meta):
                    core = meta["core"]
                    monitor = meta["monitor"]
                    metrics_obj = meta["metrics"]
                    frame = meta["frame"]
                    f_idx = meta["frame_index"]

                    if frame is None:
                        continue

                    detections = batch_detections_map.get(i, []) if meta["should_detect"] else []
                    vis_tracks, lane_bounds, lane_lines = core.detect_and_track(
                        frame, f_idx, external_detections=detections, timestamp=meta.get("timestamp")
                    )

                    if vis_tracks and meta["first_detect"]:
                        core._first_detection_done = True

                    for vid, track in vis_tracks.items():
                        emb = track.get("embedding")
                        if emb:
                            global_id = local_reid_manager.match_or_register(
                                feed_id=meta["feed_id"],
                                local_id=str(vid),
                                embedding=np.array(emb),
                                metadata={
                                    "class_name": CoreModule.vehicle_type_map.get(
                                        track["class_id"], "unknown"
                                    )
                                },
                                confidence=track.get("confidence", 1.0),
                            )
                            track["global_vehicle_id"] = global_id
                        elif not track.get("global_vehicle_id"):
                            mapped_id = local_reid_manager.get_global_id(meta["feed_id"], str(vid))
                            if mapped_id:
                                track["global_vehicle_id"] = mapped_id

                    monitor.update_vehicles(vis_tracks)
                    metrics_obj.frames_processed += 1

                    serialized_v = _serialize_tracked_vehicles_with_map(vis_tracks)

                    extra = {}
                    v_proc_cfg = config.get("video_processing", {})
                    if v_proc_cfg.get("adaptive_streaming", False) and meta["should_detect"]:
                        bg_scale = v_proc_cfg.get("roi_scale", 0.5)
                        bg_frame = cv2.resize(frame, (0, 0), fx=bg_scale, fy=bg_scale)
                        _, bg_bytes = cv2.imencode(
                            ".jpg", bg_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 50]
                        )
                        extra["bg"] = bg_bytes.tobytes()
                        roi_scale = stream_res[0] / 640.0 if stream_res[0] != 0 else 1.0
                        extra["rois"] = _extract_rois(frame, serialized_v, scale=roi_scale)

                    try:
                        central_output_queue.put_nowait(
                            (meta["feed_id"], f_idx, meta["shm_ref"], metrics_obj.to_dict(), serialized_v, extra)
                        )
                        sent_shm_refs.add(meta["shm_ref"])
                        logger.info(
                            f"[Worker {worker_id}] Pushed result for {meta['feed_id']} "
                            f"frame {f_idx} to central_output"
                        )
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
                # ACK all messages and release SHM segments not forwarded to output
                for meta_item in batch_meta:
                    msg_id = meta_item.get("msg_id")
                    slot_q_ref = meta_item.get("slot_q")
                    if msg_id and slot_q_ref and hasattr(slot_q_ref, "ack"):
                        try:
                            slot_q_ref.ack(msg_id)
                        except Exception:
                            pass

                    shm_ref = meta_item.get("shm_ref")
                    if shm_ref and frame_buffer and shm_ref not in sent_shm_refs:
                        try:
                            frame_buffer.release(shm_ref)
                        except Exception:
                            pass

    except Exception as e:
        logger.error(f"[Worker {worker_id}] Fatal error: {e}", exc_info=True)

    finally:
        if local_reid_manager:
            logger.debug(f"[Worker {worker_id}] Cleaning up local ReID manager...")
            try:
                if hasattr(local_reid_manager, "cleanup"):
                    local_reid_manager.cleanup()
                elif hasattr(local_reid_manager, "close"):
                    local_reid_manager.close()
            except Exception as e:
                logger.error(f"[Worker {worker_id}] Error cleaning up ReID manager: {e}")

        for feed_id, cm in core_modules.items():
            try:
                cm.cleanup()
            except Exception as e:
                logger.error(f"[Worker {worker_id}] Error cleaning up CoreModule for {feed_id}: {e}")

        logger.info(f"Inference process {os.getpid()} terminated.")