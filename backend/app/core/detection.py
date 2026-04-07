import logging
import numpy as np
import cv2
from typing import List, Tuple, Optional, Any
from ultralytics import YOLO
from collections import deque

logger = logging.getLogger("app.ml.detection")

def iou(boxA, boxB):
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])
    interArea = max(0, xB - xA) * max(0, yB - yA)
    boxAArea = max(0, boxA[2] - boxA[0]) * max(0, boxA[3] - boxA[1])
    boxBArea = max(0, boxB[2] - boxB[0]) * max(0, boxB[3] - boxB[1])
    denominator = float(boxAArea + boxBArea - interArea)
    return interArea / denominator if denominator > 1e-6 else 0.0

class DetectionEngine:
    def __init__(self, model_path: str, config: dict, device: str = "cpu"):
        self.model_path = model_path
        self.config = config
        self.device = device
        self.model = None
        vd_cfg = config.get("vehicle_detection", {})
        self.imgsz = vd_cfg.get("yolo_imgsz", 640)
        self.target_classes = vd_cfg.get("target_classes", [2, 3, 5, 7])
        self.roi_mask = None
        self.detection_history = deque(maxlen=3)
        self.fusion_min_iou = vd_cfg.get("fusion_min_iou", 0.6)
        self.fusion_min_frames = vd_cfg.get("fusion_min_frames", 1)

    def load_model(self, preloaded_model: Optional[Any] = None):
        try:
            if preloaded_model:
                self.model = preloaded_model
                return
            self.model = YOLO(self.model_path)
            if self.model_path.endswith(".pt"): self.model.to(self.device)
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            raise

    def initialize_roi(self, resolution: List[int], roi_points: List[List[int]]):
        # This function is no longer needed as ROI is handled in CoreModule.
        pass

    def _bbox_iou_matrix(self, boxes1: np.ndarray, boxes2: np.ndarray) -> np.ndarray:
        if boxes1.size == 0 or boxes2.size == 0: return np.zeros((boxes1.shape[0], boxes2.shape[0]))
        x11, y11, x12, y12 = boxes1[:, 0:1], boxes1[:, 1:2], boxes1[:, 2:3], boxes1[:, 3:4]
        x21, y21, x22, y22 = boxes2[:, 0:1], boxes2[:, 1:2], boxes2[:, 2:3], boxes2[:, 3:4]
        xA = np.maximum(x11, x21.T)
        yA = np.maximum(y11, y21.T)
        xB = np.minimum(x12, x22.T)
        yB = np.minimum(y12, y22.T)
        interArea = np.maximum(0, xB - xA) * np.maximum(0, yB - yA)
        boxAArea = (x12 - x11) * (y12 - y11)
        boxBArea = (x22 - x21) * (y22 - y21)
        return interArea / (boxAArea + boxBArea.T - interArea + 1e-6)

    def _fuse_detections(self) -> List[Tuple]:
        if len(self.detection_history) < self.fusion_min_frames: return self.detection_history[-1] if self.detection_history else []
        current_detections = self.detection_history[-1]
        if not current_detections: return []
        curr_boxes = np.array([d[0] for d in current_detections])
        match_counts = np.ones(len(current_detections), dtype=int)
        for past_detections in list(self.detection_history)[:-1]:
            if not past_detections: continue
            past_boxes = np.array([d[0] for d in past_detections])
            iou_matrix = self._bbox_iou_matrix(curr_boxes, past_boxes)
            if iou_matrix.shape[0] > 0: match_counts += (iou_matrix > self.fusion_min_iou).any(axis=1)
        fused = [current_detections[i] for i, count in enumerate(match_counts) if count >= self.fusion_min_frames]
        return self._nms_dedup(fused)

    def _nms_dedup(self, detections: List[Tuple], iou_threshold: float = 0.5) -> List[Tuple]:
        if not detections: return []
        sorted_dets = sorted(detections, key=lambda d: d[2], reverse=True)
        keep_indices = []
        suppressed = np.zeros(len(sorted_dets), dtype=bool)
        for i in range(len(sorted_dets)):
            if suppressed[i]: continue
            keep_indices.append(i)
            for j in range(i + 1, len(sorted_dets)):
                if not suppressed[j] and iou(sorted_dets[i][0], sorted_dets[j][0]) > iou_threshold:
                    suppressed[j] = True
        return [sorted_dets[i] for i in keep_indices]

    def detect(self, frame: np.ndarray, confidence_threshold: float) -> List[Tuple]:
        if not self.model: raise RuntimeError("Model not loaded")
        # BUG 13 FIX: Removed bitwise_and mask application. Cropping is done in CoreModule.
        results = self.model(frame, conf=confidence_threshold, classes=self.target_classes, verbose=False, device=self.device, half=False)
        detections = [(r.boxes.xyxy[0].cpu().numpy(), int(r.boxes.cls[0]), float(r.boxes.conf[0])) for r in results[0]]
        return self.fuse(detections)

    def fuse(self, detections: List[Tuple]) -> List[Tuple]:
        self.detection_history.append(detections)
        return self._fuse_detections()