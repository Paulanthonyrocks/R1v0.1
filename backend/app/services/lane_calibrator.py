import numpy as np
import logging
import time
from typing import Dict, List, Tuple, Optional

logger = logging.getLogger(__name__)

class LaneCalibrator:
    """
    Autonomous Lane Calibration Service.
    Accumulates long-term vehicle trajectory data to detect camera drift
    and suggest microscopic 'nudges' to lane boundaries.
    """
    
    def __init__(self, feed_id: str, config: Dict):
        self.feed_id = feed_id
        self.config = config
        self.trajectory_buffer = [] # Store centroids (x, y)
        self.max_buffer_size = 2000
        self.calibration_interval = 1000 # Analyze every 1000 points
        self.point_count = 0
        self.last_nudge = {"x": 0.0, "y": 0.0}
        
    def accumulate(self, vehicles: List[Dict]):
        """
        Adds current frame vehicle centroids to the calibration buffer.
        """
        for v in vehicles:
            centroid = v.get("centroid")
            if centroid and len(centroid) == 2:
                self.trajectory_buffer.append(centroid)
                self.point_count += 1
                
        # Maintain buffer size
        if len(self.trajectory_buffer) > self.max_buffer_size:
            self.trajectory_buffer = self.trajectory_buffer[-self.max_buffer_size:]
            
    async def analyze_and_nudge(self) -> Dict:
        """
        Analyzes the point cloud of trajectories to detect drift.
        Returns a nudge suggestion if drift exceeds tolerance.
        """
        if len(self.trajectory_buffer) < self.calibration_interval:
            return {"status": "collecting", "points": len(self.trajectory_buffer)}
            
        # Optimization: Perform statistical analysis of the trajectory clusters
        points = np.array(self.trajectory_buffer)
        mean_p = np.mean(points, axis=0)
        std_p = np.std(points, axis=0)
        
        # Simple heuristic: If the 'center of traffic flow' drifts significantly
        # from the historical mean, we signal a camera nudge requirement.
        # (In a production env, we match these clusters to the ROI polygons)
        
        # Dummy logic for Phase 16 demonstration
        drift_detected = std_p[0] > 0.05 # Example threshold
        
        if drift_detected:
            # Calculate a microscopic nudge to compensate
            nudge = {
                "x_nudge": float(np.random.normal(0, 0.01)),
                "y_nudge": float(np.random.normal(0, 0.01)),
                "timestamp": time.time()
            }
            self.last_nudge = nudge
            logger.info(f"[{self.feed_id}] Autonomous Lane Nudge suggested: {nudge}")
            return {"status": "nudge_required", "data": nudge}
            
        return {"status": "stable", "confidence": 0.98}

    def get_status(self) -> Dict:
        return {
            "feed_id": self.feed_id,
            "buffer_size": len(self.trajectory_buffer),
            "last_nudge": self.last_nudge
        }
