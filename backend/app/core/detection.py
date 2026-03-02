import logging
import numpy as np
from typing import List, Tuple, Optional, Any
from ultralytics import YOLO
from collections import deque

logger = logging.getLogger("app.ml.detection")

def iou(boxA, boxB):
    # Determine the (x, y)-coordinates of the intersection rectangle
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])

    # Compute the area of intersection
    interArea = max(0, xB - xA) * max(0, yB - yA)

    # Compute the area of both the prediction and ground-truth rectangles
    boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
    boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
    
    # handle division by zero
    denominator = float(boxAArea + boxBArea - interArea)
    if denominator == 0:
        return 0.0

    # Compute the intersection over union
    iou = interArea / denominator
    return iou

class DetectionEngine:
    def __init__(self, model_path: str, config: dict, device: str = "cpu"):
        self.model_path = model_path
        self.config = config
        self.device = device
        self.model = None
        
        # Read from nested vehicle_detection config if available
        vd_cfg = config.get("vehicle_detection", {})
        self.model_type = vd_cfg.get("model_type", config.get("model_type", "yolo"))
        self.imgsz = vd_cfg.get("yolo_imgsz", config.get("yolo_imgsz", 640))
        self.target_classes = vd_cfg.get("target_classes", [2, 3, 5, 7]) # car, motorcycle, bus, truck
        
        # ROI Cache
        self.roi_mask = None
        self.resolution = None
        
        # Temporal Fusion
        self.detection_history = deque(maxlen=3)
        self.fusion_min_iou = vd_cfg.get("fusion_min_iou", 0.6)
        self.fusion_min_frames = vd_cfg.get("fusion_min_frames", 2)

    def load_model(self, preloaded_model: Optional[Any] = None):
        """Loads the model into the specified device or uses a preloaded one."""
        try:
            if preloaded_model is not None:
                self.model = preloaded_model
                logger.info(f"Using preloaded {self.model_type} model.")
                return

            if self.model_type == "yolo":
                self.model = YOLO(self.model_path)
                if self.model_path.endswith(".pt"):
                    self.model.to(self.device)
                
                # Warmup inference
                logger.info(f"Warming up YOLO model on {self.device}...")
                dummy_frame = np.zeros((self.imgsz, self.imgsz, 3), dtype=np.uint8)
                self.model(dummy_frame, imgsz=self.imgsz, verbose=False)
                
                logger.info(f"YOLO model loaded from {self.model_path} on {self.device}")
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            raise

    def initialize_roi(self, resolution: List[int], roi_points: List[List[int]]):
        """Creates an ROI mask."""
        import cv2
        self.resolution = resolution
        if not roi_points:
            # Default to all-255 (entire frame included)
            self.roi_mask = np.full(resolution[::-1], 255, dtype=np.uint8)
        else:
            self.roi_mask = np.zeros(resolution[::-1], dtype=np.uint8)
            pts = np.array(roi_points, np.int32)
            cv2.fillPoly(self.roi_mask, [pts], 255)

    def _fuse_detections(self) -> List[Tuple]:
        if len(self.detection_history) < self.fusion_min_frames:
            return self.detection_history[-1] # Not enough history, return latest

        current_detections = self.detection_history[-1]
        fused_detections = []

        for current_det in current_detections:
            current_box, current_cls, current_conf = current_det
            match_count = 1 # The detection matches itself in the current frame
            
            # Look for matches in previous frames
            for i in range(len(self.detection_history) - 1):
                past_frame_detections = self.detection_history[i]
                found_match_in_frame = False
                for past_det in past_frame_detections:
                    past_box, past_cls, _ = past_det
                    # Check for class match and sufficient IoU
                    if current_cls == past_cls and iou(current_box, past_box) > self.fusion_min_iou:
                        found_match_in_frame = True
                        break # Move to the next past frame
                if found_match_in_frame:
                    match_count += 1
            
            if match_count >= self.fusion_min_frames:
                # A simple fusion: just take the latest detection if it's stable
                # A more advanced fusion could average box coordinates or confidence
                fused_detections.append(current_det)
                
        return fused_detections

    def detect(self, frame: np.ndarray, confidence_threshold: float) -> List[Tuple]:
        """Runs detection on the frame and returns bounding boxes and classes."""
        if self.model is None:
            raise RuntimeError("Model not loaded")

        # TensorRT/ONNX models have device baked in
        device_arg = self.device
        if self.model_path.endswith((".engine", ".onnx")):
            device_arg = None

        results = self.model(frame, conf=confidence_threshold, imgsz=self.imgsz, 
                           classes=self.target_classes, verbose=False, device=device_arg)
        
        detections = []
        for r in results:
            boxes = r.boxes
            for box in boxes:
                b = box.xyxy[0].cpu().numpy()
                cls = int(box.cls[0])
                conf = float(box.conf[0])
                
                detections.append((b, cls, conf))
        
        self.detection_history.append(detections)
        fused_detections = self._fuse_detections()
        
        return fused_detections