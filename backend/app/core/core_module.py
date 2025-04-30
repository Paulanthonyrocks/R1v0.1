import cv2
import os
import logging
import time
import numpy as np
from ultralytics import YOLO
from filterpy.kalman import KalmanFilter
from scipy.spatial import KDTree
from multiprocessing import Queue as MPQueue
import queue # For queue.Full exception
from typing import Dict, List, Tuple, Optional
from pathlib import Path
from collections import deque # Import deque

# Import LicensePlatePreprocessor from utils
try:
    from ..utils.utils import LicensePlatePreprocessor
except ImportError:
    print("Error importing utils for CoreModule. Ensure utils.py is accessible.")
    LicensePlatePreprocessor = None

# Logging setup
logger = logging.getLogger(__name__)

class CoreModule:
    vehicle_id_counter = 1 # Counter remains class-level, reset for each process instance

    def __init__(self, feed_id: str, gemini_api_key: str, model_path: str, config: Dict, fps: int, db_queue: Optional[MPQueue]):
        self.feed_id = feed_id # Store the feed_id for unique vehicle IDs
        self.config = config
        self.model_path = str(Path(model_path).resolve())
        self.fps = max(1, fps)
        self.db_queue = db_queue

        # Config extraction with defaults
        vehicle_detection_cfg = self.config.get('vehicle_detection', {})
        lane_detection_cfg = self.config.get('lane_detection', {})
        performance_cfg = self.config.get('performance', {})
        self.ocr_cfg = self.config.get('ocr_engine', {}) # <-- Store as self.ocr_cfg
        perspective_cfg = self.config.get('perspective_calibration', {})
        self.kf_params = self.config.get('kalman_filter_params', {}) # <-- Store as self.kf_params

        # Use stored self attributes where needed
        self.vehicle_class_ids = vehicle_detection_cfg.get('vehicle_class_ids', [2, 3, 5, 7])
        self.confidence_threshold = vehicle_detection_cfg.get('confidence_threshold', 0.5)
        self.proximity_threshold = vehicle_detection_cfg.get('proximity_threshold', 50)
        self.track_timeout = vehicle_detection_cfg.get('track_timeout', 5)
        self.max_active_tracks = vehicle_detection_cfg.get('max_active_tracks', 50)
        self.yolo_imgsz = vehicle_detection_cfg.get('yolo_imgsz', 640)
        frame_w, frame_h = vehicle_detection_cfg.get('frame_resolution',[640,480])

        self.num_lanes = lane_detection_cfg.get('num_lanes', 6)
        self.lane_width_pixels = lane_detection_cfg.get('lane_width', frame_w / (self.num_lanes + 1) if self.num_lanes > 0 else frame_w / 4)
        self.lane_change_buffer = lane_detection_cfg.get('lane_change_buffer', 5)

        self.lane_width_meters = 3.7
        self.pixels_per_meter = self.config.get('pixels_per_meter', self.lane_width_pixels / self.lane_width_meters if self.lane_width_meters > 0 else 50)
        self.speed_limit = self.config.get('speed_limit', 60)

        # Perspective matrix loading (remains the same)
        matrix_path_str = perspective_cfg.get('matrix_path', '')
        perspective_matrix = None
        if matrix_path_str:
            matrix_path = Path(matrix_path_str)
            # Resolve relative path based on app.py's parent if not absolute
            if not matrix_path.is_absolute():
                 # Assume core_module is in the same dir or subdir relative to app.py structure
                 # This might need adjustment based on exact project layout
                 # If utils.py is guaranteed to be co-located with app.py:
                 # app_dir = Path(__file__).parent.parent.resolve() # Go up one level if core_module is in a subdir
                 app_dir = Path(__file__).parent.resolve() # Assumes co-location for simplicity
                 matrix_path = app_dir / matrix_path
            if matrix_path.exists():
                try:
                    perspective_matrix = np.load(matrix_path)
                    logger.info(f"Loaded perspective matrix from: {matrix_path}")
                except Exception as e: logger.error(f"Failed to load perspective matrix from {matrix_path}: {e}")
            else: logger.warning(f"Perspective matrix path specified but not found: {matrix_path}")

        # Initialize LPP
        if LicensePlatePreprocessor:
            # Pass self.config directly, LPP handles its own sub-dictionary access
            self.preprocessor = LicensePlatePreprocessor(
                config=self.config,
                perspective_matrix=perspective_matrix
            )
        else:
            self.preprocessor = None
            logger.error("LicensePlatePreprocessor not available.")

        # Behavior thresholds
        self.stopped_speed_threshold_kmh = config.get('stopped_speed_threshold_kmh', 5)
        self.accel_threshold_mps2 = config.get('accel_threshold_mps2', 0.5)

        # Tracking state
        self.vehicle_data: Dict[str, Dict] = {}

        self.model = None
        # Pass GPU preference from performance config
        self._load_model(performance_cfg.get('gpu_acceleration', True))

    def _load_model(self, use_gpu: bool):
        if not Path(self.model_path).exists():
            logger.error(f"Model file not found at {self.model_path}")
            raise FileNotFoundError(f"Model file not found at {self.model_path}")
        try:
            import torch
            device = 'cpu'
            if use_gpu:
                if torch.cuda.is_available():
                    device = 'cuda'
                else:
                    logger.warning("GPU acceleration requested but CUDA not available. Falling back to CPU.")
            else:
                 logger.info("GPU acceleration disabled in config. Using CPU.")

            start_time = time.time()
            self.model = YOLO(self.model_path)
            self.model.to(device)
            load_time = time.time() - start_time
            logger.info(f"YOLO model loaded from {self.model_path} on '{device}' in {load_time:.3f}s")
        except ImportError as e:
            logger.error(f"PyTorch/Ultralytics import error: {e}")
            raise ImportError("PyTorch/Ultralytics is required.")
        except Exception as e:
            logger.error(f"Failed to load YOLO model: {e}", exc_info=True)
            raise RuntimeError(f"Model loading failed: {e}")

    def detect_and_track(self, frame: np.ndarray, frame_index: int,
                         confidence_threshold: Optional[float] = None,
                         proximity_threshold: Optional[int] = None,
                         track_timeout: Optional[int] = None) -> Dict[str, Dict]:
        if frame is None or frame.size == 0: return {}
        if self.model is None: return {}
        logger.debug("detect_and_track executed")

        # Use parameters passed during the call, falling back to instance defaults
        used_confidence = confidence_threshold if confidence_threshold is not None else self.confidence_threshold
        used_proximity = proximity_threshold if proximity_threshold is not None else self.proximity_threshold
        used_track_timeout = track_timeout if track_timeout is not None else self.track_timeout
        current_time = time.time()

        try:
            detections = self._detect_vehicles(frame, frame_index, used_confidence)
            current_tracks = self._update_tracks(frame, detections, used_proximity, current_time, frame_index)
            logger.debug("Tracks updated")
            logger.debug("Removing stale tracks")
            self._remove_stale_tracks(current_time, used_track_timeout)
            self._save_vehicle_data(current_tracks) # Pass currently tracked vehicles
            return current_tracks

        except Exception as e:
            logger.error(f"Frame {frame_index}: Unhandled error in detect_and_track: {e}", exc_info=True)
            return {}

    def _detect_vehicles(self, frame: np.ndarray, frame_index: int, confidence_threshold: float) -> List[Tuple]:
        detections = []
        try:
            img_size = self.yolo_imgsz
            if not isinstance(img_size, int) or img_size <= 0: img_size = 640

            results = self.model.predict(frame, conf=confidence_threshold, imgsz=img_size, classes=self.vehicle_class_ids, max_det=self.max_active_tracks, verbose=False)

            for r in results:
                boxes = r.boxes
                for box in boxes:
                    if not all(hasattr(box, attr) for attr in ['conf', 'xyxy', 'cls']): continue
                    conf = float(box.conf[0])
                    cls = int(box.cls[0])
                    if cls not in self.vehicle_class_ids: continue

                    xyxy = box.xyxy[0].tolist()
                    x1, y1, x2, y2 = map(int, xyxy)
                    center_x = (x1 + x2) / 2
                    center_y = (y1 + y2) / 2
                    vehicle_bbox = [x1, y1, x2, y2]
                    # Format: (center_x, center_y, conf, class_id, frame_idx, bbox)
                    detections.append((center_x, center_y, conf, cls, frame_index, vehicle_bbox))
            return detections

        except Exception as e:
            logger.error(f"Frame {frame_index}: Error during YOLO detection: {e}", exc_info=True)
            return []

    def _update_tracks(self, frame: np.ndarray, detections: List[Tuple], proximity_threshold: int,
                      current_time: float, frame_index: int) -> Dict[str, Dict]:
        current_tracks_in_frame = {}
        if not detections:
            for track in self.vehicle_data.values():
                 if track.get('kalman_filter'): track['kalman_filter'].predict()
            return current_tracks_in_frame

        detection_centers = np.array([d[:2] for d in detections])
        detection_indices = list(range(len(detections)))
        matched_detection_indices = set()

        track_ids_to_match = list(self.vehicle_data.keys())
        predicted_positions = []
        kalman_filters = [] # Store KFs to update dt correctly

        for vehicle_id in track_ids_to_match:
            track = self.vehicle_data[vehicle_id]
            kf = track.get('kalman_filter')
            if kf:
                # Kalman dt calculation - robust against time gaps
                dt = min(1.0, max(0.01, current_time - track.get('last_seen', current_time))) # Bounded dt
                kf.F[0, 2] = dt
                kf.F[1, 3] = dt
                kf.predict()
                predicted_positions.append(kf.x[:2])
                kalman_filters.append(kf) # Keep reference
            else:
                predicted_positions.append(np.array([np.nan, np.nan]))
                kalman_filters.append(None)

        if predicted_positions and detection_centers.size > 0:
            try:
                kdtree = KDTree(detection_centers)
                distances, indices = kdtree.query(predicted_positions, k=1)

                for i, vehicle_id in enumerate(track_ids_to_match):
                    if np.isnan(distances[i]): continue
                    best_match_idx = indices[i]
                    distance = distances[i]

                    if distance < proximity_threshold and best_match_idx not in matched_detection_indices:
                        track = self.vehicle_data[vehicle_id]
                        track['kalman_filter'] = kalman_filters[i] # Ensure using the predicted KF
                        detection_data = detections[best_match_idx]
                        self._update_track(track, detection_data, current_time, frame, frame_index)
                        current_tracks_in_frame[vehicle_id] = track
                        matched_detection_indices.add(best_match_idx)

            except ValueError as ve: logger.error(f"KDTree query error: {ve}")
            except Exception as tree_err: logger.error(f"Error during KDTree matching: {tree_err}", exc_info=True)

        unmatched_detections_indices = set(detection_indices) - matched_detection_indices
        for idx in unmatched_detections_indices:
            if len(self.vehicle_data) >= self.max_active_tracks: break
            new_vehicle_id = self._initialize_new_track(detections[idx], current_time, frame_index)
            if new_vehicle_id: current_tracks_in_frame[new_vehicle_id] = self.vehicle_data[new_vehicle_id]

        return current_tracks_in_frame


    def _initialize_new_track(self, detection: Tuple, current_time: float, frame_index: int) -> Optional[str]:
        try:
            center_x, center_y, conf, class_id, _, vehicle_bbox = detection
            if (vehicle_bbox[2]-vehicle_bbox[0]) * (vehicle_bbox[3]-vehicle_bbox[1]) < 1000: return None

            # Generate globally unique vehicle ID using feed_id prefix
            vehicle_id = f"{self.feed_id}-{CoreModule.vehicle_id_counter}"
            CoreModule.vehicle_id_counter += 1

            kf = self._initialize_kalman_filter(center_x, center_y)
            lane = self._estimate_lane(vehicle_bbox)

            self.vehicle_data[vehicle_id] = {
                'vehicle_id': vehicle_id, 'first_seen': current_time, 'last_seen': current_time,
                'frame_index': frame_index, 'bbox': vehicle_bbox, 'confidence': conf,
                'kalman_filter': kf, 'license_plate': "Unknown", 'plate_attempts': 0,
                'lane': lane, 'lane_history': deque([(frame_index, lane)], maxlen=10),
                'speed': 0.0, 'speed_history': deque(maxlen=5),
                'behavior': 'unknown', 'class_id': class_id, 'timestamp': current_time
            }
            logger.info(f"Initialized vehicle {vehicle_id} (Class: {class_id}), lane {lane}")
            return vehicle_id
        except Exception as e:
             logger.error(f"Error initializing track: {e}", exc_info=True)
             return None

    def _update_track(self, track: Dict, detection: Tuple, current_time: float, frame: np.ndarray, frame_index: int) -> None:
        try:
            center_x, center_y, conf, class_id, _, vehicle_bbox = detection
            prev_time = track['last_seen']
            track['last_seen'] = current_time
            track['timestamp'] = current_time
            track['frame_index'] = frame_index
            track['bbox'] = vehicle_bbox
            track['confidence'] = conf

            kf = track.get('kalman_filter')
            if kf:
                try: kf.update(np.array([center_x, center_y], dtype=np.float32))
                except Exception as kf_err:
                    logger.warning(f"Kalman update failed for {track['vehicle_id']}: {kf_err}")
                    track['kalman_filter'] = self._initialize_kalman_filter(center_x, center_y)
            else: track['kalman_filter'] = self._initialize_kalman_filter(center_x, center_y)

            # Estimate Speed (using Kalman velocity)
            track['speed'] = self._estimate_speed_kalman(track, current_time, prev_time) # Pass prev_time
            track['speed_history'].append(track['speed'])

            new_lane = self._estimate_lane(track['bbox'])
            last_recorded_lane = track['lane_history'][-1][1] if track['lane_history'] else -1
            center_lane_new = (new_lane - 0.5) * self.lane_width_pixels
            center_lane_old = (last_recorded_lane - 0.5) * self.lane_width_pixels if last_recorded_lane != -1 else center_x
            if last_recorded_lane != -1 and new_lane != -1 and new_lane != last_recorded_lane and abs(center_x - center_lane_old) > self.lane_change_buffer:
                logger.info(f"Vehicle {track['vehicle_id']} lane change {last_recorded_lane} -> {new_lane}")
                track['behavior'] = 'lane_changing'
            track['lane'] = new_lane
            if not track['lane_history'] or track['lane_history'][-1][1] != new_lane:
                 track['lane_history'].append((frame_index, new_lane))

            self._classify_behavior(track) # Classify based on new speed/state

            # --- Access ocr_cfg using self.ocr_cfg ---
            ocr_interval_frames = int(self.fps * self.ocr_cfg.get('ocr_interval', 15))
            max_ocr_attempts = 3
            if (track['license_plate'] == "Unknown" and
                self.preprocessor and
                track.get('plate_attempts', 0) < max_ocr_attempts and
                frame_index % max(1, ocr_interval_frames) == 0):
                 logger.debug(f"Attempting OCR for vehicle {track['vehicle_id']} (Attempt {track.get('plate_attempts', 0) + 1})")
                 plate_text = self._ocr_license_plate(frame, track['bbox'])
                 # Check for various "unknown" responses before assigning
                 if plate_text not in ["Unknown", "Unknown (Error)", "Unknown (BadROI)", "Unknown (SmallROI)", "Unknown (NoPrep)", "Unknown (RetryFail)", "Unknown (Refused)", "Unknown (Blocked)", "Unknown (GenFail)", "Unknown (InvalidResp)", "Unknown (OCRError)", "Unknown (PreprocFail)", "Unknown (TessFail)", "Unknown (NoTess)", "Unknown (TessError)", None]:
                      track['license_plate'] = plate_text
                      logger.info(f"OCR Success for {track['vehicle_id']}: {plate_text}")
                 track['plate_attempts'] = track.get('plate_attempts', 0) + 1

        except Exception as e:
            logger.error(f"Error updating track {track.get('vehicle_id', 'N/A')}: {e}", exc_info=True)

    def _classify_behavior(self, track: Dict) -> None:
        current_speed_kmh = track['speed']

        if current_speed_kmh < self.stopped_speed_threshold_kmh:
            track['behavior'] = 'stopped'
            return

        # Skip accel/decel check if just changed lanes
        # if track['behavior'] == 'lane_changing':
        #    return # Or maybe reset to 'moving' after a short period

        if current_speed_kmh > self.speed_limit:
            track['behavior'] = 'speeding'
            return

        if len(track['speed_history']) >= 3:
            avg_recent_speed = np.mean(list(track['speed_history'])[-3:])
            speed_diff_kmh = current_speed_kmh - avg_recent_speed
            # Convert accel threshold m/s^2 to km/h difference over ~0.5s (rough estimate)
            accel_kmh_thresh_over_period = self.accel_threshold_mps2 * 3.6 * 0.5

            if speed_diff_kmh > accel_kmh_thresh_over_period:
                track['behavior'] = 'accelerating'
            elif speed_diff_kmh < -accel_kmh_thresh_over_period:
                track['behavior'] = 'decelerating'
            else:
                track['behavior'] = 'moving'
        else:
            track['behavior'] = 'moving'

    def _estimate_speed_kalman(self, track: Dict, current_time: float, prev_time: float) -> float:
        kf = track.get('kalman_filter')
        if not kf: return 0.0
        try:
            vx, vy = kf.x[2], kf.x[3] # Velocity in pixels/dt (where dt was used in F matrix)
            # Use the actual time difference between updates for scaling
            time_diff = min(1.0, max(0.01, current_time - prev_time)) # Use the passed prev_time
            pixel_speed_per_sec = np.sqrt(vx**2 + vy**2) / time_diff if time_diff > 0 else 0
            speed_mps = pixel_speed_per_sec / self.pixels_per_meter if self.pixels_per_meter > 0 else 0
            speed_kmph = speed_mps * 3.6
            # Don't append here, append the smoothed speed
            # track['speed_history'].append(speed_kmph)
            # Apply smoothing to the history before returning
            current_history = list(track['speed_history'])
            current_history.append(speed_kmph) # Add current estimate to history for smoothing
            smoothed_speed = np.mean(current_history) # Smooth over the window
            return round(max(0, smoothed_speed), 1)
        except Exception as e:
            logger.warning(f"Speed estimation error for {track.get('vehicle_id', 'N/A')}: {e}")
            return 0.0

    def _ocr_license_plate(self, frame: np.ndarray, bbox: List[int]) -> str:
        if not self.preprocessor: return "Unknown (NoPrep)"
        try:
            x1, y1, x2, y2 = map(int, bbox)
            h, w = frame.shape[:2]
            roi_h = y2 - y1
            roi_w = x2 - x1

            # --- Read ROI adjustment factors from config with defaults ---
            top_margin_factor = self.ocr_cfg.get('roi_top_margin_factor', 0.5) # Default: Start lower
            bottom_margin_factor = self.ocr_cfg.get('roi_bottom_margin_factor', 0.1) # Default: End slightly higher (smaller cut from bottom)
            left_margin_factor = self.ocr_cfg.get('roi_left_margin_factor', 0.15) # Default: Crop left side
            right_margin_factor = self.ocr_cfg.get('roi_right_margin_factor', 0.15) # Default: Crop right side

            # --- Apply configurable factors ---
            roi_y_start = max(0, int(y1 + roi_h * top_margin_factor))
            roi_y_end = min(h, int(y2 - roi_h * bottom_margin_factor))
            roi_x_start = max(0, int(x1 + roi_w * left_margin_factor))
            roi_x_end = min(w, int(x2 - roi_w * right_margin_factor))
            # -------------------------------

            if roi_x_start >= roi_x_end or roi_y_start >= roi_y_end: return "Unknown (BadROI)"
            roi = frame[roi_y_start:roi_y_end, roi_x_start:roi_x_end]
            if roi.size < self.preprocessor.min_roi_size: return "Unknown (SmallROI)"
            return self.preprocessor.preprocess_and_ocr(roi)
        except Exception as e:
            logger.error(f"OCR processing failed: {e}", exc_info=True)
            return "Unknown (OCRError)"

    def _initialize_kalman_filter(self, initial_x: float, initial_y: float) -> KalmanFilter:
        try:
            kf = KalmanFilter(dim_x=4, dim_z=2)
            kf.F = np.array([[1, 0, 1, 0], [0, 1, 0, 1], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=float) # dt=1 initially
            kf.H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=float)
            kf.x = np.array([initial_x, initial_y, 0., 0.], dtype=float)
            # --- Use self.kf_params ---
            kf.P = np.diag([
                self.kf_params.get('kf_sigma_px', 2.0)**2, self.kf_params.get('kf_sigma_py', 2.0)**2,
                self.kf_params.get('kf_sigma_pvx', 5.0)**2, self.kf_params.get('kf_sigma_pvy', 5.0)**2
            ])
            kf.R = np.diag([
                self.kf_params.get('kf_sigma_mx', 0.5)**2, self.kf_params.get('kf_sigma_my', 0.5)**2
            ])
            q_ax = self.kf_params.get('kf_sigma_ax', 0.5)**2
            q_ay = self.kf_params.get('kf_sigma_ay', 0.5)**2
            # Simplified Q matrix based on typical state-space noise models
            # Assuming dt=1 for initial Q calculation. It scales with dt^n in predict step.
            dt = 1 # Reference dt for Q
            kf.Q = np.diag([0.25*dt**4*q_ax, 0.25*dt**4*q_ay, dt**2*q_ax, dt**2*q_ay])
            # Or simpler diagonal if process noise is less coupled:
            # kf.Q = np.diag([0.1, 0.1, q_ax, q_ay]) # Keep original simpler version if preferred
            return kf
        except Exception as e:
            logger.error(f"Kalman filter initialization failed: {e}", exc_info=True)
            raise

    def _estimate_lane(self, bbox: List[int]) -> int:
        if not bbox or len(bbox) != 4: return -1
        x_center = (bbox[0] + bbox[2]) / 2
        if self.lane_width_pixels <= 0: return -1
        # Correct calculation: lane based on which width segment the center falls into
        lane = int(x_center // self.lane_width_pixels) + 1
        return max(1, min(lane, self.num_lanes)) # Clamp

    def _remove_stale_tracks(self, current_time: float, track_timeout: int) -> None:
        stale_ids = [vid for vid, track in self.vehicle_data.items() if current_time - track['last_seen'] > track_timeout]
        for vid in stale_ids: del self.vehicle_data[vid]

        if len(self.vehicle_data) > self.max_active_tracks:
             sorted_tracks = sorted(self.vehicle_data.items(), key=lambda item: item[1]['last_seen'])
             num_to_remove = len(self.vehicle_data) - self.max_active_tracks
             for i in range(num_to_remove):
                 vid_to_remove = sorted_tracks[i][0]
                 del self.vehicle_data[vid_to_remove]

        if stale_ids or len(self.vehicle_data) > self.max_active_tracks:
             logger.debug(f"Removed {len(stale_ids)} stale. Active tracks: {len(self.vehicle_data)}")


    def _save_vehicle_data(self, current_tracks: Dict[str, Dict]) -> None:
        # --- ADDED: Check if db_queue exists ---
        if not self.db_queue:
            # logger.debug("DB queue not configured or provided. Skipping data save.") # Optional: Log only once or less frequently
            return
        # ----------------------------------------
        if not current_tracks: return

        vehicle_data_list = []
        for track_id, track in current_tracks.items():
            if not track: continue # Skip if track is None somehow
            try:
                vehicle_data = {
                    'vehicle_id': track_id, # Already includes feed_id prefix
                    'timestamp': track.get('timestamp', time.time()),
                    'frame_index': track.get('frame_index'),
                    'license_plate': track.get('license_plate', 'Unknown'),
                    'vehicle_type': self._get_vehicle_type(track.get('class_id', -1)),
                    'first_seen': track.get('first_seen'),
                    'last_seen': track.get('last_seen'),
                    'x1': track['bbox'][0], 'y1': track['bbox'][1],
                    'x2': track['bbox'][2], 'y2': track['bbox'][3],
                    'speed': track.get('speed'),
                    'lane': track.get('lane'),
                    'confidence': track.get('confidence'),
                    'car_model': 'Unknown', # Placeholder
                    'car_color': 'Unknown', # Placeholder
                }
                vehicle_data_list.append(vehicle_data)
            except KeyError as ke:
                logger.warning(f"Missing key {ke} in track data for {track_id}. Skipping DB save for this entry.")
            except Exception as e:
                 logger.error(f"Error preparing data for DB for {track_id}: {e}", exc_info=True)


        if not vehicle_data_list: return

        try:
            for vehicle_data in vehicle_data_list:
                 self.db_queue.put_nowait(vehicle_data)
            # logger.debug(f"Put {len(vehicle_data_list)} vehicle records onto db_queue.") # Reduce log frequency
        except queue.Full:
            logger.warning("Database queue is full. Dropping vehicle data batch.")
        except Exception as e:
            logger.error(f"Failed to put vehicle data onto db_queue: {e}", exc_info=True)

    def _get_vehicle_type(self, class_id: int) -> str:
        type_map = {2: 'car', 3: 'motorcycle', 5: 'bus', 7: 'truck'}
        return type_map.get(class_id, 'unknown')

    def cleanup(self):
        logger.info(f"CoreModule cleanup initiated for {self.feed_id}. Active tracks: {len(self.vehicle_data)}")
        self.vehicle_data.clear()
        # Model cleanup (if possible)
        if hasattr(self.model, 'session') and self.model.session: del self.model.session
        if hasattr(self.model, 'predictor') and self.model.predictor: del self.model.predictor
        del self.model
        if self.preprocessor and hasattr(self.preprocessor, 'gemini_model'): del self.preprocessor.gemini_model

        import gc; gc.collect()
        try:
            import torch
            if torch.cuda.is_available(): torch.cuda.empty_cache()
        except ImportError: pass
        except Exception as e: logger.warning(f"Error during CUDA cache clear on cleanup: {e}")
        logger.info(f"CoreModule cleanup finished for {self.feed_id}.")

# --- Example standalone usage ---
if __name__ == "__main__":
    # Basic config for testing
    test_config = {
        'vehicle_detection': {
            'model_path': '../models/yolov8n.pt', # Adjust path as needed
            'vehicle_class_ids': [2, 3, 5, 7],
            'confidence_threshold': 0.4, 'proximity_threshold': 60, 'track_timeout': 5,
            'max_active_tracks': 50, 'yolo_imgsz': 320, 'frame_resolution': [640, 480]
        },
        'lane_detection': {'num_lanes': 4},
        'performance': {'gpu_acceleration': False}, # Test CPU path
        'ocr_engine': {
            'gemini_api_key': os.environ.get("TEST_GEMINI_API_KEY", ""),
            'roi_top_margin_factor': 0.4, # Example: Add config params here for testing
            'roi_bottom_margin_factor': 0.1,
            'roi_left_margin_factor': 0.1,
            'roi_right_margin_factor': 0.1
        },
        'kalman_filter_params': {}, # Use defaults
        'pixels_per_meter': 40,
        'speed_limit': 60,
    }
    logging.basicConfig(level=logging.DEBUG)
    logger.info("Starting CoreModule standalone test...")

    # Dummy frame and queues
    dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(dummy_frame, "Test Frame", (50, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    dummy_db_queue = MPQueue()
    dummy_feed_id = "TestFeed_01" # Provide a feed ID for testing

    try:
        core_module = CoreModule(
            feed_id=dummy_feed_id, # Pass feed_id
            gemini_api_key=test_config['ocr_engine']['gemini_api_key'],
            model_path=test_config['vehicle_detection']['model_path'],
            config=test_config,
            fps=30,
            db_queue=dummy_db_queue
        )
        logger.info("CoreModule initialized for test.")

        # Simulate a few frames
        for i in range(5):
            frame_index = i * 5 # Simulate skipping frames
            # Simulate some movement or change detections if needed
            if i==1: cv2.rectangle(dummy_frame, (100,100), (150,150), (0,255,0), -1) # Add a "vehicle"
            if i==2: cv2.rectangle(dummy_frame, (110,110), (160,160), (0,255,0), -1) # Move it
            if i==3: cv2.rectangle(dummy_frame, (200,200), (250,250), (0,0,255), -1) # Add another

            logger.info(f"\n--- Processing frame {frame_index} ---")
            tracked = core_module.detect_and_track(dummy_frame, frame_index)
            logger.info(f"Tracked vehicles: {len(tracked)}")
            for vid, data in tracked.items():
                logger.debug(f"  ID: {vid}, Lane: {data.get('lane')}, Speed: {data.get('speed')}, Behavior: {data.get('behavior')}, Pos: {data.get('bbox')}")
            time.sleep(0.1)

        core_module.cleanup()
        logger.info("CoreModule test finished.")

    except FileNotFoundError as fnf:
        logger.error(f"Test failed: Model file not found. {fnf}")
    except Exception as e:
        logger.error(f"CoreModule test failed: {e}", exc_info=True)

    # Check if items were added to the dummy queue
    items_in_queue = 0
    while not dummy_db_queue.empty():
        try:
            dummy_db_queue.get_nowait()
            items_in_queue += 1
        except queue.Empty:
            break
    logger.info(f"Items put in dummy DB queue: {items_in_queue}")
