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

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["TF_FORCE_GPU_ALLOW_GROWTH"] = "true"

from app.config import initialize_config
from app.core.core_module import CoreModule
from app.utils.monitoring import TrafficMonitor
from app.utils.process import start_parent_monitor
from .worker_utils import WorkerMetrics, serialize_tracked_vehicles

logger = logging.getLogger("Inference")


def _unpack_queue_result(res) -> Tuple[Optional[Any], Any]:
    """Normalise the (msg_id, task) pair returned by different queue backends."""
    if isinstance(res, tuple) and len(res) == 2:
        return res  # Works for both RedisStreamQueue and plain tuples
    if isinstance(res, dict) and "msg_id" in res:
        return res["msg_id"], res
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
    
    This worker manages:
    1. Command handling (config updates).
    2. Batching frames from multiple slots to optimize GPU inference.
    3. Coordinating with CoreModule for detection and tracking.
    4. Forwarding results to the central output queue.
    
    Args:
        worker_id: Unique identifier for this process.
        central_input_queue: List of queues (slots) containing frame tasks.
        command_queue: Queue for receiving control messages.
        stop_event: Signal to trigger graceful shutdown.
        config: Global application configuration.
        db_queue: Queue for asynchronous database writes.
        frame_buffer: Shared memory manager for frame access.
        pipeline_pressure: RedisValue indicating system-wide congestion.
        slots: List of slot indices assigned to this worker.
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

    # Clean up stale stop signal from previous runs to prevent immediate exit
    try:
        redis_client.delete("signal:pipeline_stop")
    except Exception as e:
        logger.warning(f"Failed to clear stale pipeline stop signal: {e}")

    if stop_event and hasattr(stop_event, "clear"):
        try:
            stop_event.clear()
            logger.debug(f"[Worker {worker_id}] Cleared shared stop event on boot.")
        except Exception as e:
            logger.warning(f"Failed to clear shared stop event: {e}")

    slots = slots or []

    signal_stop = False

    # Adaptive backpressure state (per-worker, persists across loop iterations).
    # None => not yet synced; synced to the configured skip cadence on the
    # first loop iteration so steady-state detection frequency is unchanged.

    def should_stop() -> bool:
        nonlocal signal_stop
        if signal_stop:
            return True
        if stop_event and getattr(stop_event, "is_set", lambda: False)():
            return True
        if redis_client and redis_client.exists("signal:pipeline_stop"):
            return True
        return False

    def signal_handler(signum, frame):
        nonlocal signal_stop
        logger.info(f"[Worker {worker_id}] Received signal {signum}, setting local stop flag.")
        signal_stop = True

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
    inference_cfg = config.get("inference", {})
    target_fps = config.get("video_processing", {}).get("target_fps", 15)
    ocr_cfg = config.get("ocr_engine", {})
    stream_res = tuple(config.get("video_output", {}).get("stream_resolution", (640, 480)))
    # Read skip_frames from inference section (preferred) or vehicle_detection (fallback)
    skip_frames = inference_cfg.get("skip_frames", vehicle_det_cfg.get("skip_frames", 2))
    skip_frames_base = skip_frames  # baseline cadence; adaptive skip multiplies this
    skip_factor = None  # None => unset; lazily set to 1.0 on first loop iteration
    model_path = vehicle_det_cfg.get("model_path")

    # Detection filtering params (applied in the batched inference path so it
    # matches DetectionEngine.detect(): honor the configured low-confidence
    # floor for ByteTrack's second association stage, and restrict to vehicle
    # classes. ROI filtering is applied per-feed via core.detector.is_in_roi().
    vehicle_class_ids = set(vehicle_det_cfg.get("vehicle_class_ids", [2, 3, 5, 7]))
    batch_conf_floor = vehicle_det_cfg.get("low_confidence_threshold", 0.1)

    # --- Shared model loading ---
    shared_reid_embedder = None
    device = "cpu"  # Default; overridden below if GPU is available

    if model_path:
        # Worker always loads models on boot. should_stop() is checked in the
        # main processing loop only -- not during init -- to prevent premature
        # exits from stale Redis stop signals left by prior runs.
        try:
            logger.info(f"[Worker {worker_id}] Loading shared models...")
            root_dir = config.get("project_root_dir", "")
            full_model_path = str(Path(root_dir) / model_path)
            use_gpu = config.get("performance", {}).get("gpu_acceleration", False)

            try:
                from ultralytics import YOLO
            except ImportError as e:
                logger.critical(f"[Worker {worker_id}] Failed to import 'ultralytics' package: {e}. Ensure it is installed in the environment.")
                return

            import torch

            # Multi-GPU support: distribute workers across available GPUs
            use_gpu = config.get("performance", {}).get("gpu_acceleration", False)
            if use_gpu and torch.cuda.is_available():
                num_gpus = torch.cuda.device_count()
                # Assign worker to GPU in round-robin fashion
                gpu_id = worker_id % num_gpus
                device = f"cuda:{gpu_id}"
                logger.info(f"[Worker {worker_id}] Multi-GPU: using GPU {gpu_id}/{num_gpus} ({torch.cuda.get_device_name(gpu_id)})")
            else:
                device = "cpu"
            engine_path = Path(full_model_path).with_suffix(".engine")

            if engine_path.exists():
                logger.info(f"[Worker {worker_id}] Found TensorRT engine: {engine_path}")
                shared_model = YOLO(str(engine_path))
            else:
                shared_model = YOLO(full_model_path)

            shared_model.to(device)
            logger.info(f"[Worker {worker_id}] Shared model loaded on {device}.")

            if vehicle_det_cfg.get("reid_enabled", True):
                from app.ml.reid_model import ReIDEmbedder

                logger.info(f"[Worker {worker_id}] Pre-loading ReID Embedder on {device}...")
                shared_reid_embedder = ReIDEmbedder(config, device=device)
                logger.info(f"[Worker {worker_id}] ReID Embedder pre-loaded.")

            # Signal readiness to FeedManager
            try:
                redis_client.set(f"worker:{worker_id}:ready", "1")
                redis_client.set(f"worker:{worker_id}:status", "ready")
                # Add to ready set for robust counting by FeedManager
                # Store as string to ensure consistent deserialization
                redis_client.sadd("workers:ready_set", str(worker_id))
                logger.info(f"[Worker {worker_id}] Readiness signal sent to Redis.")
            except Exception as e:
                logger.warning(f"[Worker {worker_id}] Failed to send readiness signal: {e}")

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

            # Read batch config from inference section (performance.batch_size is for ingestion)
            batch_size = config.get("inference", {}).get("batch_size", config.get("performance", {}).get("batch_size", 1))

            # Adaptive backpressure relief. The pipeline_pressure arg is ALWAYS
            # None here (the pool manager passes None and the worker reads
            # pressure from Redis instead), so the upstream pressure_val block
            # was dead code and batch_size never shrank. Derive real pressure
            # from THIS worker's own queue depth vs the configured ceiling so
            # the autoscaler stops fighting an unbounded backlog by spawning
            # more workers that only contend on the 2 GPUs.
            perf_cfg = config.get("performance", {})
            queue_max = float(perf_cfg.get("queue_max_size", 500))
            fullness = min(1.0, q_depth / queue_max) if queue_max > 0 else 0.0

            # Half the batch under heavy backlog so GPU forward calls stay
            # cheap and the queue drains instead of growing.
            if fullness > 0.7:
                batch_size = max(1, batch_size // 2)
                logger.debug(
                    f"[Worker {worker_id}] Queue fullness {fullness:.2f} "
                    f"(depth {q_depth}). Batch size -> {batch_size}"
                )

            # Grow skip_frames under sustained backlog (detect less often, more
            # tracks per YOLO call) to shed per-frame YOLO+ReID cost. The
            # `global_skip_factor` in config is a MULTIPLIER on the configured
            # baseline skip cadence (1.0x = normal, 2.0x = detect half as
            # often), bounded by min/max so detection cadence never collapses.
            # The warm-up frame (first_detect) always forces a detect regardless
            # of skip, so tracking stays alive.
            if skip_factor is None:
                skip_factor = 1.0  # start at baseline cadence
            min_skip = float(perf_cfg.get("min_global_skip_factor", 1.0))
            max_skip = float(perf_cfg.get("max_global_skip_factor", 2.0))
            increase_step = float(perf_cfg.get("skip_factor_increase_step", 0.1))
            decrease_step = float(perf_cfg.get("skip_factor_decrease_step", 0.05))
            if fullness > perf_cfg.get("queue_fullness_threshold_for_skip_increase", 0.8):
                skip_factor = min(max_skip, skip_factor + increase_step)
            else:
                skip_factor = max(min_skip, skip_factor - decrease_step)
            skip_frames = int(round(skip_frames_base * skip_factor))
            batch_size = min(batch_size, 8)
            # Read inference_timeout from inference section
            inference_timeout = config.get("inference", {}).get("inference_timeout", config.get("performance", {}).get("inference_timeout", 0.05))

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

            logger.debug(f"[Worker {worker_id}] Received {len(batch_tasks)} tasks from inference queue")

            # Fill batch up to batch_size within timeout
            start_wait = time.time()
            skip_threshold = config.get("performance", {}).get("skip_threshold", 200)
            while len(batch_tasks) < batch_size and (time.time() - start_wait < inference_timeout):
                for slot_id in slots:
                    try:
                        slot_q = central_input_queue[slot_id]
                        res = slot_q.get_nowait()
                        if not res:
                            continue
                        msg_id, task = _unpack_queue_result(res)

                        # Smart skip: drop non-control frames under heavy queue pressure
                        if q_depth > skip_threshold and isinstance(task, (tuple, list)) and len(task) >= 4:
                            t_feed_id, t_frame_idx = task[0], task[1]
                            shm_ref = task[2]
                            if t_frame_idx not in (-888, -999):
                                if t_feed_id in core_modules and getattr(
                                    core_modules[t_feed_id], "_first_detection_done", False
                                ):
                                    if msg_id and hasattr(slot_q, "ack"):
                                        slot_q.ack(msg_id)
                                        acked_msgs.add(msg_id)
                                    if frame_buffer:
                                        frame_buffer.release(shm_ref)
                                    continue

                        batch_tasks.append((msg_id, task, slot_q))
                    except (queue.Empty, IndexError):
                        continue
                time.sleep(0.0005)

            # --- Process batch ---
            acked_msgs: set = set()
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
                            acked_msgs.add(msg_id)
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
                            acked_msgs.add(msg_id)
                        continue

                    # Read frame from shared memory
                    if frame_buffer:
                        try:
                            res = frame_buffer.read(shm_ref, expected_feed_id=feed_id)
                        except Exception as e:
                            logger.error(f"[Worker {worker_id}] SHM read failed for ref {shm_ref}: {e}")
                            if msg_id and hasattr(slot_q_ref, "ack"):
                                slot_q_ref.ack(msg_id)
                                acked_msgs.add(msg_id)
                            try:
                                frame_buffer.release(shm_ref)
                            except Exception:
                                pass
                            continue

                        if res is None:
                            # SHM read returns None when segment was recycled (feed mismatch) or stale.
                            # This is normal under high load - frame is dropped to keep pipeline moving.
                            # Increment drop counter for metrics tracking.
                            if feed_id in metrics_map:
                                metrics_map[feed_id].frames_dropped += 1
                            if msg_id and hasattr(slot_q_ref, "ack"):
                                slot_q_ref.ack(msg_id)
                            try:
                                frame_buffer.release(shm_ref)
                            except Exception:
                                pass
                            continue
                        if isinstance(res, tuple) and len(res) == 2:
                            frame_bytes, dims = res
                        else:
                            logger.error(
                                f"[Worker {worker_id}] SHM read unexpected format: {type(res)}"
                            )
                            if msg_id and hasattr(slot_q_ref, "ack"):
                                slot_q_ref.ack(msg_id)
                                acked_msgs.add(msg_id)
                            try:
                                frame_buffer.release(shm_ref)
                            except Exception:
                                pass
                            continue
                    else:
                        raise RuntimeError(f"[Worker {worker_id}] Frame buffer is missing but SHM reference {shm_ref} was provided.")

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
                            preloaded_model=shared_model,
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
                        if frame_bytes is None:
                            logger.error(f"[Worker {worker_id}] frame_bytes is None for ref {shm_ref}")
                            if msg_id and hasattr(slot_q_ref, "ack"):
                                slot_q_ref.ack(msg_id)
                                acked_msgs.add(msg_id)
                            continue

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
                                acked_msgs.add(msg_id)
                            try:
                                frame_buffer.release(shm_ref)
                            except Exception:
                                pass
                            continue

                        core.last_activity = time.time()

                    meta_entry = {
                        "msg_id": msg_id,
                        "slot_q": slot_q_ref,
                        "shm_ref": shm_ref,
                        "frame_bytes": frame_bytes,
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
                        results = shared_model(frames_to_infer, conf=batch_conf_floor, verbose=False, stream=False)
                        for i, result in enumerate(results):
                            meta_idx = inference_indices[i]
                            meta = batch_meta[meta_idx]
                            boxes_data = result.boxes.data.cpu().numpy()
                            x_off, y_off = meta.get("crop_offsets", (0, 0))
                            detector = meta["core"].detector
                            formatted_dets = []
                            for row in boxes_data:
                                rx1, ry1, rx2, ry2, conf, cls_id = row
                                # Restrict to vehicle classes (parity with DetectionEngine.detect)
                                if int(cls_id) not in vehicle_class_ids:
                                    continue
                                bbox = (rx1 + x_off, ry1 + y_off, rx2 + x_off, ry2 + y_off)
                                # ROI filtering (parity with DetectionEngine.detect)
                                if not detector.is_in_roi(np.array(bbox)):
                                    continue
                                formatted_dets.append((bbox, cls_id, conf))
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

                    # Re-ID matching (guarded by config)
                    if vehicle_det_cfg.get("reid_enabled", True):
                        for vid, track in vis_tracks.items():
                            vehicle_map = core.vehicle_type_map
                            # global_vehicle_id persists in the tracker's
                            # vehicle_data across frames. Re-running
                            # match_or_register for an already-identified track
                            # every frame causes a Redis round-trip storm
                            # (hget/incr/set/rpush/publish per call) that pins
                            # the worker to ~1 fps/feed. Only match/register
                            # tracks that have not yet been assigned an id.
                            if track.get("global_vehicle_id"):
                                continue
                            emb = track.get("embedding")
                            if emb is not None:
                                global_id = local_reid_manager.match_or_register(
                                    feed_id=meta["feed_id"],
                                    local_id=str(vid),
                                    embedding=np.array(emb),
                                    metadata={
                                        "class_name": vehicle_map.get(
                                            track["class_id"], "unknown"
                                        )
                                    },
                                    confidence=track.get("confidence", 1.0),
                                )
                                track["global_vehicle_id"] = global_id
                            else:
                                mapped_id = local_reid_manager.get_global_id(meta["feed_id"], str(vid))
                                if mapped_id:
                                    track["global_vehicle_id"] = mapped_id

                    monitor.update_vehicles(vis_tracks)
                    
                    # Merge operational metrics with traffic analytics
                    combined_metrics = metrics_obj.to_dict()
                    combined_metrics.update(monitor.get_metrics())
                    
                    metrics_obj.frames_processed += 1
                    metrics_obj.mark_frame()  # keep rolling fps real (was never called -> fps pinned at 0.0)

                    serialized_v = serialize_tracked_vehicles(
                        vis_tracks, vehicle_type_map=core.vehicle_type_map
                    )

                    extra = {}

                    try:
                        # RedisStreamQueue uses put(), not put_nowait()
                        # Option A (zero-SHM-race): forward the decoded frame
                        # bytes instead of the SHM segment ref. The frame data
                        # is copied out of shared memory here, so the segment
                        # is released immediately (see finally block) and the
                        # result processor never touches SHM. This eliminates
                        # the ~14% read-failure race where a segment could be
                        # recycled under the async result reader.
                        central_output_queue.put(
                            (meta["feed_id"], f_idx, meta["frame_bytes"], combined_metrics, serialized_v, extra),
                            timeout=0.05
                        )
                        logger.debug(
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
                # ACK all messages and release SHM segments.
                # Option A: the frame bytes were copied out and forwarded to
                # central_output, so every segment we touched is now safe to
                # release back to the pool here -- including forwarded refs.
                # The result processor consumes the copied bytes and never
                # reads SHM, so there is no longer any reason to hold a
                # forwarded segment in-flight (that hold was the source of the
                # ~14% read-failure race).
                for meta_item in batch_meta:
                    msg_id = meta_item.get("msg_id")
                    slot_q_ref = meta_item.get("slot_q")
                    if msg_id and slot_q_ref and hasattr(slot_q_ref, "ack") and msg_id not in acked_msgs:
                        try:
                            slot_q_ref.ack(msg_id)
                        except Exception:
                            pass

                    shm_ref = meta_item.get("shm_ref")
                    if shm_ref and frame_buffer:
                        try:
                            frame_buffer.release(shm_ref)
                        except Exception:
                            m_obj = meta_item.get("metrics")
                            if m_obj:
                                m_obj.shm_leaks += 1
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