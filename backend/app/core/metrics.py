import json
from typing import Dict, Any

class WorkerMetrics:
    def __init__(self, feed_id: str):
        self.feed_id = feed_id
        self.frames_processed = 0
        self.frames_dropped = 0
        self.inference_time_ms = 0
        self.tracking_time_ms = 0
        self.total_time_ms = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "feed_id": self.feed_id,
            "frames_processed": self.frames_processed,
            "frames_dropped": self.frames_dropped,
            "avg_inference_time_ms": self.inference_time_ms / self.frames_processed if self.frames_processed > 0 else 0,
            "avg_tracking_time_ms": self.tracking_time_ms / self.frames_processed if self.frames_processed > 0 else 0,
            "avg_total_time_ms": self.total_time_ms / self.frames_processed if self.frames_processed > 0 else 0,
        }

def serialize_tracked_vehicles(tracked_vehicles: Dict, scale_x: float, scale_y: float, vehicle_type_map: Dict) -> Dict:
    """Serializes tracked vehicle data for API output."""
    # ... (implementation from original file) ...
    return {v_id: {"bbox": [d["bbox"][0] * scale_x, d["bbox"][1] * scale_y, d["bbox"][2] * scale_x, d["bbox"][3] * scale_y], "type": vehicle_type_map.get(d.get("class_id"), "Unknown")} for v_id, d in tracked_vehicles.items()}
