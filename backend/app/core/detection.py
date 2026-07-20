import logging
import numpy as np
import cv2
from typing import List, Tuple, Optional, Any

logger = logging.getLogger("app.ml.detection")

class DetectionEngine:
    def __init__(self, model_path: str, config: dict, device: str = "cpu", preloaded_model: Optional[Any] = None):
        self.model_path = model_path
        self.config = config
        self.device = device
        self.model = preloaded_model
        # Fix: Read from correct config level
        self.model_type = config.get("vehicle_detection", {}).get("model_type", "yolo")
        # Fix: Read from correct config level
        self.imgsz = config.get("vehicle_detection", {}).get("yolo_imgsz", 640)
        
        # ROI Cache
        self.roi_mask = None
        self.normalized_roi_points: Optional[np.ndarray] = None
        self._exclusion_zones: List = []

    def load_model(self):
        """Loads the model and validate the model object."""
        try:
            if self.model is not None:
                # Fix: Add type validation for preloaded model
                if not hasattr(self.model, 'predict') or not callable(getattr(self.model, '__call__', None)):
                    logger.warning("Preloaded model validation failed, reloading model")
                    self.model = None
                else:
                    logger.info(f"Using preloaded {self.model_type} model.")
                    # Run a warm-up inference to verify the model actually works
                    self._warmup()
                    return
            # Fix: Remove redundant device parameter from inference
            if self.model_type == "yolo":
                from ultralytics import YOLO
                self.model = YOLO(self.model_path)
                self.model.to(self.device)
                logger.info(f"YOLO model loaded on {self.device}")
                self._warmup()
            else:
                raise ValueError(f"Unsupported model type: {self.model_type}. Only 'yolo' is currently supported.")
        except Exception as e:
            self.model = None
            logger.error(f"Failed to load model: {e}")
            raise

    def _warmup(self):
        """Performs a dummy inference to warm up the model and GPU."""
        dummy_img = np.zeros((self.imgsz, self.imgsz, 3), dtype=np.uint8)
        self.model(dummy_img, imgsz=self.imgsz, verbose=False)
        logger.info("Model warm-up inference completed successfully.")

    def initialize_roi(self, resolution: List[int], roi_points: List[List[int]], exclusion_zones: Optional[List] = None):
        """Creates an ROI mask from normalized [0,1] polygon points, then
        subtracts any configured exclusion zones (e.g. sidewalks, opposing
        lanes) so detections inside them are filtered out by ``is_in_roi()``.

        Both ``roi_points`` and ``exclusion_zones`` are expected in NORMALIZED
        [0,1] coordinates. Conversion to pixels is ``point * resolution`` (a
        full-frame mask is the four corners ``[0,0],[1,0],[1,1],[0,1]``).

        NOTE (audit finding #7 / cv2>=5): the previous code did
        ``roi_points / resolution`` here AND ``* resolution`` again in
        ``_create_mask`` -- a double divide that collapsed normalized [0,1]
        coords to a ~1x1 box, so the entire ROI mask was inert (every pixel
        flagged out-of-ROI) and ``exclusion_zones`` were never applied at all.
        We now store the normalized points verbatim and convert to pixels only
        once, at fill time, using integer rounding so ``cv2.fillPoly`` (which
        mis-scales sub-pixel input on cv2>=5) behaves correctly.
        """
        h, w = resolution[1], resolution[0]
        if roi_points:
            # Store NORMALIZED [0,1] points verbatim -- do NOT pre-divide.
            self.normalized_roi_points = np.array(roi_points, dtype=np.float32)
        else:
            self.normalized_roi_points = None

        self._exclusion_zones = exclusion_zones or []
        self.roi_mask = self._create_mask(h, w, self.normalized_roi_points)
        self._apply_exclusion_zones(h, w)

    def _apply_exclusion_zones(self, h: int, w: int):
        if not self._exclusion_zones:
            return
        for zone in self._exclusion_zones:
            try:
                # NORMALIZED [0,1] coords * resolution -> integer pixels.
                zone_np = (np.array(zone, dtype=np.float32) * [w, h]).round().astype(np.int32)
                cv2.fillPoly(self.roi_mask, [zone_np], 0)
            except Exception as e:
                logger.warning(f"Failed to apply exclusion zone {zone!r}: {e}")

    def _create_mask(self, h: int, w: int, points: Optional[np.ndarray]) -> np.ndarray:
        """Helper to build a binary ROI mask from normalized [0,1] points.

        Conversion is ``point * [w, h]`` with integer rounding. The previous
        implementation divided by resolution *and* multiplied by it, collapsing
        the polygon to a ~1x1 box under cv2>=5 (which mis-scales sub-pixel
        input), leaving the ROI inert.
        """
        mask = np.zeros((h, w), dtype=np.uint8)
        if points is not None:
            pts = (points * [w, h]).round().astype(np.int32)
            cv2.fillPoly(mask, [pts], 255)
        else:
            mask.fill(255)
        return mask

    def is_in_roi(self, bbox: np.ndarray) -> bool:
        if self.roi_mask is None:
            return True
        
        # Extract coordinates
        x1, y1, x2, y2 = map(int, bbox)
        h, w = self.roi_mask.shape
        
        # Clamp coordinates to mask dimensions
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        
        if x2 <= x1 or y2 <= y1:
            return False
            
        # Calculate intersection area
        # We use a slice of the binary mask to find how many pixels are 'on' (255)
        roi_crop = self.roi_mask[y1:y2, x1:x2]
        # Fix: Use explicit check for 255 values
        intersection_pixels = np.sum(roi_crop == 255)
        box_area = (x2 - x1) * (y2 - y1)
        
        # Keep detection if a configurable threshold of the box is within the ROI
        roi_overlap_threshold = self.config.get("vehicle_detection", {}).get("roi_overlap_threshold", 0.1)
        return (intersection_pixels / (box_area + 1e-6)) >= roi_overlap_threshold

    def detect(self, frame: np.ndarray, confidence_threshold: float) -> List[Tuple]:
        """Runs detection on the frame and returns bounding boxes and classes."""
        if frame is None:
            return []

        if self.model is None:
            raise RuntimeError("Model not loaded")

        # Ensure ROI mask matches current frame resolution to avoid scaling artifacts
        if self.roi_mask is not None:
            if frame.shape[0] != self.roi_mask.shape[0] or frame.shape[1] != self.roi_mask.shape[1]:
                h, w = frame.shape[0], frame.shape[1]
                self.roi_mask = self._create_mask(h, w, self.normalized_roi_points)
                self._apply_exclusion_zones(h, w)
        
        # Fix: Add exception handling around model inference
        try:
            results = self.model(frame, conf=confidence_threshold, imgsz=self.imgsz, verbose=False)  # Remove device parameter
        except Exception as e:
            logger.warning(f"Model inference failed: {e}")
            return []
        
        detections = []
        for r in results:
            boxes = r.boxes
            for box in boxes:
                b = box.xyxy[0].cpu().numpy()
                cls = int(box.cls[0])
                conf = float(box.conf[0])
                
                # Fix: Only include vehicle classes (car, motorcycle, bus, truck, etc.)
                # Read from config to allow adaptation without code changes
                vehicle_class_ids = self.config.get("vehicle_detection", {}).get("vehicle_class_ids", [2, 3, 5, 7])
                if cls in vehicle_class_ids:
                    # Use original bbox for the result
                    orig_bbox = (b[0], b[1], b[2], b[3])
                    
                    if self.is_in_roi(orig_bbox):
                        # Return original bbox and explicit None for embedding
                        detections.append((orig_bbox, cls, conf, None))
                elif len(results) > 0 and not detections:
                    # Warn if detections exist but none match the filtered vehicle classes
                    logger.debug(f"Detection found class {cls}, but it is not in vehicle_class_ids {vehicle_class_ids}")
        
        return detections
