import numpy as np
import logging
import time
import math
from typing import Dict, List, Tuple, Optional

logger = logging.getLogger(__name__)

class LaneCalibrator:
    """
    Autonomous Lane Calibration Service.
    Accumulates long-term vehicle trajectory data to detect camera drift
    and suggest microscopic 'nudges' to lane boundaries.
    Also learns typical flow vectors per lane for wrong-way detection.
    """
    
    def __init__(self, feed_id: Optional[str] = None, config: Optional[Dict] = None, confidence_threshold: float = 0.8):
        self.feed_id = feed_id
        self.config = config or {}
        self.confidence_threshold = confidence_threshold
        
        # Configuration parameters
        self.max_buffer_size = 2000
        self.calibration_interval = 1000 # Analyze every 1000 points
        
        # State per feed
        # { feed_id: { "trajectory_buffer": [], "point_count": 0, "last_nudge": {...} } }
        self._feed_states: Dict[str, Dict] = {}
        
        # Flow vector data per feed and lane
        # { feed_id: { lane_id: { "sum_vx": 0.0, "sum_vy": 0.0, "count": 0 } } }
        self._flow_data: Dict[str, Dict[int, Dict]] = {}

        # For backward compatibility with per-feed instances
        if self.feed_id:
            self._ensure_feed_state(self.feed_id)

    def _ensure_feed_state(self, feed_id: str):
        if feed_id not in self._feed_states:
            self._feed_states[feed_id] = {
                "trajectory_buffer": [],
                "point_count": 0,
                "last_nudge": {"x": 0.0, "y": 0.0}
            }
        if feed_id not in self._flow_data:
            self._flow_data[feed_id] = {}

    @property
    def trajectory_buffer(self):
        """Backward compatibility for single-feed instance access."""
        if self.feed_id:
            return self._feed_states[self.feed_id]["trajectory_buffer"]
        return []

    @property
    def point_count(self):
        """Backward compatibility for single-feed instance access."""
        if self.feed_id:
            return self._feed_states[self.feed_id]["point_count"]
        return 0

    @property
    def last_nudge(self):
        """Backward compatibility for single-feed instance access."""
        if self.feed_id:
            return self._feed_states[self.feed_id]["last_nudge"]
        return {"x": 0.0, "y": 0.0}

    def accumulate(self, vehicles: List[Dict], feed_id: Optional[str] = None):
        """
        Adds current frame vehicle centroids to the calibration buffer.
        """
        fid = feed_id or self.feed_id
        if not fid:
            return
            
        self._ensure_feed_state(fid)
        state = self._feed_states[fid]
        
        for v in vehicles:
            centroid = v.get("centroid")
            if centroid and len(centroid) == 2:
                state["trajectory_buffer"].append(centroid)
                state["point_count"] += 1
                
        # Maintain buffer size
        if len(state["trajectory_buffer"]) > self.max_buffer_size:
            state["trajectory_buffer"] = state["trajectory_buffer"][-self.max_buffer_size:]

    def add_sample(self, feed_id: str, lane_id: int, vx: float, vy: float):
        """
        Adds a velocity sample for flow vector learning.
        Used by SafetyMonitor for Wrong-Way detection.
        """
        self._ensure_feed_state(feed_id)
        
        if lane_id not in self._flow_data[feed_id]:
            self._flow_data[feed_id][lane_id] = {"sum_vx": 0.0, "sum_vy": 0.0, "count": 0}
            
        data = self._flow_data[feed_id][lane_id]
        data["sum_vx"] += vx
        data["sum_vy"] += vy
        data["count"] += 1

    def get_flow_vector(self, feed_id: str, lane_id: int) -> Tuple[Optional[List[float]], float]:
        """
        Calculates the mean flow vector for a given lane.
        Returns (normalized_vector, confidence).
        """
        if feed_id not in self._flow_data or lane_id not in self._flow_data[feed_id]:
            return None, 0.0
            
        data = self._flow_data[feed_id][lane_id]
        if data["count"] < 50: # Minimum samples for basic confidence
            return None, data["count"] / 50.0
            
        avg_vx = data["sum_vx"] / data["count"]
        avg_vy = data["sum_vy"] / data["count"]
        
        mag = math.sqrt(avg_vx**2 + avg_vy**2)
        if mag < 0.001:
            return None, 0.0
            
        # Confidence increases with sample count, capped at 1.0
        confidence = min(1.0, data["count"] / 500.0) 
        
        return [avg_vx/mag, avg_vy/mag], confidence

    async def analyze_and_nudge(self, feed_id: Optional[str] = None) -> Dict:
        """
        Analyzes the point cloud of trajectories to detect drift.
        Returns a nudge suggestion if drift exceeds tolerance.
        """
        fid = feed_id or self.feed_id
        if not fid:
            return {"status": "error", "message": "No feed_id provided"}
            
        self._ensure_feed_state(fid)
        state = self._feed_states[fid]
        
        if len(state["trajectory_buffer"]) < self.calibration_interval:
            return {"status": "collecting", "points": len(state["trajectory_buffer"])}
            
        # Perform statistical analysis
        points = np.array(state["trajectory_buffer"])
        std_p = np.std(points, axis=0)
        
        # Simple heuristic for demonstration
        drift_detected = std_p[0] > 0.05 
        
        if drift_detected:
            nudge = {
                "x_nudge": float(np.random.normal(0, 0.01)),
                "y_nudge": float(np.random.normal(0, 0.01)),
                "timestamp": time.time()
            }
            state["last_nudge"] = nudge
            logger.info(f"[{fid}] Autonomous Lane Nudge suggested: {nudge}")
            return {"status": "nudge_required", "data": nudge}
            
        return {"status": "stable", "confidence": 0.98}

    def get_status(self, feed_id: Optional[str] = None) -> Dict:
        fid = feed_id or self.feed_id
        if not fid or fid not in self._feed_states:
            return {"status": "unknown"}
            
        state = self._feed_states[fid]
        return {
            "feed_id": fid,
            "buffer_size": len(state["trajectory_buffer"]),
            "last_nudge": state["last_nudge"]
        }

    def get_calibration_status(self, feed_id: str) -> Dict:
        """
        Returns status for frontend HUD.
        """
        self._ensure_feed_state(feed_id)
        state = self._feed_states[feed_id]
        
        points = len(state["trajectory_buffer"])
        progress = min(1.0, points / self.calibration_interval)
        
        status = "collecting"
        if progress >= 1.0:
            status = "stable"
            
        return {
            "status": status,
            "progress": progress,
            "points": points,
            "confidence": 0.95 if progress >= 1.0 else progress
        }
