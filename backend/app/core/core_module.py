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
        device = self._check_gpu_availability()
        self.detector = DetectionEngine(
            str(self.model_path), self.config, device, preloaded_model=preloaded_model
        )
        self.detector.load_model()

        res = v_cfg.get("frame_resolution", [640, 480])
        self.roi_polygon_points = self.config.get("roi_processing", {}).get("polygon_points", None)
        self.detector.initialize_roi(res, self.roi_polygon_points)
        self._initialize_roi_mask(res)

        self.tracker = TrackingManager(self.config, self.fps, feed_id=self.feed_id)

        calib_cfg = v_cfg.get("calibration", {})
        self.transformer = CoordinateTransformer(calib_cfg)
        self.homography_matrix = None
        self._update_homography(calib_cfg)

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
        self.lane_detection_interval = l_cfg.get("detection_interval", 1.0)

        # 5. Behavior & Speed
        self.pixels_per_meter = v_cfg.get("pixels_per_meter", 10.0)
        self.ewma_alpha = b_cfg.get("speed_smoothing_factor", 0.3)
        self.speed_limit = b_cfg.get("speed_limit_kmh", 60)
        self.accel_threshold_mps2 = b_cfg.get("acceleration_threshold_mps2", 2.0)
        self.stopped_speed_threshold_kmh = b_cfg.get("stopped_speed_threshold_kmh", 5.0)

        # Session Metrics
        self.speed_history: deque = deque(maxlen=300)  # 300 frames @ 1 FPS = 5 min
        self.congestion_history: deque = deque(maxlen=300)
        self._homography_fallback_warned = False

        self.preprocessor = None
        self.local_ocr = None
        self.last_activity = 0.0
        self._reid_updates_this_frame = 0  # Per-frame budget control
        self._lock = threading.RLock()

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
            points_np = np.array(self.roi_polygon_points, dtype=np.int32)
            mask = np.zeros((h, w), dtype=np.uint8)
            cv2.fillPoly(mask, [points_np], 255)
            self.roi_mask = cv2.bitwise_and(self.roi_mask, mask)

        exclusion = self.config.get("roi_processing", {}).get("exclusion_zones", [])
        for zone in exclusion:
            zone_np = (np.array(zone, dtype=np.float32) * [w, h]).astype(np.int32)
            cv2.fillPoly(self.roi_mask, [zone_np], 0)

    def _preprocess_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, bool, int, int]:
        """
        Pre-processes the frame for inference. If an ROI is defined, it crops the frame 
        to the ROI's bounding box to reduce inference load.

        Returns:
            Tuple of (processed_frame, roi_enabled, x_offset, y_offset).
        """
        if self.roi_polygon_points:
            pts = np.array(self.roi_polygon_points)
            x_min = int(np.min(pts[:, 0]))
            y_min = int(np.min(pts[:, 1]))
            x_max = int(np.max(pts[:, 0]))
            y_max = int(np.max(pts[:, 1]))

            # Clamp to frame dimensions
            h, w = frame.shape[:2]
            x_min, y_min = max(0, x_min), max(0, y_min)
            x_max, y_max = min(w, x_max), min(h, y_max)

            if x_max > x_min and y_max > y_min:
                cropped_frame = frame[y_min:y_max, x_min:x_max]
                return cropped_frame, True, x_min, y_min

        return frame, False, 0, 0

    def _update_homography(self, calibration_cfg: Dict, resolution: Optional[List[int]] = None):
        """
        Computes the perspective transformation matrix for distance and speed calculations.

        Args:
            calibration_cfg: Configuration containing image_points and world_points.
            resolution: Optional explicit resolution [width, height] to use for scaling.
        """
        if not calibration_cfg or "image_points" not in calibration_cfg:
            return

        img_pts = np.array(calibration_cfg["image_points"], dtype=np.float32)
        world_pts = np.array(calibration_cfg.get("world_points", []), dtype=np.float32)

        if len(img_pts) < 4 or len(world_pts) < 4:
            return

        # Normalised points (0–1) need scaling to pixel coordinates
        if np.max(img_pts) <= 1.0:
            if resolution:
                width, height = resolution
            else:
                v_cfg = self.config.get("vehicle_detection", {})
                width, height = v_cfg.get("frame_resolution", [640, 480])
            img_pts = img_pts * [width, height]

        H, status = cv2.findHomography(img_pts, world_pts)
        if H is None:
            logger.warning(f"[{self.feed_id}] Homography computation failed – points may be degenerate.")
            return
        self.homography_matrix = H
        logger.info(f"[{self.feed_id}] Homography matrix recalibrated.")

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

        # FIX: Use module-level imports; removed redundant local re-imports
        if ocr_cfg.get("use_gemini_ocr", False):
            if self.gemini_api_key:
                if LicensePlatePreprocessor is not None:
                    try:
                        self.preprocessor = LicensePlatePreprocessor(self.gemini_api_key)
                        initialized = True
                    except Exception as e:
                        logger.error(f"Failed to initialize Gemini OCR: {e}")
                else:
                    logger.warning("LicensePlatePreprocessor unavailable (import failed at startup).")
            else:
                logger.warning(f"[{self.feed_id}] Gemini OCR enabled in config but gemini_api_key is missing. Skipping.")

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
            self.ocr_executor = ThreadPoolExecutor(max_workers=2)
        
        return initialized

    def _pixel_based_speed(self, track: TrackData) -> float:
        """
        Calculates vehicle speed in km/h based on pixel-space velocity.
        Used as a fallback when ground-plane coordinates are unavailable.

        Args:
            track: The track data containing 'vx' and 'vy'.

        Returns:
            The calculated speed in km/h.
        """
        vx = track.get("vx", 0.0)
        vy = track.get("vy", 0.0)
        pixel_speed = math.sqrt(vx ** 2 + vy ** 2)
        return (pixel_speed / self.pixels_per_meter) * 3.6 if self.pixels_per_meter > 0 else 0.0

    def _compute_congestion_score(self, vehicles: List[Dict], avg_speed: float) -> float:
        """
        Computes congestion score [0-1] from vehicle density and speed.
        0 = free flow, 1 = jammed.
        """
        # Speed component (assuming free_flow = speed_limit)
        free_flow_speed = self.speed_limit
        speed_component = max(0.0, 1.0 - (avg_speed / max(free_flow_speed, 1.0)))
        
        # Density component (vehicles per hypothetical road segment)
        # Assume ROI represents ~100m of road
        density_per_100m = len(vehicles) / 100.0
        jam_density = 0.15  # vehicles/meter (15m spacing at standstill)
        density_component = min(1.0, density_per_100m / jam_density)
        
        # Weighted average
        congestion = 0.5 * speed_component + 0.5 * density_component
        return round(congestion, 3)

    def _compute_feed_metrics(self, vis_tracks: Dict[str, TrackData]) -> Dict[str, Any]:
        """
        Aggregate per-vehicle data into feed-level metrics.
        """
        active_vehicles = [
            t for t in vis_tracks.values() 
            if t.get("status") == "active" and t.get("speed", 0) > 0
        ]
        
        if not active_vehicles:
            return {
                "average_speed_kmh": 0.0,
                "congestion_score": 0.0,
                "vehicle_count": len(vis_tracks),
            }
        
        speeds = [t["speed"] for t in active_vehicles]
        avg_speed = float(np.median(speeds))  # Robust to outliers
        congestion = self._compute_congestion_score(active_vehicles, avg_speed)
        
        # Update session histories
        self.speed_history.append(avg_speed)
        self.congestion_history.append(congestion)
        
        session_avg_speed = float(np.mean(self.speed_history)) if self.speed_history else 0.0
        session_avg_congestion = float(np.mean(self.congestion_history)) if self.congestion_history else 0.0
        
        return {
            "average_speed_kmh": round(avg_speed, 1),
            "session_average_speed_kmh": round(session_avg_speed, 1),
            "congestion_score": congestion,
            "session_average_congestion_score": round(session_avg_congestion, 3),
            "vehicle_count": len(active_vehicles),
            "total_vehicles_cumulative": 0, # This is typically tracked in a separate counter
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
        if (
            self.config.get("lane_detection", {}).get("enabled", False)
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

        # 3. Tracking (pass detections without embeddings first)
        dets_for_tracker = [(d[0], d[1], d[2], None) for d in detections]
        
        # Capture current statuses as 'previous' before updating the tracker
        # We use a copy of the tracker's data or our own persistent mapping
        current_statuses = {tid: track.get("status", "unknown") 
                           for tid, track in self.tracker.vehicle_data.items()} if hasattr(self.tracker, 'vehicle_data') else {}

        vehicle_data = self.tracker.update(dets_for_tracker, current_time, frame.shape).copy()

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
                    # Transformer unavailable — fall back to pixel velocity
                    if not self._homography_fallback_warned:
                        logger.warning(f"[{self.feed_id}] Homography unavailable, using pixel-based speed (calibration recommended)")
                        self._homography_fallback_warned = True
                    track["speed"] = self._pixel_based_speed(track)

                # Physical speed cap to prevent anomalies (e.g., 180 km/h)
                track["speed"] = min(track["speed"], 180.0)

                # Filtering for visualisation
                if track["status"] == "active":
                    vis_tracks[tid] = track
                    if (self.local_ocr or self.preprocessor) and track.get("confidence", 0) > 0.7:
                        bbox = track.get("bbox")
                        if bbox:
                            x1, y1, x2, y2 = map(int, bbox)
                            roi = frame[
                                max(0, y1):min(frame.shape[0], y2),
                                max(0, x1):min(frame.shape[1], x2),
                            ]
                            if roi.size > 0:
                                self.ocr_executor.submit(self._run_ocr, tid, roi)
                elif track["status"] == "predicting":
                    if (current_time - track["last_seen"]) < self.predict_timeout:
                        vis_tracks[tid] = track

        # Compute feed-level metrics (average speed, congestion)
        feed_metrics = self._compute_feed_metrics(vis_tracks)
        self._save_vehicle_data(vis_tracks, feed_metrics)
        self._process_ocr_results(vehicle_data)

        return vis_tracks, self.cached_lane_boundaries, self.last_detected_lane_lines

    def _run_ocr(self, tid: str, roi: np.ndarray):
        """Worker function for OCR executor."""
        try:
            text = None
            # 1. Try Gemini OCR via preprocessor if available
            if self.preprocessor:
                text = self.preprocessor.preprocess_and_ocr(roi)
            
            # 2. Fallback to local OCR if Gemini failed or is disabled
            if not text and self.local_ocr:
                text = self.local_ocr.process(roi)

            if text:
                try:
                    self.ocr_results_queue.put_nowait({"track_id": tid, "plate_text": text})
                except queue.Full:
                    logger.warning(f"[{self.feed_id}] OCR results queue full. Dropping result for {tid}.")
        except Exception as e:
            logger.error(f"OCR processing failed for {tid}: {e}")

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
            if bbox is None or centroid is None:
                continue

            try:
                self.db_queue.put_nowait({
                    "type": "vehicle_data",
                    "feed_id": self.feed_id,
                    "vehicle_id": str(vehicle_id),
                    "global_vehicle_id": str(data.get("global_vehicle_id", "")),
                    "timestamp": float(now),
                    "bbox": [float(x) for x in bbox],
                    "centroid": [float(x) for x in centroid],
                    "speed": float(data.get("speed", 0.0)),
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
                
                # Recompute homography if resolution changed or calibration updated
                if "calibration" in v_cfg_update or res_changed:
                    calib_cfg = v_cfg_update.get("calibration", v_cfg.get("calibration", {}))
                    self._update_homography(calib_cfg, resolution=res)
                    self.transformer.update_calibration(calib_cfg)

            if "ocr_engine" in updates:
                # Re-initialize OCR if enabled/disabled or settings changed
                self._init_ocr()

            if "roi" in updates:
                roi_points = updates["roi"]
                if isinstance(roi_points, list):
                    self.roi_polygon_points = roi_points
                    self._initialize_roi_mask(res)
                    self.detector.initialize_roi(res, self.roi_polygon_points)