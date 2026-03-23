import logging
import numpy as np
import cv2
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
        self.resolution = resolution
        if not roi_points:
            # Default to all-255 (entire frame included)
            self.roi_mask = np.full(resolution[::-1], 255, dtype=np.uint8)
        else:
            self.roi_mask = np.zeros(resolution[::-1], dtype=np.uint8)
            pts = np.array(roi_points, np.int32)
            cv2.fillPoly(self.roi_mask, [pts], 255)

    def _bbox_iou_matrix(self, boxes1: np.ndarray, boxes2: np.ndarray) -> np.ndarray:
        if len(boxes1) == 0 or len(boxes2) == 0:
            return np.zeros((len(boxes1), len(boxes2)))
        
        x11, y11, x12, y12 = np.split(boxes1, 4, axis=1)
        x21, y21, x22, y22 = np.split(boxes2, 4, axis=1)

        xA = np.maximum(x11, np.transpose(x21))
        yA = np.maximum(y11, np.transpose(y21))
        xB = np.minimum(x12, np.transpose(x22))
        yB = np.minimum(y12, np.transpose(y22))

        interArea = np.maximum(0, xB - xA) * np.maximum(0, yB - yA)
        boxAArea = (x12 - x11) * (y12 - y11)
        boxBArea = (x22 - x21) * (y22 - y21)

        iou = interArea / (boxAArea + np.transpose(boxBArea) - interArea + 1e-6)
        return iou

    def _fuse_detections(self) -> List[Tuple]:
        if len(self.detection_history) < self.fusion_min_frames:
            return self.detection_history[-1] # Not enough history, return latest

        current_detections = self.detection_history[-1]
        if not current_detections:
            return []
            
        # Extract features for vectorized comparison
        curr_boxes = np.array([d[0] for d in current_detections])
        curr_classes = np.array([d[1] for d in current_detections])
        
        match_counts = np.ones(len(current_detections), dtype=int)
        
        for i in range(len(self.detection_history) - 1):
            past_frame_detections = self.detection_history[i]
            if not past_frame_detections:
                continue
                
            past_boxes = np.array([d[0] for d in past_frame_detections])
            past_classes = np.array([d[1] for d in past_frame_detections])
            
            # Vectorized IoU Matrix (N x M)
            iou_matrix = self._bbox_iou_matrix(curr_boxes, past_boxes)
            
            # Vectorized Class Match Matrix (N x M)
            cls_matrix = curr_classes[:, None] == past_classes[None, :]
            
            # Valid matches boolean matrix
            valid_matches = (iou_matrix > self.fusion_min_iou) & cls_matrix
            
            # True if current detection matched ANY past detection in this frame
            matched_in_frame = valid_matches.any(axis=1)
            match_counts += matched_in_frame
            
        fused_detections = [current_detections[i] for i, count in enumerate(match_counts) if count >= self.fusion_min_frames]
        
        # D1 Fix: NMS dedup to eliminate overlapping fused detections
        if len(fused_detections) > 1:
            fused_detections = self._nms_dedup(fused_detections, iou_threshold=0.5)
                
        return fused_detections

    def _nms_dedup(self, detections: List[Tuple], iou_threshold: float = 0.5) -> List[Tuple]:
        """Greedy NMS to remove duplicate overlapping detections after fusion."""
        if not detections:
            return detections
        
        # Sort by confidence descending
        sorted_dets = sorted(detections, key=lambda d: d[2], reverse=True)
        keep = []
        
        while sorted_dets:
            best = sorted_dets.pop(0)
            keep.append(best)
            
            remaining = []
            for det in sorted_dets:
                if iou(best[0], det[0]) < iou_threshold:
                    remaining.append(det)
            sorted_dets = remaining
        
        return keep

    def detect(self, frame: np.ndarray, confidence_threshold: float) -> List[Tuple]:
        """Runs detection on the frame and returns bounding boxes and classes."""
        if self.model is None:
            raise RuntimeError("Model not loaded")

        # Apply ROI Mask if configured and not full-screen
        if self.roi_mask is not None:
             # Check if mask is full screen (all 255)
            if not np.all(self.roi_mask == 255):
                # Ensure mask matches frame size
                if self.roi_mask.shape != frame.shape[:2]:
                    # Resize mask to match frame if needed (e.g. if resolution changed)
                    mask_resized = cv2.resize(self.roi_mask, (frame.shape[1], frame.shape[0]), interpolation=cv2.INTER_NEAREST)
                    frame = cv2.bitwise_and(frame, frame, mask=mask_resized)
                else:
                    frame = cv2.bitwise_and(frame, frame, mask=self.roi_mask)

        # TensorRT/ONNX models have device baked in
        device_arg = self.device
        if self.model_path.endswith((".engine", ".onnx")):
            device_arg = None

        # FP16 inference on CUDA for ~2x speedup
        use_half = False # Explicitly disabled for CPU stability
        detections = []
        try:
            results = self.model(frame, conf=confidence_threshold, imgsz=self.imgsz, 
                               classes=self.target_classes, verbose=False, device=device_arg,
                               half=use_half)
            
            for r in results:
                boxes = r.boxes
                for box in boxes:
                    b = box.xyxy[0].cpu().numpy()
                    cls = int(box.cls[0])
                    conf = float(box.conf[0])
                    
                    detections.append((b, cls, conf))
        except Exception as e:
            logger.error(f"Inference failed on frame: {e}")
        
        self.detection_history.append(detections)
        fused_detections = self._fuse_detections()
        
        return fused_detections