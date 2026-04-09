import threading
import cv2
import logging
import time
import math
import numpy as np
import torch
import queue
import hashlib
from collections import deque
from multiprocessing import Queue as MPQueue
from typing import Dict, List, Tuple, Optional, Any, Set
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# Modular components
from .detection import DetectionEngine
from .tracking import TrackingManager
from .transforms import CoordinateTransformer, CameraMotionEstimator

# Utility imports
try:
    from ..utils.image_processing import LicensePlatePreprocessor
    from ..utils.lane_detection import process_frame_for_lanes, get_lane_boundaries_from_lines
    from ..utils.local_ocr import LocalOCR
    from ..ml.reid_model import ReIDEmbedder
    from ..services.calibration_monitor import CalibrationMonitor
except ImportError as e:
    # This is a fallback for environments where utils are not yet compiled/available
    logger = logging.getLogger("app.ml")
    logger.error(f"Error importing utils for CoreModule: {e}. System functionality may be limited.")
    LicensePlatePreprocessor = None
    process_frame_for_lanes = None
    get_lane_boundaries_from_lines = None
    LocalOCR = None
    ReIDEmbedder = None
    CalibrationMonitor = None

logger = logging.getLogger("app.ml")

class CoreModule:
    """ The main processing module for a single video feed. """
    # Class-level mapping for vehicle types
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
        self.use_shm = False
        self.feed_id = feed_id
        import copy
        self.config = copy.deepcopy(config) # Avoid mutation of shared config
        self.fps = fps
        self.db_queue = db_queue
        self.gemini_api_key = gemini_api_key
        
        # 1. Load configuration from the provided dictionary
        v_cfg = self.config.get("vehicle_detection", {})
        b_cfg = self.config.get("behavior_analysis", {})
        l_cfg = self.config.get("lane_detection", {})
        
        # Resolve model path relative to project root if necessary
        self.project_root = Path(self.config.get("project_root_dir", ""))
        self.model_path = self.project_root / model_path if not Path(model_path).is_absolute() else Path(model_path)
        
        # 2. Set detection and tracking parameters
        self.confidence_threshold = v_cfg.get("confidence_threshold", 0.4)
        self.proximity_threshold = v_cfg.get("proximity_threshold", 60) # pixels
        self.predict_timeout = v_cfg.get("predict_timeout", 0.4) # seconds
        self.max_active_tracks = v_cfg.get("max_active_tracks", 50)
        self.reid_interval = v_cfg.get("reid_interval_frames", 30)

        # 3. Initialize modular processing engines
        self.device = self._check_gpu_availability()
        self.detector = DetectionEngine(str(self.model_path), self.config, self.device)
        self.detector.load_model(preloaded_model)
        
        res = v_cfg.get("frame_resolution", [640, 480])
        self.roi_polygon_points = self.config.get("roi_processing", {}).get("polygon_points", None)
        self._initialize_roi_mask(res)
        self.detector.initialize_roi(res, self.roi_polygon_points)
        
        self.tracker = TrackingManager(self.config, self.fps)
        self.vehicle_data = {} # Current state of tracked vehicles
        
        # Motion compensation and calibration
        self.motion_estimator = CameraMotionEstimator()
        self.calib_monitor = CalibrationMonitor(self.feed_id, self.config) if CalibrationMonitor else None
        self._calibration_initialized = False
        
        calib_cfg = v_cfg.get("calibration", {})
        self.transformer = CoordinateTransformer(calib_cfg)
        self.homography_matrix = None
        self._update_homography(calib_cfg)

        # 4. Initialize state variables and helper utilities
        self.reid_embedder = preloaded_reid or (ReIDEmbedder(self.config) if (v_cfg.get("reid_enabled", True) and "ReIDEmbedder" in globals() and ReIDEmbedder is not None) else None)
        self.ocr_executor = ThreadPoolExecutor(max_workers=2)
        self.ocr_results_queue = queue.Queue()
        
        # Fix: Use BoundedSemaphore for thread-safe OCR capacity management
        self.ocr_semaphore = threading.BoundedSemaphore(4)
        
        self.last_detected_lane_lines = None
        self.cached_lane_boundaries = []
        self.last_lane_detection_frame = -1
        self.lane_detection_interval = l_cfg.get("detection_interval", 1.0) # seconds

        # CRITICAL #1 FIX: Use a monotonic clock for physics calculations
        self._last_mono_time = time.monotonic()
        
        # 5. Behavior and Speed related parameters
        self.pixels_per_meter = v_cfg.get("pixels_per_meter", 10.0)
        self.ewma_alpha = b_cfg.get("speed_smoothing_factor", 0.3)
        self.speed_limit = b_cfg.get("speed_limit_kmh", 60)
        self.accel_threshold_mps2 = b_cfg.get("acceleration_threshold_mps2", 2.0)
        self.stopped_speed_threshold_kmh = b_cfg.get("stopped_speed_threshold_kmh", 5.0)
        
        # OCR specific initializations
        self.preprocessor = None
        self.local_ocr = None
        
        self._pending_snapshot_incident_id = None
        
        self._last_save_time: Dict[str, float] = {} # Tracks when data was last saved for each vehicle
        
        if self.config.get("ocr_engine", {}).get("enabled", False):
            self._init_ocr()

    def _check_gpu_availability(self) -> str:
        use_gpu = self.config.get("performance", {}).get("gpu_acceleration", False)
        if use_gpu and torch.cuda.is_available():
            logger.info(f"[{self.feed_id}] GPU acceleration enabled and detected. Using CUDA.")
            return "0" # Assume first GPU device
        return "cpu"

    def _initialize_roi_mask(self, resolution: List[int]):
        w, h = resolution
        self.roi_mask = np.ones((h, w), dtype=np.uint8) * 255
        if self.roi_polygon_points:
            points_np = np.array(self.roi_polygon_points, dtype=np.int32)
            mask = np.zeros((h, w), dtype=np.uint8)
            cv2.fillPoly(mask, [points_np], 255)
            self.roi_mask = cv2.bitwise_and(self.roi_mask, mask)
        
        if exclusion := self.config.get("roi_processing", {}).get("exclusion_zones", []):
            for zone in exclusion:
                zone_np = (np.array(zone, dtype=np.float32) * [w, h]).astype(np.int32)
                cv2.fillPoly(self.roi_mask, [zone_np], 0)

    def _update_homography(self, calibration_cfg: Dict):
        if not calibration_cfg or "image_points" not in calibration_cfg: return
        img_pts = np.array(calibration_cfg["image_points"], dtype=np.float32)
        world_pts = np.array(calibration_cfg.get("world_points", []), dtype=np.float32)
        if len(img_pts) < 4 or len(world_pts) < 4: return
        self.homography_matrix, _ = cv2.findHomography(img_pts, world_pts)
        logger.info(f"[{self.feed_id}] Homography matrix recalibrated.")

    def _init_ocr(self):
        if self.config.get("ocr_engine", {}).get("use_local", True) and LocalOCR is not None:
            try: self.local_ocr = LocalOCR(self.config)
            except Exception as e: logger.error(f"Failed to initialize Local OCR: {e}")

    def predict_only(self, width: int, height: int) -> Dict[str, Dict]:
        current_mono_time = time.monotonic()
        dt = current_mono_time - self._last_mono_time
        self._last_mono_time = current_mono_time
        skip_factor = max(0, int(dt * self.fps) - 1)

        self.vehicle_data = self.tracker.update([], dt, (height, width), skip_factor=skip_factor)
        
        vis_tracks = {}
        for tid, track in self.vehicle_data.items():
            if track.get("status") == "predicting" and (cx := (track["bbox"][0] + track["bbox"][2]) / 2) and (cy := (track["bbox"][1] + track["bbox"][3]) / 2):
                if ground_pos := self.transformer.pixel_to_ground(cx, cy): track["ground_coordinates"] = ground_pos
                vis_tracks[tid] = track
            elif track.get("status") == "active": vis_tracks[tid] = track
        return vis_tracks
        
    def detect_and_track(
        self, frame: Optional[np.ndarray], frame_index: int, **kwargs
    ) -> Tuple[Dict, List, Any, Dict]:
        # CRITICAL #1 FIX: Use monotonic clock for delta-time (dt)
        current_mono_time = time.monotonic()
        dt = current_mono_time - self._last_mono_time
        self._last_mono_time = current_mono_time
        timestamp = kwargs.get("timestamp") or time.time()

        if frame is None or frame.size == 0:
            return {}, [], None, {"is_calibrated": self.transformer.homography_matrix is not None}
        
        drift_score, is_drifted = (self.calib_monitor.check_drift(frame) if self.calib_monitor else (0.0, False, None))[:2]

        detections = self._run_detection(frame, kwargs.get("external_detections"), frame_index, kwargs.get("selected_ids"))

        # Update tracker with new detections and get tracked vehicle data
        self.vehicle_data = self.tracker.update(detections, dt, frame.shape)
        
        vis_tracks = {}
        for tid, track in self.vehicle_data.items():
            if track["status"] == "deleted": continue # Skip processing for deleted tracks

            self._update_track_physics(track, current_mono_time, dt, timestamp)
            
            # Submit for OCR if applicable
            self._submit_for_ocr(track, frame, frame_index)
            
            if track["status"] == "active" or (track["status"] == "predicting" and (current_mono_time - track.get("last_seen_mono", 0)) < self.predict_timeout):
                vis_tracks[tid] = track

        self._save_vehicle_data(vis_tracks) # Persist data to DB queue
        self._process_ocr_results() # Check for and apply any new OCR results
        
        # Handle snapshot saving if requested
        if self._pending_snapshot_incident_id:
            self._save_snapshot(frame, self._pending_snapshot_incident_id)
            self._pending_snapshot_incident_id = None
            
        return vis_tracks, self.cached_lane_boundaries, self.last_detected_lane_lines, {"drift_score": drift_score, "is_drifted": is_drifted, "is_calibrated": True}

    def _run_detection(self, frame: np.ndarray, external_detections, frame_index, selected_ids) -> List:
        # Preprocess frame (e.g., ROI cropping)
        processed_frame, is_cropped, x_off, y_off = self._preprocess_frame(frame)
        raw_detections = self.detector.detect(processed_frame, self.confidence_threshold) if external_detections is None else external_detections
        
        # Adjust detection coordinates if cropped
        detections = [((b[0] + x_off, b[1] + y_off, b[2] + x_off, b[3] + y_off), c, conf) for b, c, conf in raw_detections] if is_cropped else raw_detections
        
        # Filter detections based on ROI and other criteria
        detections = self._filter_detections(detections)

        # Fuse overlapping detections for the same class
        detections = self.detector.fuse(detections)

        # Enrich detections with ReID embeddings if enabled and applicable
        return self._enrich_with_reid(detections, frame, frame_index, selected_ids)

    def _filter_detections(self, detections: List[Tuple]) -> List[Tuple]:
        v_cfg = self.config.get("vehicle_detection", {})
        min_area, min_dim, max_ratio = v_cfg.get("min_bbox_area", 0), v_cfg.get("min_bbox_dimension", 0), v_cfg.get("max_aspect_ratio", 100.0)
        fh, fw = self.roi_mask.shape[:2]

        filtered_detections = []
        for det in detections:
            bbox, _, _ = det
            x1, y1, x2, y2 = map(int, bbox)
            w, h = x2 - x1, y2 - y1
            if w * h < min_area or w < min_dim or h < min_dim or (max(w,h) / max(1,min(w,h))) > max_ratio: continue
            
            # ROI check
            cx1, cy1, cx2, cy2 = max(0, x1), max(0, y1), min(fw, x2), min(fh, y2)
            if cx2 > cx1 and cy2 > cy1:
                box_area_in_roi = np.sum(self.roi_mask[cy1:cy2, cx1:cx2] > 0)
                if (box_area_in_roi / max(1, (x2 - x1) * (y2 - y1))) < 0.3: continue
            else: continue
            filtered_detections.append(det)
        return filtered_detections

    def _enrich_with_reid(self, detections: List, frame: np.ndarray, frame_index: int, selected_ids: Optional[Set]) -> List:
        if not self.reid_embedder: return [(d[0], d[1], d[2], None) for d in detections]

        # Decide if ReID should run based on interval or if specific IDs are selected for update
        should_run_reid = (frame_index % max(1, self.reid_interval) == 0) or selected_ids
        if not should_run_reid or not detections: return [(d[0], d[1], d[2], None) for d in detections]

        rois, indices = [], []
        for idx, (bbox, _, _) in enumerate(detections):
            if selected_ids and not any(self.tracker._bbox_iou(bbox, t["bbox"]) > 0.5 for tid, t in self.tracker.vehicle_data.items() if tid in selected_ids):
                continue
            x1, y1, x2, y2 = map(int, bbox)
            roi = frame[max(0, y1):min(frame.shape[0], y2), max(0, x1):min(frame.shape[1], x2)]
            if roi.size > 0 and self._calculate_image_quality(roi) > 30.0:
                rois.append(roi); indices.append(idx)
        
        emb_map = {}
        if rois:
            try: embs = self.reid_embedder.get_batch_embeddings(rois); emb_map = {original_idx: embs[i] for i, original_idx in enumerate(indices)}
            except Exception as e: logger.error(f"ReID batch error: {e}")

        return [(d[0], d[1], d[2], emb_map.get(idx)) for idx, d in enumerate(detections)]

    def _update_track_physics(self, track: Dict, current_mono_time: float, dt: float, timestamp: float):
        cx, cy = track["centroid"]
        if ground_pos := self.transformer.pixel_to_ground(cx, cy): track["ground_coordinates"] = ground_pos
        
        # CRITICAL #1 FIX: Pass monotonic time difference to speed estimation
        track["speed"] = self._estimate_speed_kalman(track, dt)
        
        # Use dt for acceleration calculation
        if dt > 1e-6: track["acceleration"] = ((track["speed"] - track.get("prev_speed", 0.0)) * 1000 / 3600) / dt
        else: track["acceleration"] = 0.0
        
        track["prev_speed"] = track["speed"]
        track["last_update_ts"] = timestamp
        track["lane"] = self._estimate_lane((cx, cy))

    def _submit_for_ocr(self, track: Dict, frame: np.ndarray, frame_index: int):
        if self.local_ocr and track["status"] == "active" and "license_plate" not in track and frame_index % 10 == 0:
            x1, y1, x2, y2 = map(int, track["bbox"])
            roi = frame[max(0, y1):min(frame.shape[0], y2), max(0, x1):min(frame.shape[1], x2)].copy()
            if roi.size > 0 and self._calculate_image_quality(roi) > 150.0:
                # Fix: Use semaphore to manage OCR capacity in a thread-safe way
                if self.ocr_semaphore.acquire(blocking=False):
                    future = self.ocr_executor.submit(self._run_ocr_task, track["id"], roi)
                    future.add_done_callback(lambda f: self.ocr_semaphore.release())

    def _save_snapshot(self, frame: np.ndarray, incident_id: str):
        # Fix: Offload synchronous disk I/O to the DB queue to avoid blocking the main loop
        try:
            fmt, qual = self.config.get("snapshot_format", "webp"), self.config.get("snapshot_quality", 80)
            fname = f"snapshot_{self.feed_id}_{incident_id}_{int(time.time())}.{fmt}"
            
            # We send the frame to the DB queue. The consumer of the db_queue 
            # (which is a separate process) should handle the actual writing to disk.
            if self.db_queue:
                self.db_queue.put_nowait({
                    "type": "save_snapshot",
                    "feed_id": self.feed_id,
                    "incident_id": incident_id,
                    "frame": frame,
                    "filename": fname,
                    "format": fmt,
                    "quality": qual
                })
        except Exception as e: logger.error(f"[{self.feed_id}] Failed to queue snapshot: {e}")

    def _run_ocr_task(self, tid, roi):
        if self.local_ocr and (plate_result := self.local_ocr.read_plate(roi)):
            self.ocr_results_queue.put({"track_id": tid, "plate_text": plate_result[0], "confidence": plate_result[1]})

    def _estimate_speed_kalman(self, track: Dict, dt: float) -> float:
        if not (kf := track.get("kalman_filter")): return 0.0
        try:
            raw_speed_kmph = 0.0
            cx, cy = kf.x[0][0], kf.x[1][0]
            
            current_ground = self.transformer.pixel_to_ground(cx, cy)
            prev_ground = track.get("ground_centroid")
            
            # CRITICAL #1 FIX: Use monotonic dt for speed calculation
            if current_ground and prev_ground:
                raw_speed_kmph = (math.sqrt((current_ground[0] - prev_ground[0])**2 + (current_ground[1] - prev_ground[1])**2) / dt) * 3.6
            
            if current_ground: track["ground_centroid"] = current_ground
            
            # Fallback to pixel velocity if ground truth is not available
            if raw_speed_kmph == 0.0 and (ppm := self._get_dynamic_pixels_per_meter(cy)) > 0:
                vx, vy = kf.x[4][0], kf.x[5][0]
                raw_speed_kmph = (np.sqrt(vx**2 + vy**2) / ppm) * 3.6
                
            prev_smoothed = track.get("smoothed_speed", raw_speed_kmph) # Initialize with current raw speed
            new_smoothed = (self.ewma_alpha * raw_speed_kmph) + ((1 - self.ewma_alpha) * prev_smoothed)
            track["smoothed_speed"] = new_smoothed
            return round(float(max(0, new_smoothed)), 1)
        except Exception as e: 
            logger.warning(f"Speed estimation error for track {track.get('id')}: {e}")
            return 0.0

    def _get_dynamic_pixels_per_meter(self, y_pixel: float) -> float:
        if self.homography_matrix is not None and (p1 := self.transformer.pixel_to_ground(100, y_pixel)) and (p2 := self.transformer.pixel_to_ground(110, y_pixel)) and (d := math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2)) > 1e-3:
             return 10.0/d
        return self.pixels_per_meter

    def _save_vehicle_data(self, tracked_vehicles: Dict[str, Dict]):
        from ..utils.encryption import encryption_manager
        now = time.time()
        for vid, data in tracked_vehicles.items():
            if (now - self._last_save_time.get(vid, 0)) < 0.5: continue # Rate limit DB writes per vehicle
            if self.db_queue and data.get("status") == "active":
                try: 
                    # Fix: Encrypt license plate PII before sending to DB queue
                    if "license_plate" in data:
                        data["license_plate"] = encryption_manager.encrypt(data["license_plate"])
                    
                    self.db_queue.put_nowait({"type": "vehicle_data", "feed_id": self.feed_id, "data": data}); 
                    self._last_save_time[vid] = now
                except queue.Full: pass # Non-critical, can be dropped

    def _process_ocr_results(self):
        from collections import Counter
        try:
            while result := self.ocr_results_queue.get_nowait():
                if (tid := result.get("track_id")) and tid in self.vehicle_data:
                    track = self.vehicle_data[tid]
                    if "plate_candidates" not in track: track["plate_candidates"] = deque(maxlen=10)
                    track["plate_candidates"].append((result["plate_text"], result["confidence"]))
                    if candidates := [t for t, c in track["plate_candidates"]]: track["license_plate"] = Counter(candidates).most_common(1)[0][0]
        except queue.Empty: pass

    def _estimate_lane(self, centroid: Tuple[float, float]) -> int:
        if not centroid: return -1

        # ISSUE 2 FIX: Use ground coordinates for lane estimation
        ground_pos = self.transformer.pixel_to_ground(centroid[0], centroid[1])
        if not ground_pos:
            return -1 # Cannot determine lane without ground projection

        # Assumes ground_pos[0] is the lateral position (x-coordinate in meters)
        # Lane boundaries should be defined in the config in meters from the edge of the road
        lane_config = self.config.get("lane_detection", {})
        world_lane_boundaries = lane_config.get("world_lane_boundaries_m", [])

        if not world_lane_boundaries:
             # Fallback if not defined, but this is less accurate
            num_lanes = lane_config.get("num_lanes", 4)
            lane_width_m = lane_config.get("default_lane_width_m", 3.5)
            world_lane_boundaries = [i * lane_width_m for i in range(num_lanes + 1)]

        lateral_pos_m = ground_pos[0]
        
        # Find which lane the lateral position falls into
        for i in range(len(world_lane_boundaries) - 1):
            if world_lane_boundaries[i] <= lateral_pos_m < world_lane_boundaries[i+1]:
                return i + 1
        
        return -1 # Outside defined lanes


    def _calculate_image_quality(self, roi: np.ndarray) -> float:
        if roi.size == 0: return 0.0
        try:
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            # Fix: Apply GaussianBlur to reduce sensor noise (especially at night)
            # before calculating Laplacian variance.
            blurred = cv2.GaussianBlur(gray, (3, 3), 0)
            return cv2.Laplacian(blurred, cv2.CV_64F).var()
        except cv2.error: return 0.0

    def cleanup(self):
        if self.ocr_executor: self.ocr_executor.shutdown(wait=False)
        logger.info(f"[{self.feed_id}] CoreModule cleaned up.")

    def _preprocess_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, bool, int, int]:
        if not self.config.get("roi_processing", {}).get("enabled", True) or self.roi_polygon_points is None: return frame, False, 0, 0
        x, y, w, h = cv2.boundingRect(np.array(self.roi_polygon_points, np.int32))
        return (frame[y:y+h, x:x+w], True, x, y) if w>0 and h>0 else (frame, False, 0, 0)

    def update_config(self, updates: Dict[str, Any]):
        # Deep merge would be better, but for now, simple updates.
        if "vehicle_detection" in updates: 
            self.config["vehicle_detection"].update(updates["vehicle_detection"])
            v_cfg = self.config["vehicle_detection"]
            self.confidence_threshold = v_cfg.get("confidence_threshold", self.confidence_threshold)
            if "calibration" in v_cfg: self._update_homography(v_cfg["calibration"]); self.transformer.update_calibration(v_cfg["calibration"])
        if "roi_processing" in updates:
            self.config["roi_processing"].update(self.config["roi_processing"])
            self.roi_polygon_points = self.config["roi_processing"].get("polygon_points")
            res = self.config.get("vehicle_detection", {}).get("frame_resolution", [640, 480])
            self._initialize_roi_mask(res)
        logger.info(f"[{self.feed_id}] CoreModule config updated.")
