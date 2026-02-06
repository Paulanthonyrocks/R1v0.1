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
