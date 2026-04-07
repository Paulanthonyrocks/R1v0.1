import json
from typing import Dict, Any, List

class WorkerMetrics:
    """A simple class to track metrics for an inference worker."""
    def __init__(self, feed_id: str):
        self.feed_id = feed_id
        self.frames_processed = 0
        self.frames_dropped = 0
        self.start_time = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "feed_id": self.feed_id,
            "frames_processed": self.frames_processed,
            "frames_dropped": self.frames_dropped,
            "uptime_seconds": time.time() - self.start_time,
        }

def prepare_vehicles_for_transport(tracked_vehicles: Dict[str, Any], scale_x: float, scale_y: float, vehicle_type_map: Dict[int, str]) -> List[Dict[str, Any]]:
    """Prepares tracked vehicle data for transport between processes."""
    vehicles_to_send = []
    for tid, data in tracked_vehicles.items():
        if data.get("status") not in ["active", "predicting"]:
            continue

        v_data = {
            "vehicle_id": tid,
            "bbox": [
                data["bbox"][0] * scale_x,
                data["bbox"][1] * scale_y,
                data["bbox"][2] * scale_x,
                data["bbox"][3] * scale_y
            ],
            "class_id": data.get("class_id"),
            "class_name": vehicle_type_map.get(data.get("class_id"), "unknown"),
            "confidence": data.get("confidence"),
            "speed": data.get("speed"),
            "status": data.get("status"),
            "behavior": data.get("behavior", "normal"),
            "embedding": data.get("embedding"),
        }
        vehicles_to_send.append(v_data)

    return vehicles_to_send
