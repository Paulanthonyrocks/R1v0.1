"""
Shared utilities for video processing workers.

This module contains common classes and functions used by multiple worker processes
to ensure consistency and reduce code duplication.
"""

import time
import logging
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
    vehicle_type_map: Optional[Dict[int, str]] = None
) -> List[Dict[str, Any]]:
    """
    Serialize tracked vehicle data for JSON transmission.
    
    Args:
        tracked_vehicles: Dictionary of vehicle_id -> vehicle data
        scale_x: X scaling factor for bbox coordinates
        scale_y: Y scaling factor for bbox coordinates
        vehicle_type_map: Optional mapping of class_id to class_name
        
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
            elif bbox is not None:
                logger.warning(f"Malformed bbox for vehicle {vehicle_id}: {bbox}")

            serialized_list.append({
                "vehicle_id": str(vehicle_id),
                "bbox": [make_serializable(x) for x in scaled_bbox] if scaled_bbox else [],
                "speed": make_serializable(data.get("speed", 0.0)),
                "license_plate": str(data.get("license_plate", "Unknown")),
                "class_id": int(c_id),
                "class_name": c_name,
                "behavior": str(data.get("behavior", "unknown")),
                "confidence": make_serializable(data.get("confidence", 0.0)),
                "is_occluded": bool(data.get("is_occluded", False)),
                "lane": int(data.get("lane", -1)),
                "status": str(data.get("status", "unknown")),
                "vx": make_serializable(data.get("vx", 0.0)),
                "vy": make_serializable(data.get("vy", 0.0)),
                "ground_coordinates": [make_serializable(x) for x in data.get("ground_coordinates")] if "ground_coordinates" in data else None,
                "car_model": data.get("car_model"),
                "car_model_confidence": make_serializable(data.get("car_model_confidence", 0.0)),
                "gallery_size": make_serializable(data.get("gallery_size", 0)),
                "embedding": make_serializable(data.get("embedding")),
            })
        except Exception as e:
            logger.warning(f"Failed to serialize vehicle {vehicle_id}: {e}", exc_info=True)
            serialized_list.append({"vehicle_id": str(vehicle_id), "serialization_error": True})
            continue
    
    return serialized_list
