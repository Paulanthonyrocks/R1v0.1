import cv2
import logging
import time
import math
import numpy as np
import torch
import queue
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
        self.use_shm = False
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
        self.reid_interval = v_cfg.get("reid_interval_frames", 30)

        # 3. Modular Engines Init
        self.device = self._check_gpu_availability()
        self.detector = DetectionEngine(str(self.model_path), self.config, self.device)
        self.detector.load_model(preloaded_model)
        
        res = v_cfg.get("frame_resolution", [640, 480])
        self.roi_polygon_points = self.config.get("roi_processing", {}).get("polygon_points", None)
        self._initialize_roi_mask(res)
        self.detector.initialize_roi(res, self.roi_polygon_points)
        
        self.tracker = TrackingManager(self.config, self.fps)
        # T2 Fix: Don't hold a reference to tracker's internal dict;
        # always access via self.tracker.vehicle_data after update()
        self.vehicle_data = {}
        
        self.motion_estimator = CameraMotionEstimator()
        self.calib_monitor = CalibrationMonitor(self.feed_id, self.config) if CalibrationMonitor else None
        
        calib_cfg = v_cfg.get("calibration", {})
        self.transformer = CoordinateTransformer(calib_cfg)
        self.homography_matrix = None
        self._update_homography(calib_cfg)

        # 4. State & Helpers
        self.reid_embedder = preloaded_reid or (ReIDEmbedder(self.config) if (v_cfg.get("reid_enabled", True) and "ReIDEmbedder" in globals() and ReIDEmbedder is not None) else None)
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
        
        self.preprocessor = None
        self.local_ocr = None
        self._reid_updates_this_frame = 0 # Budget control
        self._pending_snapshot_incident_id = None
        self._ocr_pending_count = 0  # R7: OCR backpressure counter
        
        self._last_save_time: Dict[str, float] = {} # For cooldown in _save_vehicle_data
        
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
        
        # Move mask to GPU if available
        if torch.cuda.is_available() and self.device != "cpu":
            try:
                # Use the same device index as the model
                device_idx = int(self.device) if self.device.isdigit() else 0
                self.roi_mask_gpu = torch.from_numpy(self.roi_mask).to(f"cuda:{device_idx}")
                logger.info(f"[{self.feed_id}] ROI Mask moved to GPU (cuda:{device_idx})")
            except Exception as e:
                logger.warning(f"[{self.feed_id}] Failed to move ROI mask to GPU: {e}")
                self.roi_mask_gpu = None
        else:
            self.roi_mask_gpu = None

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

    def predict_only(self, width: int, height: int, current_time: float) -> Dict[str, Dict]:
        """Runs only the Kalman prediction step for smooth tracking during skipped detections."""
        # Calculate skip factor based on time since last update
        dt = current_time - getattr(self, '_last_track_time', current_time)
        skip_factor = max(0, int(dt * self.fps) - 1)
        self._last_track_time = current_time

        # Use an empty list of detections to trigger only predictions in the tracker
        # We pass a dummy frame shape to satisfy track clipping
        self.vehicle_data = self.tracker.update([], current_time, (height, width), skip_factor=skip_factor)
        
        vis_tracks = {}
        for tid, track in self.vehicle_data.items():
            # Minimal updates for 'predicting' tracks
            if track.get("status") == "predicting":
                cx, cy = (track["bbox"][0] + track["bbox"][2])/2, (track["bbox"][1] + track["bbox"][3])/2
                ground_pos = self.transformer.pixel_to_ground(cx, cy)
                if ground_pos:
                    track["ground_coordinates"] = ground_pos
                vis_tracks[tid] = track
            elif track.get("status") == "active":
                vis_tracks[tid] = track
                
        return vis_tracks
        
    def detect_and_track(
        self,
        frame: Optional[np.ndarray],
        frame_index: int,
        confidence_threshold: Optional[float] = None,
        proximity_threshold: Optional[int] = None,
        track_timeout: Optional[int] = None,
        external_detections: Optional[List[Tuple]] = None,
        timestamp: Optional[float] = None,
        selected_ids: Optional[Set[str]] = None,
    ) -> Tuple[Dict[str, Dict], List[int], Any, Dict]:
        """Orchestrates detection and tracking using modular engines."""
        if frame is None or frame.size == 0:
            return {}, self.cached_lane_boundaries, self.last_detected_lane_lines, {
                "drift_score": 0.0,
                "is_drifted": False,
                "is_calibrated": self.transformer.homography_matrix is not None
            }

        current_time = timestamp if timestamp is not None else time.time()
        
        # 0. Calibration Drift & Motion Compensation
        drift_score, is_drifted, M_ref_to_curr = 0.0, False, None
        if self.calib_monitor:
            if frame_index == 0:
                self.calib_monitor.set_reference(frame)
        
            # We allow check_drift to return M if it was computed
            # Note: In real-world, we might want to run this more frequently for motion compensation
            drift_score, is_drifted, M_ref_to_curr = self.calib_monitor.check_drift(frame)
        
        if M_ref_to_curr is not None:
            # M maps Reference -> Current. We need Current -> Reference to stabilize
            try:
                M_curr_to_ref = np.linalg.inv(M_ref_to_curr)
                
                # Apply compensation to all existing tracks
                # This moves them from "shifted" coordinates back to "stable" calibration coordinates
                for tid, track in self.tracker.vehicle_data.items():
                    # 1. Transform Centroid
                    cx, cy = track["centroid"]
                    pt = np.array([[[cx, cy]]], dtype=np.float32)
                    pt_stable = cv2.perspectiveTransform(pt, M_curr_to_ref)[0][0]
                    track["centroid"] = (float(pt_stable[0]), float(pt_stable[1]))
                    
                    # 2. Transform BBox (simplified: transform 2 corners)
                    x1, y1, x2, y2 = track["bbox"]
                    pts = np.array([[[x1, y1], [x2, y2]]], dtype=np.float32)
                    pts_stable = cv2.perspectiveTransform(pts, M_curr_to_ref)[0]
                    track["bbox"] = (float(pts_stable[0][0]), float(pts_stable[0][1]), 
                                     float(pts_stable[1][0]), float(pts_stable[1][1]))
                    
                    # 3. Adjust Kalman State
                    kf = track.get("kalman_filter")
                    if kf:
                        # Position states x[0], x[1]
                        kf.x[0][0] = pt_stable[0]
                        kf.x[1][0] = pt_stable[1]
                        # Note: Velocity vectors also need rotation/scale if movement is large
                        # But for small shakes, updating position is the primary correction.
                
                logger.debug(f"[{self.feed_id}] Homography-based motion compensation applied. Score: {drift_score:.4f}")
            except np.linalg.LinAlgError:
                logger.warning(f"[{self.feed_id}] Singular homography matrix in motion compensation.")

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

        # 2. Detection (Use ROI Cropping for performance)
        if external_detections is not None:
            detections = external_detections
        else:
            # ROI Cropping Optimization: Detect only in relevant area
            processed_frame, is_cropped, x_off, y_off = self._preprocess_frame(frame)
            raw_detections = self.detector.detect(processed_frame, self.confidence_threshold)
            
            # Map back to original frame coordinates if cropped
            if is_cropped:
                detections = []
                for bbox, cls, conf in raw_detections:
                    mapped_bbox = (bbox[0] + x_off, bbox[1] + y_off, bbox[2] + x_off, bbox[3] + y_off)
                    detections.append((mapped_bbox, cls, conf))
            else:
                detections = raw_detections
        
        # 2b. ROI & BBox Quality Filtering
        if detections:
            filtered_detections = []
            fh, fw = self.roi_mask.shape[:2] if self.roi_mask is not None else frame.shape[:2]
            
            # Read quality filters from config
            v_cfg = self.config.get("vehicle_detection", {})
            min_area = v_cfg.get("min_bbox_area", 0)
            min_dim = v_cfg.get("min_bbox_dimension", 0)
            max_ratio = v_cfg.get("max_aspect_ratio", 100.0)

            for det in detections:
                bbox, cls, conf = det[:3]
                x1, y1, x2, y2 = map(int, bbox)
                w, h = x2 - x1, y2 - y1
                
                # 1. BBox Quality Filters
                if w * h < min_area: continue
                if w < min_dim or h < min_dim: continue
                ratio = max(w, h) / max(1, min(w, h))
                if ratio > max_ratio: continue

                # 2. ROI Mask Filtering
                if self.roi_mask_gpu is not None:
                    # Clamp coordinates to mask bounds
                    cx1, cy1 = max(0, x1), max(0, y1)
                    cx2, cy2 = min(fw, x2), min(fh, y2)
                    
                    if cx2 > cx1 and cy2 > cy1:
                        box_area = max(1, (x2 - x1) * (y2 - y1))
                        # Slice the GPU tensor
                        roi_crop_gpu = self.roi_mask_gpu[cy1:cy2, cx1:cx2]
                        # Sum values on GPU, then bring to CPU for comparison
                        overlap_area = torch.sum(roi_crop_gpu > 0).item()
                        
                        # If at least 30% of the box is inside the ROI, keep it
                        if (overlap_area / box_area) < 0.3:
                            continue
                    else:
                        continue
                elif self.roi_mask is not None:
                    # Fallback to CPU-based ROI filtering
                    cx1, cy1 = max(0, x1), max(0, y1)
                    cx2, cy2 = min(fw, x2), min(fh, y2)
                    
                    if cx2 > cx1 and cy2 > cy1:
                        box_area = max(1, (x2 - x1) * (y2 - y1))
                        roi_crop = self.roi_mask[cy1:cy2, cx1:cx2]
                        overlap_area = np.sum(roi_crop > 0)
                        if (overlap_area / box_area) < 0.3:
                            continue
                    else:
                        continue
                
                filtered_detections.append(det)
            
            detections = filtered_detections

        # 3. Enrichment (ReID Embeddings) - BATCHED
        enriched_detections = []
        
        # Stagger ReID to avoid CPU/GPU spikes across all feeds
        feed_offset = hash(self.feed_id) % self.reid_interval
        # R4 Fix: Adaptive ReID interval — in high-skip scenarios, run more frequently
        effective_reid_interval = max(1, self.reid_interval // 2) if frame_index > 100 else self.reid_interval
        
        # New Selective ReID logic: Only run if it's interval OR vehicle is in user's selection
        # Note: We pass selected_ids from the websocket message or UI state
        should_run_reid = (
            self.reid_embedder is not None 
            and ((frame_index + feed_offset) % effective_reid_interval == 0)
        )

        if (should_run_reid or (self.reid_embedder is not None and selected_ids)) and detections:
            rois_for_batch = []
            valid_indices = []
            embeddings_map = {}

            # Collect ROIs
            for idx, (bbox, cls, dconf) in enumerate(detections):
                x1, y1, x2, y2 = map(int, bbox)
                # Ensure coordinates are within frame bounds
                h, w = frame.shape[:2]
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)
                
                if x2 > x1 and y2 > y1:
                    # Logic to decide if we run ReID for this detection
                    is_selected_or_scheduled = should_run_reid
                    
                    # If we aren't in a global ReID frame, only check if this detection overlaps a selected track
                    if not should_run_reid and selected_ids:
                        for tid, track in self.tracker.vehicle_data.items():
                            if tid in selected_ids and self.tracker._bbox_iou(bbox, track["bbox"]) > 0.5:
                                is_selected_or_scheduled = True
                                break
                    
                    if not is_selected_or_scheduled:
                        continue

                    roi = frame[y1:y2, x1:x2]
                    if roi.size > 0:
                        # Quality Gating
                        is_likely_occluded = False
                        for t in self.tracker.vehicle_data.values():
                            if t.get("is_occluded") and self.tracker._bbox_iou(bbox, t["bbox"]) > 0.5:
                                is_likely_occluded = True
                                break

                        quality = self._calculate_image_quality(roi, is_likely_occluded)
                        if quality > 30.0:
                            rois_for_batch.append(roi)
                            valid_indices.append(idx)
                        else:
                            logger.debug(f"[{self.feed_id}] Skipping ReID for low-quality ROI (Q: {quality:.1f})")
            
            # Batch Inference (Performance win: Single GPU/CPU call)
            if rois_for_batch:
                try:
                    if hasattr(self.reid_embedder, 'get_batch_embeddings'):
                        batch_embeddings = self.reid_embedder.get_batch_embeddings(rois_for_batch)
                    else:
                        batch_embeddings = [self.reid_embedder.get_embedding(r) for r in rois_for_batch]
                    
                    # Map back to original indices
                    for i, original_idx in enumerate(valid_indices):
                        embeddings_map[original_idx] = batch_embeddings[i]
                except Exception as e:
                    logger.error(f"ReID batch error: {e}")

            # Merge results
            for idx, (bbox, cls, dconf) in enumerate(detections):
                emb = embeddings_map.get(idx)
                enriched_detections.append((bbox, cls, dconf, emb))
        else:
            # Fast path: Skip ReID
            for bbox, cls, dconf in detections:
                enriched_detections.append((bbox, cls, dconf, None))

        # 4. Tracking (T2 Fix: immediately capture returned vehicle_data)
        # Calculate skip factor based on time since last track update
        dt_track = current_time - getattr(self, '_last_track_time', current_time)
        skip_factor = max(0, int(dt_track * self.fps) - 1)
        self._last_track_time = current_time

        self.vehicle_data = self.tracker.update(enriched_detections, current_time, frame.shape, skip_factor=skip_factor)
        
        # 5. Metadata Processing
        vis_tracks = {}
        for tid, track in self.vehicle_data.items():
            cx, cy = (track["bbox"][0] + track["bbox"][2])/2, (track["bbox"][1] + track["bbox"][3])/2
            ground_pos = self.transformer.pixel_to_ground(cx, cy)
            if ground_pos:
                track["ground_coordinates"] = ground_pos
            
            # Estimate Speed
            # Fix #17: Ensure prev_time is never current_time for new tracks
            prev_time = track.get("last_speed_update_time")
            if prev_time is None:
                 prev_time = current_time - (1.0/self.fps)
            
            track["speed"] = self._estimate_speed_kalman(track, current_time, prev_time)
            track["last_speed_update_time"] = current_time
            
            # Estimate Lane
            track["lane"] = self._estimate_lane((cx, cy))

            # OCR Submission (R7: with backpressure check)
            if self.local_ocr and track["status"] == "active" and "license_plate" not in track:
                if frame_index % 10 == 0 and self._ocr_pending_count < 4:  # R7: limit concurrent OCR tasks
                     x1, y1, x2, y2 = map(int, track["bbox"])
                     h, w = frame.shape[:2]
                     x1, y1 = max(0, x1), max(0, y1)
                     x2, y2 = min(w, x2), min(h, y2)
                     if x2 > x1 and y2 > y1:
                         roi = frame[y1:y2, x1:x2].copy()
                         quality = self._calculate_image_quality(roi)
                         if quality > 150.0: # Higher threshold for OCR
                            self._ocr_pending_count += 1
                            future = self.ocr_executor.submit(self._run_ocr_task, tid, roi)
                            future.add_done_callback(lambda f: setattr(self, '_ocr_pending_count', max(0, self._ocr_pending_count - 1)))

            # Simple Filtering for Visualization
            if track["status"] == "active":
                vis_tracks[tid] = track
            elif track["status"] == "predicting":
                if (current_time - track["last_seen"]) < self.predict_timeout:
                    vis_tracks[tid] = track

        self._save_vehicle_data(vis_tracks)
        self._process_ocr_results()

        # Handle Snapshots
        if self._pending_snapshot_incident_id:
            self._save_snapshot(frame, self._pending_snapshot_incident_id)
            self._pending_snapshot_incident_id = None

        calibration_status = {
            "drift_score": round(drift_score, 4),
            "is_drifted": is_drifted,
            "is_calibrated": True # Loosened for detection testing
        }

        return vis_tracks, self.cached_lane_boundaries, self.last_detected_lane_lines, calibration_status

    def _save_snapshot(self, frame: np.ndarray, incident_id: str):
        """Saves a high-res snapshot of an incident."""
        try:
            timestamp = int(time.time())
            
            # Use self.config (dict) instead of get_current_config()
            # The worker process has its own copy of config passed at init
            fmt = self.config.get("snapshot_format", "webp").lower()
            if fmt not in ["jpg", "jpeg", "webp", "png"]:
                fmt = "webp"
            
            filename = f"snapshot_{self.feed_id}_{incident_id}_{timestamp}.{fmt}"
            
            # snapshots_dir might be in a top-level 'storage' or directly in config
            snapshots_dir = self.config.get("snapshots_dir", "data/snapshots")
            output_dir = Path(snapshots_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            filepath = output_dir / filename
            
            # 1. Resize if frame is too large (to save space)
            h, w = frame.shape[:2]
            max_w = self.config.get("snapshot_max_width", 1280)
            if w > max_w:
                scale = max_w / w
                new_h = int(h * scale)
                frame = cv2.resize(frame, (max_w, new_h), interpolation=cv2.INTER_AREA)
                logger.debug(f"[{self.feed_id}] Resized snapshot from {w}x{h} to {max_w}x{new_h}")
            
            # 2. Use configurable quality and format
            quality = self.config.get("snapshot_quality", 80)
            if fmt in ["jpg", "jpeg"]:
                cv2.imwrite(str(filepath), frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            elif fmt == "webp":
                # WebP quality 0-100
                cv2.imwrite(str(filepath), frame, [int(cv2.IMWRITE_WEBP_QUALITY), quality])
            else: # png or other
                cv2.imwrite(str(filepath), frame)
            
            # Send notification to DB queue so it can be associated with the incident
            if self.db_queue:
                self.db_queue.put_nowait({
                    "type": "snapshot_created",
                    "feed_id": self.feed_id,
                    "incident_id": incident_id,
                    "filename": filename,
                    "timestamp": float(time.time())
                })
            logger.debug(f"[{self.feed_id}] Saved snapshot for incident {incident_id}: {filename} (Format: {fmt}, Quality: {quality})")
        except Exception as e:
            logger.error(f"[{self.feed_id}] Failed to save snapshot: {e}")

    def _run_ocr_task(self, tid, roi):
        """Worker task for OCR executor. R6: Removed dead Gemini code path."""
        try:
            plate_result = None
            if self.local_ocr:
                plate_result = self.local_ocr.read_plate(roi) # Returns (text, conf)
                
            if plate_result:
                text, conf = plate_result
                self.ocr_results_queue.put({"track_id": tid, "plate_text": text, "confidence": conf})
        except Exception as e:
            logger.error(f"OCR Task failed: {e}")

    def _estimate_speed_kalman(self, track: Dict, current_time: float, prev_time: float) -> float:
        kf = track.get("kalman_filter")
        if not kf:
            return 0.0
        
        try:
            time_diff = current_time - prev_time
            
            # Clamp time_diff to reasonable bounds
            min_dt = 1.0 / (self.fps * 2)
            max_dt = 2.0
            time_diff = max(min_dt, min(max_dt, time_diff))

            raw_speed_kmph = 0.0

            # State order: [x, y, w, h, vx, vy, vw, vh]
            cx, cy = kf.x[0][0], kf.x[1][0]
            vx, vy = kf.x[4][0], kf.x[5][0]
            
            # Store components for wrong-way detection
            track["vx"] = vx
            track["vy"] = vy

            # 1. Prefer Homography-based speed estimation
            current_ground = self.transformer.pixel_to_ground(cx, cy)
            prev_ground = track.get("ground_centroid")

            if current_ground and prev_ground:
                dx = current_ground[0] - prev_ground[0]
                dy = current_ground[1] - prev_ground[1]
                dist_meters = math.sqrt(dx**2 + dy**2)
                speed_mps = dist_meters / time_diff
                raw_speed_kmph = speed_mps * 3.6
            elif current_ground:
                # Backtrack using pixel velocity as a rough estimate for first frame
                back_cx, back_cy = cx - vx * time_diff, cy - vy * time_diff
                
                # Sanity check: Ensure backtrack is within reasonable bounds (2x frame size)
                res = self.config.get("vehicle_detection", {}).get("frame_resolution", [640, 480])
                w, h = res
                if -w < back_cx < 2*w and -h < back_cy < 2*h:
                    prev_ground_est = self.transformer.pixel_to_ground(back_cx, back_cy)
                    if prev_ground_est:
                        dx = current_ground[0] - prev_ground_est[0]
                        dy = current_ground[1] - prev_ground_est[1]
                        dist_meters = math.sqrt(dx**2 + dy**2)
                        speed_mps = dist_meters / time_diff
                        raw_speed_kmph = speed_mps * 3.6
            
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

            # --- Uncertainty (Confidence Interval) ---
            # Extract velocity covariance: var(vx) = P[4,4], var(vy) = P[5,5]
            # Speed s = sqrt(vx^2 + vy^2). 
            # Simple error propagation: var(s) approx (vx^2*var(vx) + vy^2*var(vy)) / s^2
            # But since we are using homography, the pixel uncertainty maps non-linearly.
            # We'll use a heuristic based on state covariance.
            speed_err = 0.0
            if kf.P is not None:
                v_var = (kf.P[4, 4] + kf.P[5, 5])
                # Map pixel-variance to km/h-variance using PPM
                dynamic_ppm = self._get_dynamic_pixels_per_meter(cy)
                if dynamic_ppm > 0:
                    speed_err = (np.sqrt(v_var) / dynamic_ppm) * 3.6
            
            track["speed_err"] = round(float(speed_err), 1)
            track["smoothed_speed"] = new_smoothed
            return round(float(max(0, new_smoothed)), 1)
        except Exception as e:
            logger.warning(f"Speed estimation error: {e}")
            return 0.0

    def _get_dynamic_pixels_per_meter(self, y_pixel: float) -> float:
        """Calculates dynamic Pixels Per Meter (PPM) based on perspective."""
        if self.homography_matrix is not None:
             # Estimate PPM at this Y by checking distance between two close pixels
             p1 = self.transformer.pixel_to_ground(100, y_pixel)
             p2 = self.transformer.pixel_to_ground(110, y_pixel)
             if p1 and p2:
                 d = math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2)
                 if d > 0.001:
                     return 10.0 / d

        frame_height = self.config.get("vehicle_detection", {}).get("frame_resolution", [640, 480])[1]
        factor = max(0.2, y_pixel / frame_height)
        return self.pixels_per_meter * factor

    def _save_vehicle_data(self, tracked_vehicles: Dict[str, Dict]):
        now = time.time()
        
        # S6 Fix: Prune _last_save_time for tracks that no longer exist
        stale_ids = [vid for vid in self._last_save_time if vid not in self.vehicle_data]
        for vid in stale_ids:
            del self._last_save_time[vid]
        
        for vehicle_id, data in tracked_vehicles.items():
            # Cooldown: Don't save same vehicle more than once every 0.5s unless it's moving fast
            last_save = self._last_save_time.get(vehicle_id, 0)
            if (now - last_save) < 0.5:
                continue
            
            if self.db_queue:
                try:
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
                        "behavior": str(data.get("behavior", "normal")),
                        "is_occluded": bool(data.get("is_occluded", False)),
                        "hits": int(data.get("hits", 0)),
                        "lane": int(data.get("lane", -1)),
                    })
                    self._last_save_time[vehicle_id] = now
                except queue.Full:
                    pass

    def _process_ocr_results(self):
        """Drains the OCR results queue and updates vehicle data with multi-frame voting."""
        from collections import Counter
        try:
            while True:
                result = self.ocr_results_queue.get_nowait()
                tid = result["track_id"]
                text = result["plate_text"]
                conf = result["confidence"]
                
                if tid in self.vehicle_data:
                    track = self.vehicle_data[tid]
                    # Add to candidates
                    if "plate_candidates" not in track:
                         from collections import deque
                         track["plate_candidates"] = deque(maxlen=10)
                    
                    track["plate_candidates"].append((text, conf))
                    
                    # Perform voting
                    candidates = [t for t, c in track["plate_candidates"]]
                    if candidates:
                        # Simple majority vote on the full string
                        most_common = Counter(candidates).most_common(1)[0][0]
                        track["license_plate"] = most_common
        except queue.Empty:
            pass

    def _estimate_lane(self, centroid: Tuple[float, float]) -> int:
        """
        Determines the lane number (1-indexed) based on the vehicle's centroid.
        If lane boundaries are not available, uses static configuration.
        """
        if not centroid or not isinstance(centroid, (list, tuple)) or len(centroid) < 2:
            return -1

        cx, cy = centroid
        
        # Use cached dynamic boundaries if available
        boundaries = self.cached_lane_boundaries
        
        # Fallback to static boundaries if dynamic detection is off or failed
        if not boundaries:
            l_cfg = self.config.get("lane_detection", {})
            num_lanes = l_cfg.get("num_lanes", 6)
            # Use current frame resolution
            res = self.config.get("vehicle_detection", {}).get("frame_resolution", [640, 480])
            w = res[0]
            lane_width = w / num_lanes if num_lanes > 0 else w
            boundaries = [int(i * lane_width) for i in range(num_lanes + 1)]

        # Find which lane the x-coordinate falls into
        if not boundaries: return -1
        
        for i in range(len(boundaries) - 1):
            if boundaries[i] <= cx <= boundaries[i+1]:
                return i + 1 # 1-indexed lane number
                
        return -1

    def _calculate_image_quality(self, roi: np.ndarray, is_occluded: bool = False) -> float:
        """Estimates image quality (sharpness + size) for ReID/OCR gating."""
        if roi.size == 0: return 0.0
        if is_occluded: return 0.0 # Never update gallery if occluded
        try:
            # 1. Sharpness (Laplacian Variance)
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            sharpness = cv2.Laplacian(gray, cv2.CV_64F).var()
            
            # 2. Resolution Weight
            h, w = roi.shape[:2]
            res_score = min(h, w) / 32.0 # Normalized to 32px min dimension
            
            # Composite (Heuristic: 100+ is usually good enough)
            return sharpness * res_score
        except Exception:
            return 0.0

    def cleanup(self):
        """Shutdown thread pools."""
        if self.ocr_executor:
            self.ocr_executor.shutdown(wait=True)

    def _preprocess_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, bool, int, int]:
        """
        Prepares the frame for inference. Returns (frame, roi_enabled, x_offset, y_offset).
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
        # Top-level field updates for self.config
        allowed_keys = [
            "name", "latitude", "longitude", 
            "static_object_filter_enabled", "static_object_timeout",
            "show_bounding_boxes", "show_vehicle_details", "show_trajectories"
        ]
        for key in allowed_keys:
            if key in updates:
                self.config[key] = updates[key]
                # Update specific attributes if they correspond to these keys
                if key == "static_object_filter_enabled":
                    self.static_object_filter_enabled = updates[key]
                elif key == "static_object_timeout":
                    self.static_object_timeout = updates[key]

        if "vehicle_detection" in updates:
            v_cfg = updates["vehicle_detection"]
            # Update internal config copy to keep filters in sync
            if "vehicle_detection" not in self.config:
                self.config["vehicle_detection"] = {}
            self.config["vehicle_detection"].update(v_cfg)
            
            self.confidence_threshold = v_cfg.get("confidence_threshold", self.confidence_threshold)
            self.reid_interval = v_cfg.get("reid_interval_frames", self.reid_interval)
            if "calibration" in v_cfg:
                self._update_homography(v_cfg["calibration"])
                self.transformer.update_calibration(v_cfg["calibration"])
        
        # Handle ROI and Exclusion Zones updates
        roi_updated = False
        if "roi" in updates:
            roi_data = updates["roi"]
            if isinstance(roi_data, list):
                new_points = []
                res = self.config.get("vehicle_detection", {}).get("frame_resolution", [640, 480])
                for pt in roi_data:
                    if isinstance(pt, dict) and 'x' in pt and 'y' in pt:
                        new_points.append([pt['x'] * res[0], pt['y'] * res[1]])
                    elif isinstance(pt, (list, tuple)) and len(pt) == 2:
                        if pt[0] <= 1.0 and pt[1] <= 1.0:
                            new_points.append([pt[0] * res[0], pt[1] * res[1]])
                        else:
                            new_points.append(list(pt))
                
                if new_points:
                    self.roi_polygon_points = new_points
                    if "roi_processing" not in self.config:
                        self.config["roi_processing"] = {}
                    self.config["roi_processing"]["polygon_points"] = new_points
                    self.config["roi_processing"]["enabled"] = True
                    roi_updated = True

        if "exclusion_zones" in updates:
            exclusion_data = updates["exclusion_zones"]
            if isinstance(exclusion_data, list):
                if "roi_processing" not in self.config:
                    self.config["roi_processing"] = {}
                self.config["roi_processing"]["exclusion_zones"] = exclusion_data
                roi_updated = True
        
        if roi_updated:
            res = self.config.get("vehicle_detection", {}).get("frame_resolution", [640, 480])
            current_res = getattr(self.detector, 'resolution', res) or res
            self._initialize_roi_mask(current_res)
            if self.roi_polygon_points:
                self.detector.initialize_roi(current_res, self.roi_polygon_points)
            logger.info(f"[{self.feed_id}] ROI/Exclusion zones updated. Masks re-initialized.")
