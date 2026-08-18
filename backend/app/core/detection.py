import logging
import os
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
        
        # Detect if preloaded model is a TensorRT engine (shape-locked to export imgsz=320)
        # Check multiple indicators: model path, model file attribute, or explicit engine loading
        self.is_trt_engine = False
        if preloaded_model is not None:
            # Check if the model was loaded from an .engine file
            model_path_attr = getattr(preloaded_model, 'ckpt_path', None) or getattr(preloaded_model, 'model_path', None)
            if model_path_attr and str(model_path_attr).endswith('.engine'):
                self.is_trt_engine = True
            # Also check internal model reference
            internal_model = getattr(preloaded_model, 'model', None)
            if internal_model and str(internal_model).endswith('.engine'):
                self.is_trt_engine = True
            # Check if model_path itself is an engine (when loading directly, not preloaded)
            elif str(model_path).endswith('.engine'):
                self.is_trt_engine = True
        
        if self.is_trt_engine:
            # CONTRACT (see inference_worker.py:~991 and export_tensorrt.py): a
            # TRT engine is shape-locked to the imgsz it was exported with, and
            # export_tensorrt.py reads vehicle_detection.yolo_imgsz. We MUST feed
            # exactly that size, so use the configured value -- NOT a hardcoded
            # 320. A mismatch ("input size != max model size") makes warm-up /
            # every detect frame fail. The previous hardcoded 320 only lined up
            # when yolo_imgsz happened to be 320; with the current 960 engine it
            # raised "input size [1,3,320,320] not equal to max model size
            # (1,3,960,960)" and killed the feed's CoreModule init.
            self.imgsz = int(self.config.get("vehicle_detection", {}).get("yolo_imgsz", 640))
        
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
                # BOOT INTEGRITY GUARD: reject a corrupt/truncated .pt before
                # handing it to ultralytics (which fails much later inside the
                # C++ PytorchStreamReader with the opaque
                # "failed reading zip archive: failed finding central directory"
                # error -- the exact crash observed at boot). A valid YOLO .pt is
                # a ZIP archive: starts with b'PK\x03\x04', ends with the EOCD
                # record b'PK\x05\x06', and is > ~1 MB. Anything else (empty,
                # a half-written download, a JPEG, a text error page) is rejected
                # up front with an actionable message so the operator re-downloads
                # rather than the worker dying mid-run and thrashing the autoscaler.
                mp = self.model_path
                if not os.path.exists(mp):
                    raise FileNotFoundError(f"YOLO weights not found at {mp}")
                if os.path.getsize(mp) < 1_000_000:
                    raise ValueError(
                        f"YOLO weights at {mp} are only {os.path.getsize(mp)} bytes "
                        f"(expected ~6.2 MB). The file is truncated/corrupt -- "
                        f"re-download it (e.g. `curl -L https://github.com/"
                        f"ultralytics/assets/releases/download/v8.4.0/yolov8n.pt "
                        f"-o {mp}`)."
                    )
                with open(mp, "rb") as _fh:
                    _head = _fh.read(4)
                    # A valid YOLO .pt is a ZIP archive whose End-Of-Central-
                    # Directory record begins with b'PK\x05\x06'. Ultralytics
                    # appends a small binary footer AFTER the EOCD, so the
                    # signature is NOT at the very tail -- scan the last 64
                    # bytes for it instead of demanding it at offset -4.
                    _fh.seek(-64, os.SEEK_END)
                    _tail = _fh.read(64)
                if _head[:4] != b"PK\x03\x04" or b"PK\x05\x06" not in _tail:
                    raise ValueError(
                        f"YOLO weights at {mp} are not a valid PyTorch .pt archive "
                        f"(bad ZIP magic: head={_head!r}, no EOCD record). The file "
                        f"is corrupt -- re-download it before starting the backend."
                    )
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
