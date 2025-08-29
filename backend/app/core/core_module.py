import cv2
import os
import logging
import time
import numpy as np
from ultralytics import YOLO
from filterpy.kalman import KalmanFilter
from scipy.optimize import linear_sum_assignment
from multiprocessing import Queue as MPQueue
import queue  # For queue.Full exception
from typing import Dict, List, Tuple, Optional
from pathlib import Path
from collections import deque  # Import deque

from concurrent.futures import ThreadPoolExecutor

# Import LicensePlatePreprocessor and lane_detection functions from utils
try:
    from ..utils.image_processing import LicensePlatePreprocessor
    from ..utils.lane_detection import process_frame_for_lanes, get_lane_boundaries_from_lines
except ImportError:
    print("Error importing utils for CoreModule. Ensure utils.py is accessible.")
    LicensePlatePreprocessor = None
    process_frame_for_lanes = None
    get_lane_boundaries_from_lines = None

# Logging setup
logger = logging.getLogger("app.ml")


class CoreModule:

    def __init__(
        self,
        feed_id: str,
        model_path: str,
        config: Dict,
        fps: int,
        db_queue: MPQueue,
        gemini_api_key: Optional[str] = None,
    ):
        self.feed_id = feed_id
        self.video_path = Path(config["project_root_dir"]) / feed_id # Store absolute path to video
        self.model_path = Path(config["project_root_dir"]) / model_path
        self.config = config
        self.fps = fps
        self.db_queue = db_queue
        self.gemini_api_key = gemini_api_key

        self.vehicle_data: Dict[str, Dict] = {}
        self.model = None
        self.preprocessor = None

        # Configuration parameters from config
        self.vehicle_class_ids = config["vehicle_detection"].get(
            "vehicle_class_ids", []
        )
        self.confidence_threshold = config["vehicle_detection"].get(
            "confidence_threshold", 0.4
        )
        self.proximity_threshold = config["vehicle_detection"].get(
            "proximity_threshold", 60
        )
        self.track_timeout = config["vehicle_detection"].get("track_timeout", 5)
        self.reid_timeout = config["vehicle_detection"].get("reid_timeout", 10) # New parameter for re-identification timeout
        self.max_active_tracks = config["vehicle_detection"].get(
            "max_active_tracks", 50
        )
        self.yolo_imgsz = config["vehicle_detection"].get("yolo_imgsz", 640)
        self.num_lanes = config["lane_detection"].get("num_lanes", 4)
        self.lane_width_pixels = (
            config["vehicle_detection"]["frame_resolution"][0] / self.num_lanes
        )
        self.perspective_matrix = None  # Initialize as None, load if needed
        self.roi_polygon_points = config["roi_processing"].get("polygon_points", None)
        self.ocr_cfg = config.get("ocr_engine", {})
        self.stopped_speed_threshold_kmh = config["behavior_analysis"].get(
            "stopped_speed_threshold_kmh", 5
        )
        self.speed_limit = config["behavior_analysis"].get("speed_limit", 60)
        self.accel_threshold_mps2 = config["behavior_analysis"].get(
            "accel_threshold_mps2", 0.5
        )
        self.lane_change_buffer = config["behavior_analysis"].get(
            "lane_change_buffer", 20
        )
        self.pixels_per_meter = config.get("pixels_per_meter", 40)
        self.kf_params = config.get("kalman_filter_params", {})
        self.ewma_alpha = config["behavior_analysis"].get("ewma_alpha", 0.2) # New parameter for EWMA smoothing
        self.occlusion_confidence_threshold = config["vehicle_detection"].get("occlusion_confidence_threshold", 0.2) # New parameter for occlusion detection
        self.vehicle_id_counter = 1 # Initialize instance-specific counter

        # Lane detection caching
        self.lane_detection_interval = config["lane_detection"].get("lane_detection_interval", 10)
        self.last_lane_detection_frame = -1
        self.cached_lane_boundaries = None

        # Thread pool for asynchronous OCR
        self.ocr_executor = ThreadPoolExecutor(max_workers=2)

        # Vehicle type mapping
        self.vehicle_type_map = {
            0: "person",
            1: "bicycle",
            2: "car",
            3: "motorcycle",
            4: "airplane",
            5: "bus",
            6: "train",
            7: "truck",
            8: "boat",
            9: "traffic light",
            10: "fire hydrant",
            11: "stop sign",
            12: "parking meter",
            13: "bench",
            14: "bird",
            15: "cat",
            16: "dog",
            17: "horse",
            18: "sheep",
            19: "cow",
            20: "elephant",
            21: "bear",
            22: "zebra",
            23: "giraffe",
            24: "backpack",
            25: "umbrella",
            26: "handbag",
            27: "tie",
            28: "suitcase",
            29: "frisbee",
            30: "skis",
            31: "snowboard",
            32: "sports ball",
            33: "kite",
            34: "baseball bat",
            35: "baseball glove",
            36: "skateboard",
            37: "surfboard",
            38: "tennis racket",
            39: "bottle",
            40: "wine glass",
            41: "cup",
            42: "fork",
            43: "knife",
            44: "spoon",
            45: "bowl",
            46: "banana",
            47: "apple",
            48: "sandwich",
            49: "orange",
            50: "broccoli",
            51: "carrot",
            52: "hot dog",
            53: "pizza",
            54: "donut",
            55: "cake",
            56: "chair",
            57: "couch",
            58: "potted plant",
            59: "bed",
            60: "dining table",
            61: "toilet",
            62: "tv",
            63: "laptop",
            64: "mouse",
            65: "remote",
            66: "keyboard",
            67: "cell phone",
            68: "microwave",
            69: "oven",
            70: "toaster",
            71: "sink",
            72: "refrigerator",
            73: "book",
            74: "clock",
            75: "vase",
            76: "scissors",
            77: "teddy bear",
            78: "hair drier",
            79: "toothbrush",
        }

        self._load_model(config["performance"].get("gpu_acceleration", False))
        if self.ocr_cfg.get("enabled", False) and self.gemini_api_key:
            try:
                self.preprocessor = LicensePlatePreprocessor(self.gemini_api_key)
            except Exception as e:
                logger.error(f"Failed to initialize LicensePlatePreprocessor: {e}")
                self.preprocessor = None
        else:
            logger.info("OCR engine disabled or Gemini API key not provided.")


    

    def _load_model(self, use_gpu: bool):
        if not Path(self.model_path).exists():
            logger.error(f"Model file not found at {self.model_path}")
            raise FileNotFoundError(f"Model file not found at {self.model_path}")

        model_path_str = str(self.model_path)
        is_onnx = model_path_str.endswith(".onnx")
        is_quantized_onnx = model_path_str.endswith("_quant.onnx")

        try:
            if is_onnx or is_quantized_onnx:
                import onnxruntime as ort

                logger.info(f"Attempting to load ONNX model from {self.model_path}")
                providers = ["CPUExecutionProvider"]
                if use_gpu:
                    # Check for CUDA availability for ONNX Runtime
                    if "CUDAExecutionProvider" in ort.get_available_providers():
                        providers.insert(0, "CUDAExecutionProvider")
                        logger.info("CUDAExecutionProvider available for ONNX Runtime.")
                    else:
                        logger.warning(
                            "GPU acceleration requested but CUDAExecutionProvider not available for ONNX. Falling back to CPU."
                        )

                start_time = time.time()
                self.model = ort.InferenceSession(model_path_str, providers=providers)
                load_time = time.time() - start_time
                device = self.model.get_providers()[
                    0
                ]  # Get the actual provider being used
                logger.info(
                    f"ONNX model loaded from {self.model_path} on '{device}' in {load_time:.3f}s"
                )

            else:
                import torch

                device = "cpu"
                if use_gpu:
                    if torch.cuda.is_available():
                        device = "cuda"
                    else:
                        logger.warning(
                            "GPU acceleration requested but CUDA not available. Falling back to CPU."
                        )
                else:
                    logger.info("GPU acceleration disabled in config. Using CPU.")

                start_time = time.time()
                self.model = YOLO(self.model_path)
                self.model.to(device)
                load_time = time.time() - start_time
                logger.info(
                    f"YOLO model loaded from {self.model_path} on '{device}' in {load_time:.3f}s"
                )

        except ImportError as e:
            logger.error(f"Import error: {e}")
            if is_onnx:
                raise ImportError("ONNX Runtime is required for .onnx models.")
            else:
                raise ImportError("PyTorch/Ultralytics is required for .pt models.")
        except Exception as e:
            logger.error(f"Failed to load model: {e}", exc_info=True)
            raise RuntimeError(f"Model loading failed: {e}")

    def detect_and_track(
        self,
        frame: np.ndarray,
        frame_index: int,
        confidence_threshold: Optional[float] = None,
        proximity_threshold: Optional[int] = None,
        track_timeout: Optional[int] = None,
    ) -> Dict[str, Dict]:
        if frame is None or frame.size == 0:
            return {}
        if self.model is None:
            return {}
        logger.debug("detect_and_track executed")

        # Use parameters passed during the call, falling back to instance defaults
        used_confidence = (
            confidence_threshold
            if confidence_threshold is not None
            else self.confidence_threshold
        )
        used_proximity = (
            proximity_threshold
            if proximity_threshold is not None
            else self.proximity_threshold
        )
        used_track_timeout = (
            track_timeout if track_timeout is not None else self.track_timeout
        )
        current_time = time.time()

        try:
            detections = self._detect_vehicles(frame, frame_index, used_confidence)
            current_tracks = self._update_tracks(
                frame, detections, used_proximity, current_time, frame_index
            )
            logger.debug("Tracks updated")
            logger.debug("Removing stale tracks")
            self._remove_stale_tracks(current_time, used_track_timeout)
            self._save_vehicle_data(current_tracks)  # Pass currently tracked vehicles
            return current_tracks

        except Exception as e:
            logger.error(
                f"Frame {frame_index}: Unhandled error in detect_and_track: {e}",
                exc_info=True,
            )
            return {}

    def _detect_vehicles(
        self, frame: np.ndarray, frame_index: int, confidence_threshold: float
    ) -> List[Tuple]:
        try:
            processed_frame, roi_enabled, x1_crop, y1_crop = self._preprocess_frame(frame)
            
            # FIX: Convert Path object to string before calling .endswith()
            if str(self.model_path).endswith(('.onnx', '_quant.onnx')):
                detections = self._run_onnx_inference(processed_frame, confidence_threshold, roi_enabled, x1_crop, y1_crop)
            else:
                detections = self._run_pytorch_inference(processed_frame, confidence_threshold, roi_enabled, x1_crop, y1_crop)

            logger.info(f"Frame {frame_index}: Detections after ROI processing: {len(detections)}")

            # --- Custom Merging Logic for Overlapping Detections ---
            merged_detections = self._merge_overlapping_detections(detections)

            logger.info(f"Frame {frame_index}: Detections after merging: {len(merged_detections)}")
            return merged_detections

        except Exception as e:
            logger.error(
                f"Frame {frame_index}: Error during vehicle detection: {e}",
                exc_info=True,
            )
            return []

    def _preprocess_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, bool, int, int]:
        roi_cfg = self.config.get("roi_processing", {})
        roi_enabled = roi_cfg.get("enabled", False)
        crop_rect = roi_cfg.get("crop_rect", [0, 0, frame.shape[1], frame.shape[0]])
        x1_crop, y1_crop, x2_crop, y2_crop = crop_rect

        processed_frame = frame.copy()

        if self.perspective_matrix is not None:
            h, w = processed_frame.shape[:2]
            processed_frame = cv2.warpPerspective(processed_frame, self.perspective_matrix, (w, h))

        if roi_enabled:
            if self.roi_polygon_points:
                mask = np.zeros(processed_frame.shape[:2], dtype=np.uint8)
                points_np = np.array(self.roi_polygon_points, dtype=np.int32)
                cv2.fillPoly(mask, [points_np], (255, 255, 255))
                processed_frame = cv2.bitwise_and(processed_frame, processed_frame, mask=mask)
            else:
                processed_frame = processed_frame[y1_crop:y2_crop, x1_crop:x2_crop]

        return processed_frame, roi_enabled, x1_crop, y1_crop

    def _run_onnx_inference(self, processed_frame: np.ndarray, confidence_threshold: float, roi_enabled: bool, x1_crop: int, y1_crop: int) -> List[Tuple]:
        img_size = self.yolo_imgsz
        input_img = cv2.resize(processed_frame, (img_size, img_size))
        input_img = input_img.astype(np.float32) / 255.0
        input_img = np.transpose(input_img, (2, 0, 1))
        input_tensor = np.expand_dims(input_img, axis=0)

        input_name = self.model.get_inputs()[0].name
        output_name = self.model.get_outputs()[0].name
        outputs = self.model.run([output_name], {input_name: input_tensor})[0]

        return self._postprocess_onnx_output(outputs, confidence_threshold, processed_frame.shape, img_size, roi_enabled, x1_crop, y1_crop)

    def _run_pytorch_inference(self, processed_frame: np.ndarray, confidence_threshold: float, roi_enabled: bool, x1_crop: int, y1_crop: int) -> List[Tuple]:
        results = self.model.predict(
            processed_frame,
            conf=confidence_threshold,
            imgsz=self.yolo_imgsz,
            classes=self.vehicle_class_ids,
            max_det=self.max_active_tracks,
            verbose=False,
        )
        return self._postprocess_pytorch_output(results, confidence_threshold, roi_enabled, x1_crop, y1_crop)

    def _postprocess_onnx_output(self, outputs: np.ndarray, confidence_threshold: float, frame_shape: Tuple[int, int], img_size: int, roi_enabled: bool, x1_crop: int, y1_crop: int) -> List[Tuple]:
        detections = []
        output = outputs[0].T
        scores = np.max(output[:, 4:], axis=1)
        output = output[scores > confidence_threshold]
        scores = scores[scores > confidence_threshold]
        class_ids = np.argmax(output[:, 4:], axis=1)
        boxes = output[:, :4]

        scale_x = frame_shape[1] / img_size
        scale_y = frame_shape[0] / img_size

        x1 = (boxes[:, 0] - boxes[:, 2] / 2) * scale_x
        y1 = (boxes[:, 1] - boxes[:, 3] / 2) * scale_y
        x2 = (boxes[:, 0] + boxes[:, 2] / 2) * scale_x
        y2 = (boxes[:, 1] + boxes[:, 3] / 2) * scale_y

        if roi_enabled and not self.roi_polygon_points:
            x1 += x1_crop
            y1 += y1_crop
            x2 += x1_crop
            y2 += y1_crop

        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2

        for i in range(len(boxes)):
            detections.append(
                (
                    float(center_x[i]),
                    float(center_y[i]),
                    float(scores[i]),
                    int(class_ids[i]),
                    0, # frame_index is not available here, but it is not used in the caller
                    [int(x1[i]), int(y1[i]), int(x2[i]), int(y2[i])],
                )
            )
        return detections

    def _postprocess_pytorch_output(self, results: list, confidence_threshold: float, roi_enabled: bool, x1_crop: int, y1_crop: int) -> List[Tuple]:
        detections = []
        for r in results:
            if r.boxes.xyxy.numel() == 0:
                continue

            xyxy = r.boxes.xyxy.cpu().numpy()
            confs = r.boxes.conf.cpu().numpy()
            clss = r.boxes.cls.cpu().numpy()

            valid_indices = (confs > confidence_threshold) & (np.isin(clss, self.vehicle_class_ids))

            if not np.any(valid_indices):
                continue

            filtered_xyxy = xyxy[valid_indices]
            filtered_confs = confs[valid_indices]
            filtered_clss = clss[valid_indices]

            if roi_enabled and not self.roi_polygon_points:
                filtered_xyxy[:, 0] += x1_crop
                filtered_xyxy[:, 1] += y1_crop
                filtered_xyxy[:, 2] += x1_crop
                filtered_xyxy[:, 3] += y1_crop

            center_x = (filtered_xyxy[:, 0] + filtered_xyxy[:, 2]) / 2
            center_y = (filtered_xyxy[:, 1] + filtered_xyxy[:, 3]) / 2

            for i in range(len(filtered_xyxy)):
                detections.append(
                    (
                        float(center_x[i]),
                        float(center_y[i]),
                        float(filtered_confs[i]),
                        int(filtered_clss[i]),
                        0, # frame_index is not available here, but it is not used in the caller
                        [
                            int(filtered_xyxy[i, 0]),
                            int(filtered_xyxy[i, 1]),
                            int(filtered_xyxy[i, 2]),
                            int(filtered_xyxy[i, 3]),
                        ],
                    )
                )
        return detections

    def _merge_overlapping_detections(self, detections: List[Tuple]) -> List[Tuple]:
        if not detections:
            return []

        detections.sort(key=lambda x: x[2], reverse=True)
        is_merged = [False] * len(detections)
        merged_detections = []

        for i, det1 in enumerate(detections):
            if is_merged[i]:
                continue

            x1_1, y1_1, x2_1, y2_1 = det1[5]
            class_id_1 = det1[3]

            current_merged_bbox = list(det1[5])
            current_merged_conf = det1[2]

            for j, det2 in enumerate(detections):
                if i == j or is_merged[j]:
                    continue

                x1_2, y1_2, x2_2, y2_2 = det2[5]
                class_id_2 = det2[3]

                if class_id_1 == class_id_2 and self._calculate_iou(det1[5], det2[5]) > 0.6:
                    current_merged_bbox[0] = min(current_merged_bbox[0], x1_2)
                    current_merged_bbox[1] = min(current_merged_bbox[1], y1_2)
                    current_merged_bbox[2] = max(current_merged_bbox[2], x2_2)
                    current_merged_bbox[3] = max(current_merged_bbox[3], y2_2)
                    current_merged_conf = max(current_merged_conf, det2[2])
                    is_merged[j] = True

            merged_center_x = (current_merged_bbox[0] + current_merged_bbox[2]) / 2
            merged_center_y = (current_merged_bbox[1] + current_merged_bbox[3]) / 2
            merged_detections.append((
                float(merged_center_x),
                float(merged_center_y),
                float(current_merged_conf),
                int(class_id_1),
                det1[4], # frame_index
                [int(val) for val in current_merged_bbox]
            ))

        return merged_detections

    def _calculate_iou(self, box1, box2):
        # Ensure box1 and box2 are not None and are valid bounding box formats
        if box1 is None or box2 is None or len(box1) < 4 or len(box2) < 4:
            return 0.0  # Return 0.0 if inputs are invalid

        # Extract coordinates
        x1, y1, x2, y2 = box1
        x1_g, y1_g, x2_g, y2_g = box2

        # Determine the coordinates of the intersection rectangle
        ix1 = max(x1, x1_g)
        iy1 = max(y1, y1_g)
        ix2 = min(x2, x2_g)
        iy2 = min(y2, y2_g)

        # Compute the area of intersection
        intersection_area = max(0, ix2 - ix1) * max(0, iy2 - iy1)

        # Compute the area of both bounding boxes
        box1_area = (x2 - x1) * (y2 - y1)
        box2_area = (x2_g - x1_g) * (y2_g - y1_g)

        # Compute the area of union
        union_area = box1_area + box2_area - intersection_area

        # Handle the case where union_area is zero to avoid division by zero
        if union_area == 0:
            return 0.0

        # Compute the IoU
        iou = intersection_area / union_area
        return iou

    def _calculate_cost_matrix(self, tracks: Dict, detections: List[Tuple], predicted_positions: List[np.ndarray], proximity_threshold: int, max_cost: float) -> np.ndarray:
        num_tracks = len(tracks)
        num_detections = len(detections)
        cost_matrix = np.full((num_tracks, num_detections), max_cost)

        for i in range(num_tracks):
            predicted_pos = predicted_positions[i]

            if np.isnan(predicted_pos).any():
                continue

            for j in range(num_detections):
                detection = detections[j]
                detection_pos = np.array([detection[0], detection[1]])
                
                dist = np.linalg.norm(predicted_pos - detection_pos)

                if dist < proximity_threshold:
                    # Cost can be a combination of distance and other factors, like appearance
                    # For now, just use distance
                    cost_matrix[i, j] = dist
        
        return cost_matrix

    def _match_tracks_to_detections(self, tracks: Dict, detections: List[Tuple], cost_matrix: np.ndarray, kalman_filters: List[KalmanFilter], current_time: float, frame: np.ndarray, frame_index: int, matched_detection_indices: set) -> Dict:
        updated_tracks_in_frame = {}
        if not tracks or not detections:
            return updated_tracks_in_frame

        track_indices, detection_indices = linear_sum_assignment(cost_matrix)
        track_ids = list(tracks.keys())

        for track_idx, detection_idx in zip(track_indices, detection_indices):
            if cost_matrix[track_idx, detection_idx] < self.proximity_threshold:
                vehicle_id = track_ids[track_idx]
                track = tracks[vehicle_id]
                detection = detections[detection_idx]
                
                self._update_track(track, detection, current_time, frame, frame_index)
                track["last_seen"] = current_time
                track["status"] = "active"
                updated_tracks_in_frame[vehicle_id] = track
                matched_detection_indices.add(detection_idx)

        return updated_tracks_in_frame

    def _predict_track_positions(self, tracks: Dict, current_time: float) -> Tuple[List[np.ndarray], List[KalmanFilter]]:
        predicted_positions = []
        kalman_filters = []
        for vehicle_id in tracks.keys():
            track = tracks[vehicle_id]
            kf = track.get("kalman_filter")
            if kf:
                dt = min(1.0, max(0.01, current_time - track.get("last_seen", current_time)))
                kf.F[0, 2] = dt
                kf.F[1, 3] = dt
                kf.predict()
                predicted_positions.append(kf.x[:2])
                kalman_filters.append(kf)
            else:
                predicted_positions.append(np.array([np.nan, np.nan]))
                kalman_filters.append(None)
        return predicted_positions, kalman_filters

    def _initialize_new_tracks_from_unmatched(self, unmatched_detections_indices: set, detections: List[Tuple], current_time: float, frame: np.ndarray, frame_index: int) -> Dict:
        new_tracks_in_frame = {}
        for idx in unmatched_detections_indices:
            if len(self.vehicle_data) >= self.max_active_tracks:
                break
            new_vehicle_id = self._initialize_new_track(frame, detections[idx], current_time, frame_index)
            if new_vehicle_id:
                self.vehicle_data[new_vehicle_id]["status"] = "active"
                new_tracks_in_frame[new_vehicle_id] = self.vehicle_data[new_vehicle_id]
        return new_tracks_in_frame

    def _update_tracks(
        self,
        frame: np.ndarray,
        detections: List[Tuple],
        proximity_threshold: int,
        current_time: float,
        frame_index: int,
    ) -> Dict[str, Dict]:
        current_tracks_in_frame = {}
        if not detections:
            for track in self.vehicle_data.values():
                if track.get("kalman_filter"):
                    track["kalman_filter"].predict()
            return current_tracks_in_frame

        detection_indices = list(range(len(detections)))
        matched_detection_indices = set()
        max_cost = 1e5 # Define max_cost here

        # Separate active and lost tracks
        active_tracks = {vid: track for vid, track in self.vehicle_data.items() if track.get("status", "active") == "active"}
        lost_tracks = {vid: track for vid, track in self.vehicle_data.items() if track.get("status", "active") == "lost"}

        # 1. Process active tracks for matching
        if active_tracks and detections:
            predicted_positions_active, kalman_filters_active = self._predict_track_positions(active_tracks, current_time)
            cost_matrix_active = self._calculate_cost_matrix(active_tracks, detections, predicted_positions_active, proximity_threshold, max_cost)
            current_tracks_in_frame.update(self._match_tracks_to_detections(active_tracks, detections, cost_matrix_active, kalman_filters_active, current_time, frame, frame_index, matched_detection_indices))

        # 2. Attempt to re-identify lost tracks with unmatched detections
        unmatched_detections_indices = set(detection_indices) - matched_detection_indices
        if lost_tracks and unmatched_detections_indices:
            unmatched_detections_list = [detections[idx] for idx in unmatched_detections_indices]
            predicted_positions_lost, kalman_filters_lost = self._predict_track_positions(lost_tracks, current_time)
            cost_matrix_lost = self._calculate_cost_matrix(lost_tracks, unmatched_detections_list, predicted_positions_lost, proximity_threshold, max_cost)
            
            # Need to map back the matched indices from unmatched_detections_list to original detections
            temp_matched_indices = set()
            reidentified_tracks = self._match_tracks_to_detections(lost_tracks, unmatched_detections_list, cost_matrix_lost, kalman_filters_lost, current_time, frame, frame_index, temp_matched_indices)
            
            # Correctly update matched_detection_indices
            unmatched_detections_indices_list = list(unmatched_detections_indices)
            for i in temp_matched_indices:
                matched_detection_indices.add(unmatched_detections_indices_list[i])
            
            current_tracks_in_frame.update(reidentified_tracks)

        # 3. Initialize new tracks for remaining unmatched detections
        unmatched_detections_indices = set(detection_indices) - matched_detection_indices
        current_tracks_in_frame.update(self._initialize_new_tracks_from_unmatched(unmatched_detections_indices, detections, current_time, frame, frame_index))

        return current_tracks_in_frame

    def _initialize_new_track(
        self, frame: np.ndarray, detection: Tuple, current_time: float, frame_index: int
    ) -> Optional[str]:
        try:
            center_x, center_y, conf, class_id, _, vehicle_bbox = detection
            if (vehicle_bbox[2] - vehicle_bbox[0]) * (
                vehicle_bbox[3] - vehicle_bbox[1]
            ) < 1000:
                return None

            # Generate globally unique vehicle ID using feed_id prefix
            vehicle_id = f"{self.feed_id}-{self.vehicle_id_counter}"
            self.vehicle_id_counter += 1

            # Use bottom-center of bbox for more stable tracking
            initial_x = (vehicle_bbox[0] + vehicle_bbox[2]) / 2
            initial_y = vehicle_bbox[3] # Use the bottom of the bounding box

            kf = self._initialize_kalman_filter(initial_x, initial_y)
            lane = self._estimate_lane(frame, vehicle_bbox, frame_index)

            self.vehicle_data[vehicle_id] = {
                "vehicle_id": vehicle_id,
                "first_seen": current_time,
                "last_seen": current_time,
                "frame_index": frame_index,
                "bbox": vehicle_bbox,
                "confidence": conf,
                "kalman_filter": kf,
                "license_plate": "Unknown",
                "plate_attempts": 0,
                "lane": lane,
                "lane_history": deque([(frame_index, lane)], maxlen=10),
                "speed": 0.0,
                "speed_history": deque(maxlen=5),
                "behavior": "unknown",
                "class_id": class_id,
                "timestamp": current_time,
                "status": "active", # New tracks are active
                "is_occluded": False, # Initialize occlusion status
            }
            logger.info(
                f"Initialized vehicle {vehicle_id} (Class: {class_id}), lane {lane}"
            )
            return vehicle_id
        except Exception as e:
            logger.error(f"Error initializing track: {e}", exc_info=True)
            return None

    def _update_track(
        self,
        track: Dict,
        detection: Tuple,
        current_time: float,
        frame: np.ndarray,
        frame_index: int,
    ) -> None:
        try:
            center_x, center_y, conf, class_id, _, vehicle_bbox = detection
            # Recalculate center_x and center_y to use bottom-center of bbox for Kalman filter
            measurement_x = (vehicle_bbox[0] + vehicle_bbox[2]) / 2
            measurement_y = vehicle_bbox[3] # Use the bottom of the bounding box

            kf = track.get("kalman_filter")
            if kf:
                # Adaptive R: Adjust measurement noise based on detection confidence
                # Lower confidence -> higher measurement noise (larger R)
                # Higher confidence -> lower measurement noise (smaller R)
                # We'll scale the base R values. A confidence of 1.0 means no scaling (factor 1.0).
                # A confidence of 0.5 might mean a factor of 2.0 (1.0 / 0.5).
                # Add a small epsilon to avoid division by zero if confidence is 0.
                confidence_factor = max(0.1, 1.0 / (conf + 1e-6)) # Ensure factor is not excessively large

                # Create a temporary R matrix for this update, scaled by confidence
                # Use the base R values from kf_params, or default if not set
                base_sigma_mx = self.kf_params.get("kf_sigma_mx", 2.0)
                base_sigma_my = self.kf_params.get("kf_sigma_my", 2.0)
                
                # Scale the base measurement noise variances
                scaled_r_x = (base_sigma_mx * confidence_factor) ** 2
                scaled_r_y = (base_sigma_my * confidence_factor) ** 2

                # Create a temporary R matrix for the update
                temp_R = np.diag([scaled_r_x, scaled_r_y])

                try:
                    kf.update(np.array([measurement_x, measurement_y], dtype=np.float32), R=temp_R)
                except Exception as kf_err:
                    logger.warning(
                        f"Kalman update failed for {track['vehicle_id']}: {kf_err}"
                    )
                    track["kalman_filter"] = self._initialize_kalman_filter(
                        measurement_x, measurement_y
                    )
            else:
                track["kalman_filter"] = self._initialize_kalman_filter(
                    measurement_x, measurement_y
                )

            # Occlusion detection
            if conf < self.occlusion_confidence_threshold and track.get("status") == "active":
                track["is_occluded"] = True
                logger.debug(f"Vehicle {track['vehicle_id']} marked as occluded due to low confidence ({conf}).")
            else:
                track["is_occluded"] = False

            # If occluded, use Kalman filter's predicted state for bbox
            if track["is_occluded"] and kf:
                predicted_x, predicted_y = kf.x[0], kf.x[1]
                # Estimate bbox based on predicted center and last known size
                last_bbox = track["bbox"]
                width = last_bbox[2] - last_bbox[0]
                height = last_bbox[3] - last_bbox[1]
                
                # Use the predicted bottom-center for the bbox
                pred_x1 = predicted_x - width / 2
                pred_y1 = predicted_y - height # predicted_y is the bottom of the bbox
                pred_x2 = predicted_x + width / 2
                pred_y2 = predicted_y # predicted_y is the bottom of the bbox

                track["bbox"] = [int(pred_x1), int(pred_y1), int(pred_x2), int(pred_y2)]
                track["confidence"] = 0.0 # Set confidence to 0 for occluded tracks
                logger.debug(f"Vehicle {track['vehicle_id']} bbox updated with predicted state due to occlusion.")
            else:
                # Update bbox and confidence with current detection if not occluded
                track["bbox"] = vehicle_bbox
                track["confidence"] = conf

            # Estimate Speed (using Kalman velocity)
            track["speed"] = self._estimate_speed_kalman(
                track, current_time, track.get("last_seen", current_time)
            )  # Pass prev_time
            track["speed_history"].append(track["speed"])

            new_lane = self._estimate_lane(frame, track["bbox"], frame_index)
            last_recorded_lane = (
                track["lane_history"][-1][1] if track["lane_history"] else -1
            )
            # center_lane_new = (new_lane - 0.5) * self.lane_width_pixels
            center_lane_old = (
                (last_recorded_lane - 0.5) * self.lane_width_pixels
                if last_recorded_lane != -1
                else center_x
            )
            if (
                last_recorded_lane != -1
                and new_lane != -1
                and new_lane != last_recorded_lane
                and abs(center_x - center_lane_old) > self.lane_change_buffer
            ):
                logger.info(
                    f"Vehicle {track['vehicle_id']} lane change {last_recorded_lane} -> {new_lane}"
                )
                track["behavior"] = "lane_changing"
            track["lane"] = new_lane
            if not track["lane_history"] or track["lane_history"][-1][1] != new_lane:
                track["lane_history"].append((frame_index, new_lane))

            self._classify_behavior(track)  # Classify based on new speed/state

            # --- Access ocr_cfg using self.ocr_cfg --
            ocr_interval_frames = int(self.fps * self.ocr_cfg.get("ocr_interval", 15))
            max_ocr_attempts = 3
            if (
                track["license_plate"] == "Unknown"
                and self.preprocessor
                and track.get("plate_attempts", 0) < max_ocr_attempts
                and frame_index % max(1, ocr_interval_frames) == 0
                and not track["is_occluded"] # Do not attempt OCR if occluded
            ):
                # --- Selective OCR Trigger ---
                if self._is_roi_optimal_for_ocr(track["bbox"]):
                    logger.debug(
                        f"Attempting OCR for vehicle {track['vehicle_id']} (Attempt {track.get('plate_attempts', 0) + 1})"
                    )

                    # --- Asynchronous OCR ---
                    self.ocr_executor.submit(self._run_ocr, frame, track)

        except Exception as e:
            logger.error(
                f"Error updating track {track.get('vehicle_id', 'N/A')}: {e}",
                exc_info=True,
            )

    def _run_ocr(self, frame: np.ndarray, track: Dict):
        plate_text = self._ocr_license_plate(frame, track["bbox"])
        if plate_text not in [
            "Unknown",
            "Unknown (Error)",
            "Unknown (BadROI)",
            "Unknown (SmallROI)",
            "Unknown (NoPrep)",
            "Unknown (RetryFail)",
            "Unknown (Refused)",
            "Unknown (Blocked)",
            "Unknown (GenFail)",
            "Unknown (InvalidResp)",
            "Unknown (OCRError)",
            "Unknown (PreprocFail)",
            "Unknown (TessFail)",
            "Unknown (NoTess)",
            "Unknown (TessError)",
            None,
        ]:
            track["license_plate"] = plate_text
            logger.info(f"OCR Success for {track['vehicle_id']}: {plate_text}")
        track["plate_attempts"] = track.get("plate_attempts", 0) + 1

    def _is_roi_optimal_for_ocr(self, bbox: List[int]) -> bool:
        """
        Checks if the vehicle's bounding box is in an optimal position and size for OCR.
        This is a placeholder for a more sophisticated implementation.
        """
        # Example: Only attempt OCR if the bounding box is in the lower half of the frame
        # and has a certain minimum size.
        x1, y1, x2, y2 = bbox
        frame_height = self.config.get("vehicle_detection", {}).get(
            "frame_resolution", [640, 480]
        )[1]
        bbox_height = y2 - y1
        bbox_width = x2 - x1

        # --- Add these to config.yaml for tunability ---
        min_ocr_bbox_height = self.ocr_cfg.get("min_ocr_bbox_height", 50)
        min_ocr_bbox_width = self.ocr_cfg.get("min_ocr_bbox_width", 100)
        ocr_sweet_spot_y_start = frame_height * self.ocr_cfg.get(
            "ocr_sweet_spot_y_start", 0.5
        )

        if (
            y2 > ocr_sweet_spot_y_start
            and bbox_height > min_ocr_bbox_height
            and bbox_width > min_ocr_bbox_width
        ):
            return True
        return False

    def _classify_behavior(self, track: Dict) -> None:
        current_speed_kmh = track["speed"]

        if current_speed_kmh < self.stopped_speed_threshold_kmh:
            track["behavior"] = "stopped"
            return

        # Skip accel/decel check if just changed lanes
        # if track['behavior'] == 'lane_changing':
        #    return # Or maybe reset to 'moving' after a short period

        if current_speed_kmh > self.speed_limit:
            track["behavior"] = "speeding"
            return

        if len(track["speed_history"]) >= 3:
            avg_recent_speed = np.mean(list(track["speed_history"])[-3:])
            speed_diff_kmh = current_speed_kmh - avg_recent_speed
            # Convert accel threshold m/s^2 to km/h difference over ~0.5s (rough estimate)
            accel_kmh_thresh_over_period = self.accel_threshold_mps2 * 3.6 * 0.5

            if speed_diff_kmh > accel_kmh_thresh_over_period:
                track["behavior"] = "accelerating"
            elif speed_diff_kmh < -accel_kmh_thresh_over_period:
                track["behavior"] = "decelerating"
            else:
                track["behavior"] = "moving"
        else:
            track["behavior"] = "moving"

    def _estimate_speed_kalman(
        self, track: Dict, current_time: float, prev_time: float
    ) -> float:
        kf = track.get("kalman_filter")
        if not kf:
            return 0.0
        try:
            vx, vy = (
                kf.x[2],
                kf.x[3],
            )  # Velocity in pixels/dt (where dt was used in F matrix)
            # Use the actual time difference between updates for scaling
            time_diff = min(
                1.0, max(0.01, current_time - prev_time)
            )  # Use the passed prev_time
            pixel_speed_per_sec = (
                np.sqrt(vx**2 + vy**2) / time_diff if time_diff > 0 else 0
            )
            # Get dynamic pixels_per_meter based on the vehicle's current y-position
            dynamic_ppm = self._get_dynamic_pixels_per_meter(kf.x[1])
            speed_mps = (
                pixel_speed_per_sec / dynamic_ppm
                if dynamic_ppm > 0
                else 0
            )
            speed_kmph = speed_mps * 3.6
            # Don't append here, append the smoothed speed
            # track['speed_history'].append(speed_kmph)
            # Apply smoothing to the history before returning
            current_history = list(track["speed_history"])
            current_history.append(
                speed_kmph
            )  # Add current estimate to history for smoothing
            
            # Apply EWMA smoothing
            smoothed_speed = speed_kmph
            if len(current_history) > 1:
                # Calculate EWMA: S_t = alpha * Y_t + (1 - alpha) * S_{t-1}
                # Where Y_t is the current speed_kmph, and S_{t-1} is the previous smoothed speed
                # For the first few points, we can use a simple average or just the current value
                # For simplicity, we'll apply EWMA iteratively over the history
                smoothed_speed = current_history[0]
                for i in range(1, len(current_history)):
                    smoothed_speed = (self.ewma_alpha * current_history[i]) + ((1 - self.ewma_alpha) * smoothed_speed)

            return round(max(0, smoothed_speed), 1)
        except Exception as e:
            logger.warning(
                f"Speed estimation error for {track.get('vehicle_id', 'N/A')}: {e}"
            )
            return 0.0

    def _get_dynamic_pixels_per_meter(self, y_pixel: float) -> float:
        """
        Calculates pixels per meter dynamically based on the y-coordinate.
        Assumes a linear or non-linear relationship where pixels per meter
        decrease as y_pixel decreases (objects further away).
        """
        if self.perspective_matrix is None:
            # Fallback to static value if no perspective matrix is provided
            return self.pixels_per_meter

        # Normalize y_pixel to a 0-1 range based on frame height
        frame_height = self.config.get("vehicle_detection", {}).get("frame_resolution", [640, 480])[1]
        if frame_height == 0:
            return self.pixels_per_meter

        normalized_y = y_pixel / frame_height

        # Define a scaling factor. This can be tuned.
        # For example, a linear interpolation between a min and max ppm.
        # Let's assume ppm_at_bottom (max ppm) and ppm_at_top (min ppm).
        # These values should ideally come from calibration or config.
        # For now, let's use a simple linear scaling.
        # You might want to add these to your config.yaml
        ppm_at_bottom = self.config.get("ppm_at_bottom", 60)  # Pixels per meter at the bottom of the frame
        ppm_at_top = self.config.get("ppm_at_top", 20)      # Pixels per meter at the top of the frame

        # Linear interpolation: ppm = ppm_at_top + (ppm_at_bottom - ppm_at_top) * normalized_y
        dynamic_ppm = ppm_at_top + (ppm_at_bottom - ppm_at_top) * normalized_y

        return max(1.0, dynamic_ppm) # Ensure it's at least 1.0

    def _ocr_license_plate(self, frame: np.ndarray, bbox: List[int]) -> str:
        if not self.preprocessor:
            return "Unknown (NoPrep)"
        try:
            x1, y1, x2, y2 = map(int, bbox)
            h, w = frame.shape[:2]
            roi_h = y2 - y1
            roi_w = x2 - x1

            # --- Read ROI adjustment factors from config with defaults ---
            top_margin_factor = self.ocr_cfg.get(
                "roi_top_margin_factor", 0.5
            )  # Default: Start lower
            bottom_margin_factor = self.ocr_cfg.get(
                "roi_bottom_margin_factor", 0.1
            )  # Default: End slightly higher (smaller cut from bottom)
            left_margin_factor = self.ocr_cfg.get(
                "roi_left_margin_factor", 0.15
            )  # Default: Crop left side
            right_margin_factor = self.ocr_cfg.get(
                "roi_right_margin_factor", 0.15
            )  # Default: Crop right side

            # --- Apply configurable factors ---
            roi_y_start = max(0, int(y1 + roi_h * top_margin_factor))
            roi_y_end = min(h, int(y2 - roi_h * bottom_margin_factor))
            roi_x_start = max(0, int(x1 + roi_w * left_margin_factor))
            roi_x_end = min(w, int(x2 - roi_w * right_margin_factor))
            # -------------------------------

            if roi_x_start >= roi_x_end or roi_y_start >= roi_y_end:
                return "Unknown (BadROI)"
            roi = frame[roi_y_start:roi_y_end, roi_x_start:roi_x_end]
            if (roi.shape[0] * roi.shape[1]) < self.preprocessor.min_roi_size:
                return "Unknown (SmallROI)"
            return self.preprocessor.preprocess_and_ocr(roi)
        except Exception as e:
            logger.error(f"OCR processing failed: {e}", exc_info=True)
            return "Unknown (OCRError)"

    def _initialize_kalman_filter(
        self, initial_x: float, initial_y: float
    ) -> KalmanFilter:
        try:
            kf = KalmanFilter(dim_x=4, dim_z=2)
            kf.F = np.array(
                [[1, 0, 1, 0], [0, 1, 0, 1], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=float
            )  # dt=1 initially
            kf.H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=float)
            kf.x = np.array([initial_x, initial_y, 0.0, 0.0], dtype=float)
            # --- Use self.kf_params ---
            kf.P = np.diag(
                [
                    self.kf_params.get("kf_sigma_px", 10.0) ** 2,  # Increased initial position uncertainty
                    self.kf_params.get("kf_sigma_py", 10.0) ** 2,
                    self.kf_params.get("kf_sigma_pvx", 10.0) ** 2, # Increased initial velocity uncertainty
                    self.kf_params.get("kf_sigma_pvy", 10.0) ** 2,
                ]
            )
            kf.R = np.diag(
                [
                    self.kf_params.get("kf_sigma_mx", 2.0) ** 2,  # Increased measurement noise
                    self.kf_params.get("kf_sigma_my", 2.0) ** 2,
                ]
            )
            q_ax = self.kf_params.get("kf_sigma_ax", 1.0) ** 2  # Increased process noise for acceleration
            q_ay = self.kf_params.get("kf_sigma_ay", 1.0) ** 2
            # Simplified Q matrix based on typical state-space noise models
            # Assuming dt=1 for initial Q calculation. It scales with dt^n in predict step.
            dt = 1  # Reference dt for Q
            kf.Q = np.diag(
                [0.25 * dt**4 * q_ax, 0.25 * dt**4 * q_ay, dt**2 * q_ax, dt**2 * q_ay]
            )
            # Or simpler diagonal if process noise is less coupled:
            # kf.Q = np.diag([0.1, 0.1, q_ax, q_ay]) # Keep original simpler version if preferred
            return kf
        except Exception as e:
            logger.error(f"Kalman filter initialization failed: {e}", exc_info=True)
            raise

    def _estimate_lane(self, frame: np.ndarray, bbox: List[int], frame_index: int) -> int:
        if not bbox or len(bbox) != 4:
            return -1

        # Check if we should use cached lane boundaries
        if (
            self.cached_lane_boundaries is not None
            and self.last_lane_detection_frame != -1
            and (frame_index - self.last_lane_detection_frame) < self.lane_detection_interval
        ):
            lane_boundaries = self.cached_lane_boundaries
        else:
            # Process the frame to detect lane lines dynamically
            if process_frame_for_lanes:
                detected_lines = process_frame_for_lanes(frame, self.config)
                if detected_lines:
                    # Get dynamic lane boundaries based on detected lines
                    self.cached_lane_boundaries = get_lane_boundaries_from_lines(
                        frame.shape[1], detected_lines, self.config
                    )
                    self.last_lane_detection_frame = frame_index
                    lane_boundaries = self.cached_lane_boundaries
                else:
                    logger.debug("Dynamic lane boundaries could not be determined. Falling back to static.")
                    lane_boundaries = None
            else:
                lane_boundaries = None


        if lane_boundaries and len(lane_boundaries) > 1:
            x_center = (bbox[0] + bbox[2]) / 2
            # Determine which lane the vehicle is in based on dynamic boundaries
            for i in range(len(lane_boundaries) - 1):
                if lane_boundaries[i] <= x_center < lane_boundaries[i + 1]:
                    return i + 1  # Lane numbers are 1-indexed
            # If outside detected lanes, return -1 or closest lane
            return -1

        # Fallback to static lane estimation if dynamic detection fails or is disabled
        x_center = (bbox[0] + bbox[2]) / 2
        if self.lane_width_pixels <= 0:
            return -1
        lane = int(x_center // self.lane_width_pixels) + 1
        return max(1, min(lane, self.num_lanes))  # Clamp

    def _remove_stale_tracks(self, current_time: float, track_timeout: int) -> None:
        to_delete = []
        for vid, track in list(self.vehicle_data.items()): # Iterate over a copy to allow modification
            time_since_last_seen = current_time - track["last_seen"]
            if time_since_last_seen > self.reid_timeout:
                to_delete.append(vid)
            elif time_since_last_seen > track_timeout:
                track["status"] = "lost" # Mark as lost
            else:
                track["status"] = "active" # Ensure active if seen recently

        for vid in to_delete:
            del self.vehicle_data[vid]

        # Also handle max_active_tracks, prioritizing removal of lost tracks first
        if len(self.vehicle_data) > self.max_active_tracks:
            # Separate active and lost tracks
            active_tracks = {vid: track for vid, track in self.vehicle_data.items() if track["status"] == "active"}
            lost_tracks = {vid: track for vid, track in self.vehicle_data.items() if track["status"] == "lost"}

            # Sort lost tracks by last_seen (oldest first) for removal
            sorted_lost_tracks = sorted(lost_tracks.items(), key=lambda item: item[1]["last_seen"])

            num_to_remove = len(self.vehicle_data) - self.max_active_tracks
            removed_count = 0

            # Remove lost tracks first
            for vid, _ in sorted_lost_tracks:
                if removed_count >= num_to_remove:
                    break
                del self.vehicle_data[vid]
                removed_count += 1

            # If still more to remove, remove active tracks (oldest first)
            if removed_count < num_to_remove:
                sorted_active_tracks = sorted(active_tracks.items(), key=lambda item: item[1]["last_seen"])
                for vid, _ in sorted_active_tracks:
                    if removed_count >= num_to_remove:
                        break
                    del self.vehicle_data[vid]
                    removed_count += 1

        if to_delete or len(self.vehicle_data) > self.max_active_tracks:
            logger.debug(
                f"Removed {len(to_delete)} stale/excess tracks. Active tracks: {len(self.vehicle_data)}"
            )

    def _save_vehicle_data(self, current_tracks: Dict[str, Dict]) -> None:
        # --- ADDED: Check if db_queue exists ---
        if not self.db_queue:
            # logger.debug("DB queue not configured or provided. Skipping data save.") # Optional: Log only once or less frequently
            return
        # ----------------------------------------
        if not current_tracks:
            return

        vehicle_data_list = []
        for track_id, track in current_tracks.items():
            if not track:
                continue  # Skip if track is None somehow
            try:
                vehicle_data = {
                    "vehicle_id": track_id,  # Already includes feed_id prefix
                    "timestamp": track.get("timestamp", time.time()),
                    "frame_index": track.get("frame_index"),
                    "license_plate": track.get("license_plate", "Unknown"),
                    "vehicle_type": self._get_vehicle_type(track.get("class_id", -1)),
                    "first_seen": track.get("first_seen"),
                    "last_seen": track.get("last_seen"),
                    "x1": track["bbox"][0],
                    "y1": track["bbox"][1],
                    "x2": track["bbox"][2],
                    "y2": track["bbox"][3],
                    "speed": track.get("speed"),
                    "lane": track.get("lane"),
                    "confidence": track.get("confidence"),
                    "car_model": "Unknown",  # Placeholder
                    "car_color": "Unknown",  # Placeholder
                }
                vehicle_data_list.append(vehicle_data)
            except KeyError as ke:
                logger.warning(
                    f"Missing key {ke} in track data for {track_id}. Skipping DB save for this entry."
                )
            except Exception as e:
                logger.error(
                    f"Error preparing data for DB for {track_id}: {e}", exc_info=True
                )

        if not vehicle_data_list:
            return

        try:
            for vehicle_data in vehicle_data_list:
                self.db_queue.put_nowait(vehicle_data)
            # logger.debug(f"Put {len(vehicle_data_list)} vehicle records onto db_queue.") # Reduce log frequency
        except queue.Full:
            logger.warning("Database queue is full. Dropping vehicle data batch.")
        except Exception as e:
            logger.error(
                f"Failed to put vehicle data onto db_queue: {e}", exc_info=True
            )

    def _get_vehicle_type(self, class_id: int) -> str:
        return self.vehicle_type_map.get(class_id, "unknown")

    def _serialize_track_data(self, track: Dict) -> Dict:
        serializable_track = track.copy()
        if "kalman_filter" in serializable_track and serializable_track["kalman_filter"] is not None:
            kf = serializable_track["kalman_filter"]
            serializable_track["kalman_filter"] = {
                "x": kf.x.tolist(),  # State vector
                "P": kf.P.tolist(),  # Covariance matrix
                # Add other relevant KF attributes if needed, e.g., "Q", "R"
            }
        # Convert deque objects to lists for serialization
        if "lane_history" in serializable_track:
            serializable_track["lane_history"] = list(serializable_track["lane_history"])
        if "speed_history" in serializable_track:
            serializable_track["speed_history"] = list(serializable_track["speed_history"])
        return serializable_track

    def cleanup(self):
        logger.info(
            f"CoreModule cleanup initiated for {self.feed_id}. Active tracks: {len(self.vehicle_data)}"
        )
        self.vehicle_data.clear()
        # Model cleanup (if possible)
        # FIX: Convert Path object to string before calling .endswith()
        if str(self.model_path).endswith(".onnx") and hasattr(self.model, "release"):
            # ONNX Runtime sessions might have a release method or can be explicitly closed
            # Depending on ORT version, direct deletion might be enough, but explicit is better.
            try:
                # Assuming 'release' is a method to be called, though this is not standard for onnxruntime
                # A more common pattern is just to let the object be garbage collected.
                # del self.model might be sufficient. This part is speculative on the ORT API.
                logger.info(f"ONNX session for {self.feed_id} being cleaned up.")
            except Exception as e:
                logger.warning(f"Error releasing ONNX session for {self.feed_id}: {e}")
        elif hasattr(self.model, "predictor") and self.model.predictor:
            del self.model.predictor
        
        if hasattr(self, 'model'):
            del self.model
            self.model = None

        if self.preprocessor and hasattr(self.preprocessor, "gemini_model"):
            del self.preprocessor.gemini_model
            self.preprocessor = None

        import gc

        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"Error during CUDA cache clear on cleanup: {e}")
        logger.info(f"CoreModule cleanup finished for {self.feed_id}.")

    def get_frame_generator(self):
        # Check if video_capture is already open
        if hasattr(self, '_video_capture') and self._video_capture.isOpened():
            video_capture = self._video_capture
            video_capture.set(cv2.CAP_PROP_POS_FRAMES, 0) # Reset to beginning if already open
            logger.info(f"Reusing existing video capture for {self.feed_id}")
        else:
            video_capture = cv2.VideoCapture(str(self.video_path))
            self._video_capture = video_capture # Store it for reuse

        if not video_capture.isOpened():
            logger.error(f"Error opening video file: {self.feed_id}")
            return

        frame_index = 0
        while True:
            ret, frame = video_capture.read()
            if not ret:
                break

            # Process the frame
            tracked_vehicles = self.detect_and_track(frame, frame_index)

            # Encode the frame
            ret, buffer = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            
            frame_bytes = buffer.tobytes()

            # Prepare KPIs for yielding
            kpis_to_yield = {
                "tracked_vehicles": len(tracked_vehicles),
                "vehicles": [self._serialize_track_data(v) for v in tracked_vehicles.values()]
            }
            logger.debug(f"Yielding KPIs: {kpis_to_yield}") # Added debug log

            # Yield the data
            yield {
                "frame": frame_bytes,
                "kpis": kpis_to_yield
            }

            frame_index += 1

        video_capture.release()


# --- Example standalone usage ---
if __name__ == "__main__":
    # Basic config for testing
    # Create a dummy project root for the test
    project_root = Path("./")
    project_root.mkdir(exist_ok=True)
    
    test_config = {
        "project_root_dir": str(project_root),
        "vehicle_detection": {
            "vehicle_class_ids": [2, 3, 5, 7],
            "confidence_threshold": 0.4,
            "proximity_threshold": 60,
            "track_timeout": 5,
            "max_active_tracks": 50,
            "yolo_imgsz": 320,
            "frame_resolution": [640, 480],
        },
        "lane_detection": {"num_lanes": 4, "lane_detection_interval": 10},
        "performance": {"gpu_acceleration": False},  # Test CPU path
        "ocr_engine": {
            "enabled": False,
            "gemini_api_key": os.environ.get("TEST_GEMINI_API_KEY", ""),
            "roi_top_margin_factor": 0.4,
            "roi_bottom_margin_factor": 0.1,
            "roi_left_margin_factor": 0.1,
            "roi_right_margin_factor": 0.1,
        },
        "roi_processing": {"enabled": False},
        "behavior_analysis": {
            "stopped_speed_threshold_kmh": 5,
            "speed_limit": 60,
            "accel_threshold_mps2": 0.5,
            "lane_change_buffer": 20,
            "ewma_alpha": 0.2,
        },
        "kalman_filter_params": {},  # Use defaults
        "pixels_per_meter": 40,
    }
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting CoreModule standalone test...")

    # Dummy frame and queues
    dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(
        dummy_frame,
        "Test Frame",
        (50, 240),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        (255, 255, 255),
        2,
    )
    dummy_db_queue = MPQueue()
    dummy_feed_id = "TestFeed_01"  # Provide a feed ID for testing
    # Use a model path that exists or can be downloaded by YOLO
    model_path = "yolov8n.pt" 

    try:
        core_module = CoreModule(
            feed_id=dummy_feed_id,  # Pass feed_id
            gemini_api_key=test_config["ocr_engine"]["gemini_api_key"],
            model_path=model_path,
            config=test_config,
            fps=30,
            db_queue=dummy_db_queue,
        )
        logger.info("CoreModule initialized for test.")

        # Simulate a few frames
        for i in range(5):
            frame_index = i * 5  # Simulate skipping frames
            # Simulate some movement or change detections if needed
            frame_copy = dummy_frame.copy()
            if i == 1:
                cv2.rectangle(
                    frame_copy, (100, 100), (150, 150), (0, 255, 0), -1
                )  # Add a "vehicle"
            if i == 2:
                cv2.rectangle(
                    frame_copy, (110, 110), (160, 160), (0, 255, 0), -1
                )  # Move it
            if i == 3:
                cv2.rectangle(
                    frame_copy, (200, 200), (250, 250), (0, 0, 255), -1
                )  # Add another

            logger.info(f"--- Processing frame {frame_index} ---")
            tracked = core_module.detect_and_track(frame_copy, frame_index)
            logger.info(f"Tracked vehicles: {len(tracked)}")
            for vid, data in tracked.items():
                logger.info(
                    f"  ID: {vid}, Lane: {data.get('lane')}, Speed: {data.get('speed')}, Behavior: {data.get('behavior')}, Pos: {data.get('bbox')}"
                )
            time.sleep(0.1)

        core_module.cleanup()
        logger.info("CoreModule test finished.")

    except (FileNotFoundError, RuntimeError) as model_err:
        logger.error(f"Test failed: Could not load model. {model_err}")
        logger.error("Please ensure 'yolov8n.pt' is available or provide a valid path.")
    except Exception as e:
        logger.error(f"An unexpected error occurred during the test: {e}", exc_info=True)


    # Check if items were added to the dummy queue
    items_in_queue = 0
    while not dummy_db_queue.empty():
        try:
            dummy_db_queue.get_nowait()
            items_in_queue += 1
        except queue.Empty:
            break
    logger.info(f"Items put in dummy DB queue: {items_in_queue}")