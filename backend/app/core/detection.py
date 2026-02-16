import logging
import numpy as np
from typing import List, Tuple, Optional, Any
from ultralytics import YOLO

logger = logging.getLogger("app.ml.detection")

class DetectionEngine:
    def __init__(self, model_path: str, config: dict, device: str = "cpu"):
        self.model_path = model_path
        self.config = config
        self.device = device
        self.model = None
        self.model_type = config.get("model_type", "yolo")
        self.imgsz = config.get("yolo_imgsz", 640)
        
        # ROI Cache
        self.roi_mask = None
        self.resolution = None

    def load_model(self):
        """Loads the model into the specified device."""
        try:
            if self.model_type == "yolo":
                self.model = YOLO(self.model_path)
                if self.model_path.endswith(".pt"):
                    self.model.to(self.device)
                logger.info(f"YOLO model loaded from {self.model_path} on {self.device}")
            # Placeholder for other model types (RT-DETR, etc.)
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            raise

    def initialize_roi(self, resolution: List[int], roi_points: List[List[int]]):
        """Creates an ROI mask."""
        import cv2
        self.resolution = resolution
        self.roi_mask = np.zeros(resolution[::-1], dtype=np.uint8)
        if roi_points:
            pts = np.array(roi_points, np.int32)
            cv2.fillPoly(self.roi_mask, [pts], 255)

    def is_in_roi(self, x: float, y: float) -> bool:
        if self.roi_mask is None:
            return True
        h, w = self.roi_mask.shape
        ix, iy = int(x), int(y)
        if 0 <= ix < w and 0 <= iy < h:
            return self.roi_mask[iy, ix] > 0
        return False

    def detect(self, frame: np.ndarray, confidence_threshold: float) -> List[Tuple]:
        """Runs detection on the frame and returns bounding boxes and classes."""
        if self.model is None:
            raise RuntimeError("Model not loaded")

        results = self.model(frame, conf=confidence_threshold, imgsz=self.imgsz, verbose=False, device=self.device)
        
        detections = []
        for r in results:
            boxes = r.boxes
            for box in boxes:
                b = box.xyxy[0].cpu().numpy()
                cls = int(box.cls[0])
                conf = float(box.conf[0])
                
                # Center point check for ROI
                cx = (b[0] + b[2]) / 2
                cy = (b[1] + b[3]) / 2
                
                if self.is_in_roi(cx, cy):
                    detections.append((b, cls, conf))
        
        return detections
