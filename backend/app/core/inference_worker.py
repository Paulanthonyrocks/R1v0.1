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
from .worker_utils import WorkerMetrics, serialize_tracked_vehicles, postprocess_detections

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


# --- GPU init serialization & CUDA-fatal handling ---------------------------
# 24 pinned workers across 2 T4s = 12 concurrent torch CUDA context creations
# per device at boot. Observed (backend_ml.log 2026-08-13): one cuda:1 context
# came up corrupted, and every subsequent model call on that device failed with
# `cudaErrorIllegalAddress` for the whole run -- 1,038 identical ERROR lines in
# 19s, zero results emitted for the feed. flock() on a per-device lockfile
# serializes model init across the worker processes (spawn'd, separate PIDs,
# shared host filesystem) so only one worker touches a GPU at boot.
#
# 2026-08-16 root-cause update: standalone probes that saw exactly ONE GPU per
# process (CUDA_VISIBLE_DEVICES=1) passed 100% on BOTH T4s -- same engine, same
# warmup call, fresh processes; only the pool's both-GPUs-visible workers fault
# deterministically. The pool manager now pins each worker to one physical GPU
# at spawn (CUDA_VISIBLE_DEVICES + R1_PHYSICAL_GPU_ID, see
# InferencePoolManager._resolve_gpu_pin); _apply_gpu_pin() below is the
# in-process safety net, and the flock serialization remains the second line
# of defense (locking on the PHYSICAL device id, see the lock_dev logic).
_CORE_REBUILD_BACKOFF = 30.0  # seconds between failed CoreModule rebuilds
_CUDA_FATAL_MARKERS = ("illegal memory access", "CUDA error", "CUDA out of memory")


def _apply_gpu_pin() -> None:
    """Give the worker a single-GPU view (see InferencePoolManager._resolve_gpu_pin).

    The pool manager sets R1_PHYSICAL_GPU_ID + CUDA_VISIBLE_DEVICES in the
    parent env before spawn; the child inherits both. This safety net covers
    any spawn path that set only the id. It MUST run before the first
    torch.cuda call in the process: torch fixes its device list at the first
    CUDA init, so a late CUDA_VISIBLE_DEVICES change is ignored.
    """
    phys = os.environ.get("R1_PHYSICAL_GPU_ID")
    if phys is not None and "CUDA_VISIBLE_DEVICES" not in os.environ:
        os.environ["CUDA_VISIBLE_DEVICES"] = phys


def _is_cuda_fatal(err: str) -> bool:
    """True when a CUDA error is permanent for this process' device context
    (illegal-address poisoning, OOM leaving the context unusable). Retrying in
    place cannot recover -- the worker must exit so the watchdog respawns a
    fresh process with a clean context."""
    return any(m in str(err) for m in _CUDA_FATAL_MARKERS)


class _PerDeviceInitLock:
    """Serializes CUDA model initialization per GPU device across the
    multiprocessing worker pool via flock() on /tmp/r1_gpu_init_<dev>.lock.
    Workers block until their device's turn; init then runs one process at a
    time per GPU, eliminating the concurrent-init race that poisoned a cuda:1
    context at boot."""

    def __init__(self, device: str):
        self._path = f"/tmp/r1_gpu_init_{device.replace(':', '_')}.lock"
        self._fh = None

    def acquire(self) -> "_PerDeviceInitLock":
        import fcntl

        self._fh = open(self._path, "w")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def release(self) -> None:
        import fcntl

        try:
            if self._fh is not None:
                fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            if self._fh is not None:
                self._fh.close()
                self._fh = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


def _exit_worker_fatal(worker_id: int, feed_id: str, err: str) -> None:
    """Logs a CUDA-fatal init failure once and exits the worker so the
    FeedWatchdog respawns it (exponential backoff, feed_watchdog.py:91-113).
    Previously the poisoned context was retried on EVERY frame -- the
    1,038-line `Failed to load model: CUDA error: an illegal memory access`
    storm -- with zero chance of recovery in place."""
    logger.error(
        f"[Worker {worker_id}] Feed {feed_id}: CUDA-fatal error during model "
        f"init: {err}. The device context is poisoned for this process; "
        f"exiting (code 42) so the watchdog respawns a fresh worker with a "
        f"clean CUDA context."
    )
    try:
        logging.shutdown()
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(42)


# Last known decoded-frame dimensions per feed, set by the detect path. Skip/
# passthrough frames re-serialize persisted tracks and need the same dims to
# normalize bbox coordinates consistently.
_FRAME_DIMS_BY_FEED: Dict[str, tuple] = {}

# Per-feed last-track-id set (churn telemetry). The aggregate vehicles_count is
# BLIND to churn: a churned track gets re-detected next frame, so the count stays
# flat while individual boxes flicker. Counting NEW track ids per detect frame
# is the direct measure of the vanish/jitter churn -- a high new-per-frame with
# a steady aggregate count is exactly the "moving car loses its track" symptom.
_TRACK_IDS_BY_FEED: Dict[str, set] = {}
# Normalized bbox center per track id, persisted per feed, so when a track
# vanishes we know WHERE its last detection was (the "detected only halfway to
# the exit" diagnostic): vanish at frame center == detection stops mid-frame;
# vanish at the edge == association/timing, not detectability.
_TRACK_CENTERS_BY_FEED: Dict[str, Dict[str, tuple]] = {}


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

        # Traffic telemetry on skip/passthrough frames. The detect path
        # merges ``monitor.get_metrics()`` into the payload; without the
        # same merge here, skip-frames (and detection-failure passthroughs)
        # carry ONLY operational WorkerMetrics (fps/drops/errors). Every
        # traffic key the frontend MetricsPanel reads (total_vehicles*,
        # average_speed_kmh, session_*, congestion_*) is then absent, the
        # panel falls back to 0, and the telemetry readout "resets" on
        # every skip frame even though the tracker state hasn't changed.
        # The monitor persists across frames, so this emits the
        # last-detected traffic state -- the same values the next detect
        # frame refreshes -- keeping the panel continuous. A metrics
        # failure must never stall the forward, so it degrades to
        # ops-only rather than skipping the put.
        monitor = meta.get("monitor")
        if monitor is not None:
            try:
                combined_metrics.update(monitor.get_metrics())
            except Exception:
                pass

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
                        _fw, _fh = _FRAME_DIMS_BY_FEED.get(meta.get("feed_id"), (None, None))
                        serialized_v = serialize_tracked_vehicles(
                            live_tracks,
                            vehicle_type_map=getattr(core, "vehicle_type_map", None),
                            norm_width=_fw, norm_height=_fh,
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
    # feed_id -> {"ts": float, "error": str}; failed CoreModule builds are
    # cached here so a broken init is retried on a backoff, not per frame.
    core_fail_state: Dict[str, Dict] = {}
    shared_model = None

    from app.services.reid_manager import GlobalReIDManager
    local_reid_manager = GlobalReIDManager(config)

    from app.utils.shared_frame_buffer import SharedFrameBuffer

    if frame_buffer is None:
        frame_buffer = SharedFrameBuffer(
            pool_size=config.get("performance", {}).get("shm_pool_size", 100),
            # Size segments to the FRAME (~1.5MB for 640x480 RGB) instead of the
            # 10MB default. With 10MB, shm_pool_size 6000 plans 60GB (> /dev/shm),
            # so only ~1400 segments materialize and the shed valve computes
            # free_fraction against an unreachable 6000 denominator -> perpetually
            # under the resume floor -> "skip decode" stalls (0.1 fps).
            max_frame_size=int(config.get("performance", {}).get("shm_frame_size", 1500000)),
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
            # Models live under <root>/backend/models/ (export_tensorrt.py writes
            # the TensorRT engine there). project_root_dir defaults to the repo
            # root (config.py:86), so a bare "models/..." path would resolve to
            # <root>/models/... and miss the real <root>/backend/models/ dir.
            # YOLO silently auto-downloads the .pt when the path is wrong, which
            # hides the bug for the weights but exposes it for the .engine (which
            # does NOT auto-download) -> the "engine NOT found" warning. Resolve
            # backend-aware: prefer <root>/backend/<model_path> when the plain
            # join doesn't exist. Mirrors main.py:215's path convention.
            full_model_path = str(Path(root_dir) / model_path)
            backend_variant = str(Path(root_dir) / "backend" / model_path)
            if not Path(full_model_path).exists() and Path(backend_variant).exists():
                full_model_path = backend_variant
            use_gpu = config.get("performance", {}).get("gpu_acceleration", False)

            # Apply the pool manager's GPU pin (single-GPU view) BEFORE any
            # torch.cuda call -- torch fixes its device list at first CUDA
            # init, so this must precede ultralytics import and is_available().
            _apply_gpu_pin()

            try:
                from ultralytics import YOLO
            except ImportError as e:
                logger.critical(f"[Worker {worker_id}] Failed to import 'ultralytics' package: {e}. Ensure it is installed in the environment.")
                return

            import torch

            # Multi-GPU support: distribute workers across available GPUs
            use_gpu = config.get("performance", {}).get("gpu_acceleration", False)
            gpu_id = 0  # default; reassigned in the GPU branch below
            if use_gpu and torch.cuda.is_available():
                num_gpus = torch.cuda.device_count()
                # Assign worker to GPU in round-robin fashion
                gpu_id = worker_id % num_gpus
                device = f"cuda:{gpu_id}"
                pinned = os.environ.get("R1_PHYSICAL_GPU_ID")
                if pinned is not None:
                    # Pool-manager pin: this process sees exactly ONE GPU
                    # (CUDA_VISIBLE_DEVICES set at spawn), so device is
                    # cuda:0 and the PHYSICAL gpu id is what logs and the
                    # init lock must use.
                    logger.info(
                        f"[Worker {worker_id}] Multi-GPU: pinned to physical GPU {pinned} "
                        f"(CUDA_VISIBLE_DEVICES={os.environ.get('CUDA_VISIBLE_DEVICES')}, "
                        f"logical device {device})"
                    )
                else:
                    logger.info(f"[Worker {worker_id}] Multi-GPU: using GPU {gpu_id}/{num_gpus} ({torch.cuda.get_device_name(gpu_id)})")
            else:
                device = "cpu"
            # Serialize per-device model init across the worker pool: 12
            # workers boot on each T4, and 12 concurrent CUDA context
            # creations corrupted one cuda:1 context at boot (illegal memory
            # access, permanent for the process). Hold the per-device flock
            # for the entire YOLO + ReID load so init runs one-at-a-time per
            # GPU.
            _init_lock = None
            if use_gpu and torch.cuda.is_available():
                # Lock on the PHYSICAL device: pinned workers all see "cuda:0"
                # logically, but init must serialize per physical GPU so lock
                # names stay stable between pinned and unpinned workers.
                lock_dev = f"cuda:{os.environ.get('R1_PHYSICAL_GPU_ID', gpu_id)}"
                _init_lock = _PerDeviceInitLock(lock_dev)
                _init_lock.acquire()
                logger.info(f"[Worker {worker_id}] Acquired GPU init lock for {lock_dev}")

            try:
                engine_path = Path(full_model_path).with_suffix(".engine")

                if engine_path.exists():
                    logger.info(f"[Worker {worker_id}] Found TensorRT engine: {engine_path}")
                    try:
                        # Load the TRT engine with an EXPLICIT task. A bare
                        # YOLO(str(engine_path)) makes ultralytics guess the task off
                        # the file, it cannot read task metadata from an engine, so it
                        # warns ("Unable to automatically guess model task") and then
                        # falls into the *.pt-only code path -> "should be a *.pt
                        # PyTorch model" -> the whole worker dies (CRITICAL -> Exiting
                        # -> watchdog respawn loop, never recovering). Passing
                        # task="detect" skips the guess and uses the engine correctly
                        # (see ultralytics GH issue #7644, maintainer-confirmed).
                        shared_model = YOLO(str(engine_path), task="detect")
                        # TensorRT engines are shape-locked to the batch they were
                        # exported with (export_tensorrt.py builds batch=1). Feeding
                        # a multi-frame list to a static-batch engine errors or
                        # silently drops all but the first frame, so force per-frame
                        # inference.
                        is_trt_engine = True
                        logger.info(f"[Worker {worker_id}] TensorRT engine loaded successfully.")
                    except Exception as e:
                        logger.warning(
                            f"[Worker {worker_id}] TensorRT engine load FAILED: {e}. "
                            f"Falling back to float32 PyTorch ({device}) -- expect "
                            f"~0.7-3 fps/worker vs 5-10x with a working TRT engine. "
                            f"Rebuild it on the GPU box: python scripts/export_tensorrt.py "
                            f"(FP16, imgsz from vehicle_detection.yolo_imgsz, batch=1). "
                            f"If the engine was exported "
                            f"with a different ultralytics/TensorRT version than the "
                            f"runtime, re-export it on the target box."
                        )
                        shared_model = YOLO(full_model_path)
                        is_trt_engine = False
                else:
                    # TENSORRT ENGINE ABSENT -> we fall back to float32 PyTorch on
                    # the T4. This is the single biggest throughput lever in the
                    # system: a static-batch TensorRT engine (built by
                    # scripts/export_tensorrt.py on the GPU box, FP16 / imgsz from
                    # config / 
                    # batch=1) typically yields 5-10x the inference fps of raw
                    # PyTorch. Without it, inference runs at ~0.7-3 fps/worker
                    # while ingestion delivers 8 fps/feed, producing low/choppy
                    # video. This is expected on a fresh deploy (the .engine must
                    # be built ON the target GPU, which needs nvidia-tensorrt +
                    # CUDA), but we warn loudly so the gap is never silent.
                    #
                    # NOTE on the path: engine_path is derived as
                    # Path(full_model_path).with_suffix(".engine"), where
                    # full_model_path is now resolved backend-aware (see the
                    # root_dir handling above). So when model_path = "models/...",
                    # engine_path resolves to "<root>/backend/models/<name>.engine"
                    # -- exactly where scripts/export_tensorrt.py writes it. This
                    # warning now means one of: (a) the .engine truly hasn't been
                    # built yet on this GPU box, or (b) project_root_dir is wrong.
                    # It is no longer a "models/ prefix" trap.
                    logger.warning(
                        f"[Worker {worker_id}] TensorRT engine NOT found at {engine_path}. "
                        f"Falling back to float32 PyTorch ({device}) -- expect ~0.7-3 fps/worker "
                        f"vs 5-10x with a TRT engine. Build it on the GPU box: "
                        f"python scripts/export_tensorrt.py (FP16, imgsz from "
                        f"vehicle_detection.yolo_imgsz, batch=1). "
                        f"Requires model_path to keep its 'models/' prefix so the "
                        f".engine path resolves."
                    )
                    shared_model = YOLO(full_model_path)
                    is_trt_engine = False

                if not is_trt_engine:
                    shared_model.to(device)
                logger.info(f"[Worker {worker_id}] Shared model loaded on {device}.")

                if vehicle_det_cfg.get("reid_enabled", True):
                    from app.ml.reid_model import ReIDEmbedder

                    logger.info(f"[Worker {worker_id}] Pre-loading ReID Embedder on {device}...")
                    shared_reid_embedder = ReIDEmbedder(config, device=device)
                    logger.info(f"[Worker {worker_id}] ReID Embedder pre-loaded.")
            finally:
                if _init_lock is not None:
                    _init_lock.release()
                    logger.info(f"[Worker {worker_id}] Released GPU init lock for {device}")

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
                except Exception as poll_err:
                    # Audit M3 (2026-08-23): RedisStreamQueue.get_nowait can raise
                    # redis.ConnectionError/TimeoutError from xreadgroup. Those used
                    # to escape to the outermost handler and KILL the worker ->
                    # watchdog respawn -> full YOLO+ReID reload churn per transient
                    # Redis blip. A transient poll failure just means this tick
                    # finds nothing; back off briefly and retry next loop.
                    logger.warning(
                        f"[Worker {worker_id}] Transient slot-{slot_id} poll error "
                        f"({type(poll_err).__name__}: {poll_err}); skipping this tick."
                    )
                    time.sleep(0.05)
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
                    except Exception as poll_err:
                        # Audit M3: transient Redis error during batch fill — same
                        # treatment as the initial poll above. Log, back off, keep
                        # the worker alive with whatever the batch already holds.
                        logger.warning(
                            f"[Worker {worker_id}] Transient slot poll error during "
                            f"batch fill ({type(poll_err).__name__}); using partial batch."
                        )
                        break
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
                        # Sentinel collision guard: ingestion reuses -999 for
                        # BOTH feed_ended and snapshot_saved (the snapshot
                        # path puts (feed_id, -999, b"", {"type":
                        # "snapshot_saved"})). Tearing down the CoreModule on
                        # a snapshot killed ReID/OCR/lane state + metrics_map
                        # and forced a full warm-up rebuild (~90s churn +
                        # worker uptime reset per snapshot). Only a genuine
                        # feed_ended tears down; snapshot_saved is just acked.
                        _ctrl_type = (
                            extra_payload.get("type")
                            if isinstance(extra_payload, dict)
                            else None
                        )
                        if _ctrl_type == "snapshot_saved":
                            if msg_id and hasattr(slot_q_ref, "ack"):
                                slot_q_ref.ack(msg_id)
                                acked_msgs.add(msg_id)
                            continue
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
                        # Rebuild guard: a failed CoreModule build (e.g. the
                        # detector warm-up hitting a poisoned CUDA context)
                        # used to be re-attempted on EVERY frame -- observed
                        # 1,038 identical "Failed to load model: CUDA error:
                        # an illegal memory access" lines in backend_ml.log
                        # over 19s with zero results for the feed. Cache the
                        # failure and retry on a backoff; a CUDA-fatal error
                        # is permanent for this process, so exit and let the
                        # watchdog respawn a fresh worker instead.
                        _now = time.time()
                        _last_fail = core_fail_state.get(feed_id)
                        if _last_fail is not None and (_now - _last_fail.get("ts", 0.0)) < _CORE_REBUILD_BACKOFF:
                            if msg_id and hasattr(slot_q_ref, "ack"):
                                slot_q_ref.ack(msg_id)
                                acked_msgs.add(msg_id)
                            continue
                        try:
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
                                device=device,
                            )
                            core_modules[feed_id]._first_detection_done = False
                            core_fail_state.pop(feed_id, None)
                            traffic_monitors[feed_id] = TrafficMonitor(config)
                            if feed_id in pending_configs:
                                core_modules[feed_id].update_config(pending_configs.pop(feed_id))
                        except Exception as e:
                            _err = str(e)
                            core_fail_state[feed_id] = {"ts": _now, "error": _err}
                            if _is_cuda_fatal(_err):
                                _exit_worker_fatal(worker_id, feed_id, _err)
                            logger.error(
                                f"[Worker {worker_id}] Failed to initialize "
                                f"CoreModule for {feed_id}: {_err}. Retrying in "
                                f"{int(_CORE_REBUILD_BACKOFF)}s."
                            )
                            if msg_id and hasattr(slot_q_ref, "ack"):
                                slot_q_ref.ack(msg_id)
                                acked_msgs.add(msg_id)
                            continue

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
                        # TensorRT engines are shape-locked to their export
                        # imgsz, which is the SAME config value --
                        # export_tensorrt.py reads vehicle_detection.yolo_imgsz.
                        # For an engine we MUST pass exactly that size (the
                        # letterbox feeds the engine's baked input shape); the
                        # frame-edge clamp below is only for the dynamic
                        # PyTorch path. A size mismatch ("input size != max
                        # model size") errors EVERY detect frame. CONTRACT:
                        # re-export the engine after changing yolo_imgsz.
                        if is_trt_engine:
                            runtime_imgsz = yolo_imgsz_cap
                        else:
                            runtime_imgsz = min(max(64, yolo_imgsz_cap), max(first_h, first_w))
                        results = shared_model(frames_to_infer, conf=batch_conf_floor, imgsz=runtime_imgsz, verbose=False, stream=False)
                        for i, result in enumerate(results):
                            meta_idx = inference_indices[i]
                            meta = batch_meta[meta_idx]
                            boxes_data = result.boxes.data.cpu().numpy()
                            x_off, y_off = meta.get("crop_offsets", (0, 0))
                            detector = meta["core"].detector
                            formatted_dets = []
                            # Detection telemetry (debug): split WHERE detections
                            # are lost so a free-lane vanish is attributable to
                            # (a) the model found few, (b) class/conf floor cut
                            # them, (c) ROI dropped them, or (d) the cap clipped
                            # them -- instead of guessing between association vs
                            # detection. Logged at DEBUG to stay out of steady-state.
                            _n_raw = 0
                            _n_vehicle_conf = 0
                            for row in boxes_data:
                                _n_raw += 1
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
                                _n_vehicle_conf += 1
                                bbox = (rx1 + x_off, ry1 + y_off, rx2 + x_off, ry2 + y_off)
                                # ROI filtering (parity with DetectionEngine.detect)
                                if not detector.is_in_roi(np.array(bbox)):
                                    continue
                                formatted_dets.append((bbox, cls_id, conf))
                            # Audit #3: cap detections per frame by confidence
                            # so busiest scenes (314+ boxes on sample traffic)
                            # do not pin the worker on serialize / ws broadcast.
                            # Sort is in-place; slicing keeps top-N.
                            _n_after_roi = len(formatted_dets)
                            if max_detections_per_frame > 0 and len(formatted_dets) > max_detections_per_frame:
                                formatted_dets.sort(key=lambda d: d[2], reverse=True)
                                formatted_dets = formatted_dets[:max_detections_per_frame]
                            _n_capped = len(formatted_dets)  # true post-cap count, pre-postprocess
                            # Post-NMS cleanup: drop implausible geometry (thin/
                            # elongated lane-marker false positives) and merge
                            # same-class SPLIT detections (a truck seen as cab +
                            # trailer) so one vehicle is not counted twice.
                            # Conservative by default: gap-merge OFF (merge_gap_px=0)
                            # and a strong IoU (0.5) so DISTINCT cars driving close
                            # together are NOT grouped into one box. Tune via the
                            # `detection_postprocess` config block.
                            _pp = config.get("detection_postprocess", {}) or {}
                            if _pp.get("enabled", True):
                                formatted_dets = postprocess_detections(
                                    formatted_dets,
                                    max_aspect=float(_pp.get("max_aspect", 6.0)),
                                    min_dim=float(_pp.get("min_dim", 6.0)),
                                    merge_iou=float(_pp.get("merge_iou", 0.50)),
                                    merge_gap_px=float(_pp.get("merge_gap_px", 0.0)),
                                    merge_gap_classes=set(
                                        _pp.get("merge_gap_classes", []) or []
                                    ),
                                )
                            if meta["frame_index"] % 25 == 0:
                                # Detection reachability probe: the furthest-right
                                # detection center (post-ROI). If a feed's detections
                                # never pass ~0.66 here, the MODEL isn't firing at the
                                # periphery (detection-reachability problem); if they
                                # reach ~0.9+, the periphery IS being detected and the
                                # vanish is elsewhere (tracking/association).
                                _xmax = 0.0
                                if formatted_dets and frame is not None:
                                    _fh2, _fw2 = frame.shape[:2]
                                    for _dd in formatted_dets:
                                        _db = _dd[0]
                                        if len(_db) == 4 and _fw2:
                                            _xmax = max(
                                                _xmax,
                                                ((_db[0] + _db[2]) / 2.0) / _fw2,
                                            )
                                logger.info(
                                    f"[Worker {worker_id}][{meta['feed_id']}] det frame={meta['frame_index']} "
                                    f"raw={_n_raw} vehicle_conf={_n_vehicle_conf} after_roi={_n_after_roi} "
                                    f"xmax={_xmax:.2f} "
                                    f"capped={_n_capped} postproc={len(formatted_dets)} cap={max_detections_per_frame}"
                                )
                            batch_detections_map[meta_idx] = formatted_dets
                    except Exception as e:
                        batch_inference_failed = True
                        logger.error(f"[Worker {worker_id}] Batch inference failed: {e}")
                        # A CUDA-fatal error (illegal address / OOM) means the
                        # device context is poisoned -- the per-frame fallback
                        # below would fail identically on every frame. Exit so
                        # the watchdog respawns a fresh worker.
                        if _is_cuda_fatal(str(e)):
                            _exit_worker_fatal(worker_id, "*batch*", str(e))

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

                        # Churn telemetry: NEW track ids per detect frame (per feed).
                        # High new-per-frame while the aggregate vehicles_count stays
                        # flat == tracks vanishing + re-creating (the free-lane vanish).
                        # This is the direct, count-independent measure of the churn.
                        # Also log WHERE new tracks FIRST appear (normalized bbox
                        # center) so the "detected only halfway into the lane" report
                        # is attributable: new tracks at the frame EDGE (x/y near 0/1)
                        # = detection reaches the entry; new tracks ONLY near the
                        # center = the ROI-crop / edge-detectability is cutting them.
                        _feed_key = meta["feed_id"]
                        _prev_ids = _TRACK_IDS_BY_FEED.get(_feed_key, set())
                        _cur_ids = set(vis_tracks.keys()) if vis_tracks else set()
                        _new_ids = _cur_ids - _prev_ids
                        _vanish_ids = _prev_ids - _cur_ids
                        _n_new = len(_new_ids)
                        # Current-frame normalized centers, persisted per feed so we
                        # can report the LAST position of tracks that vanish next
                        # frame (the "detected only halfway to the exit" probe).
                        _center_map: Dict[str, tuple] = {}
                        _frm = frame
                        if vis_tracks and _frm is not None:
                            _fh, _fw = _frm.shape[:2]
                            for _tid, _tr in vis_tracks.items():
                                _tb = _tr.get("bbox")
                                if _tb and len(_tb) == 4 and _fw and _fh:
                                    _center_map[_tid] = (
                                        round(((_tb[0] + _tb[2]) / 2) / _fw, 2),
                                        round(((_tb[1] + _tb[3]) / 2) / _fh, 2),
                                    )
                        _vx, _vy = [], []
                        _prev_centers = _TRACK_CENTERS_BY_FEED.get(_feed_key, {})
                        for _vtid in _vanish_ids:
                            _c = _prev_centers.get(_vtid)
                            if _c:
                                _vx.append(_c[0])
                                _vy.append(_c[1])
                        _TRACK_IDS_BY_FEED[_feed_key] = _cur_ids
                        _TRACK_CENTERS_BY_FEED[_feed_key] = _center_map
                        if f_idx % 25 == 0:
                            _cx, _cy = [], []
                            for _ntid in _new_ids:
                                _c = _center_map.get(_ntid)
                                if _c:
                                    _cx.append(_c[0])
                                    _cy.append(_c[1])
                            logger.info(
                                f"[Worker {worker_id}][{_feed_key}] churn frame={f_idx} "
                                f"live={len(_cur_ids)} new={_n_new} new_cx={_cx[:8]} new_cy={_cy[:8]} "
                                f"vanish={len(_vanish_ids)} vx={_vx[:8]} vy={_vy[:8]}"
                            )

                        if vis_tracks and meta["first_detect"]:
                            core._first_detection_done = True

                        # Re-ID matching (guarded by config)
                        if vehicle_det_cfg.get("reid_enabled", True):
                            # Per-frame MATCH budget. With appearance tracking on,
                            # many new tracks carry embeddings every frame, and
                            # each match_or_register for a genuinely new track
                            # pays ~5 synchronous Redis calls (hget + incr + set
                            # + hset + rpush/publish), plus a throttled full
                            # gallery re-pull (~1s) every 30s. Unbounded, that
                            # storm pinned workers to ~1 fps/feed on dense scenes
                            # (130-180 vehicles). Cap attempts per frame; the
                            # remaining unassigned tracks are matched on
                            # subsequent frames as earlier ones get ids.
                            match_budget = int(vehicle_det_cfg.get("reid_match_budget_per_frame", 6))
                            matched_this_frame = 0
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
                                    if matched_this_frame >= match_budget:
                                        # Budget exhausted: defer to next frame.
                                        # Tracks that already got ids this frame
                                        # will skip next frame, so the budget
                                        # keeps cycling through the backlog.
                                        continue
                                    matched_this_frame += 1
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

                        # NORMALIZED wire contract: the frontend multiplies bbox
                        # values by canvas size, so ship 0..1 coords (audit
                        # 2026-08-24 — pixel-space boxes rendered off-canvas).
                        _fh, _fw = frame.shape[:2]
                        _FRAME_DIMS_BY_FEED[meta["feed_id"]] = (_fw, _fh)
                        serialized_v = serialize_tracked_vehicles(
                            vis_tracks, vehicle_type_map=core.vehicle_type_map,
                            norm_width=_fw, norm_height=_fh,
                        )

                        extra = {}
                        try:
                            # Lane geometry (normalized 0-1): forwarded so the
                            # frontend can render lane-flow overlays client-side
                            # instead of the dead toggle it used to be. Present
                            # only when lane detection produced lines; the
                            # result processor ships it as the "ln" payload key.
                            if lane_lines or lane_bounds:
                                h, w = frame.shape[:2]
                                extra["ln"] = {
                                    "lines": [
                                        [float(x1) / w, float(y1) / h, float(x2) / w, float(y2) / h]
                                        for (x1, y1, x2, y2) in (lane_lines or [])
                                    ],
                                    "bounds": [float(b) / w for b in (lane_bounds or [])],
                                }
                        except Exception:
                            # Lane metadata is best-effort; never fail the frame.
                            pass

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