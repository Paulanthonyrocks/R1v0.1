import cv2
import logging
import time
import math
import numpy as np
import torch
import queue
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
        db_queue: Any,
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
        self.detector = DetectionEngine(str(self.model_path), self.config, self.device, preloaded_model=preloaded_model)
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
            
        res = self.config.get("vehicle_detection", {})
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

    def _preprocess_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, bool, int, int]:
        """
        Preprocesses the frame for inference.
        Can be extended to implement ROI cropping to improve performance/accuracy.
        Returns: (processed_frame, roi_enabled, x_offset, y_offset)
        """
        # Currently, we use the full frame.
        return frame, False, 0, 0

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
        
        # 3. Tracking (Optimized: Pass detections without embeddings first)
        dets_for_tracker = [(d[0], d[2], d[1], None) for d in detections]
        self.vehicle_data = self.tracker.update(dets_for_tracker, current_time, frame.shape)

        # 4. Selective ReID Enrichment (Batch)
        if self.reid_embedder:
            needs_emb = []
            for tid, track in self.vehicle_data.items():
                # Extract if: new track OR periodic update (every 30 frames)
                if tid not in self.vehicle_data or track.get("last_reid_update", -1) == -1 or (frame_index - track.get("last_reid_update", -1)) >= 30:
                    bbox = track.get("bbox")
                    if bbox:
                        x1, y1, x2, y2 = map(int, bbox)
                        roi = frame[y1:y2, x1:x2]
                        if roi.size > 0:
                            needs_emb.append((tid, roi))
            
            if needs_emb:
                tids, rois = zip(*needs_emb)
                embeddings = self.reid_embedder.get_batch_embeddings(list(rois))
                for tid, emb in zip(tids, embeddings):
                    if emb is not None:
                        self.vehicle_data[tid]["embedding"] = emb
                        self.vehicle_data[tid]["last_reid_update"] = frame_index

        # 5. Metadata Processing (Vectorized)
        vis_tracks = {}
        if self.vehicle_data:
            # Collect all centroids for batch transformation
            tids = list(self.vehicle_data.keys())
            centroids = np.array([
                [(track["bbox"][0] + track["bbox"][2])/2, (track["bbox"][1] + track["bbox"][3])/2]
                for track in self.vehicle_data.values()
            ], dtype=np.float32)
            
            ground_positions = self.transformer.pixel_to_ground(centroids)
            
            for idx, tid in enumerate(tids):
                track = self.vehicle_data[tid]
                if ground_positions is not None:
                    track["ground_coordinates"] = ground_positions[idx]
                
                # --- Calculate Speed (km/h) ---
                if "ground_coordinates" in track:
                    curr_gx, curr_gy = track["ground_coordinates"]
                    prev_ground_pos = track.get("prev_ground_pos")
                    prev_t = track.get("prev_t")
                    
                    if prev_ground_pos and prev_t:
                        prev_gx, prev_gy = prev_ground_pos
                        dt = current_time - prev_t
                        if dt > 0:
                            dist = math.sqrt((curr_gx - prev_gx)**2 + (curr_gy - prev_gy)**2)
                            raw_speed = (dist / dt) * 3.6
                            
                            # Apply EWMA smoothing
                            prev_speed = track.get("speed", raw_speed)
                            track["speed"] = (self.ewma_alpha * raw_speed) + ((1 - self.ewma_alpha) * prev_speed)
                        else:
                            track["speed"] = track.get("speed", 0.0)
                    else:
                        # Fallback to pixel-based for first frame
                        vx = track.get("vx", 0.0)
                        vy = track.get("vy", 0.0)
                        pixel_speed = math.sqrt(vx**2 + vy**2)
                        track["speed"] = (pixel_speed / self.pixels_per_meter) * 3.6 if self.pixels_per_meter > 0 else 0.0
                    
                    track["prev_ground_pos"] = (curr_gx, curr_gy)
                    track["prev_t"] = current_time
                else:
                    # Fallback if transformer fails
                    vx = track.get("vx", 0.0)
                    vy = track.get("vy", 0.0)
                    pixel_speed = math.sqrt(vx**2 + vy**2)
                    track["speed"] = (pixel_speed / self.pixels_per_meter) * 3.6 if self.pixels_per_meter > 0 else 0.0
                
                # Simple Filtering for Visualization
                if track["status"] == "active":
                    vis_tracks[tid] = track
                elif track["status"] == "predicting":
                    if (current_time - track["last_seen"]) < self.predict_timeout:
                        vis_tracks[tid] = track

        self._save_vehicle_data(vis_tracks)
        self._process_ocr_results()

        return vis_tracks, self.cached_lane_boundaries, self.last_detected_lane_lines

    def _save_vehicle_data(self, tracked_vehicles: Dict[str, Dict]):
        for vehicle_id, data in tracked_vehicles.items():
            if self.db_queue:
                try:
                    now = time.time()
                    self.db_queue.put_nowait({
                        "type": "vehicle_data",
                        "feed_id": self.feed_id,
                        "vehicle_id": str(vehicle_id),
                        "global_vehicle_id": str(data.get("global_vehicle_id", "")),
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

    def update_config(self, updates: Dict[str, Any]):
        """Dynamically updates configuration."""
        if "vehicle_detection" in updates:
            v_cfg = updates["vehicle_detection"]
            self.confidence_threshold = v_cfg.get("confidence_threshold", self.confidence_threshold)
            if "calibration" in v_cfg:
                self._update_homography(v_cfg["calibration"])
                self.transformer.update_calibration(v_cfg["calibration"])
        
        if "roi" in updates:
            # Handle ROI updates
            pass
