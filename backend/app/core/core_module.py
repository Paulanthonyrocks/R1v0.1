import copy
import cv2
import logging
import time
import math
import numpy as np
import torch
import queue
import threading
from collections import deque
from typing import Dict, List, Tuple, Optional, Any, TypedDict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor


# Modular components
from .detection import DetectionEngine
from .tracking import TrackingManager
from .transforms import CoordinateTransformer
from ..utils.polygons import pixel_polygon

# Utility imports
try:
    from ..utils.image_processing import LicensePlatePreprocessor
    from ..utils.lane_detection import process_frame_for_lanes, get_lane_boundaries_from_lines
    from ..utils.local_ocr import LocalOCR
    from ..ml.reid_model import ReIDEmbedder
except ImportError:
    logging.getLogger("app.ml").error(
        "Error importing utils for CoreModule. System functionality may be limited."
    )
    LicensePlatePreprocessor = None
    process_frame_for_lanes = None
    get_lane_boundaries_from_lines = None
    ReIDEmbedder = None

logger = logging.getLogger("app.ml")

class OCRQueueFull(Exception):
    """Raised when the bounded OCR executor's pending queue is at capacity."""


class _BoundedThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor with a hard cap on the *pending* (queued, not yet
    running) task count.

    The stock executor has an unbounded work queue: under a burst of OCR
    submissions the queue grows without limit, the 2 worker threads can never
    keep up, and memory/latency climb until OCR is minutes behind (and the
    queued jobs hold references to frame buffers). Capping the pending queue
    turns overload into a clean drop: the caller's dedup logic re-submits the
    track on a later frame, so a skipped plate is simply re-attempted rather
    than stacked.
    """

    def __init__(self, max_workers: int = 2, max_queue: int = 64, **kwargs):
        super().__init__(max_workers=max_workers, **kwargs)
        self._max_queue = max(1, int(max_queue))

    def submit(self, fn, *args, **kwargs):
        # _work_queue is the base class's deque-backed queue.Queue; qsize() is
        # available on queue.Queue. We reject rather than block so the producer
        # (detect_and_track) keeps its real-time cadence.
        if self._work_queue.qsize() >= self._max_queue:
            raise OCRQueueFull("ocr pending queue at capacity")
        return super().submit(fn, *args, **kwargs)


class TrackData(TypedDict, total=False):
    """Type definition for vehicle track metadata."""
    bbox: List[float]
    centroid: List[float]
    status: str
    prev_status: str
    speed: float
    last_reid_speed: float
    embedding: Optional[np.ndarray]
    last_reid_update: int
    last_reid_attempt: int
    ground_coordinates: Optional[Tuple[float, float]]
    prev_ground_pos: Optional[Tuple[float, float]]
    prev_t: float
    confidence: float
    class_id: int
    license_plate: str
    lane: int
    last_seen: float
    global_vehicle_id: str


class CoreModule:
    def __init__(
        self,
        feed_id: str,
        model_path: str,
        config: Dict,
        fps: int,
        db_queue: Any,
        gemini_api_key: Optional[str] = None,
        model_type: str = "yolo",
        preloaded_model: Optional[Any] = None,
        preloaded_reid: Optional[Any] = None,
        device: Optional[str] = None,
    ):
        """
        Core processing module for a single video feed.
        Handles detection, tracking, ReID, and metadata extraction.

        Args:
            feed_id: Unique identifier for the video stream.
            model_path: Path to the detection model weights.
            config: System configuration dictionary.
            fps: Frames per second of the input stream.
            db_queue: Queue for sending vehicle data to the database.
            gemini_api_key: API key for Gemini OCR (optional).
            model_type: Type of detection model (e.g., 'yolo').
            preloaded_model: Pre-loaded model instance to avoid reloading.
            preloaded_reid: Pre-loaded ReID model instance.
            device: Explicit device to use (e.g., 'cuda:0', 'cuda:1', 'cpu'). 
                    Required when preloaded_model is provided to ensure device alignment.
        """
        self.feed_id = feed_id
        self.config = copy.deepcopy(config)
        self.fps = fps
        self.db_queue = db_queue
        self.gemini_api_key = gemini_api_key
        self.model_type = model_type

        # 1. Configuration sections
        v_cfg = self.config.get("vehicle_detection", {})
        b_cfg = self.config.get("behavior_analysis", {})
        l_cfg = self.config.get("lane_detection", {})

        # Configurable vehicle type mapping
        self.vehicle_type_map = v_cfg.get("vehicle_type_map", {
            0: "person", 1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck",
        })

        self.project_root = Path(self.config.get("project_root_dir", ""))
        self.model_path = (
            Path(model_path)
            if Path(model_path).is_absolute()
            else self.project_root / model_path
        )

        # 2. Thresholds & Params
        self.confidence_threshold = v_cfg.get("confidence_threshold", 0.4)
        self.proximity_threshold = v_cfg.get("proximity_threshold", 60)
        self.predict_timeout = v_cfg.get("predict_timeout", 0.4)
        self.max_active_tracks = v_cfg.get("max_active_tracks", 50)

        # 3. Modular engines
        # If device is explicitly provided (e.g., by worker for multi-GPU alignment),
        # use it. Otherwise fall back to auto-detection.
        if device is not None:
            self.device = device
            logger.info(f"[{self.feed_id}] Using explicit device: {device}")
        else:
            self.device = self._check_gpu_availability()
        self.detector = DetectionEngine(
            str(self.model_path), self.config, self.device, preloaded_model=preloaded_model
        )
        self.detector.load_model()

        res = v_cfg.get("frame_resolution", [640, 480])
        self.roi_polygon_points = self.config.get("roi_processing", {}).get("polygon_points", None)
        # Pass exclusion zones through so DetectionEngine actually filters
        # them (audit finding #7) -- previously only the polygon reached the
        # detector and exclusion_zones were dead config.
        self.detector.initialize_roi(
            res,
            self.roi_polygon_points,
            self.config.get("roi_processing", {}).get("exclusion_zones", []),
        )
        self._initialize_roi_mask(res)

        self.tracker = TrackingManager(self.config, self.fps, feed_id=self.feed_id)

        calib_cfg = v_cfg.get("calibration", {})
        self.transformer = CoordinateTransformer(calib_cfg)
        # If the transformer has no usable homography, ground-plane speed is
        # impossible. Don't leave this silent -- the previous behavior reported
        # speed=0.0 for every vehicle, which pinned congestion ~75/100 and made
        # the KPI claim a gridlock that wasn't real. Surface it at startup.
        if not self.transformer.is_calibrated:
            logger.warning(
                f"[{self.feed_id}] Camera UNCALIBRATED: no perspective homography "
                f"available. Speed will be reported as UNCALIBRATED (null), not 0 "
                f"km/h. Provide calibration image_points/world_points or a valid "
                f"matrix_path in feed config to enable km/h speed."
            )

        # 4. State & Helpers
        self.reid_embedder = preloaded_reid or (
            ReIDEmbedder(self.config) if v_cfg.get("reid_enabled", True) else None
        )
        self.ocr_executor = None
        self.ocr_results_queue: queue.Queue = queue.Queue(maxsize=100)

        # Persistent state for tracking across frames
        self._first_detection_done = False
        self._last_db_save_times: Dict[str, float] = {}
        self._last_queue_warn_time = 0.0

        self.last_detected_lane_lines = None
        self.cached_lane_boundaries: list = []
        self.last_lane_detection_time = 0.0
        # `detection_interval_seconds` is the WALL-CLOCK throttle on the
        # expensive lane-CV recompute (real-time cadence). It is deliberately
        # NOT frame-counted -- that is inference_worker's `frame_interval`
        # (see inference_worker.py lane admission gate), which only forces a
        # decode so lane detection *can* run. Keep the two distinct (audit #6).
        # Old key `detection_interval` is read as a fallback for one release.
        _raw_interval = l_cfg.get("detection_interval_seconds", l_cfg.get("detection_interval", 1.0))
        self.lane_detection_interval = float(_raw_interval)
        if "detection_interval" in l_cfg:
            logger.warning(
                "lane_detection.detection_interval is deprecated; "
                "use lane_detection.detection_interval_seconds instead."
            )

        # 5. Behavior & Speed
        # NOTE: these keys live under `behavior_analysis:` in config.yaml.
        # A prior refactor read the WRONG key names (speed_limit_kmh /
        # acceleration_threshold_mps2 / speed_smoothing_factor), so every one
        # fell through to its default and silently ignored the operator-tuned
        # values. The only behavioural knobs read correctly are
        # stopped_speed_threshold_kmh (same name) and speed_limit (a
        # backwards-compatible alias the .get() accepts).
        # calibration_monitor.py has its OWN homography_matrix (local var) and
        # is unaffected by the key names here.
        self.pixels_per_meter = config.get("pixels_per_meter", 30)  # top-level key, default 30 (config.yaml)
        self.ewma_alpha = b_cfg.get("ewma_alpha", 0.3)              # was 'speed_smoothing_factor' (nonexistent)
        self.speed_limit = b_cfg.get("speed_limit", 60)             # was 'speed_limit_kmh' (nonexistent)
        self.accel_threshold_mps2 = b_cfg.get("accel_threshold_mps2", 2.0)  # was 'acceleration_threshold_mps2'
        self.stopped_speed_threshold_kmh = b_cfg.get("stopped_speed_threshold_kmh", 5.0)
        # False-hard-braking guards (traffic_monitor consumes acceleration for the
        # "Sudden deceleration" anomaly). A raw per-frame speed delta flags a
        # "hard brake" from (a) slow-crawl decel, (b) frame-closeness noise, and
        # (c) track-churn spikes. These tune how far we trust that signal.
        self.min_speed_for_accel_kmh = float(b_cfg.get("min_speed_for_accel_kmh", 15.0))
        self.min_accel_dt_seconds = float(b_cfg.get("min_accel_dt_seconds", 0.15))
        self.min_physical_accel_mps2 = float(b_cfg.get("min_physical_accel_mps2", -15.0))

        # Session Metrics
        # 300 frames is NOT 5 minutes: the deque holds the most recent 300
        # *processed* frames. At a typical inference rate of 15+ FPS that is
        # ~20s of history; at 30 FPS it is ~10s. (Prior comment assumed 1 FPS.)
        self.speed_history: deque = deque(maxlen=300)
        self.congestion_history: deque = deque(maxlen=300)
        self._homography_fallback_warned = False

        self.preprocessor = None
        self.local_ocr = None
        self.last_activity = 0.0
        self._reid_updates_this_frame = 0  # Per-frame budget control
        self._lock = threading.RLock()

        # OCR submission de-duplication. Without this, every active track with
        # confidence > 0.7 re-submits an OCR job on *every frame* (line ~682),
        # so N vehicles at F FPS produce up to N*F concurrent submissions for
        # the same plate. Combined with the previously-unbounded executor that
        # let the pending queue grow without limit. Tracking in-flight track
        # ids bounds work to at most one pending OCR per vehicle; a plate is
        # re-submitted only after the prior one finishes (so it still refreshes).
        self._ocr_in_flight: set = set()
        self._ocr_lock = threading.Lock()
        self._ocr_max_pending = self.config.get("ocr_engine", {}).get("max_pending_jobs", 64)

        if self.config.get("ocr_engine", {}).get("enabled", False):
            self._init_ocr()

    def _check_gpu_availability(self) -> str:
        """
        Checks for GPU availability for YOLO and engines, respecting the config.

        Returns:
            A string representing the device to use ('cuda:0' or 'cpu').
        """
        use_gpu = self.config.get("performance", {}).get("gpu_acceleration", True)
        if use_gpu and torch.cuda.is_available():
            logger.info(f"[{self.feed_id}] GPU detected and enabled. Using CUDA.")
            return "cuda:0"
        
        if use_gpu:
            logger.info(f"[{self.feed_id}] GPU acceleration enabled but CUDA not available. Falling back to CPU.")
        else:
            logger.info(f"[{self.feed_id}] GPU acceleration disabled in config. Using CPU.")
            
        return "cpu"

    def _initialize_roi_mask(self, resolution: List[int]):
        """
        Initializes ROI and exclusion masks once per resolution change.

        Args:
            resolution: The frame resolution as [width, height].
        """
        w, h = resolution
        self.roi_mask = np.ones((h, w), dtype=np.uint8) * 255

        if self.roi_polygon_points:
            # Wire polygons arrive as [{x,y},...] (normalized) or [[x,y],...];
            # legacy configs may hold pixel pairs. pixel_polygon normalizes all
            # of them to pixel ints for cv2.fillPoly. Previously this cast the
            # dict array straight to int32 -> TypeError, killing the feed.
            pts = pixel_polygon(self.roi_polygon_points, w, h)
            if pts is not None:
                mask = np.zeros((h, w), dtype=np.uint8)
                cv2.fillPoly(mask, [pts], 255)
                self.roi_mask = cv2.bitwise_and(self.roi_mask, mask)

        exclusion = self.config.get("roi_processing", {}).get("exclusion_zones", [])
        for zone in exclusion:
            zone_np = pixel_polygon(zone, w, h)
            if zone_np is not None:
                cv2.fillPoly(self.roi_mask, [zone_np], 0)

    def _preprocess_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, bool, int, int]:
        """
        Pre-processes the frame for inference. If an ROI is defined, it crops the frame 
        to the ROI's bounding box to reduce inference load.

        Returns:
            Tuple of (processed_frame, roi_enabled, x_offset, y_offset).
        """
        if self.roi_polygon_points:
            h, w = frame.shape[:2]
            pts = pixel_polygon(self.roi_polygon_points, w, h)
            if pts is not None:
                x_min = int(np.min(pts[:, 0]))
                y_min = int(np.min(pts[:, 1]))
                x_max = int(np.max(pts[:, 0]))
                y_max = int(np.max(pts[:, 1]))

                # Clamp to frame dimensions
                x_min, y_min = max(0, x_min), max(0, y_min)
                x_max, y_max = min(w, x_max), min(h, y_max)

                if x_max > x_min and y_max > y_min:
                    cropped_frame = frame[y_min:y_max, x_min:x_max]
                    return cropped_frame, True, x_min, y_min

        return frame, False, 0, 0

    def _init_ocr(self) -> bool:
        """
        Initializes OCR engines (Gemini and Local) based on the provided configuration.
        Sets up LicensePlatePreprocessor for Gemini and LocalOCR for local processing.
        Ensures any existing executor is shut down to prevent resource leaks.

        Returns:
            True if at least one OCR engine was initialized, False otherwise.
        """
        ocr_cfg = self.config.get("ocr_engine", {})
        initialized = False

        # Shut down existing executor to prevent resource leaks during re-init
        if self.ocr_executor is not None:
            self.ocr_executor.shutdown(wait=False)
            self.ocr_executor = None

        # FIX: pass the FULL config (not the raw key string) so the preprocessor
        # — which reads ocr_engine.* itself — gets a real dict. Passing the key
        # string here was the latent bug that surfaced the moment Gemini was
        # enabled. Config is the single source of truth for the API key.
        gemini_key = self.gemini_api_key or ocr_cfg.get("gemini_api_key")
        if ocr_cfg.get("use_gemini_ocr", False):
            if gemini_key:
                if LicensePlatePreprocessor is not None:
                    try:
                        self.preprocessor = LicensePlatePreprocessor(self.config)
                        initialized = True
                        logger.info(
                            f"[{self.feed_id}] OCR init: Gemini enabled "
                            f"(model={self.preprocessor.model_id})"
                        )
                    except Exception as e:
                        logger.error(f"Failed to initialize Gemini OCR: {e}")
                else:
                    logger.warning("LicensePlatePreprocessor unavailable (import failed at startup).")
            else:
                logger.warning(
                    f"[{self.feed_id}] Gemini OCR requested but no gemini_api_key "
                    f"(set ocr_engine.gemini_api_key). Skipping Gemini."
                )

        if ocr_cfg.get("use_local", True):
            if LocalOCR is not None:
                try:
                    self.local_ocr = LocalOCR(self.config)
                    initialized = True
                except Exception as e:
                    logger.error(f"Failed to initialize Local OCR: {e}")
            else:
                logger.warning("LocalOCR unavailable (import failed at startup).")

        # Create executor if any OCR engine is enabled
        if initialized:
            # Clear stale in-flight tracking: a re-init (e.g. via update_config)
            # swaps the executor, so track ids from the previous engine must not
            # block new submissions forever.
            with self._ocr_lock:
                self._ocr_in_flight.clear()
            # Bounded queue: see _BoundedThreadPoolExecutor. Default 64 pending
            # jobs is enough headroom for 20 vehicles @ 15 FPS with 2 workers
            # while preventing unbounded memory growth under sustained load.
            self._ocr_max_pending = self.config.get("ocr_engine", {}).get("max_pending_jobs", 64)
            self.ocr_executor = _BoundedThreadPoolExecutor(
                max_workers=2, max_queue=self._ocr_max_pending
            )
        
        return initialized

    def _pixel_based_speed(self, track: TrackData) -> Optional[float]:
        """
        Calculates vehicle speed in km/h based on pixel-space velocity.
        Used as a fallback when ground-plane coordinates are unavailable.

        Args:
            track: The track data containing 'vx' and 'vy'.

        Returns:
            The calculated speed in km/h, or None if the fallback cannot
            produce a meaningful value (uncalibrated camera with no populated
            pixel velocity). Returning None (not 0.0) is deliberate: a 0.0 km/h
            speed reads as "stopped in gridlock" and corrupts the congestion
            KPI, whereas None is explicitly "unknown / uncalibrated".
        """
        # The ground-plane path was unavailable (no homography) and the
        # pixel-fallback needs vx/vy, which are never populated by the tracker
        # in this codebase. In that uncalibrated state there is no honest
        # speed to report -- return None rather than a fake 0.0.
        if not self.transformer.is_calibrated and "vx" not in track and "vy" not in track:
            return None
        vx = track.get("vx", 0.0)
        vy = track.get("vy", 0.0)
        pixel_speed = math.sqrt(vx ** 2 + vy ** 2)
        return (pixel_speed / self.pixels_per_meter) * 3.6 if self.pixels_per_meter > 0 else 0.0

    def _compute_congestion_score(self, vehicles: List[Dict], avg_speed: Optional[float]) -> float:
        """
        Computes congestion score on a 0-100 scale.
        0 = free flow, 100 = jammed.

        NOTE: This must stay in sync with TrafficMonitor.get_metrics()
        (app/utils/monitoring.py) — same weights and scale — so the DB
        feed_metrics record and the live WebSocket/predictor feature agree
        (audit C4). Canonical: 0.7 * speed_factor + 0.3 * density_factor, x100.

        When ``avg_speed`` is None (uncalibrated camera — speed unknowable),
        the speed_factor term is dropped so congestion reflects DENSITY ONLY.
        Crucially this does NOT default to a gridlock reading: without a speed
        signal we cannot claim congestion either, so the score is explicitly
        flagged uncalibrated by the caller rather than pinned near 75/100.
        """
        density_factor = len(vehicles) / 100.0
        density_factor = max(0.0, min(1.0, density_factor))

        if avg_speed is None:
            # No speed signal available: report density-only congestion with
            # no speed contribution (rather than pretending gridlock).
            congestion = density_factor * 0.3 * 100.0
        else:
            free_flow_speed = self.speed_limit
            speed_factor = 1.0 - (avg_speed / free_flow_speed) if free_flow_speed > 0 else 0.0
            speed_factor = max(0.0, min(1.0, speed_factor))
            congestion = (speed_factor * 0.7 + density_factor * 0.3) * 100.0
        return round(congestion, 1)

    def _compute_feed_metrics(self, vis_tracks: Dict[str, TrackData]) -> Dict[str, Any]:
        """
        Aggregate per-vehicle data into feed-level metrics.

        The vehicle population is EVERY currently-visible track (active OR
        predicting) -- including stopped vehicles. Filtering on
        ``speed > 0`` here (the old behaviour) caused three bugs:
          1. During total gridlock (every vehicle at speed 0.0) it hit the
             early-return branch and reported ``congestion_score: 0.0`` --
             free flow -- which is the exact condition this metric exists to
             flag (audit finding #4).
          2. Density (``len(vehicles)/100``) was computed over only the
             moving subset, so congestion *fell* as more vehicles stopped --
             backwards from what density means.
          3. ``vehicle_count`` switched meaning frame-to-frame (all tracks on
             the empty path vs moving-only on the normal path).

        This must stay in sync with TrafficMonitor.get_metrics()
        (app/utils/monitoring.py): same weights and scale, and the same
        vehicle population (all tracked vehicles), so the DB feed_metrics
        record and the live WebSocket/predictor feature agree (audit C4).
        """
        # All currently-visible tracks, active or predicting -- including
        # stopped vehicles, which is the whole point of congestion scoring.
        vehicles = list(vis_tracks.values())
        if not vehicles:
            return {
                "average_speed_kmh": 0.0,
                "congestion_score": 0.0,
                "vehicle_count": 0,
            }

        valid_speeds = [t.get("speed") for t in vehicles]
        speeds = [float(s) for s in valid_speeds if s is not None]
        # If no vehicle has a calibrated speed (e.g. uncalibrated camera),
        # report the average speed as None ("uncalibrated") rather than 0.0.
        # A 0.0 avg speed would be interpreted as gridlock and pin the
        # congestion KPI near 75/100 -- a fake-but-plausible number. None is
        # the honest signal that speed simply isn't measurable here.
        if speeds:
            avg_speed = float(np.median(speeds))  # Robust to outliers
            speed_uncalibrated = False
        else:
            avg_speed = 0.0
            speed_uncalibrated = True

        # Congestion from a null speed must not silently read as free-flow OR
        # as gridlock. When speed is uncalibrated we still have vehicle density,
        # so compute the density component but flag the score as uncalibrated.
        if speed_uncalibrated:
            congestion = self._compute_congestion_score(vehicles, None)
        else:
            congestion = self._compute_congestion_score(vehicles, avg_speed)

        # Update session histories (only record numeric avg speeds so the
        # EMA session average isn't polluted by uncalibrated frames)
        if not speed_uncalibrated:
            self.speed_history.append(avg_speed)
        self.congestion_history.append(congestion)

        session_avg_speed = float(np.mean(self.speed_history)) if self.speed_history else 0.0
        session_avg_congestion = float(np.mean(self.congestion_history)) if self.congestion_history else 0.0

        return {
            "average_speed_kmh": round(avg_speed, 1) if not speed_uncalibrated else None,
            "speed_uncalibrated": speed_uncalibrated,
            "session_average_speed_kmh": round(session_avg_speed, 1) if self.speed_history else None,
            "congestion_score": congestion,
            "congestion_uncalibrated": speed_uncalibrated,
            "session_average_congestion_score": round(session_avg_congestion, 3),
            "vehicle_count": len(vehicles),
            # total_vehicles_cumulative is tracked by TrafficMonitor.seen_vehicle_ids;
            # _compute_feed_metrics only writes to DB. Use monitor.get_metrics() for
            # the authoritative cumulative count (broadcast via VIDEO_FRAME).
        }

    def _should_update_reid(self, tid: str, track: TrackData, frame_index: int) -> bool:
        """
        Determines if a vehicle's ReID embedding should be updated for the current frame.
        Logic includes mandatory updates for new tracks, periodic fallbacks,
        occlusion recovery, and significant velocity changes.

        Args:
            tid: The track ID.
            track: The track data.
            frame_index: The current frame index.

        Returns:
            True if an embedding update is requested, False otherwise.
        """
        # 1. Mandatory update for new tracks
        if track.get("last_reid_update", -1) == -1:
            return True

        # Cooldown for failed attempts to avoid looping on embedding failure
        last_attempt = track.get("last_reid_attempt", -1)
        if last_attempt != -1 and (frame_index - last_attempt) < 30:
            return False

        # 2. Periodic safety fallback (every 60 frames)
        if (frame_index - track.get("last_reid_update", -1)) >= 60:
            return True

        # 3. Recovery from occlusion
        if track.get("status") == "active" and track.get("prev_status") == "predicting":
            return True

        # 4. Significant velocity change (> 15 km/h since last update)
        current_speed = track.get("speed", 0.0)
        last_speed = track.get("last_reid_speed", current_speed)
        if abs(current_speed - last_speed) > 15.0:
            return True

        return False

    def detect_and_track(
        self,
        frame: Optional[np.ndarray],
        frame_index: int,
        confidence_threshold: Optional[float] = None,
        proximity_threshold: Optional[int] = None,
        track_timeout: Optional[int] = None,
        external_detections: Optional[List[Tuple]] = None,
        timestamp: Optional[float] = None,
    ) -> Tuple[Dict[str, TrackData], List[int], Any]:
        """
        Orchestrates the full pipeline: lane detection, vehicle detection, tracking, 
        ReID embedding updates, and ground-plane metadata extraction.

        Args:
            frame: The input video frame.
            frame_index: Sequential index of the frame.
            confidence_threshold: Override for detection confidence.
            proximity_threshold: Override for track proximity.
            track_timeout: Override for track timeout.
            external_detections: Pre-computed detections to skip the detector.
            timestamp: Frame timestamp; defaults to wall clock if None.

        Returns:
            A tuple containing:
            - vis_tracks: A dictionary of tracks currently visible/predictable.
            - cached_lane_boundaries: Current detected lane boundaries.
            - last_detected_lane_lines: Raw lane lines from the last detection.
        """
        if frame is None or frame.size == 0:
            return {}, self.cached_lane_boundaries, self.last_detected_lane_lines

        current_time = timestamp if timestamp is not None else time.time()

        # 1. Lane Detection (Periodic)
        # `enabled` is the canonical gate (audit #6). Old key
        # `dynamic_lane_detection_enabled` falls back for one release.
        _lane_cfg = self.config.get("lane_detection", {})
        _lane_enabled = _lane_cfg.get("enabled", _lane_cfg.get("dynamic_lane_detection_enabled", False))
        if (
            _lane_enabled
            and process_frame_for_lanes
            and (current_time - self.last_lane_detection_time) >= self.lane_detection_interval
        ):
            try:
                lines = process_frame_for_lanes(frame, self.config)
                self.last_detected_lane_lines = lines
                if lines and get_lane_boundaries_from_lines:
                    self.cached_lane_boundaries = get_lane_boundaries_from_lines(
                        frame.shape[1], lines, self.config
                    )
                    self.last_lane_detection_time = current_time
            except (cv2.error, RuntimeError) as e:
                logger.warning(f"Lane detection failed: {e}")

        # 2. Detection (skip if external detections provided)
        if external_detections is not None:
            detections = external_detections
        else:
            thresh = confidence_threshold if confidence_threshold is not None else self.confidence_threshold
            detections = self.detector.detect(frame, thresh)

        # 3. Compute detection embeddings BEFORE association so the tracker's
        # appearance-weighted matching (ReID) actually contributes. Previously
        # embeddings were only computed after update(), leaving det embeddings
        # None and making the tracker's ReID branch dead code (audit T1). Gated
        # by config flag and a per-frame budget to bound GPU cost.
        use_reid_assoc = (
            self.reid_embedder is not None
            and self.config.get("tracking", {}).get("use_appearance_in_tracking", True)
        )
        dets_for_tracker = []
        if use_reid_assoc and detections:
            h, w = frame.shape[:2]
            assoc_budget = self.config.get("performance", {}).get("reid_assoc_budget_per_frame", 20)
            crops = []
            crop_det_idx = []
            for di, d in enumerate(detections):
                if len(crops) >= assoc_budget:
                    break
                # Only embed high-confidence detections (association-relevant)
                if d[2] < self.confidence_threshold:
                    continue
                x1, y1, x2, y2 = map(int, d[0])
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)
                if x2 > x1 and y2 > y1:
                    crops.append(frame[y1:y2, x1:x2])
                    crop_det_idx.append(di)

            det_embs: Dict[int, Any] = {}
            if crops:
                try:
                    embs = self.reid_embedder.get_batch_embeddings(crops)
                    for di, emb in zip(crop_det_idx, embs):
                        if emb is not None:
                            det_embs[di] = emb
                except Exception as e:
                    logger.warning(f"[{self.feed_id}] Detection embedding failed: {e}")

            dets_for_tracker = [(d[0], d[1], d[2], det_embs.get(di)) for di, d in enumerate(detections)]
        else:
            # Pass detections without embeddings (motion-only association)
            dets_for_tracker = [(d[0], d[1], d[2], None) for d in detections]

        # Capture current statuses as 'previous' before updating the tracker
        # We use a copy of the tracker's data or our own persistent mapping
        current_statuses = {tid: track.get("status", "unknown") 
                           for tid, track in self.tracker.vehicle_data.items()} if hasattr(self.tracker, 'vehicle_data') else {}

        vehicle_data = self.tracker.update(dets_for_tracker, current_time, frame.shape).copy()

        # Enforce the configured active-track cap (the key was read at init
        # but NEVER enforced -- dead). At low detection fps the frame-based
        # track timeout stretches in wall-clock (10 frames @ 0.6fps = ~17s),
        # so stale tracks accumulate far past max_active_tracks (observed
        # 178 live vehicles with the cap set to 100), bloating the wire
        # payload, ReID matching, and tracker cost. Cull the least-recently
        # seen tracks to honor the operator's declared cap.
        if len(vehicle_data) > self.max_active_tracks:
            by_seen = sorted(
                vehicle_data.items(),
                key=lambda kv: kv[1].get("last_seen", 0.0),
                reverse=True,
            )
            vehicle_data = dict(by_seen[: self.max_active_tracks])

        # Apply previous statuses for adaptive ReID
        for tid, track in vehicle_data.items():
            track["prev_status"] = current_statuses.get(tid, "unknown")
        if self.reid_embedder:
            needs_emb = []
            h, w = frame.shape[:2]
            self._reid_updates_this_frame = 0
            budget_cap = self.config.get("performance", {}).get("reid_budget_per_frame", 10)

            for tid, track in vehicle_data.items():
                if self._reid_updates_this_frame >= budget_cap:
                    break
                if self._should_update_reid(tid, track, frame_index):
                    bbox = track.get("bbox")
                    if bbox:
                        # Mark attempt immediately to prevent loops on failure
                        track["last_reid_attempt"] = frame_index
                        x1, y1, x2, y2 = map(int, bbox)
                        x1, y1 = max(0, x1), max(0, y1)
                        x2, y2 = min(w, x2), min(h, y2)
                        if x2 > x1 and y2 > y1:
                            needs_emb.append((tid, frame[y1:y2, x1:x2]))
                            self._reid_updates_this_frame += 1

            if needs_emb:
                tids, rois = zip(*needs_emb)
                embeddings = self.reid_embedder.get_batch_embeddings(list(rois))
                for tid, emb in zip(tids, embeddings):
                    if emb is not None:
                        vehicle_data[tid]["embedding"] = emb
                        vehicle_data[tid]["last_reid_update"] = frame_index
                        vehicle_data[tid]["last_reid_speed"] = vehicle_data[tid].get("speed", 0.0)

        # 5. Metadata processing (vectorised centroid batch transform)
        vis_tracks: Dict[str, Dict] = {}
        if vehicle_data:
            valid_tids = []
            centroids_list = []
            for tid, track in vehicle_data.items():
                # Optimization: Only process transforms for active or predicting tracks
                if track.get("status") not in ("active", "predicting"):
                    continue
                bbox = track.get("bbox")
                if bbox and len(bbox) == 4:
                    valid_tids.append(tid)
                    centroids_list.append([(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2])

            if not centroids_list:
                return {}, self.cached_lane_boundaries, self.last_detected_lane_lines

            centroids = np.array(centroids_list, dtype=np.float32)
            ground_positions = self.transformer.pixel_to_ground(centroids)

            for idx, tid in enumerate(valid_tids):
                track = vehicle_data[tid]

                if ground_positions is not None and idx < len(ground_positions):
                    track["ground_coordinates"] = ground_positions[idx]

                # --- Speed (km/h) ---
                if "ground_coordinates" in track:
                    curr_gx, curr_gy = track["ground_coordinates"]
                    prev_ground_pos = track.get("prev_ground_pos")
                    prev_t = track.get("prev_t")

                    if prev_ground_pos and prev_t:
                        prev_gx, prev_gy = prev_ground_pos
                        dt = current_time - prev_t
                        if dt > 0:
                            dist = math.sqrt((curr_gx - prev_gx) ** 2 + (curr_gy - prev_gy) ** 2)
                            raw_speed = (dist / dt) * 3.6
                            # Clamp raw_speed BEFORE it enters the EWMA (fix #6):
                            # a single bad sample (e.g. a jumpy track after a long
                            # occlusion gap) would otherwise seed the running average
                            # and corrupt every subsequent smoothed value. The 180
                            # km/h physical cap below is the final safety net.
                            raw_speed = min(raw_speed, 180.0)
                            # Sanity cap: skip EWMA after long occlusion gaps
                            if dt > 2.0:
                                track["speed"] = raw_speed
                            else:
                                prev_speed = track.get("speed", raw_speed)
                                track["speed"] = (
                                    self.ewma_alpha * raw_speed
                                    + (1 - self.ewma_alpha) * prev_speed
                                )
                        else:
                            track["speed"] = track.get("speed", 0.0)
                    else:
                        # First frame for this track — fall back to pixel velocity
                        track["speed"] = self._pixel_based_speed(track)

                    track["prev_ground_pos"] = (curr_gx, curr_gy)
                    track["prev_t"] = current_time
                else:
                    # Transformer/homography unavailable — fall back to pixel velocity.
                    # This is expected and correct for feeds without a calibration
                    # matrix (e.g. sample feeds, freshly added uncalibrated cameras).
                    # It is a one-time informational note, not an error condition, so
                    # downgrade from WARNING to INFO to avoid alarm in normal operation.
                    if not self._homography_fallback_warned:
                        logger.info(f"[{self.feed_id}] Homography unavailable, using pixel-based speed (calibration recommended)")
                        self._homography_fallback_warned = True
                    track["speed"] = self._pixel_based_speed(track)

                # Physical speed cap to prevent anomalies (e.g., 180 km/h).
                # Only clamp when we actually have a numeric speed; an
                # uncalibrated feed stores None (reported as UNCALIBRATED).
                if track["speed"] is not None:
                    track["speed"] = min(track["speed"], 180.0)

                # --- Acceleration (m/s^2) & direction ---
                # Consumed by TrafficMonitor._detect_anomalies (hard_braking /
                # wrong_way). Previously never computed, making those anomaly
                # branches permanent no-ops (audit C2).
                prev_speed_accel = track.get("prev_speed_for_accel")
                prev_speed_t = track.get("prev_speed_t")
                if prev_speed_accel is not None and prev_speed_t is not None:
                    a_dt = current_time - prev_speed_t
                    # Only trust acceleration when it reflects REAL motion: the
                    # prior speed had to be above a floor (a slow crawl easing off
                    # isn't "hard braking"), the time step is meaningful (frame-close
                    # noise), and the magnitude is physically plausible (anything
                    # more negative than ~-15 m/s^2 is a track-churn / bbox-jump
                    # artifact, not a car braking). This kills the false "Sudden
                    # deceleration" incident storm while preserving real hard-brakes.
                    raw_accel = 0.0
                    if (
                        a_dt > self.min_accel_dt_seconds
                        and prev_speed_accel >= self.min_speed_for_accel_kmh
                    ):
                        raw_accel = (track["speed"] - prev_speed_accel) / 3.6 / a_dt
                        if raw_accel < self.min_physical_accel_mps2:
                            raw_accel = 0.0  # physics-impossible spike = tracking noise
                    track["acceleration"] = raw_accel
                track["prev_speed_for_accel"] = track["speed"]
                track["prev_speed_t"] = current_time

                # Compass-style direction from Kalman velocity (image coords:
                # +y is down, so North = moving up = negative vy).
                vx = track.get("vx", 0.0)
                vy = track.get("vy", 0.0)
                if abs(vx) > 1e-3 or abs(vy) > 1e-3:
                    if abs(vy) >= abs(vx):
                        track["direction"] = "North" if vy < 0 else "South"
                    else:
                        track["direction"] = "East" if vx > 0 else "West"

                # Filtering for visualisation
                if track["status"] == "active":
                    vis_tracks[tid] = track
                    if (self.local_ocr or self.preprocessor) and track.get("confidence", 0) > 0.5:
                        self._maybe_submit_ocr(tid, frame, track.get("bbox"))
                elif track["status"] == "predicting":
                    if (current_time - track["last_seen"]) < self.predict_timeout:
                        vis_tracks[tid] = track

        # Compute feed-level metrics (average speed, congestion)
        feed_metrics = self._compute_feed_metrics(vis_tracks)

        # Drain OCR results FIRST so any plate recognised this frame is reflected
        # in the DB write (and in vis_tracks) instead of being deferred a frame
        # (audit 3.1). Previously _save_vehicle_data ran before OCR processing.
        self._process_ocr_results(vehicle_data)

        self._save_vehicle_data(vis_tracks, feed_metrics)

        return vis_tracks, self.cached_lane_boundaries, self.last_detected_lane_lines

    def _locate_plate_region(self, roi) -> Optional[tuple]:
        """Locate the license-plate sub-rectangle inside a vehicle crop.

        EasyOCR does NOT localize plates — it reads whatever crop it's handed, so
        feeding the whole vehicle made it read body decals/logos ("MITCHELLLINCC",
        "DEMARR") instead of the plate. Classical-CV heuristic: a plate is a
        high-contrast, roughly 1.4-6:1 rectangle in the LOWER-CENTER of the vehicle.
        Returns (x, y, w, h) in roi coords, or None if nothing plate-like is found
        (caller falls back to a bottom-center band). Never raises.
        """
        try:
            import cv2 as _cv
            gray = _cv.cvtColor(roi, _cv.COLOR_RGB2GRAY)
            rh, rw = gray.shape[:2]
            if rh < 24 or rw < 24:
                return None
            # Plates sit in the lower half of the vehicle body; skip the top half
            # (that's where windows/decals/logos live).
            y_start = int(rh * 0.5)
            region = gray[y_start:, :]
            rr_h, rr_w = region.shape[:2]
            best = None
            best_score = 0.0
            for inv in (False, True):  # dark-on-light AND light-on-dark plates
                th = _cv.threshold(region, 0, 255, _cv.THRESH_BINARY + _cv.THRESH_OTSU)[1]
                if inv:
                    th = _cv.bitwise_not(th)
                # close gaps between characters so the plate forms one blob
                k = _cv.getStructuringElement(_cv.MORPH_RECT, (max(3, int(rr_w * 0.04)), 3))
                th = _cv.morphologyEx(th, _cv.MORPH_CLOSE, k)
                cnts, _ = _cv.findContours(th, _cv.RETR_EXTERNAL, _cv.CHAIN_APPROX_SIMPLE)
                for c in cnts:
                    x, y, cw, ch = _cv.boundingRect(c)
                    if cw < rr_w * 0.22 or ch < 10:          # too small
                        continue
                    if cw > rr_w * 0.99 or ch > rr_h * 0.85:  # too big
                        continue
                    aspect = cw / max(1.0, ch)
                    if not (1.4 <= aspect <= 6.0):
                        continue
                    cx = (x + cw / 2.0) / rr_w
                    cy = (y + ch / 2.0) / rr_h
                    # reward a horizontally-centered, low candidate (a real plate)
                    center_bonus = (1.0 - abs(cx - 0.5)) * (0.6 + 0.4 * cy)
                    score = (cw * ch) * center_bonus
                    if score > best_score:
                        best_score = score
                        best = (x, y_start + y, cw, ch)
            if best is None:
                return None
            bx, by, bw2, bh2 = best
            pad_x = int(bw2 * 0.12)
            pad_y = int(bh2 * 0.18)
            bx = max(0, bx - pad_x)
            by = max(0, by - pad_y)
            bw2 = min(rw - bx, bw2 + 2 * pad_x)
            bh2 = min(rh - by, bh2 + 2 * pad_y)
            return (bx, by, bw2, bh2)
        except Exception:
            return None

    def _maybe_submit_ocr(self, tid: str, frame: np.ndarray, bbox):
        """Submit an OCR job for a track, de-duplicating in-flight requests.

        A track may only have ONE pending OCR job at a time. If an OCR is
        already pending for this track we skip; if the bounded executor's
        pending queue is saturated we drop (the track is re-attempted on a
        later frame). This bounds total OCR work to (active vehicles)
        concurrent jobs instead of (active vehicles * frames/sec), and keeps
        the executor's pending queue from growing without limit under burst
        load.
        """
        if self.ocr_executor is None or not bbox or len(bbox) < 4:
            return
        with self._ocr_lock:
            if tid in self._ocr_in_flight:
                return  # already a pending OCR for this vehicle
            self._ocr_in_flight.add(tid)
        x1, y1, x2, y2 = map(int, bbox)
        h, w = frame.shape[:2]
        roi = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
        if roi.size == 0:
            with self._ocr_lock:
                self._ocr_in_flight.discard(tid)
            return

        # PLATE-CROP: EasyOCR (app.utils.local_ocr) does NOT localize plates — it
        # OCRs whatever crop it's handed. At 640x480 the plate is a ~20-60px sliver
        # inside the FULL car crop, which EasyOCR reads as bumper stickers/logos or
        # misses entirely. Crop the bottom-center band of the vehicle box (where a
        # plate sits) and upscale it so the OCR gets a plate-sized image. Pre-filter
        # by min width + aspect ratio to drop junk crops (road texture, tall band =
        # person, tiny speck) so we don't waste EasyOCR work.
        ocr_cfg = self.config.get("ocr_engine", {})
        plate = roi
        try:
            # 1) LOCALIZE the actual plate (a low-center, high-contrast rectangle)
            # instead of OCRing the whole vehicle -- EasyOCR can't localize, and
            # the whole-vehicle crop reads body decals/logos ("MITCHELLLINCC").
            plate_box = None
            if ocr_cfg.get("plate_localize", True):
                plate_box = self._locate_plate_region(roi)
            if plate_box:
                px, py, pw, ph = (int(v) for v in plate_box)
                pw = max(pw, 1)
                ph = max(ph, 1)
                sub = roi[py:py + ph, px:px + pw]
                if sub.size > 0:
                    sb_h, sb_w = sub.shape[:2]
                    upscale = float(ocr_cfg.get("plate_upscale", 3.0))
                    min_readable = float(ocr_cfg.get("plate_min_target_w", 160))
                    tgt_w = int(min(max(int(sb_w * upscale), min_readable), 480))
                    tgt_h = max(1, int(tgt_w * sb_h / max(1.0, sb_w)))
                    plate = cv2.resize(sub, (tgt_w, tgt_h), interpolation=cv2.INTER_CUBIC)
            else:
                # 2) Fallback: narrow bottom-center band (bumper region, NOT the
                # mid-body logo), upscaled to a readable width.
                rh, rw = roi.shape[:2]
                if rh > 8 and rw > 8:
                    bottom_frac = float(ocr_cfg.get("plate_crop_bottom_factor", 0.28))
                    width_frac = float(ocr_cfg.get("plate_crop_width_factor", 0.60))
                    upscale = float(ocr_cfg.get("plate_upscale", 3.0))
                    min_w = float(ocr_cfg.get("plate_min_width", 15))
                    min_aspect = float(ocr_cfg.get("plate_min_aspect", 0.6))
                    max_aspect = float(ocr_cfg.get("plate_max_aspect", 10.0))
                    band_h = max(1, int(rh * bottom_frac))
                    band_w = max(1, int(rw * width_frac))
                    y0 = rh - band_h
                    x0 = max(0, (rw - band_w) // 2)
                    band = roi[y0:rh, x0:x0 + band_w]
                    if band.size > 0:
                        bh, bw = band.shape[:2]
                        aspect = bw / max(1.0, bh)
                        if bw >= min_w and min_aspect <= aspect <= max_aspect:
                            min_readable = float(ocr_cfg.get("plate_min_target_w", 140))
                            tgt_w = int(min(max(int(bw * upscale), min_readable), 480))
                            tgt_h = max(1, int(tgt_w * bh / max(1.0, bw)))
                            band = cv2.resize(band, (tgt_w, tgt_h), interpolation=cv2.INTER_CUBIC)
                            plate = band
                        else:
                            # junk crop -> skip OCR entirely (no executor waste)
                            with self._ocr_lock:
                                self._ocr_in_flight.discard(tid)
                            return
        except Exception as e:
            logger.debug(f"plate-crop failed [{self.feed_id}] {tid}: {e}; using full-vehicle crop")

        try:
            self.ocr_executor.submit(self._run_ocr, tid, plate)
        except OCRQueueFull:
            # Executor saturated: drop this submission, keep the track out of
            # the in-flight set so it can be retried next frame.
            with self._ocr_lock:
                self._ocr_in_flight.discard(tid)

    def _run_ocr(self, tid: str, roi: np.ndarray):
        """Worker function for OCR executor."""
        try:
            text = None
            # 1. Try Gemini OCR via preprocessor if available
            if self.preprocessor:
                text = self.preprocessor.preprocess_and_ocr(roi)

            # 2. Fallback to local OCR if Gemini failed or is disabled
            if not text and self.local_ocr:
                text = self.local_ocr.read_plate(roi)

            # OCR diagnostic: log crop size + result so we can see WHY nothing is
            # read (tiny/blurred band → empty, vs a real read). Reads always log;
            # empties are throttled to avoid a flood across 25 vehicles/frame.
            try:
                rh, rw = roi.shape[:2]
            except Exception:
                rh = rw = -1
            if text:
                logger.info(f"[{self.feed_id}] OCR {tid}: {rw}x{rh} -> '{text}'")
            else:
                self._ocr_empty_log = getattr(self, "_ocr_empty_log", 0) + 1
                if self._ocr_empty_log <= 15 or self._ocr_empty_log % 100 == 0:
                    logger.info(
                        f"[{self.feed_id}] OCR {tid}: {rw}x{rh} -> <empty> "
                        f"(empty_count={self._ocr_empty_log})"
                    )

            if text:
                try:
                    self.ocr_results_queue.put_nowait({"track_id": tid, "plate_text": text})
                except queue.Full:
                    logger.warning(f"[{self.feed_id}] OCR results queue full. Dropping result for {tid}.")
        except Exception as e:
            logger.error(f"OCR processing failed for {tid}: {e}")
        finally:
            # Always release the in-flight slot so the track can be OCR'd again.
            with self._ocr_lock:
                self._ocr_in_flight.discard(tid)

    def _save_vehicle_data(self, tracked_vehicles: Dict[str, TrackData], feed_metrics: Optional[Dict[str, Any]] = None):
        """
        Throttled write of vehicle state to the DB queue (max 1 Hz per vehicle).
        Filters for valid bbox and centroid before sending.

        Args:
            tracked_vehicles: Dictionary of currently tracked vehicles and their metadata.
            feed_metrics: Feed-level aggregated metrics (speed, congestion).
        """
        if not self.db_queue:
            return

        now = time.time()
        
        # 1. Save Feed-Level Metrics (once per frame/cycle)
        if feed_metrics:
            try:
                self.db_queue.put_nowait({
                    "type": "feed_metrics",
                    "feed_id": self.feed_id,
                    "timestamp": float(now),
                    **feed_metrics
                })
            except queue.Full:
                if now - self._last_queue_warn_time > 1.0:
                    logger.warning(f"[{self.feed_id}] DB queue full. Dropping feed metrics.")
                    self._last_queue_warn_time = now

        # 2. Save Individual Vehicle Data (throttled)
        for vehicle_id, data in tracked_vehicles.items():
            # Use persistent storage for last save time to ensure throttling works across frames
            if now - self._last_db_save_times.get(vehicle_id, 0) < 1.0:
                continue

            # Fix: Check for existence of bbox and centroid to avoid KeyError
            bbox = data.get("bbox")
            centroid = data.get("centroid")
            # Hardened guards (audit 3.2): skip malformed bbox/centroid rather
            # than letting the list-comprehension below crash or write bad rows.
            if (
                not bbox
                or len(bbox) < 4
                or not centroid
                or len(centroid) < 2
            ):
                continue

            try:
                self.db_queue.put_nowait({
                    "type": "vehicle_data",
                    "feed_id": self.feed_id,
                    "vehicle_id": str(vehicle_id),
                    "global_vehicle_id": str(data.get("global_vehicle_id") or ""),
                    "timestamp": float(now),
                    "bbox": [float(x) for x in bbox],
                    "centroid": [float(x) for x in centroid],
                    "speed": data.get("speed"),
                    # No float() coercion: uncalibrated feeds store speed=None
                    # by design, and float(None) would raise TypeError (this
                    # try only catches queue.Full, so the exception escapes
                    # into the detect loop). Pass NULL through to both the
                    # SQLite and Timescale writers -- both bind None directly.
                    "license_plate": str(data.get("license_plate", "Unknown")),
                    "class_id": int(data.get("class_id", -1)),
                    "class_name": str(self.vehicle_type_map.get(data.get("class_id", -1), "unknown")),
                    "confidence": float(data.get("confidence", 0.0)),
                    "status": str(data.get("status", "unknown")),
                    "lane": int(data.get("lane", -1)),
                })
                self._last_db_save_times[vehicle_id] = now
            except queue.Full:
                if now - self._last_queue_warn_time > 1.0:
                    logger.warning(f"[{self.feed_id}] DB queue full. Dropping vehicle data update.")
                    self._last_queue_warn_time = now

        # Prune stale save times for vehicles no longer tracked
        active_ids = set(tracked_vehicles.keys())
        stale_keys = [vid for vid in self._last_db_save_times if vid not in active_ids]
        for vid in stale_keys:
            del self._last_db_save_times[vid]

    def _process_ocr_results(self, vehicle_data: Dict[str, TrackData]):
        """
        Drains the OCR results queue and updates the vehicle track data with detected license plates.

        Args:
            vehicle_data: Dictionary of current vehicle tracks to update.
        """
        try:
            while True:
                result = self.ocr_results_queue.get_nowait()
                tid = result["track_id"]
                if tid in vehicle_data:
                    vehicle_data[tid]["license_plate"] = result["plate_text"]
        except queue.Empty:
            pass

    def cleanup(self):
        """
        Gracefully shuts down thread pools and releases resources used by the module.
        """
        if self.ocr_executor:
            self.ocr_executor.shutdown(wait=True)
        self.reid_embedder = None
        self.preprocessor = None
        self.local_ocr = None
        # Release cached lane-detection state (audit 5.4).
        self.last_detected_lane_lines = None
        self.cached_lane_boundaries = []
        logger.info(f"[{self.feed_id}] CoreModule resources cleaned up.")

    def update_config(self, updates: Dict[str, Any]):
        """
        Dynamically updates configuration and triggers necessary recalculations 
        (e.g., ROI masks, homography matrices).

        Args:
            updates: Dictionary containing the configuration keys to update.
        """
        with self._lock:
            # 1. Update config and extract resolution
            res_changed = False
            if "vehicle_detection" in updates:
                v_cfg_update = updates["vehicle_detection"]
                current_v_cfg = self.config.get("vehicle_detection", {})
                
                if "frame_resolution" in v_cfg_update:
                    res_changed = True
                
                self.config["vehicle_detection"] = {**current_v_cfg, **v_cfg_update}

            v_cfg = self.config.get("vehicle_detection", {})
            res = v_cfg.get("frame_resolution", [640, 480])

            # 2. Apply updates to internal state
            if "vehicle_detection" in updates:
                v_cfg_update = updates["vehicle_detection"]
                self.confidence_threshold = v_cfg_update.get("confidence_threshold", self.confidence_threshold)
                
                # Recompute homography if resolution changed or calibration updated.
                # The live calibration now lives only in self.transformer; the
                # old self.homography_matrix mirror was dead (never read for
                # speed). Drop the dead mirror call and keep the canonical one.
                if "calibration" in v_cfg_update or res_changed:
                    calib_cfg = v_cfg_update.get("calibration", v_cfg.get("calibration", {}))
                    self.transformer.update_calibration(calib_cfg)

            if "ocr_engine" in updates:
                # Merge OCR engine settings into config, THEN re-initialize OCR
                # engines. Previously the merge was skipped, so _init_ocr() read
                # stale settings (audit 1.1).
                ocr_cfg = self.config.get("ocr_engine", {})
                self.config["ocr_engine"] = {**ocr_cfg, **updates["ocr_engine"]}
                self._init_ocr()

            if "roi" in updates:
                roi_points = updates["roi"]
                if isinstance(roi_points, list):
                    self.roi_polygon_points = roi_points
                    # Keep config in sync so other modules reading the dict see the
                    # new polygon (audit 1.2). Previously only internal state changed.
                    roi_cfg = self.config.get("roi_processing", {})
                    roi_cfg["polygon_points"] = roi_points
                    self.config["roi_processing"] = roi_cfg
                    self._initialize_roi_mask(res)
                    if self.detector:
                        self.detector.initialize_roi(
                            res,
                            self.roi_polygon_points,
                            self.config.get("roi_processing", {}).get("exclusion_zones", []),
                        )

            if "lane_detection" in updates:
                # Previously there was NO handler for lane_detection updates, so
                # interval/enabled changes were silently ignored (audit 1.2).
                l_cfg_update = updates["lane_detection"]
                current_l_cfg = self.config.get("lane_detection", {})
                self.config["lane_detection"] = {**current_l_cfg, **l_cfg_update}
                # Audit #6: accept both the new key `detection_interval_seconds`
                # and the deprecated `detection_interval`.
                if "detection_interval_seconds" in l_cfg_update:
                    self.lane_detection_interval = float(l_cfg_update["detection_interval_seconds"])
                elif "detection_interval" in l_cfg_update:
                    self.lane_detection_interval = float(l_cfg_update["detection_interval"])
                    logger.warning(
                        "lane_detection.detection_interval is deprecated; "
                        "use lane_detection.detection_interval_seconds instead."
                    )