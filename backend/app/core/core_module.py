import cv2
import logging
import time
import math
import numpy as np
import uuid
from ultralytics import YOLO
from filterpy.kalman import KalmanFilter
from scipy.optimize import linear_sum_assignment
from multiprocessing import Queue as MPQueue
import queue
from typing import Dict, List, Tuple, Optional, Any
from pathlib import Path
from collections import deque
from concurrent.futures import ThreadPoolExecutor

# Import LicensePlatePreprocessor and lane_detection functions from utils
try:
    from ..utils.image_processing import LicensePlatePreprocessor
    from ..utils.lane_detection import process_frame_for_lanes, get_lane_boundaries_from_lines
    from ..utils.local_ocr import LocalOCR
    from ..ml.reid_model import ReIDEmbedder
    from ..ml.car_classifier import CarClassifier
    # from ..ml.segmentation.edgetam import EdgeTAMSegmenter
except ImportError:
    print("Error importing utils for CoreModule. Ensure utils.py is accessible.")
    LicensePlatePreprocessor = None
    process_frame_for_lanes = None
    get_lane_boundaries_from_lines = None
    LocalOCR = None
    ReIDEmbedder = None
    CarClassifier = None
    # EdgeTAMSegmenter = None

# Logging setup
logger = logging.getLogger("app.ml")


class CoreModule:

    # Vehicle type mapping
    vehicle_type_map = {
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
    ):
        self.feed_id = feed_id

        self.model_path = Path(config["project_root_dir"]) / model_path
        # Use deep copy to avoid shared state between instances
        import copy
        self.config = copy.deepcopy(config)
        self.fps = fps
        self.db_queue = db_queue
        self.gemini_api_key = gemini_api_key
        self.model_type = model_type

        self.vehicle_data: Dict[str, Dict] = {}
        self.model = preloaded_model
        self.preprocessor = None
        
        # Initialize OCR executor as instance attribute
        self.ocr_executor = ThreadPoolExecutor(max_workers=2)
        self.ocr_results_queue = queue.Queue()
        self.local_ocr = None
        self.reid_embedder = None

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
        self.max_reid_per_frame = config["vehicle_detection"].get(
            "max_reid_per_frame", 2
        )
        self.yolo_imgsz = config["vehicle_detection"].get("yolo_imgsz", 640)
        self.num_lanes = config["lane_detection"].get("num_lanes", 4)
        self.lane_width_pixels = (
            config["vehicle_detection"]["frame_resolution"][0] / self.num_lanes
        )
        self.perspective_matrix = None  # Initialize as None, load if needed
        self.roi_polygon_points = config["roi_processing"].get("polygon_points", None)
        self.roi_points_normalized = None # Store normalized points for resolution-independent scaling
        self.roi_polygon_points_pixel = None # Cached pixel points for pointPolygonTest
        
        # --- Optimization: Cache ROI Mask ---
        self.roi_mask = None
        if self.roi_polygon_points:
            self._initialize_roi_mask(config["vehicle_detection"]["frame_resolution"])
        
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

        # Lane detection caching
        self.lane_detection_interval = config["lane_detection"].get("lane_detection_interval", 10)
        self.last_lane_detection_frame = -1
        self.cached_lane_boundaries = None

        # Initialize OCR Preprocessor (Gemini)
        if self.ocr_cfg.get("enabled", False) and self.gemini_api_key and self.ocr_cfg.get("use_gemini_ocr", False):
            try:
                self.preprocessor = LicensePlatePreprocessor(self.gemini_api_key)
            except Exception as e:
                logger.error(f"Failed to initialize LicensePlatePreprocessor: {e}")
                self.preprocessor = None
        
        # Initialize Local OCR (EasyOCR)
        if self.ocr_cfg.get("enabled", False) and (self.ocr_cfg.get("use_local", True) or not self.gemini_api_key):
            try:
                if LocalOCR:
                    self.local_ocr = LocalOCR(self.config)
                else:
                    logger.warning("LocalOCR class not found, skipping local OCR initialization.")
            except Exception as e:
                logger.error(f"Failed to initialize LocalOCR: {e}")
                self.local_ocr = None

        if not self.preprocessor and not self.local_ocr:
            logger.info("No OCR engine (Gemini or Local) enabled or initialized.")

        # Initialize ReID Embedder
        if self.config.get("vehicle_detection", {}).get("reid_enabled", True):
            try:
                self.reid_embedder = ReIDEmbedder(self.config)
            except Exception as e:
                logger.error(f"Failed to initialize ReIDEmbedder: {e}")
                self.reid_embedder = None

        # Initialize Car Classifier
        if self.config.get("vehicle_detection", {}).get("car_classification_enabled", True):
            try:
                if CarClassifier:
                    self.car_classifier = CarClassifier(self.config)
                else:
                    logger.warning("CarClassifier class not found.")
            except Exception as e:
                logger.error(f"Failed to initialize CarClassifier: {e}")
                self.car_classifier = None

        # Load Model
        try:
            use_gpu = self.config.get("performance", {}).get("gpu_acceleration", False)
            self._load_model(use_gpu)
        except Exception as e:
            logger.error(f"Failed to load model in CoreModule __init__: {e}")
            raise
    
    def _initialize_roi_mask(self, resolution: List[int]):
        """Generate the ROI mask once to save CPU cycles per frame."""
        width, height = resolution
        self.roi_mask = np.zeros((height, width), dtype=np.uint8)
        
        points_np = None
        if self.roi_points_normalized is not None and len(self.roi_points_normalized) >= 3:
            # Scale normalized points to current resolution
            points_np = (np.array(self.roi_points_normalized, dtype=np.float32) * [width, height]).astype(np.int32)
        elif self.roi_polygon_points:
            points_np = np.array(self.roi_polygon_points, dtype=np.int32)
             
        if points_np is not None:
            self.roi_polygon_points_pixel = points_np # Cache for pointPolygonTest
            cv2.fillPoly(self.roi_mask, [points_np], 255)
        else:
            self.roi_polygon_points_pixel = None

    def _load_model(self, use_gpu: bool = False):
        if self.model is not None and not isinstance(self.model, str):
            logger.info(f"Using preloaded model for feed {self.feed_id}")
            return

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
        
        # Process asynchronous OCR results from previous frames
        self._process_ocr_results()
        
        # Reset ReID budget for this frame
        self._reid_updates_this_frame = 0

        # Use a low threshold for detection to allow ByteTrack-like association (matching low-conf detections to existing tracks)
        LOW_CONF_THRESHOLD = 0.1

        try:
            # Detect with low threshold
            detections = self._detect_vehicles(frame, frame_index, LOW_CONF_THRESHOLD)
            
            # Update tracks using ByteTrack logic
            current_tracks = self._update_tracks(
                frame, detections, used_proximity, current_time, frame_index, used_confidence
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
            
            if str(self.model_path).endswith(('.onnx', '_quant.onnx')):
                detections = self._run_onnx_inference(processed_frame, confidence_threshold, roi_enabled, x1_crop, y1_crop)
            else:
                detections = self._run_pytorch_inference(processed_frame, confidence_threshold, roi_enabled, x1_crop, y1_crop)

            # logger.debug(f"Frame {frame_index}: Detections after ROI processing: {len(detections)}") # Reduce noise
            return detections

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

        # 1. ROI Masking (Optimized)
        if roi_enabled and self.roi_mask is not None:
            # Ensure mask matches frame size (handle resolution changes)
            if self.roi_mask.shape[:2] != frame.shape[:2]: # Check shape
                self._initialize_roi_mask((frame.shape[1], frame.shape[0])) # Re-initialize if different
            
            # fast bitwise AND
            processed_frame = cv2.bitwise_and(frame, frame, mask=self.roi_mask)
            # If we are masking, the frame coordinates remain unchanged.
            x1_crop, y1_crop = 0, 0
        elif roi_enabled and not self.roi_polygon_points:
            processed_frame = frame[y1_crop:y2_crop, x1_crop:x2_crop]
        else:
            processed_frame = frame.copy() # Copy only if no slicing occurred
            # Ensure crops are 0 if no ROI/crop applied
            x1_crop, y1_crop = 0, 0

        # 2. CRITICAL FIX: Convert BGR to RGB for Inference
        # YOLO/ONNX models are trained on RGB. OpenCV gives BGR.
        processed_frame = cv2.cvtColor(processed_frame, cv2.COLOR_BGR2RGB)

        return processed_frame, roi_enabled, x1_crop, y1_crop

    def _run_onnx_inference(self, processed_frame: np.ndarray, confidence_threshold: float, roi_enabled: bool, x1_crop: int, y1_crop: int) -> List[Tuple]:
        img_size = self.yolo_imgsz
        
        # Resize and pad image while preserving aspect ratio
        h, w, _ = processed_frame.shape
        scale = min(img_size / h, img_size / w)
        new_h, new_w = int(h * scale), int(w * scale)
        resized_img = cv2.resize(processed_frame, (new_w, new_h))
        
        top_pad = (img_size - new_h) // 2
        bottom_pad = img_size - new_h - top_pad
        left_pad = (img_size - new_w) // 2
        right_pad = img_size - new_w - left_pad
        
        padded_img = cv2.copyMakeBorder(resized_img, top_pad, bottom_pad, left_pad, right_pad, cv2.BORDER_CONSTANT, value=(114, 114, 114))

        input_img = padded_img.astype(np.float32) / 255.0
        input_img = np.transpose(input_img, (2, 0, 1))
        input_tensor = np.expand_dims(input_img, axis=0)

        input_name = self.model.get_inputs()[0].name
        output_name = self.model.get_outputs()[0].name
        outputs = self.model.run([output_name], {input_name: input_tensor})[0]

        return self._postprocess_onnx_output(outputs, confidence_threshold, (h, w), (new_h, new_w), (top_pad, left_pad), roi_enabled, x1_crop, y1_crop)

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

    def _postprocess_onnx_output(self, outputs: np.ndarray, confidence_threshold: float, original_shape: Tuple[int, int], resized_shape: Tuple[int, int], padding: Tuple[int, int], roi_enabled: bool, x1_crop: int, y1_crop: int) -> List[Tuple]:
        detections = []
        output = outputs[0].T
        
        # YOLOv8 output: [x, y, w, h, class0, class1, ...]
        scores = np.max(output[:, 4:], axis=1)
        mask = scores > confidence_threshold
        output = output[mask]
        scores = scores[mask]
        class_ids = np.argmax(output[:, 4:], axis=1)
        boxes = output[:, :4]

        if len(boxes) == 0:
            return []

        # original_shape: (h_orig, w_orig)
        # resized_shape: (new_h, new_w) - the image size inside the padding
        # padding: (top_pad, left_pad)
        h_orig, w_orig = original_shape
        new_h, new_w = resized_shape
        top_pad, left_pad = padding

        scale_x = w_orig / new_w
        scale_y = h_orig / new_h

        # Convert YOLO format [x_center, y_center, w, h] to [x1, y1, w, h] for NMS
        nms_boxes = []
        nms_scores = []
        nms_class_ids = []

        for i in range(len(boxes)):
            x_center, y_center, width, height = boxes[i]
            
            # Rescale to original frame
            x_center = (x_center - left_pad) * scale_x
            y_center = (y_center - top_pad) * scale_y
            width *= scale_x
            height *= scale_y
            
            x1 = x_center - width / 2
            y1 = y_center - height / 2
            
            nms_boxes.append([int(x1), int(y1), int(width), int(height)])
            nms_scores.append(float(scores[i]))
            nms_class_ids.append(int(class_ids[i]))

        # Apply Non-Maximum Suppression (NMS)
        indices = cv2.dnn.NMSBoxes(nms_boxes, nms_scores, confidence_threshold, 0.45)
        
        if len(indices) > 0:
            # Flatten indices if needed (depends on OpenCV version)
            if isinstance(indices, np.ndarray):
                indices = indices.flatten()
            
            for i in indices:
                x1, y1, w, h = nms_boxes[i]
                x2 = x1 + w
                y2 = y1 + h
                conf = nms_scores[i]
                cls = nms_class_ids[i]

                # Adjust for ROI cropping if applied earlier
                if roi_enabled:
                    x1 += x1_crop
                    y1 += y1_crop
                    x2 += x1_crop
                    y2 += y1_crop

                detections.append(((x1, y1, x2, y2), conf, cls))
        
        return detections

    def _postprocess_pytorch_output(self, results: List[Any], confidence_threshold: float, roi_enabled: bool, x1_crop: int, y1_crop: int) -> List[Tuple]:
        detections = []
        for r in results:
            for *xyxy, conf, cls in r.boxes.data.tolist():
                if conf > confidence_threshold:
                    x1, y1, x2, y2 = map(int, xyxy)
                    if roi_enabled:
                        x1 += x1_crop
                        y1 += y1_crop
                        x2 += x1_crop
                        y2 += y1_crop
                    detections.append(((x1, y1, x2, y2), float(conf), int(cls)))
        return detections

    def predict_only(self, frame_index: int) -> Dict[str, Dict]:
        """
        Predicts the next state of existing tracks without running detection.
        Useful for maintaining high FPS when detection is skipped.
        """
        predicted_tracks = {}
        current_time = time.time()
        
        # We use a shorter timeout for showing 'predicting' tracks to avoid ghosts
        # Reduced from 1.0s to 0.4s for better responsiveness during frame skipping
        PREDICT_TIMEOUT = self.config.get("vehicle_detection", {}).get("predict_timeout", 0.4)
        
        frame_res = self.config["vehicle_detection"]["frame_resolution"]
        frame_w, frame_h = frame_res[0], frame_res[1]

        for vehicle_id, track in list(self.vehicle_data.items()):
            kf = track.get("kalman_filter")
            
            # Check if track is too old to even predict
            if (current_time - track["last_seen"]) > self.track_timeout:
                continue

            if kf:
                kf.predict()
                x, y = float(kf.x[0][0]), float(kf.x[1][0])
                
                # Update bbox based on predicted center and original dimensions
                x1_orig, y1_orig, x2_orig, y2_orig = track["bbox"]
                w = x2_orig - x1_orig
                h = y2_orig - y1_orig
                
                new_x1 = int(x - w / 2)
                new_y1 = int(y - h / 2)
                new_x2 = int(x + w / 2)
                new_y2 = int(y + h / 2)

                # Boundary Check: Remove if it leaves the frame
                if new_x2 < 0 or new_x1 > frame_w or new_y2 < 0 or new_y1 > frame_h:
                    continue
                
                # ROI Check: Remove if centroid leaves ROI
                if not self._is_point_in_roi(x, y):
                    continue

                track["bbox"] = (new_x1, new_y1, new_x2, new_y2)
                track["centroid"] = (x, y)
                track["frame_index_last_seen"] = frame_index
                
                # Confidence Decay: reduce confidence as we predict more frames without detection
                # Increased decay from 0.95 to 0.85 to clear ghosts faster
                time_since_seen = current_time - track["last_seen"]
                track["confidence"] *= 0.7 # Stronger decay per frame (was 0.85)
                
                # Only keep in output if it's recently seen AND has enough confidence
                if time_since_seen < PREDICT_TIMEOUT and track["confidence"] > 0.1:
                    track["status"] = "predicting"
                    predicted_tracks[vehicle_id] = track
                else:
                    # Still keep in vehicle_data for future matching, but don't return for rendering
                    track["status"] = "occluded"
                
                # Update speed based on prediction
                prev_time = track.get("last_speed_update_time", current_time - (1.0/self.fps))
                track["speed"] = self._estimate_speed_kalman(track, current_time, prev_time)
                track["last_speed_update_time"] = current_time
            else:
                self.vehicle_data.pop(vehicle_id, None)

        # Deduplicate to prevent overlapping ghost boxes
        # We use a stricter IoU for predicting tracks
        self.vehicle_data = self._deduplicate_tracks(self.vehicle_data, iou_threshold=0.6)
        
        # Return only the tracks we want to render/process this frame
        return {vid: t for vid, t in predicted_tracks.items() if t["status"] == "predicting" and t["confidence"] > 0.15}

    def _deduplicate_tracks(self, tracks: Dict[str, Dict], iou_threshold: float = 0.7) -> Dict[str, Dict]:
        """
        Removes overlapping tracks, keeping the most confident or recently seen ones.
        """
        if not tracks:
            return {}
        
        # Sort criteria: 1. Status (active > predicting > occluded) 2. Confidence 3. Last seen
        def sort_key(fid):
            t = tracks[fid]
            status_rank = 3 if t["status"] == "active" else (2 if t["status"] == "predicting" else 1)
            return (status_rank, t["confidence"], t["last_seen"])

        sorted_ids = sorted(tracks.keys(), key=sort_key, reverse=True)
        
        kept_tracks = {}
        for fid in sorted_ids:
            track = tracks[fid]
            bbox = track["bbox"]
            
            is_duplicate = False
            for kept_id, kept_track in kept_tracks.items():
                # For predicting tracks, use a more sensitive IoU threshold
                current_iou_thresh = iou_threshold
                if track["status"] == "predicting" or kept_track["status"] == "predicting":
                    current_iou_thresh = 0.4 # More aggressive deduplication for uncertain tracks (was 0.5)
                
                if self._bbox_iou(bbox, kept_track["bbox"]) > current_iou_thresh:
                    is_duplicate = True
                    break
            
            if not is_duplicate:
                kept_tracks[fid] = track
            else:
                logger.debug(f"Deduplicated track {fid} (overlaps with {kept_id})")
        
        return kept_tracks

    def _is_point_in_roi(self, x: float, y: float) -> bool:
        if self.roi_polygon_points_pixel is None or not self.config.get("roi_processing", {}).get("enabled", False):
            return True
        return cv2.pointPolygonTest(self.roi_polygon_points_pixel, (int(x), int(y)), False) >= 0

    def _update_tracks(
        self,
        frame: np.ndarray,
        detections: List[Tuple],
        proximity_threshold: int,
        current_time: float,
        frame_index: int,
        confidence_threshold: float,
    ) -> Dict[str, Dict]:
        new_or_updated_tracks = {}
        
        # 1. Prepare existing tracks for association
        for vehicle_id, track in self.vehicle_data.items():
            kf = track.get("kalman_filter")
            if kf:
                # CRITICAL: Always predict before association to get the prior state for current frame
                kf.predict()
                
                x, y = kf.x[0][0], kf.x[1][0]
                x1_orig, y1_orig, x2_orig, y2_orig = track["bbox"]
                width_orig = x2_orig - x1_orig
                height_orig = y2_orig - y1_orig
                track["predicted_bbox"] = (
                    x - width_orig / 2, y - height_orig / 2,
                    x + width_orig / 2, y + height_orig / 2
                )
        
        # 2. Hungarian Algorithm for Association
        cost_matrix = self._calculate_cost_matrix(detections, self.vehicle_data)
        
        if cost_matrix.size > 0:
            row_ind, col_ind = linear_sum_assignment(cost_matrix)
        else:
            row_ind, col_ind = np.array([]), np.array([])
            
        matched_detections = set()
        matched_tracks = set()
        
        # 3. Process matched tracks
        # Stricter threshold: 0.8 ensures better association quality (IoU based)
        MATCHING_THRESHOLD = 0.8 

        for i, j in zip(row_ind, col_ind):
            cost = cost_matrix[i, j]
            if cost < MATCHING_THRESHOLD:
                detection_bbox, detection_conf, detection_cls = detections[i]
                track_id = list(self.vehicle_data.keys())[j]
                track = self.vehicle_data[track_id]

                self._update_track(track, (detection_bbox, detection_conf, detection_cls), current_time, frame, frame_index)
                
                cx, cy = track["centroid"]
                if self._is_point_in_roi(cx, cy):
                    new_or_updated_tracks[track_id] = track
                    matched_detections.add(i)
                    matched_tracks.add(track_id)

        # 4. Process unmatched tracks (keep them alive if within timeout)
        h, w = frame.shape[:2]
        for track_id, track in self.vehicle_data.items():
            if track_id not in matched_tracks:
                # Keep track if within timeout
                if (current_time - track["last_seen"]) < self.track_timeout:
                    track["status"] = "predicting"
                    
                    # Update position based on Kalman Filter prediction (Coasting)
                    if "predicted_bbox" in track:
                        track["bbox"] = track["predicted_bbox"]
                        kf = track["kalman_filter"]
                        track["centroid"] = (kf.x[0][0], kf.x[1][0])
                    
                    # Boundary check
                    x1, y1, x2, y2 = track["bbox"]
                    if x2 < 0 or x1 > w or y2 < 0 or y1 > h:
                        continue

                    cx, cy = track["centroid"]
                    if self._is_point_in_roi(cx, cy):
                        new_or_updated_tracks[track_id] = track

        # 5. Process unmatched detections (new vehicles)
        for i, (detection_bbox, detection_conf, detection_cls) in enumerate(detections):
            if i not in matched_detections and detection_conf >= confidence_threshold:
                # Extra check: ensure new detection isn't extremely close to an existing track
                # to prevent 'phantom' overlapping boxes
                is_duplicate = False
                det_cx = (detection_bbox[0] + detection_bbox[2]) / 2
                det_cy = (detection_bbox[1] + detection_bbox[3]) / 2
                det_w = detection_bbox[2] - detection_bbox[0]
                det_h = detection_bbox[3] - detection_bbox[1]
                
                # Dynamic threshold: 25% of box dimension or min 30px
                dup_thresh = max(30, min(det_w, det_h) * 0.4)
                
                for existing_track in new_or_updated_tracks.values():
                    ex_cx, ex_cy = existing_track["centroid"]
                    dist = math.sqrt((det_cx - ex_cx)**2 + (det_cy - ex_cy)**2)
                    if dist < dup_thresh: 
                        is_duplicate = True
                        break
                
                if not is_duplicate:
                    new_track_id = self._initialize_new_track(frame, (detection_bbox, detection_conf, detection_cls), current_time, frame_index)
                    if new_track_id:
                        new_or_updated_tracks[new_track_id] = self.vehicle_data[new_track_id]
        
        # Deduplicate final tracks to ensure no overlaps
        self.vehicle_data = self._deduplicate_tracks(new_or_updated_tracks)
        return self.vehicle_data

    def _calculate_cost_matrix(self, detections: List[Tuple], tracks: Dict[str, Dict]) -> np.ndarray:
        if not detections or not tracks:
            return np.array([]).reshape(len(detections), len(tracks))

        num_detections = len(detections)
        num_tracks = len(tracks)
        cost_matrix = np.full((num_detections, num_tracks), 10000.0)

        track_list = list(tracks.values())
        proximity_thresh = float(self.proximity_threshold) if self.proximity_threshold > 0 else 100.0

        for d_idx, (det_bbox, det_conf, det_cls) in enumerate(detections):
            det_cx = (det_bbox[0] + det_bbox[2]) / 2
            det_cy = (det_bbox[1] + det_bbox[3]) / 2
            
            for t_idx, track in enumerate(track_list):
                if "predicted_bbox" in track:
                    # 1. IoU Cost (Primary)
                    iou = self._bbox_iou(det_bbox, track["predicted_bbox"])
                    iou_cost = 1.0 - iou
                    
                    # 2. Distance Cost (Secondary/Fallback)
                    tr_cx, tr_cy = track["centroid"]
                    dist = math.sqrt((det_cx - tr_cx)**2 + (det_cy - tr_cy)**2)
                    # Normalize distance cost using proximity threshold
                    dist_cost = dist / proximity_thresh
                    
                    # 3. Class Match Penalty (Reduced to allow some flexibility)
                    class_penalty = 0 if det_cls == track["class_id"] else 0.8

                    # Combined Cost: Prioritize IoU if overlap exists, else use distance
                    if iou > 0.1:
                        cost = iou_cost + class_penalty * 0.4
                    else:
                        # For non-overlapping boxes, use distance but with a higher base cost
                        # If class mismatch AND low overlap, punish severely
                        extra_penalty = 1.0 if class_penalty > 0 else 0.0
                        cost = 0.9 + dist_cost + class_penalty + extra_penalty

                    # Add confidence factor: less confident detections are more expensive to match
                    cost += (1.0 - det_conf) * 0.3
                    
                    cost_matrix[d_idx, t_idx] = cost
        return cost_matrix

    def _bbox_iou(self, boxA: Tuple, boxB: Tuple) -> float:
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])

        interArea = max(0, xB - xA) * max(0, yB - yA)

        boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
        boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])

        iou = interArea / float(boxAArea + boxBArea - interArea)
        return iou

    def _remove_stale_tracks(self, current_time: float, track_timeout: int):
        stale_tracks = []
        for vehicle_id, track in self.vehicle_data.items():
            if (current_time - track["last_seen"]) > track_timeout:
                stale_tracks.append(vehicle_id)

        for vehicle_id in stale_tracks:
            self.vehicle_data.pop(vehicle_id)
            logger.debug(f"Removed stale track {vehicle_id}")

    def _save_vehicle_data(self, tracked_vehicles: Dict[str, Dict]):
        for vehicle_id, data in tracked_vehicles.items():
            # If DB queue is available, put the data
            if self.db_queue and data.get("speed") is not None:
                try:
                    now = time.time()
                    self.db_queue.put_nowait({
                        "type": "vehicle_data",
                        "feed_id": self.feed_id,
                        "vehicle_id": vehicle_id,
                        "timestamp": now,
                        "bbox": data["bbox"],
                        "centroid": data["centroid"],
                        "speed": data["speed"],
                        "license_plate": data.get("license_plate", "Unknown"),
                        "class_id": data["class_id"],
                        "class_name": self.vehicle_type_map.get(data["class_id"], "unknown"),
                        "confidence": data["confidence"],
                        "status": data["status"],
                        "lane": data.get("lane", -1),
                        "is_occluded": data.get("is_occluded", False),
                        "behavior": data.get("behavior", "normal"),
                        "estimated_pixels_per_meter": data.get("estimated_pixels_per_meter"),
                        "direction": data.get("direction", "N/A"),
                        "acceleration": data.get("acceleration"),
                        "embedding": data.get("embedding") if data.get("new_embedding") else None,
                        "car_model": data.get("car_model"),
                        "car_model_confidence": data.get("car_model_confidence"),
                    })
                    data["new_embedding"] = False # Clear the flag after putting in queue
                    
                    # Persistent Identification
                    lp = data.get("license_plate")
                    if lp and lp != "Unknown":
                        self.db_queue.put_nowait({
                            "type": "identified_vehicle",
                            "license_plate": lp,
                            "vehicle_type": self.vehicle_type_map.get(data["class_id"], "unknown"),
                            "timestamp": now,
                            "confidence": data.get("ocr_confidence", 0.0),
                            # Could add more metadata here if available
                        })
                except queue.Full:
                    logger.warning(f"DB queue full, dropping data for {vehicle_id}")
                except Exception as e:
                    logger.error(f"Error putting data to DB queue for {vehicle_id}: {e}")

        # Limit active tracks if necessary (evict oldest if over limit)
        while len(self.vehicle_data) > self.max_active_tracks:
            oldest_id = min(self.vehicle_data, key=lambda k: self.vehicle_data[k]["last_seen"])
            self.vehicle_data.pop(oldest_id)
            logger.warning(f"Evicted oldest track {oldest_id} due to max_active_tracks limit.")


    def _initialize_new_track(self, frame: np.ndarray, detection: Tuple, current_time: float, frame_index: int) -> Optional[str]:
        bbox, conf, cls = detection
        x1, y1, x2, y2 = bbox
        
        # Only track specified vehicle classes
        if self.vehicle_class_ids and cls not in self.vehicle_class_ids:
            return None

        # Check if the detection is within the ROI (if a polygon is defined)
        if self.roi_polygon_points_pixel is not None and self.config.get("roi_processing", {}).get("enabled", False):
            # Check if centroid of bbox is inside the polygon
            centroid_x = int((x1 + x2) / 2)
            centroid_y = int((y1 + y2) / 2)
            if cv2.pointPolygonTest(self.roi_polygon_points_pixel, (centroid_x, centroid_y), False) < 0:
                return None # Not in ROI

        # Initialize Kalman Filter for the new track
        # State: [x, y, vx, vy] - centroid position and velocity
        kf = KalmanFilter(dim_x=4, dim_z=2) # 4 state variables, 2 measurements (x, y)
        
        # Initial state (centroid of detection)
        centroid = np.array([[(x1 + x2) / 2], [(y1 + y2) / 2], [0], [0]]) # Initial velocity 0
        kf.x = centroid

        # State transition matrix (predicts next state from current)
        dt = 1.0 / self.fps # Assuming constant velocity model between frames
        kf.F = np.array([
            [1, 0, dt, 0],
            [0, 1, 0, dt],
            [0, 0, 1, 0],
            [0, 0, 0, 1]
        ])

        # Measurement function (maps state to measurement)
        kf.H = np.array([
            [1, 0, 0, 0],
            [0, 1, 0, 0]
        ])

        # Measurement uncertainty (R - higher values mean more trust in prediction)
        kf.R = np.diag([self.kf_params.get("measurement_noise_x", 10), self.kf_params.get("measurement_noise_y", 10)]) ** 2

        # Process uncertainty (Q - higher values mean more trust in measurement)
        # Represents how much the state (position/velocity) can change between time steps
        q_std_pos = self.kf_params.get("process_noise_pos", 1) # Position noise
        q_std_vel = self.kf_params.get("process_noise_vel", 0.1) # Velocity noise
        kf.Q = np.diag([q_std_pos, q_std_pos, q_std_vel, q_std_vel]) ** 2
        
        # Covariance matrix (P - reflects initial uncertainty in state)
        # Start with high uncertainty, as we don't know the true state yet
        p_init_pos = self.kf_params.get("init_covariance_pos", 100) # Initial position uncertainty
        p_init_vel = self.kf_params.get("init_covariance_vel", 1000) # Initial velocity uncertainty
        kf.P = np.diag([p_init_pos, p_init_pos, p_init_vel, p_init_vel]) ** 2

        # Assign a unique ID
        # Use UUID to prevent collisions across worker restarts and multiple feeds
        # Shorten it for display purposes, but ensure uniqueness in DB
        raw_uuid = str(uuid.uuid4())
        vehicle_id = f"vehicle_{raw_uuid[:8]}" 

        self.vehicle_data[vehicle_id] = {
            "vehicle_id": vehicle_id,
            "bbox": bbox,
            "centroid": (kf.x[0][0], kf.x[1][0]),
            "class_id": cls,
            "class_history": deque([cls], maxlen=10), # History for voting
            "confidence": conf,
            "kalman_filter": kf,
            "last_seen": current_time,
            "frame_index_last_seen": frame_index,
            "speed": 0.0,
            "smoothed_speed": 0.0, # <--- Added state for EWMA
            "speed_history": deque(maxlen=5), # For simple behavior analysis
            "license_plate": "Unknown",
            "plate_attempts": 0,
            "status": "active",
            "is_occluded": False,
            "lane": -1,
            "last_speed_update_time": current_time,
            "direction": "N/A",
            "acceleration": 0.0,
            "estimated_pixels_per_meter": self.pixels_per_meter, # Default, can be updated
            "new_embedding": False, # Flag to signal that a new ReID embedding is available
        }
        logger.debug(f"Initialized new track {vehicle_id} at {bbox}")
        return vehicle_id

    def _update_track(self, track: Dict, detection: Tuple, current_time: float, frame: np.ndarray, frame_index: int) -> None:
        bbox, conf, cls = detection
        kf = track["kalman_filter"]

        # Update Kalman Filter with new measurement
        kf.update(np.array([[(bbox[0] + bbox[2]) / 2], [(bbox[1] + bbox[3]) / 2]]))

        # Update track properties
        track["bbox"] = bbox
        track["centroid"] = (kf.x[0][0], kf.x[1][0])
        
        # Class Voting Mechanism
        if "class_history" not in track:
            track["class_history"] = deque([track["class_id"]], maxlen=10)
        track["class_history"].append(cls)
        
        # Determine most frequent class
        from collections import Counter
        most_common = Counter(track["class_history"]).most_common(1)
        if most_common:
            track["class_id"] = most_common[0][0]

        track["confidence"] = conf
        track["last_seen"] = current_time
        track["frame_index_last_seen"] = frame_index
        track["status"] = "active"
        track["is_occluded"] = False # Reset occlusion if detected

        # Speed estimation
        prev_time = track.get("last_speed_update_time", current_time - (1.0/self.fps)) # Default to one frame ago
        track["speed"] = self._estimate_speed_kalman(track, current_time, prev_time)
        track["last_speed_update_time"] = current_time

        # Update lane information
        if self.cached_lane_boundaries:
            track["lane"] = self._get_lane_for_vehicle(track["centroid"], self.cached_lane_boundaries)
            
        # Acceleration (simple approximation for now)
        current_speed = track["speed"]
        last_speeds = list(track["speed_history"])
        
        if len(last_speeds) >= 2:
            prev_speed = last_speeds[-2] # Second to last smoothed speed
            time_delta = (1.0 / self.fps) # Approximation
            track["acceleration"] = (current_speed - prev_speed) / time_delta
        else:
            track["acceleration"] = 0.0

        # Direction (simple based on velocity)
        vx, vy = kf.x[2][0], kf.x[3][0]
        if abs(vx) > abs(vy): # Primarily horizontal motion
            track["direction"] = "East" if vx > 0 else "West"
        else: # Primarily vertical motion
            track["direction"] = "South" if vy > 0 else "North"

        # Behavior analysis (stopped, speeding, accelerating, changing lane)
        self._analyze_behavior(track)  # Classify based on new speed/state

        # Car Make/Model Classification
        car_class_interval = self.config.get("vehicle_detection", {}).get("car_classification_interval_frames", 60)
        if (
            self.car_classifier
            and track.get("car_model") is None
            and frame_index % max(1, car_class_interval) == 0
        ):
            x1, y1, x2, y2 = map(int, bbox)
            h, w = frame.shape[:2]
            crop = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
            if crop.size > 0:
                label, confidence = self.car_classifier.classify(crop)
                if label:
                    track["car_model"] = label
                    track["car_model_confidence"] = confidence
                    logger.info(f"Car classified for {track['vehicle_id']}: {label} ({confidence:.2f})")

        # ReID Embeddings - Optimized with Staggering and Rate Limiting
        reid_interval = self.config.get("vehicle_detection", {}).get("reid_interval_frames", 60)
        
        should_update_reid = False
        if self.reid_embedder:
            # Determine offset based on vehicle_id to stagger updates
            try:
                # Assumes format "vehicle_123"
                vid_parts = track.get("vehicle_id", "").split('_')
                track_id_offset = int(vid_parts[-1]) if len(vid_parts) > 1 else hash(track.get("vehicle_id", ""))
            except (ValueError, IndexError):
                track_id_offset = hash(track.get("vehicle_id", str(time.time())))

            # Always prioritize tracks without embedding (newly detected)
            if track.get("embedding") is None:
                 if self._reid_updates_this_frame < self.max_reid_per_frame:
                     should_update_reid = True
            # Periodic update with staggering and budget check
            elif (frame_index + track_id_offset) % reid_interval == 0:
                 if self._reid_updates_this_frame < self.max_reid_per_frame:
                     should_update_reid = True

        if should_update_reid:
            x1, y1, x2, y2 = map(int, bbox)
            h, w = frame.shape[:2]
            crop = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
            if crop.size > 0:
                # We'll run embedding extraction in the main worker thread for now
                # but could offload to pool if too slow
                embedding = self.reid_embedder.get_embedding(crop)
                if embedding is not None:
                    track["embedding"] = embedding.tolist() # Make serializable
                    track["new_embedding"] = True # Mark as new for sending to db_queue
                    self._reid_updates_this_frame += 1

        # OCR for license plates
        ocr_interval_frames = self.ocr_cfg.get("interval_frames", 30)
        max_ocr_attempts = self.ocr_cfg.get("max_attempts", 3)
        
        if (
            track["license_plate"] == "Unknown"
            and self.preprocessor
            and track.get("plate_attempts", 0) < max_ocr_attempts
            and frame_index % max(1, ocr_interval_frames) == 0
            and not track["is_occluded"]
        ):
            if self._is_roi_optimal_for_ocr(track["bbox"]):
                # Submit task with explicit ID and BBox copy to avoid race conditions
                self.ocr_executor.submit(
                    self._run_ocr, 
                    frame.copy(), 
                    track["vehicle_id"], 
                    list(track["bbox"]) # Pass a copy of the list
                )

    def _estimate_speed_kalman(self, track: Dict, current_time: float, prev_time: float) -> float:
        kf = track.get("kalman_filter")
        if not kf:
            return 0.0
        
        try:
            vx, vy = kf.x[2], kf.x[3]
            # No longer dividing by time_diff here. Kalman filter velocity is already per unit time.
            pixel_speed_per_sec = np.sqrt(vx**2 + vy**2)
            dynamic_ppm = self._get_dynamic_pixels_per_meter(kf.x[1])
            
            speed_mps = (pixel_speed_per_sec / dynamic_ppm) if dynamic_ppm > 0 else 0
            raw_speed_kmph = speed_mps * 3.6

            # --- Corrected EWMA Logic ---
            # S_t = alpha * Y_t + (1 - alpha) * S_{t-1}
            prev_smoothed = track.get("smoothed_speed", 0.0)
            
            # If this is the first speed reading, initialize it
            if prev_smoothed == 0.0 and raw_speed_kmph > 0:
                new_smoothed = raw_speed_kmph
            else:
                new_smoothed = (self.ewma_alpha * raw_speed_kmph) + ((1 - self.ewma_alpha) * prev_smoothed)

            # Update state
            track["smoothed_speed"] = new_smoothed
            # Update speed history (which is used for acceleration)
            track["speed_history"].append(new_smoothed)

            return round(float(max(0, new_smoothed)), 1)
        except Exception as e:
            logger.warning(f"Speed estimation error: {e}")
            return 0.0

    def _run_ocr(self, frame: np.ndarray, track_id: str, bbox: List[int]):
        """
        Modified signature: Pass track_id and bbox copy, not the whole track dict.
        Runs in a background thread. Puts results into a queue for the main thread.
        """
        try:
            plate_text = self._ocr_license_plate(frame, bbox)
            self.ocr_results_queue.put({
                "track_id": track_id,
                "plate_text": plate_text
            })
        except Exception as e:
            logger.error(f"Error in _run_ocr thread for {track_id}: {e}")

    def _get_dynamic_pixels_per_meter(self, y_pixel: float) -> float:
        # Placeholder for dynamic PPM logic (e.g., using perspective transform)
        # For now, return a constant or implement a lookup based on y_pixel
        return self.pixels_per_meter

    def _analyze_behavior(self, track: Dict):
        # Example behaviors: stopped, speeding, accelerating, decelerating, changing lane
        behavior = "normal"
        
        current_speed = track.get("smoothed_speed", 0.0)
        acceleration = track.get("acceleration", 0.0)
        
        if current_speed < self.stopped_speed_threshold_kmh:
            behavior = "stopped"
        elif current_speed > self.speed_limit:
            behavior = "speeding"
        elif acceleration > self.accel_threshold_mps2:
            behavior = "accelerating"
        elif acceleration < -self.accel_threshold_mps2: # Negative acceleration
            behavior = "decelerating"
        
        # Simple lane change detection (requires more sophisticated lane tracking)
        # This is a placeholder and would need previous lane info
        # if track.get("lane_changed", False):
        #     behavior = "changing lane"
            
        track["behavior"] = behavior

    def _process_ocr_results(self):
        """
        Drains the OCR results queue and updates vehicle data in the main thread.
        This prevents race conditions on self.vehicle_data.
        """
        try:
            while True:
                result = self.ocr_results_queue.get_nowait()
                track_id = result["track_id"]
                plate_text = result["plate_text"]
                
                if track_id in self.vehicle_data:
                    track = self.vehicle_data[track_id]
                    
                    if plate_text and not plate_text.startswith("Unknown"):
                        track["license_plate"] = plate_text
                        track["plate_attempts"] = track.get("plate_attempts", 0) + 1
                        logger.info(f"OCR Success for {track_id}: {plate_text}")
                    else:
                        track["plate_attempts"] = track.get("plate_attempts", 0) + 1
                else:
                    logger.debug(f"OCR result received for expired track {track_id}")
                    
        except queue.Empty:
            pass

    def _get_lane_for_vehicle(self, centroid: Tuple[float, float], lane_boundaries: List[Tuple]) -> int:
        cx, _ = centroid
        for i, (start_x, end_x) in enumerate(lane_boundaries):
            if start_x <= cx < end_x:
                return i + 1  # Lane numbers typically start from 1
        return -1 # Not in any defined lane

    def _ocr_license_plate(self, frame: np.ndarray, bbox: List[int]) -> str:
        """
        Extracts the license plate from the frame based on the bounding box
        and attempts to read the text using the preprocessor.
        """
        x1, y1, x2, y2 = map(int, bbox)
        
        # Ensure bounding box is within frame dimensions
        h, w, _ = frame.shape
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(w, x2)
        y2 = min(h, y2)

        if x2 <= x1 or y2 <= y1:
            logger.warning(f"Invalid bounding box for OCR: {bbox}")
            return "Unknown (Invalid BBox)"

        plate_image = frame[y1:y2, x1:x2]
        if plate_image.size == 0:
            logger.warning(f"Empty plate image for OCR: {bbox}")
            return "Unknown (Empty Image)"

        # Prefer Local OCR if enabled
        if self.local_ocr:
            try:
                result = self.local_ocr.read_plate(plate_image)
                if result:
                    return result
            except Exception as e:
                logger.error(f"Local OCR failed: {e}")

        # Fallback to Gemini if configured
        if self.preprocessor:
            try:
                # The preprocessor expects RGB, but the frame passed is already RGB because of _preprocess_frame
                # So we don't need another cvtColor here, but we ensure to use a copy.
                plate_image_rgb = plate_image # It's already RGB from _preprocess_frame -> _detect_vehicles -> _update_track
                
                # Perform OCR
                ocr_result = self.preprocessor.process_image(plate_image_rgb)
                return ocr_result
            except Exception as e:
                logger.error(f"Gemini OCR failed for bbox {bbox}: {e}")
                return f"Unknown (OCR Error: {e})"
        
        return "Unknown (No Preprocessor)"

    def _is_roi_optimal_for_ocr(self, bbox: List[int]) -> bool:
        """
        Checks if the bounding box is in a good position/size for OCR.
        Placeholder for more complex logic (e.g., aspect ratio, size relative to frame, 
        not too close to edges).
        """
        x1, y1, x2, y2 = bbox
        width = x2 - x1
        height = y2 - y1
        
        # Example: Check if bbox is reasonably sized and not too small
        if width < 50 or height < 20: # Arbitrary minimum size
            return False
        
        # Example: Check if bbox is in the lower half of the frame (common for plates)
        frame_height = self.config["vehicle_detection"]["frame_resolution"][1]
        if (y1 + y2) / 2 < frame_height / 2: # Centroid in upper half
            return False
            
        return True

    def cleanup(self):
        """Clean up resources like thread pools."""
        if self.ocr_executor:
            self.ocr_executor.shutdown(wait=True)
            logger.info(f"OCR ThreadPoolExecutor for {self.feed_id} shut down.")

    def update_config(self, updates: Dict[str, Any]):
        """
        Updates the CoreModule configuration dynamically.
        """
        # Update ROI
        if "roi_processing" in updates:
            roi_cfg = updates["roi_processing"]
            self.config["roi_processing"] = roi_cfg # Update internal config storage
            
            # Handle normalized points if passed (preferred for dynamic updates)
            if "roi_points_normalized" in roi_cfg:
                self.roi_points_normalized = roi_cfg["roi_points_normalized"]
                self.roi_polygon_points = None # Invalidate pixel points to force usage of normalized
            elif "polygon_points" in roi_cfg:
                self.roi_polygon_points = roi_cfg["polygon_points"]
                self.roi_points_normalized = None # Clear normalized if explicit pixels provided
            
            if self.roi_points_normalized or self.roi_polygon_points:
                # Use current config resolution as baseline, or it will be re-inited on next frame if mismatch
                self._initialize_roi_mask(self.config["vehicle_detection"]["frame_resolution"])
            else:
                self.roi_mask = None
            
            logger.info(f"[{self.feed_id}] ROI configuration updated.")
