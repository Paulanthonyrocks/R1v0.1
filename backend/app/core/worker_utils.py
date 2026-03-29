"""
Shared utilities for video processing workers.

This module contains common classes and functions used by multiple worker processes
to ensure consistency and reduce code duplication.
"""

import time
import logging
import numpy as np
import os
from typing import Dict, List, Any, Optional, Tuple, TYPE_CHECKING
from multiprocessing import shared_memory

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Worker Architecture Documentation
WORKER_ARCHITECTURE_DOC = """
WORKER ARCHITECTURE:
- ingestion_worker.py: Capture frames from source → central_input_queue
  Use for: Multi-feed systems where AI is shared across feeds
  
- inference_worker.py: Process frames from central_input_queue → AI results
  Use for: GPU-bound scenarios where one GPU serves multiple feeds
  
- processing_worker.py: All-in-one (capture + AI + visualization)
  Use for: Single-feed systems or when each feed needs isolated processing
  
DO NOT MIX: Choose either (ingestion + inference) OR (processing) per deployment
"""


class WorkerMetrics:
    """Tracks performance metrics for worker processes."""
    
    def __init__(self, feed_id: str):
        self.feed_id = feed_id
        self.frames_processed = 0
        self.frames_dropped = 0
        self.errors = 0
        self.start_time = time.time()
    
    def to_dict(self) -> Dict[str, Any]:
        uptime = time.time() - self.start_time
        return {
            "feed_id": self.feed_id,
            "frames_processed": self.frames_processed,
            "frames_dropped": self.frames_dropped,
            "errors": self.errors,
            "uptime_seconds": uptime,
            "fps": self.frames_processed / uptime if uptime > 0 else 0
        }
    
    def reset(self):
        """Reset metrics while preserving feed_id."""
        self.frames_processed = 0
        self.frames_dropped = 0
        self.errors = 0
        self.start_time = time.time()


class SharedFrameManager:
    """
    Manages Zero-Copy frame exchange via Shared Memory.
    Eliminates pickle overhead for high-resolution video IPC.
    """
    
    @staticmethod
    def create_shm(frame: np.ndarray) -> Tuple[str, Tuple[int, ...], str]:
        """
        Allocates shared memory and copies frame data.
        Returns (shm_name, shape, dtype_str).
        """
        shm = shared_memory.SharedMemory(create=True, size=frame.nbytes)
        # Create a numpy array view backed by the shared memory buffer
        shm_array = np.ndarray(frame.shape, dtype=frame.dtype, buffer=shm.buf)
        shm_array[:] = frame[:]
        shm.close() # Close access in producer, but it stays alive until unlinked
        return shm.name, frame.shape, str(frame.dtype)

    @staticmethod
    def access_shm(name: str, shape: Tuple[int, ...], dtype_str: str) -> np.ndarray:
        """
        Attaches to existing shared memory and returns a numpy view.
        """
        shm = shared_memory.SharedMemory(name=name)
        # Note: The caller must keep a reference to shm to keep the buffer alive while using the array
        # or we return a copy if we want to unlink immediately.
        # For inference, we attach, run model, then detach.
        return np.ndarray(shape, dtype=np.dtype(dtype_str), buffer=shm.buf), shm

    @staticmethod
    def cleanup_shm(name: str):
        """Closes and unlinks shared memory block."""
        try:
            shm = shared_memory.SharedMemory(name=name)
            shm.close()
            shm.unlink()
        except (FileNotFoundError, Exception):
            pass


def make_serializable(obj: Any) -> Any:
    """
    Convert numpy types to Python builtin types for JSON serialization.
    
    Args:
        obj: Value that may contain numpy types
        
    Returns:
        Python builtin type equivalent
    """
    if isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    if isinstance(obj, (np.floating, np.float64, np.float32)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def serialize_tracked_vehicles(
    tracked_vehicles: Dict[str, Dict], 
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

            serialized_list.append({
                "vehicle_id": str(vehicle_id),
                "bbox": [make_serializable(x) for x in scaled_bbox],
                "speed": make_serializable(data.get("speed", 0)),
                "speed_err": make_serializable(data.get("speed_err", 0.0)),
                "license_plate": str(data.get("license_plate", "Unknown")),
                "class_id": int(c_id),
                "class_name": c_name,
                "behavior": str(data.get("behavior", "unknown")),
                "acceleration": make_serializable(data.get("acceleration", 0)),
                "vx": make_serializable(data.get("vx", 0.0) * scale_x),
                "vy": make_serializable(data.get("vy", 0.0) * scale_y),
                "quality_score": make_serializable(data.get("quality_score", 1.0)),
                "hits": int(data.get("hits", 0)),
                "confidence": make_serializable(data.get("confidence", 0)),
                "is_occluded": bool(data.get("is_occluded", False)),
                "lane": int(data.get("lane", -1)),
                "status": str(data.get("status", "unknown")),
                "centroid": [make_serializable((scaled_bbox[0] + scaled_bbox[2]) / 2), make_serializable((scaled_bbox[1] + scaled_bbox[3]) / 2)] if scaled_bbox else None,
                "ground_coordinates": [make_serializable(x) for x in data.get("ground_coordinates")] if "ground_coordinates" in data else None,
                "ground_centroid": [make_serializable(x) for x in data.get("ground_centroid")] if "ground_centroid" in data else None,
                "car_model": data.get("car_model"),
                "car_model_confidence": make_serializable(data.get("car_model_confidence", 0)),
                "gallery_size": make_serializable(data.get("gallery_size", 0)),
                # Embedding excluded from serialization - only used internally for ReID
            })
        except Exception as e:
            logger.warning(f"Failed to serialize vehicle {vehicle_id}: {e}")
            continue
    
    return serialized_list
