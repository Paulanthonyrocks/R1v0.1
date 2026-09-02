"""
Shared utilities for video processing workers.

This module contains common classes and functions used by multiple worker processes
to ensure consistency and reduce code duplication.
"""

import time
import logging
import cv2
import numpy as np
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# Define as a constant Set for faster O(1) lookups
VALID_STATUSES = {"active", "predicting"}


from collections import deque

class WorkerMetrics:
    """Tracks performance metrics for worker processes."""
    
    def __init__(self, feed_id: str, window_size: float = 10.0):
        self.feed_id = feed_id
        self.frames_processed = 0
        self.frames_dropped = 0
        self.errors = 0
        self.shm_leaks = 0
        self.start_time = time.monotonic()
        # Rolling window for current FPS
        self._frame_timestamps = deque()
        self._window_size = window_size
    
    def mark_frame(self):
        """Call this every time a frame is successfully processed."""
        now = time.monotonic()
        self.frames_processed += 1
        self._frame_timestamps.append(now)
        # Prune timestamps older than the window size
        while self._frame_timestamps and self._frame_timestamps[0] < now - self._window_size:
            self._frame_timestamps.popleft()

    def to_dict(self) -> Dict[str, Any]:
        now = time.monotonic()
        uptime = now - self.start_time
        # Prune again just in case it's called without a frame recently
        while self._frame_timestamps and self._frame_timestamps[0] < now - self._window_size:
            self._frame_timestamps.popleft()
        
        rolling_fps = len(self._frame_timestamps) / self._window_size if self._window_size > 0 else 0
        
        return {
            "feed_id": self.feed_id,
            "frames_processed": self.frames_processed,
            "frames_dropped": self.frames_dropped,
            "shm_leaks": self.shm_leaks,
            "errors": self.errors,
            "uptime_seconds": uptime,
            "fps": rolling_fps,
            "lifetime_fps": self.frames_processed / uptime if uptime > 0 else 0
        }
    
    def reset(self):
        """Reset metrics while preserving feed_id."""
        self.frames_processed = 0
        self.frames_dropped = 0
        self.errors = 0
        self.shm_leaks = 0
        self.start_time = time.monotonic()
        self._frame_timestamps.clear()


def make_serializable(obj: Any) -> Any:
    """
    Recursively convert numpy types and nested structures to Python builtin types for JSON serialization.
    """
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return [make_serializable(x) for x in obj.tolist()]
    if isinstance(obj, dict):
        return {k: make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [make_serializable(x) for x in obj]
    return obj


def serialize_tracked_vehicles(
    tracked_vehicles: Dict[str, Dict[str, Any]], 
    scale_x: float = 1.0, 
    scale_y: float = 1.0,
    vehicle_type_map: Optional[Dict[int, str]] = None,
    norm_width: Optional[float] = None,
    norm_height: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Serialize tracked vehicle data for JSON transmission.
    
    Args:
        tracked_vehicles: Dictionary of vehicle_id -> vehicle data
        scale_x: X scaling factor for bbox coordinates
        scale_y: Y scaling factor for bbox coordinates
        vehicle_type_map: Optional mapping of class_id to class_name
        norm_width: When set, bboxes are divided by this width (NORMALIZED wire
            contract). The frontend draws boxes by multiplying bbox values by
            canvas size, i.e. it expects [x1,y1,x2,y2] in 0..1 — pixel-space
            coords made every box render far off-canvas (invisible overlays,
            audit 2026-08-24).
        norm_height: See norm_width.
        
    Returns:
        List of serialized vehicle dictionaries
    """
    serialized_list = []
    v_map = vehicle_type_map or {}
    
    for vehicle_id, data in tracked_vehicles.items():
        if data.get("status") not in VALID_STATUSES:
            continue

        try:
            c_id = data.get("class_id", -1)
            c_name = v_map.get(c_id, "unknown")

            bbox = data.get("bbox")
            scaled_bbox = []
            if bbox and len(bbox) == 4:
                scaled_bbox = [
                    bbox[0] * scale_x,
                    bbox[1] * scale_y,
                    bbox[2] * scale_x,
                    bbox[3] * scale_y
                ]
                if norm_width and norm_height:
                    scaled_bbox = [
                        scaled_bbox[0] / norm_width,
                        scaled_bbox[1] / norm_height,
                        scaled_bbox[2] / norm_width,
                        scaled_bbox[3] / norm_height,
                    ]
            elif bbox is not None:
                logger.warning(f"Malformed bbox for vehicle {vehicle_id}: {bbox}")

            serialized_list.append({
                "vehicle_id": str(vehicle_id),
                "global_vehicle_id": str(data.get("global_vehicle_id", "")),
                "bbox": [make_serializable(x) for x in scaled_bbox] if scaled_bbox else [],
                "speed": make_serializable(data.get("speed", 0.0)),
                "license_plate": str(data.get("license_plate", "Unknown")),
                "class_id": int(c_id),
                "class_name": c_name,
                "confidence": make_serializable(data.get("confidence", 0.0)),
                "is_wrong_way": bool(data.get("is_wrong_way", False)),
                "is_stopped": bool(data.get("is_stopped", False)),
                "lane": int(data.get("lane", -1)),
                "status": str(data.get("status", "unknown")),
                "vx": make_serializable(data.get("vx", 0.0)),
                "vy": make_serializable(data.get("vy", 0.0)),
                "ground_coordinates": [make_serializable(x) for x in data.get("ground_coordinates")] if "ground_coordinates" in data else None,
                "embedding": make_serializable(data.get("embedding")),
            })
        except Exception as e:
            logger.warning(f"Failed to serialize vehicle {vehicle_id}: {e}", exc_info=True)
            serialized_list.append({"vehicle_id": str(vehicle_id), "serialization_error": True})
            continue
    
    # Remove duplicate/ghost boxes from the wire. A track that MISSES a frame
    # goes to status "predicting" and is drawn at its Kalman-predicted bbox. When
    # it is re-detected, the tracker may spawn a fresh "active" track alongside
    # the still-predicting one (association cost above the threshold) -> TWO
    # overlapping boxes on one car. Drop a predicting entry whose bbox overlaps an
    # active entry (IoU > 0.5) so exactly one box per car reaches the frontend.
    # Genuine holds (predicting with NO overlapping active) are kept so the box
    # stays on a momentarily-occluded car until track_timeout.
    if len(serialized_list) > 1:
        active_boxes = [
            d["bbox"]
            for d in serialized_list
            if d.get("status") == "active" and len(d.get("bbox") or []) == 4
        ]
        if active_boxes:
            def _iou_boxes(a, b):
                ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
                ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
                inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
                a_area = (a[2] - a[0]) * (a[3] - a[1])
                b_area = (b[2] - b[0]) * (b[3] - b[1])
                union = a_area + b_area - inter
                return inter / union if union > 1e-9 else 0.0
            serialized_list = [
                d for d in serialized_list
                if not (
                    d.get("status") == "predicting"
                    and len(d.get("bbox") or []) == 4
                    and any(_iou_boxes(d["bbox"], ab) > 0.5 for ab in active_boxes)
                )
            ]

    return serialized_list

def _extract_rois(frame: np.ndarray, serialized_vehicles: List[Dict], scale: float = 1.0) -> List[Dict[str, Any]]:
    """
    Extracts bounding box crops from the frame for adaptive streaming.
    Returns a list of dicts with bytes and scaled coordinates.
    """
    rois = []
    h, w = frame.shape[:2]
    for veh in serialized_vehicles:
        bbox = veh.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        
        x1, y1, x2, y2 = map(int, bbox)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        
        if x2 <= x1 or y2 <= y1:
            continue
            
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            continue
            
        # Encode as JPEG
        success, encoded_img = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if success:
            rois.append({
                "b": encoded_img.tobytes(),
                "x": x1 * scale,
                "y": y1 * scale,
                "w": (x2 - x1) * scale,
                "h": (y2 - y1) * scale
            })
            
    return rois
