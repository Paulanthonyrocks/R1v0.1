import cv2
import numpy as np
import logging
from typing import Dict, List, Tuple, Optional

logger = logging.getLogger("app.ml.transforms")

class CoordinateTransformer:
    def __init__(self, calibration_cfg: dict):
        self.config = calibration_cfg
        self.homography_matrix = None
        self.inv_homography_matrix = None
        
        if "image_points" in calibration_cfg and "world_points" in calibration_cfg:
            self._update_homography(calibration_cfg)

    def _update_homography(self, calibration_cfg: Dict):
        """Calculates the homography matrix to map image pixels to real-world ground coordinates."""
        try:
            img_pts = np.array(calibration_cfg["image_points"], dtype=np.float32)
            world_pts = np.array(calibration_cfg["world_points"], dtype=np.float32)
            
            if len(img_pts) < 4:
                return

            self.homography_matrix, _ = cv2.findHomography(img_pts, world_pts)
            if self.homography_matrix is not None:
                self.inv_homography_matrix = np.linalg.inv(self.homography_matrix)
                logger.info("Homography matrix updated successfully.")
        except Exception as e:
            logger.error(f"Failed to calculate homography: {e}")

    def pixel_to_ground(self, x: float, y: float) -> Optional[Tuple[float, float]]:
        """Transforms image pixel coordinates to real-world ground coordinates (meters)."""
        if self.homography_matrix is None:
            return None
        
        point = np.array([[[x, y]]], dtype=np.float32)
        transformed = cv2.perspectiveTransform(point, self.homography_matrix)
        return float(transformed[0][0][0]), float(transformed[0][0][1])

    def ground_to_pixel(self, gx: float, gy: float) -> Optional[Tuple[float, float]]:
        """Transforms real-world ground coordinates back to image pixel coordinates."""
        if self.inv_homography_matrix is None:
            return None
        
        point = np.array([[[gx, gy]]], dtype=np.float32)
        transformed = cv2.perspectiveTransform(point, self.inv_homography_matrix)
        return float(transformed[0][0][0]), float(transformed[0][0][1])

class CameraMotionEstimator:
    """Estimates camera movement (shake/drift) between frames using optical flow."""
    def __init__(self, max_features=100):
        self.max_features = max_features
        self.prev_gray = None
        self.prev_pts = None
        # LK params
        self.lk_params = dict(winSize=(15, 15), maxLevel=2,
                             criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 10, 0.03))

    def estimate_motion(self, frame: np.ndarray) -> Tuple[float, float]:
        """Returns (dx, dy) shift from previous frame."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        dx, dy = 0.0, 0.0

        if self.prev_gray is not None and self.prev_pts is not None:
            # Calculate optical flow
            next_pts, status, error = cv2.calcOpticalFlowPyrLK(self.prev_gray, gray, self.prev_pts, None, **self.lk_params)
            
            if next_pts is not None:
                good_new = next_pts[status == 1]
                good_old = self.prev_pts[status == 1]
                
                if len(good_new) > 10:
                    # Calculate average displacement
                    diff = good_new - good_old
                    dx, dy = np.median(diff, axis=0)
                    
                    # Optional: Filter out massive jumps (unlikely to be camera shake)
                    if abs(dx) > 50 or abs(dy) > 50:
                        dx, dy = 0.0, 0.0
        
        # Update for next frame
        # We re-detect features periodically or if we have too few
        if self.prev_pts is None or len(self.prev_pts) < 20:
            self.prev_pts = cv2.goodFeaturesToTrack(gray, maxCorners=self.max_features, qualityLevel=0.3, minDistance=7, blockSize=7)
        else:
            # Keep the features we just tracked to ensure continuity
            # But we might need to refresh them occasionally to avoid tracking moving objects
            self.prev_pts = cv2.goodFeaturesToTrack(gray, maxCorners=self.max_features, qualityLevel=0.3, minDistance=7, blockSize=7)

        self.prev_gray = gray
        return float(dx), float(dy)
