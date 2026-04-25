import logging
import numpy as np
from typing import List, Tuple, Optional, Any
from ultralytics import YOLO

logger = logging.getLogger("app.ml.detection")

class DetectionEngine:
    def __init__(self, model_path: str, config: dict, device: str = "cpu", preloaded_model: Optional[Any] = None):
        self.model_path = model_path
        self.config = config
        self.device = device
        self.model = preloaded_model
        self.model_type = config.get("model_type", "yolo")
        self.imgsz = config.get("yolo_imgsz", 640)
        
        # ROI Cache
        self.roi_mask = None
        self.resolution = None

    def load_model(self):
        """Loads the model into the specified device if not already preloaded."""
        try:
            if self.model is not None:
                logger.info(f"Using preloaded {self.model_type} model.")
                return

            if self.model_type == "yolo":
                self.model = YOLO(self.model_path)
                self.model.to(self.device)
                logger.info(f"YOLO model loaded on {self.device}")
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
        intersection_pixels = np.count_nonzero(roi_crop)
        box_area = (x2 - x1) * (y2 - y1)
        
        # Keep detection if at least 10% of the box is within the ROI
        return (intersection_pixels / (box_area + 1e-6)) >= 0.1

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
                
                # Scale coordinates to match ROI mask resolution
                scale_x = self.roi_mask.shape[1] / frame.shape[1] if frame is not None else 1.0
                scale_y = self.roi_mask.shape[0] / frame.shape[0] if frame is not None else 1.0
                
                scaled_bbox = np.array([
                    b[0] * scale_x, 
                    b[1] * scale_y, 
                    b[2] * scale_x, 
                    b[3] * scale_y
                ])
                
                if self.is_in_roi(scaled_bbox):
                    detections.append((b, cls, conf))
        
        return detections
