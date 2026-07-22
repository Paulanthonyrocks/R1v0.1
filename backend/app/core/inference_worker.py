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

# Warn-once guards: the lane-detection deprecation warnings used to fire on
# every inference frame, flooding the log. Each worker process emits each
# unique warning at most once via this set.
_WARNED_DEPRECATIONS: set = set()


def _warn_once(key: str, message: str) -> None:
    if key in _WARNED_DEPRECATIONS:
        return
    _WARNED_DEPRECATIONS.add(key)
    logger.warning(message)


def _unpack_queue_result(res) -> Tuple[Optional[Any], Any]:
    """Normalise the (msg_id, task) pair returned by different queue backends."""
    if isinstance(res, tuple) and len(res) == 2:
        return res  # Works for both RedisStreamQueue and plain tuples
    if isinstance(res, dict) and "msg_id" in res:
        return res["msg_id"], res
    return None, res


def _forward_frame(central_output_queue, meta: Dict, metrics_obj, worker_id: int) -> None:
    """Forward a frame's raw bytes downstream without running detection.

    Used for skip-frames (decoded as None) and as a best-effort passthrough
    when a frame's detection/tracking throws, so the live stream never stalls
    on a single bad frame (audit findings #2 / #8).

    On skip-frames the previous implementation emitted an empty vehicles
    list -- this surfaces on the frontend as "vehicle count drops to 0"
    every (skip_frames + 1)th frame, even though the tracker is still
    tracking them through the per-feed CoreModule. To keep the overlay
    continuous we now read the persisted ``core.tracker.vehicle_data`` (if
    available) and serialize the live tracks as the vehicles payload for
    this skip-frame, so KPIs / bounding boxes hold steady until the next
    detect-frame refreshes them. Falls back to empty when the tracker is
    not yet primed (e.g. first few frames before the initial detect) --
    in that case emitting zero is correct: there are no live tracks to
    carry forward.
    """
    if meta.get("frame_bytes") is None:
        return
    try:
        combined_metrics = metrics_obj.to_dict() if metrics_obj is not None else {}

        # Live-status universe -- must mirror VALID_STATUSES in worker_utils
        # (which serialize_tracked_vehicles uses as its own gate) and the
        # status filter in core_module.transform_tracks. We pre-filter here
        # only to avoid handing a huge dict to the serializer when the
        # tracker is full of stale/lost entries; the serializer will still
        # enforce its own gate.
        _LIVE_TRACK_STATUSES = {"active", "predicting"}

        # Pull live tracks from the per-feed CoreModule so the skip-frame
        # payload keeps the freshly-last-detected vehicles on the wire
        # instead of zeroing out. Tracker state persists across detect/skip
        # frames by design; serializing it on skip-frames makes the frontend
        # KPI / bbox layer stable until the next detect refresh.
        #
        # The outer try/except is intentionally broad: a broken
        # ``vehicle_data`` property (e.g. a buggy tracker that raises
        # mid-access) must never propagate out of the forwarder -- the
        # passthrough is the safety net the whole skip-frame branch exists
        # to provide. ``hasattr``-style probes above the try block catch
        # AttributeError but NOT Runtime/Value errors buried inside a
        # property accessor.
        serialized_v: List[Dict[str, Any]] = []
        core = meta.get("core")
        if core is not None and hasattr(core, "tracker"):
            try:
                tracker = core.tracker
                vehicle_data = getattr(tracker, "vehicle_data", None)
                if isinstance(vehicle_data, dict):
                    live_tracks = {
                        str(tid): track
                        for tid, track in vehicle_data.items()
                        if isinstance(track, dict) and track.get("status") in _LIVE_TRACK_STATUSES
                    }
                    if live_tracks:
                        serialized_v = serialize_tracked_vehicles(
                            live_tracks,
                            vehicle_type_map=getattr(core, "vehicle_type_map", None),
                        )
            except Exception:
                # Tracker access must never raise out of the forwarder --
                # any exception falls back to empty and the next detect
                # frame will repopulate the wire payload.
                serialized_v = []

        extra: Dict[str, Any] = {}
        central_output_queue.put(
            (meta["feed_id"], meta["frame_index"], meta["frame_bytes"], combined_metrics, serialized_v, extra),
            timeout=0.05,
        )
    except queue.Full:
        if metrics_obj is not None:
            metrics_obj.frames_dropped += 1
    except Exception:
        # Forwarding must never raise out of the per-item handler.
        pass


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

    # NOTE (audit #3): we do NOT delete("signal:pipeline_stop") here. The
    # global stop signal is write-once-per-lifecycle and owned exclusively by
    # the orchestrator (feed_manager: before scaling / startup / auto-scaling,
    # lines 296/718/1172), which clears stale signals at controlled points.
    # A child deleting it on boot could race with an in-progress shutdown and
    # silently cancel it for every other process watching the key (design #3b).
    # Workers only ever *read* the key (via should_stop()), never clear it.

    # NOTE (audit #3): we deliberately do NOT call stop_event.clear() here.
    # If stop_event is a per-spawn Event it is already unset; if it is shared
    # across the worker pool, clearing it would un-signal every other process
    # watching that same object -- silently cancelling a shutdown in progress.
    # Ownership of stale-signal clearing belongs to the orchestrator, not the
    # child. See design sketch #3(b).

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
    skip_factor = 1.0  # baseline cadence; adaptive skip multiplies this. Initialised at boot, not lazily in the loop.
    is_trt_engine = False  # set True once a static-batch TensorRT engine is loaded
    model_path = vehicle_det_cfg.get("model_path")

    # Detection filtering params (applied in the batched inference path so it
    # matches DetectionEngine.detect(): honor the configured confidence
    # threshold for the YOLO call, restrict to vehicle classes, and cap the
    # per-frame detection count so busy scenes do not pin the worker
    # (audit findings #1 / #3 -- the old config used low_confidence_threshold
    # of 0.01 as the batch floor, 25x below the display floor 0.25, producing
    # 500+-box frames on the sample traffic scene at 320x240 / imgsz 640).
    vehicle_class_ids = set(vehicle_det_cfg.get("vehicle_class_ids", [2, 3, 5, 7]))
    display_conf_floor = float(vehicle_det_cfg.get("confidence_threshold", 0.25))
    low_conf_floor = float(vehicle_det_cfg.get("low_confidence_threshold", 0.1))
    # YOLO conf floor is the higher of the two: low_confidence_threshold is
    # now ONLY a legacy fallback for ByteTrack second-association tuning, never
    # the batch YOLO call's conf arg. Setting the batch floor lower than the
    # display floor generated noise boxes we later threw away -- wasted YOLO
    # cost on the busiest frames.
    batch_conf_floor = max(display_conf_floor, low_conf_floor)
    # Hard cap on detections per frame after class/ROI filtering. Sorted by
    # descending confidence; the top-N win. Default 100 is a generous ceiling
    # for a 320x240 highway scene with imgsz tuned down to 320 (audit #3b).
    max_detections_per_frame = int(vehicle_det_cfg.get("max_detections_per_frame", 100))

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
                # TensorRT engines are shape-locked to the batch they were
                # exported with (export_tensorrt.py builds batch=1). Feeding a
                # multi-frame list to a static-batch engine errors or silently
                # drops all but the first frame, so force per-frame inference.
                is_trt_engine = True
            else:
                shared_model = YOLO(full_model_path)
                is_trt_engine = False

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
            # Must be initialised BEFORE the batch-fill loop: the smart-skip
            # backpressure path (below) calls acked_msgs.add() under queue
            # pressure, and acked_msgs was previously first defined only after
            # that loop -> NameError crash on the worker under backlog.
            acked_msgs: set = set()
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
            if is_trt_engine:
                batch_size = 1  # static-batch=1 engine; multi-frame lists break it
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
            batch_meta: List[Dict] = []
            frames_to_infer: List[np.ndarray] = []
            inference_indices: List[int] = []

            try:
                for msg_id, task, slot_q_ref in batch_tasks:
                    # Defensive unpacking: payloads are 4-tuples
                    # (feed_id, frame_index, shm_ref, extra_payload), but
                    # tolerate malformed ones from a misbehaving producer
                    # instead of raising ValueError and killing the whole batch.
                    if not isinstance(task, (tuple, list)) or len(task) < 4:
                        logger.error(
                            f"[Worker {worker_id}] Malformed task payload: {task!r}"
                        )
                        if msg_id and hasattr(slot_q_ref, "ack"):
                            slot_q_ref.ack(msg_id)
                        continue
                    feed_id, frame_index, shm_ref, extra_payload = task
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
                            # CRITICAL (audit #1): do NOT release here. On a SHM
                            # read failure the segment may already have been
                            # recycled to another feed by the time we get here.
                            # Releasing would srem the *new* owner's acquired-set
                            # claim and push a phantom duplicate into the free
                            # pool -- the very cross-feed corruption the
                            # acquired-set was added to prevent. The real owner
                            # releases it on its own.
                            continue

                        if res is None:
                            # SHM read returns None when the segment was recycled
                            # (feed mismatch) or is stale. Normal under high load.
                            # CRITICAL (audit #1): the segment is no longer ours --
                            # some other producer already re-acquired it. Releasing
                            # would strip that owner's acquired-set entry and push a
                            # duplicate name into the free pool, letting two
                            # producers write the same segment concurrently (silent
                            # cross-feed corruption). The real owner releases it.
                            if feed_id in metrics_map:
                                metrics_map[feed_id].frames_dropped += 1
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
                                acked_msgs.add(msg_id)
                            # CRITICAL (audit #1A): do NOT release here. An
                            # unexpected-format result means read() did not hand
                            # us a live segment we own -- releasing would strip
                            # the real owner's acquired-set claim and push a
                            # phantom duplicate into the free pool. The real
                            # owner releases it. (Dead-code today: read() only
                            # returns None/(data,dims)/raises, never a third
                            # shape, but left as a trap if its contract changes.)
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

                    # Lane detection admission gate (audit #6).
                    # `enabled` gates whether lane detection can ever fire;
                    # `frame_interval` is a CHEAP, FRAME-COUNTED admission filter
                    # -- it forces a frame to decode on a cycle the object
                    # detector's `skip_frames` would otherwise skip, so lane
                    # lines keep refreshing even when vehicle detection is
                    # skipped. It is intentionally frame-counted, NOT wall-clock:
                    # that is CoreModule.detect_and_track's `detection_interval_seconds`
                    # (see core_module.py:543), which throttles the *expensive CV
                    # recompute* in real time. Do NOT collapse these two into one
                    # number -- they measure different things and the mismatch
                    # grows as frame rate varies (and `skip_factor` adapts).
                    # Old keys (dynamic_lane_detection_enabled / lane_detection_interval)
                    # are read as a fallback for one release with a deprecation
                    # warning, then removed.
                    lane_cfg = config.get("lane_detection", {})
                    lane_enabled = lane_cfg.get("enabled", lane_cfg.get("dynamic_lane_detection_enabled", False))
                    if "dynamic_lane_detection_enabled" in lane_cfg:
                        _warn_once(
                            "dynamic_lane_detection_enabled",
                            "lane_detection.dynamic_lane_detection_enabled is deprecated; "
                            "use lane_detection.enabled instead.",
                        )
                    lane_frame_interval = int(lane_cfg.get("frame_interval", lane_cfg.get("lane_detection_interval", 10)))
                    if "lane_detection_interval" in lane_cfg:
                        _warn_once(
                            "lane_detection_interval",
                            "lane_detection.lane_detection_interval is deprecated; "
                            "use lane_detection.frame_interval instead.",
                        )
                    is_lane_frame = lane_enabled and (frame_index % lane_frame_interval == 0)

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
                # Flagged True if the batched model call itself raises (CUDA
                # OOM, malformed batch, driver hiccup). In that case downstream
                # frames fall back to CoreModule's own detector instead of being
                # silently reported as an empty road (audit finding #5).
                batch_inference_failed = False
                if frames_to_infer and shared_model is not None:
                    try:
                        # Audit #3b: passing imgsz explicitly caps YOLO's
                        # internal letterbox at the input frame's longest
                        # edge instead of letting Ultralytics select the
                        # training shape (640 for yolov8n). On 320x240
                        # sample-traffic input this is ~2.5-3x faster per call
                        # with negligible mAP loss on vehicles. We clamp to
                        # the configured `yolo_imgsz` as a ceiling so we never
                        # upscale beyond the operator's accuracy budget.
                        first_h, first_w = frames_to_infer[0].shape[:2]
                        yolo_imgsz_cap = int(vehicle_det_cfg.get("yolo_imgsz", 640))
                        runtime_imgsz = min(max(64, yolo_imgsz_cap), max(first_h, first_w))
                        results = shared_model(frames_to_infer, conf=batch_conf_floor, imgsz=runtime_imgsz, verbose=False, stream=False)
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
                                # Apply the display-level confidence floor so we
                                # don't drag low-conf noise through ROI +
                                # serialization (audit #1): the batch YOLO
                                # call already used `conf=batch_conf_floor`,
                                # but on scenes where `low_confidence_threshold`
                                # was higher than the YOLO floor (rare now),
                                # honour the higher floor here too.
                                if float(conf) < display_conf_floor:
                                    continue
                                bbox = (rx1 + x_off, ry1 + y_off, rx2 + x_off, ry2 + y_off)
                                # ROI filtering (parity with DetectionEngine.detect)
                                if not detector.is_in_roi(np.array(bbox)):
                                    continue
                                formatted_dets.append((bbox, cls_id, conf))
                            # Audit #3: cap detections per frame by confidence
                            # so busiest scenes (314+ boxes on sample traffic)
                            # do not pin the worker on serialize / ws broadcast.
                            # Sort is in-place; slicing keeps top-N.
                            if max_detections_per_frame > 0 and len(formatted_dets) > max_detections_per_frame:
                                formatted_dets.sort(key=lambda d: d[2], reverse=True)
                                formatted_dets = formatted_dets[:max_detections_per_frame]
                            batch_detections_map[meta_idx] = formatted_dets
                    except Exception as e:
                        batch_inference_failed = True
                        logger.error(f"[Worker {worker_id}] Batch inference failed: {e}")

                # Tracking & output
                for i, meta in enumerate(batch_meta):
                    # Per-item fault isolation (audit finding #8): a single bad
                    # frame -- a malformed bbox hitting monitor.update_vehicles,
                    # a Re-ID call throwing -- must not abort the loop and drop
                    # the frames from *other feeds* that share this batch. We
                    # still always forward the raw frame bytes downstream so the
                    # stream never stalls (see finally-bypass below), but a
                    # detection/tracking exception only costs that one frame.
                    try:
                        core = meta["core"]
                        monitor = meta["monitor"]
                        metrics_obj = meta["metrics"]
                        frame = meta["frame"]
                        f_idx = meta["frame_index"]

                        if frame is None:
                            # Skip-frames (and any lane-only frame) are decoded
                            # as None. The raw JPEG (meta["frame_bytes"]) is
                            # still valid and is what gets forwarded, so the
                            # live stream keeps running at the full ingestion
                            # rate instead of 1/(skip_frames+1) (audit #2).
                            # We cannot run detection/tracking on a None frame,
                            # so we forward it as a passthrough and have
                            # _forward_frame re-serialize the persisted live
                            # tracks from core.tracker.vehicle_data -- this
                            # keeps the frontend KPI / bbox overlay stable
                            # between detect refreshes instead of blinking
                            # back to "0 vehicles" every skip cycle.
                            _forward_frame(
                                central_output_queue, meta, metrics_obj, worker_id
                            )
                            continue

                        # When the batched model call itself failed (CUDA OOM,
                        # malformed batch, driver hiccup) we must NOT report an
                        # empty road. Pass external_detections=None so
                        # CoreModule falls back to its own detector -- an outage
                        # is then visible as either a detection or an error,
                        # not as "0 vehicles, free flow" (audit finding #5).
                        if batch_inference_failed:
                            detections: Optional[List] = None
                        else:
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

                        metrics_obj.mark_frame()  # increments frames_processed AND records rolling fps

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
                    except Exception as e:
                        # One frame's detection/tracking/ReID blew up. Record it
                        # and keep going so sibling feeds in this batch are not
                        # silently dropped. Still attempt to forward the raw
                        # bytes so the stream does not stall on a single bad
                        # frame.
                        logger.error(
                            f"[Worker {worker_id}] Frame {meta.get('frame_index')} for "
                            f"{meta.get('feed_id')} failed processing: {e}",
                            exc_info=True,
                        )
                        m_obj = meta.get("metrics")
                        if m_obj:
                            m_obj.errors += 1
                        try:
                            _forward_frame(
                                central_output_queue, meta, m_obj, worker_id
                            )
                        except Exception:
                            pass

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