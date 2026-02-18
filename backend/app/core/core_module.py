import cv2
import logging
import time
import math
import numpy as np
import torch
import queue
from multiprocessing import Queue as MPQueue
from typing import Dict, List, Tuple, Optional, Any
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
    logger = logging.getLogger("app.ml")
    logger.error("Error importing utils for CoreModule. System functionality may be limited.")
    LicensePlatePreprocessor = None
    process_frame_for_lanes = None
    get_lane_boundaries_from_lines = None
    LocalOCR = None
    ReIDEmbedder = None

logger = logging.getLogger("app.ml")

class CoreModule:
    # Vehicle type mapping
    vehicle_type_map = {
        0: "person", 1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"
    }

    def __init__(
        self,
        feed_id: str,
        model_path: str,
        config: Dict,
        fps: int,
        db_queue: MPQueue,
        gemini_api_key: Optional[str] = None,
        model_type: str = "yolo",
        preloaded_model: Optional[Any] = None,
        preloaded_reid: Optional[Any] = None,
    ):
        self.feed_id = feed_id
        import copy
        self.config = copy.deepcopy(config)
        self.fps = fps
        self.db_queue = db_queue
        self.gemini_api_key = gemini_api_key
        
        # 1. Configuration sections
        v_cfg = self.config.get("vehicle_detection", {})
        b_cfg = self.config.get("behavior_analysis", {})
        l_cfg = self.config.get("lane_detection", {})
        
        self.project_root = Path(self.config.get("project_root_dir", ""))
        self.model_path = self.project_root / model_path if not Path(model_path).is_absolute() else Path(model_path)
        
        # 2. Thresholds & Params
        self.confidence_threshold = v_cfg.get("confidence_threshold", 0.4)
        self.proximity_threshold = v_cfg.get("proximity_threshold", 60)
        self.predict_timeout = v_cfg.get("predict_timeout", 0.4)
        self.max_active_tracks = v_cfg.get("max_active_tracks", 50)

        # 3. Modular Engines Init
        self.device = self._check_gpu_availability()
        self.detector = DetectionEngine(str(self.model_path), self.config, self.device)
        self.detector.load_model()
        
        res = v_cfg.get("frame_resolution", [640, 480])
        self.roi_polygon_points = self.config.get("roi_processing", {}).get("polygon_points", None)
        self.detector.initialize_roi(res, self.roi_polygon_points)
        
        self.tracker = TrackingManager(self.config, self.fps)
        self.vehicle_data = self.tracker.vehicle_data
        
        calib_cfg = v_cfg.get("calibration", {})
        self.transformer = CoordinateTransformer(calib_cfg)
        self.homography_matrix = None
        self._update_homography(calib_cfg)

        # 4. State & Helpers
        self.reid_embedder = preloaded_reid or (ReIDEmbedder(self.config) if v_cfg.get("reid_enabled", True) else None)
        self.ocr_executor = ThreadPoolExecutor(max_workers=2)
        self.ocr_results_queue = queue.Queue()
        
        self.last_detected_lane_lines = None
        self.cached_lane_boundaries = []
        self.last_lane_detection_frame = -1
        self.lane_detection_interval = l_cfg.get("detection_interval", 1.0)
        
        # 5. Behavior & Speed
        self.pixels_per_meter = v_cfg.get("pixels_per_meter", 10.0)
        self.ewma_alpha = b_cfg.get("speed_smoothing_factor", 0.3)
        self.speed_limit = b_cfg.get("speed_limit_kmh", 60)
        self.accel_threshold_mps2 = b_cfg.get("acceleration_threshold_mps2", 2.0)
        self.stopped_speed_threshold_kmh = b_cfg.get("stopped_speed_threshold_kmh", 5.0)
        
        self.preprocessor = None # Gemini
        self.local_ocr = None
        self._reid_updates_this_frame = 0 # Budget control
        
        if self.config.get("ocr_engine", {}).get("enabled", False):
            self._init_ocr()

    def _check_gpu_availability(self) -> str:
        """Checks for GPU availability for YOLO and engines."""
        if torch.cuda.is_available():
            logger.info(f"[{self.feed_id}] GPU detected. Using CUDA.")
            return "0" 
        return "cpu"

    def _initialize_roi_mask(self, resolution: List[int]):
        """Initializes ROI and exclusion masks once per resolution change."""
        w, h = resolution
        self.roi_mask = np.ones((h, w), dtype=np.uint8) * 255
        
        if self.roi_polygon_points:
            points_np = np.array(self.roi_polygon_points, dtype=np.int32)
            mask = np.zeros((h, w), dtype=np.uint8)
            cv2.fillPoly(mask, [points_np], 255)
            self.roi_mask = cv2.bitwise_and(self.roi_mask, mask)
        
        exclusion = self.config.get("roi_processing", {}).get("exclusion_zones", [])
        if exclusion:
            for zone in exclusion:
                zone_np = (np.array(zone, dtype=np.float32) * [w, h]).astype(np.int32)
                cv2.fillPoly(self.roi_mask, [zone_np], 0)

    def _update_homography(self, calibration_cfg: Dict):
        """Perspective transformation for distance/speed math."""
        if not calibration_cfg or "image_points" not in calibration_cfg:
            return
        
        img_pts = np.array(calibration_cfg["image_points"], dtype=np.float32)
        world_pts = np.array(calibration_cfg.get("world_points", []), dtype=np.float32)
        
        if len(img_pts) < 4 or len(world_pts) < 4:
            return
            
        res = self.config.get("vehicle_detection", {}).get("frame_resolution", [640, 480])
        if np.max(img_pts) <= 1.0:
            img_pts *= [res[0], res[1]]
            
        self.homography_matrix, _ = cv2.findHomography(img_pts, world_pts)
        logger.info(f"[{self.feed_id}] Homography matrix recalibrated.")

    def _init_ocr(self):
        """Initializes OCR engines based on configuration."""
        ocr_cfg = self.config.get("ocr_engine", {})
        if ocr_cfg.get("use_gemini_ocr", False) and self.gemini_api_key:
            try:
                from ..utils.image_processing import LicensePlatePreprocessor
                self.preprocessor = LicensePlatePreprocessor(self.gemini_api_key)
            except Exception as e:
                logger.error(f"Failed to initialize Gemini OCR: {e}")
        
        if ocr_cfg.get("use_local", True):
            try:
                from ..utils.local_ocr import LocalOCR
                self.local_ocr = LocalOCR(self.config)
            except Exception as e:
                logger.error(f"Failed to initialize Local OCR: {e}")

    def detect_and_track(
        self,
        frame: Optional[np.ndarray],
        frame_index: int,
        confidence_threshold: Optional[float] = None,
        proximity_threshold: Optional[int] = None,
        track_timeout: Optional[int] = None,
        external_detections: Optional[List[Tuple]] = None,
        timestamp: Optional[float] = None,
    ) -> Tuple[Dict[str, Dict], List[int], Any]:
        """Orchestrates detection and tracking using modular engines."""
        if frame is None or frame.size == 0:
            return {}, self.cached_lane_boundaries, self.last_detected_lane_lines

        current_time = timestamp if timestamp is not None else time.time()
        
        # 1. Lane Detection (Periodic)
        if (self.config.get("lane_detection", {}).get("enabled", False) and 
            process_frame_for_lanes and 
            (frame_index - self.last_lane_detection_frame) >= self.lane_detection_interval):
            try:
                lines = process_frame_for_lanes(frame, self.config)
                self.last_detected_lane_lines = lines
                if lines and get_lane_boundaries_from_lines:
                    self.cached_lane_boundaries = get_lane_boundaries_from_lines(frame.shape[1], lines, self.config)
                    self.last_lane_detection_frame = frame_index
            except Exception as e:
                logger.warning(f"Lane detection failed: {e}")

        # 2. Detection (Skip if external provided)
        if external_detections is not None:
            detections = external_detections
        else:
            detections = self.detector.detect(frame, 0.1)
        
        # 3. Enrichment (ReID Embeddings)
        enriched_detections = []
        for bbox, cls, dconf in detections:
            emb = None
            if self.reid_embedder:
                x1, y1, x2, y2 = map(int, bbox)
                roi = frame[y1:y2, x1:x2]
                if roi.size > 0:
                    emb = self.reid_embedder.get_embedding(roi)
            enriched_detections.append((bbox, cls, dconf, emb))

        # 4. Tracking
        self.vehicle_data = self.tracker.update(enriched_detections, current_time, frame.shape)
        
        # 5. Metadata Processing
        vis_tracks = {}
        for tid, track in self.vehicle_data.items():
            cx, cy = (track["bbox"][0] + track["bbox"][2])/2, (track["bbox"][1] + track["bbox"][3])/2
            ground_pos = self.transformer.pixel_to_ground(cx, cy)
            if ground_pos:
                track["ground_coordinates"] = ground_pos
            
            # Estimate Speed
            prev_time = track.get("last_speed_update_time", current_time - (1.0/self.fps))
            track["speed"] = self._estimate_speed_kalman(track, current_time, prev_time)
            track["last_speed_update_time"] = current_time

            # Simple Filtering for Visualization
            if track["status"] == "active":
                vis_tracks[tid] = track
            elif track["status"] == "predicting":
                if (current_time - track["last_seen"]) < self.predict_timeout:
                    vis_tracks[tid] = track

        self._save_vehicle_data(vis_tracks)
        self._process_ocr_results()

        return vis_tracks, self.cached_lane_boundaries, self.last_detected_lane_lines

    def _estimate_speed_kalman(self, track: Dict, current_time: float, prev_time: float) -> float:
        kf = track.get("kalman_filter")
        if not kf:
            return 0.0
        
        try:
            time_diff = current_time - prev_time
            
            # Fix #17: Clamp time_diff to reasonable bounds
            min_dt = 1.0 / (self.fps * 2)  # At least half frame time
            max_dt = 2.0  # Max 2 seconds
            
            time_diff = max(min_dt, min(max_dt, time_diff))

            raw_speed_kmph = 0.0

            # State order: [x, y, w, h, vx, vy, vw, vh]
            cx, cy = kf.x[0][0], kf.x[1][0]
            vx, vy = kf.x[4][0], kf.x[5][0]

            # 1. Prefer Homography-based speed estimation
            if self.homography_matrix is not None:
                # Current position in ground space
                # We use the transformer wrapper which handles the homography internally
                current_ground = self.transformer.pixel_to_ground(cx, cy)
                
                # Estimated previous position in ground space
                # We use the velocity from Kalman Filter to backtrack one step
                prev_ground = self.transformer.pixel_to_ground(cx - vx * time_diff, cy - vy * time_diff)

                if current_ground and prev_ground:
                    dx = current_ground[0] - prev_ground[0]
                    dy = current_ground[1] - prev_ground[1]
                    dist_meters = math.sqrt(dx**2 + dy**2)
                    speed_mps = dist_meters / time_diff
                    raw_speed_kmph = speed_mps * 3.6
                
                # Store ground position for analytics
                if current_ground:
                    track["ground_centroid"] = current_ground

            # 2. Fallback to constant PPM
            if raw_speed_kmph == 0.0:
                pixel_speed_per_sec = np.sqrt(vx**2 + vy**2)
                dynamic_ppm = self._get_dynamic_pixels_per_meter(cy)
                
                speed_mps = (pixel_speed_per_sec / dynamic_ppm) if dynamic_ppm > 0 else 0
                raw_speed_kmph = speed_mps * 3.6

            # --- EWMA Smoothing ---
            prev_smoothed = track.get("smoothed_speed", 0.0)
            if prev_smoothed == 0.0 and raw_speed_kmph > 0:
                new_smoothed = raw_speed_kmph
            else:
                new_smoothed = (self.ewma_alpha * raw_speed_kmph) + ((1 - self.ewma_alpha) * prev_smoothed)

            # Update state
            track["smoothed_speed"] = new_smoothed
            if "speed_history" in track:
                track["speed_history"].append(new_smoothed)

            return round(float(max(0, new_smoothed)), 1)
        except Exception as e:
            logger.warning(f"Speed estimation error: {e}")
            return 0.0

    def _get_dynamic_pixels_per_meter(self, y_pixel: float) -> float:
        """
        Calculates dynamic Pixels Per Meter (PPM) based on the Y-coordinate to account for perspective.
        """
        frame_height = self.config.get("vehicle_detection", {}).get("frame_resolution", [640, 480])[1]
        
        # Calibration point is usually at the bottom (near camera)
        # We assume PPM decreases as we go up the frame (towards the horizon)
        # Simple linear model: PPM(y) = baseline_PPM * (y / frame_height)
        # Clamped to at least 20% of baseline to avoid division by zero or extreme speeds
        factor = max(0.2, y_pixel / frame_height)
        return self.pixels_per_meter * factor

    def _save_vehicle_data(self, tracked_vehicles: Dict[str, Dict]):
        for vehicle_id, data in tracked_vehicles.items():
            if self.db_queue:
                try:
                    now = time.time()
                    self.db_queue.put_nowait({
                        "type": "vehicle_data",
                        "feed_id": self.feed_id,
                        "vehicle_id": str(vehicle_id),
                        "timestamp": float(now),
                        "bbox": [float(x) for x in data["bbox"]],
                        "centroid": [float(x) for x in data["centroid"]],
                        "speed": float(data.get("speed", 0.0)),
                        "license_plate": str(data.get("license_plate", "Unknown")),
                        "class_id": int(data["class_id"]),
                        "class_name": str(self.vehicle_type_map.get(data["class_id"], "unknown")),
                        "confidence": float(data["confidence"]),
                        "status": str(data["status"]),
                        "lane": int(data.get("lane", -1)),
                    })
                except queue.Full:
                    pass

    def _process_ocr_results(self):
        """Drains the OCR results queue and updates vehicle data."""
        try:
            while True:
                result = self.ocr_results_queue.get_nowait()
                tid = result["track_id"]
                if tid in self.vehicle_data:
                    self.vehicle_data[tid]["license_plate"] = result["plate_text"]
        except queue.Empty:
            pass

    def cleanup(self):
        """Shutdown thread pools."""
        if self.ocr_executor:
            self.ocr_executor.shutdown(wait=True)

    def _preprocess_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, bool, int, int]:
        """
        Prepares the frame for inference. If ROI is enabled, it returns a cropped
        version of the frame to the ROI's bounding box to optimize detection.
        """
        roi_cfg = self.config.get("roi_processing", {})
        if not roi_cfg.get("enabled", True) or self.roi_polygon_points is None:
            return frame, False, 0, 0

        # Calculate bounding box of the ROI
        pts = np.array(self.roi_polygon_points, np.int32)
        x, y, w, h = cv2.boundingRect(pts)
        
        # Ensure it's within frame bounds
        fh, fw = frame.shape[:2]
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(fw, x + w), min(fh, y + h)
        
        if x2 <= x1 or y2 <= y1:
            return frame, False, 0, 0
            
        cropped_frame = frame[y1:y2, x1:x2]
        return cropped_frame, True, x1, y1

    def update_config(self, updates: Dict[str, Any]):
        """Dynamically updates configuration."""
        if "vehicle_detection" in updates:
            v_cfg = updates["vehicle_detection"]
            self.confidence_threshold = v_cfg.get("confidence_threshold", self.confidence_threshold)
            if "calibration" in v_cfg:
                self._update_homography(v_cfg["calibration"])
                self.transformer.update_calibration(v_cfg["calibration"])
        
        if "roi" in updates:
            # Handle ROI updates from frontend (usually normalized list of dicts)
            roi_data = updates["roi"]
            if isinstance(roi_data, list):
                new_points = []
                res = self.config.get("vehicle_detection", {}).get("frame_resolution", [640, 480])
                for pt in roi_data:
                    if isinstance(pt, dict) and 'x' in pt and 'y' in pt:
                        new_points.append([pt['x'] * res[0], pt['y'] * res[1]])
                    elif isinstance(pt, (list, tuple)) and len(pt) == 2:
                        # Assume normalized if values <= 1.0
                        if pt[0] <= 1.0 and pt[1] <= 1.0:
                            new_points.append([pt[0] * res[0], pt[1] * res[1]])
                        else:
                            new_points.append(list(pt))
                
                if new_points:
                    self.roi_polygon_points = new_points
                    # Update internal config copy
                    if "roi_processing" not in self.config:
                        self.config["roi_processing"] = {}
                    self.config["roi_processing"]["polygon_points"] = new_points
                    
                    # Re-initialize ROI masks
                    self._initialize_roi_mask(res)
                    self.detector.initialize_roi(res, new_points)
                    logger.info(f"[{self.feed_id}] ROI updated with {len(new_points)} points.")
