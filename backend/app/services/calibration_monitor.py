import cv2
import numpy as np
import logging
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger("app.services.calibration")

class CalibrationMonitor:
    """
    Detects if the camera has moved or shifted since the initial calibration.
    Uses ORB feature matching against a reference frame.
    """
    def __init__(self, feed_id: str, config: dict):
        self.feed_id = feed_id
        self.config = config
        self.drift_threshold = config.get("calibration", {}).get("drift_threshold", 0.05) # 5% change
        
        self.orb = cv2.ORB_create(nfeatures=500)
        self.bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        
        self.reference_gray = None
        self.reference_kpts = None
        self.reference_descs = None
        
        self.last_check_time = 0.0
        self.check_interval = 300.0 # Check every 5 minutes

    def set_reference(self, frame: np.ndarray):
        """Sets the baseline frame for future drift checks."""
        self.reference_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        self.reference_kpts, self.reference_descs = self.orb.detectAndCompute(self.reference_gray, None)
        logger.info(f"[{self.feed_id}] Calibration reference frame captured with {len(self.reference_kpts)} features.")

    def check_drift(self, frame: np.ndarray) -> Tuple[float, bool, Optional[np.ndarray]]:
        """
        Estimates drift from reference. 
        Returns (drift_score, is_drifted, homography_matrix).
        """
        if self.reference_descs is None:
            return 0.0, False, None
            
        now = time.time()
        # Internal check throttle (keep state but return early if too soon)
        # Note: If we want real-time motion compensation, we might need to check every frame.
        # But homography estimation is expensive.
        # Let's keep the interval for "Drift Detection" but allow real-time for "Motion Comp"
        # by checking if self.check_interval == 0.
        if self.check_interval > 0 and (now - self.last_check_time < self.check_interval):
            return 0.0, False, None
            
        self.last_check_time = now
        
        try:
            current_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            kpts, descs = self.orb.detectAndCompute(current_gray, None)
            
            if descs is None or len(descs) < 50:
                logger.warning(f"[{self.feed_id}] Not enough features for drift check.")
                return 1.0, True, None
                
            matches = self.bf.match(self.reference_descs, descs)
            matches = sorted(matches, key=lambda x: x.distance)
            
            if len(matches) < 20:
                return 1.0, True, None
                
            # Extract matched points
            src_pts = np.float32([self.reference_kpts[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
            dst_pts = np.float32([kpts[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
            
            # Find Homography (Reference -> Current)
            M, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
            
            if M is None:
                return 1.0, True, None
                
            # Analyze M (Identity matrix means no movement)
            drift_score = np.linalg.norm(M - np.eye(3))
            
            is_drifted = drift_score > self.drift_threshold
            if is_drifted:
                logger.warning(f"[{self.feed_id}] Calibration Drift Detected! Score: {drift_score:.4f}")
                
            return float(drift_score), is_drifted, M
            
        except Exception as e:
            logger.error(f"[{self.feed_id}] Drift check failed: {e}")
            return 0.0, False, None
