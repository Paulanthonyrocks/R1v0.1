import numpy as np
import math
import logging
from typing import Dict, List, Tuple, Optional

logger = logging.getLogger("app.services.lane_calibrator")

class LaneCalibrator:
    """
    Autonomously learns the dominant traffic flow direction for each lane.
    Uses a running consensus of normalized velocity vectors.
    """
    
    def __init__(self, min_samples: int = 20, max_samples: int = 100, confidence_threshold: float = 0.8):
        self.min_samples = min_samples
        self.max_samples = max_samples
        self.confidence_threshold = confidence_threshold
        
        # { feed_id: { lane_id: { "vectors": [], "consensus": list, "confidence": float } } }
        self.lane_data: Dict[str, Dict[int, Dict]] = {}

    def add_sample(self, feed_id: str, lane_id: int, vx: float, vy: float):
        """Adds a velocity sample to the lane's calibration buffer."""
        if feed_id not in self.lane_data:
            self.lane_data[feed_id] = {}
        
        if lane_id not in self.lane_data[feed_id]:
            self.lane_data[feed_id][lane_id] = {
                "vectors": [],
                "consensus": None,
                "confidence": 0.0
            }
        
        # Normalize sample vector
        mag = math.sqrt(vx*vx + vy*vy)
        if mag < 0.5: # Ignore vehicles that are nearly stationary
            return
            
        norm_v = (vx / mag, vy / mag)
        data = self.lane_data[feed_id][lane_id]
        
        data["vectors"].append(norm_v)
        
        # Maintain rolling window
        if len(data["vectors"]) > self.max_samples:
            data["vectors"].pop(0)
            
        # Update calibration if we have enough samples
        if len(data["vectors"]) >= self.min_samples:
            self._update_consensus(feed_id, lane_id)

    def _update_consensus(self, feed_id: str, lane_id: int):
        """Calculates the mean vector and alignment confidence."""
        data = self.lane_data[feed_id][lane_id]
        vectors = data["vectors"]
        
        # Average normalized vectors
        avg_x = sum(v[0] for v in vectors) / len(vectors)
        avg_y = sum(v[1] for v in vectors) / len(vectors)
        
        # Magnitude of the average vector indicates alignment (1.0 = perfect, 0.0 = random)
        alignment = math.sqrt(avg_x**2 + avg_y**2)
        
        if alignment < 0.1:
            # Random directions, no consensus
            data["consensus"] = None
            data["confidence"] = 0.0
            return
            
        # Normalize consensus vector
        data["consensus"] = [avg_x / alignment, avg_y / alignment]
        
        # Confidence score scales with alignment and sample count
        sample_multiplier = min(1.0, len(vectors) / self.min_samples)
        data["confidence"] = alignment * sample_multiplier
        
        if data["confidence"] >= self.confidence_threshold and len(vectors) == self.min_samples:
            logger.info(f"Lane Calibration Complete for feed {feed_id}, lane {lane_id}: {data['consensus']} (Conf: {data['confidence']:.2f})")

    def get_flow_vector(self, feed_id: str, lane_id: int) -> Tuple[Optional[List[float]], float]:
        """Returns the consensus flow vector and its confidence score."""
        lane_map = self.lane_data.get(feed_id)
        if not lane_map:
            return None, 0.0
            
        data = lane_map.get(lane_id)
        if not data or not data["consensus"]:
            # Fallback: if specific lane unknown, try 'all lanes' (-1) if it has high confidence
            if lane_id != -1 and -1 in lane_map:
                fallback = lane_map[-1]
                if fallback["confidence"] > 0.9:
                    return fallback["consensus"], fallback["confidence"]
            return None, 0.0
            
        return data["consensus"], data["confidence"]

    def is_calibrated(self, feed_id: str, lane_id: int) -> bool:
        """Returns True if the lane has reached the calibration confidence threshold."""
        _, confidence = self.get_flow_vector(feed_id, lane_id)
        return confidence >= self.confidence_threshold

    def get_calibration_status(self, feed_id: str) -> Dict:
        """Returns a summary of calibration status for a feed."""
        if feed_id not in self.lane_data:
            return {}
            
        return {
            lane_id: {
                "calibrated": data["confidence"] >= self.confidence_threshold,
                "confidence": round(data["confidence"], 2),
                "samples": len(data["vectors"])
            }
            for lane_id, data in self.lane_data[feed_id].items()
        }
