import cv2
import numpy as np
import logging
from typing import Dict, List, Tuple, Optional

logger = logging.getLogger("app.ml.transforms")

class CoordinateTransformer:
    def __init__(self, calibration_cfg: dict):
        self.homography_matrix = None
        self.inv_homography_matrix = None
        
        if "image_points" in calibration_cfg and "world_points" in calibration_cfg:
            self._update_homography(calibration_cfg)

    def _update_homography(self, calibration_cfg: Dict):
        """Calculates the homography matrix to map image pixels to real-world ground coordinates.
        
        Validates:
        - Matching lengths for image_points and world_points
        - Minimum 4 points for homography
        - 2D shape (N, 2) for point arrays
        - Uses SVD for stable matrix inversion
        - Logs RANSAC inlier information
        """
        try:
            img_pts_list = calibration_cfg.get("image_points", [])
            world_pts_list = calibration_cfg.get("world_points", [])
            
            # Fix #1: Validate matching lengths
            if len(img_pts_list) != len(world_pts_list):
                logger.error(f"Point mismatch: {len(img_pts_list)} image points vs {len(world_pts_list)} world points")
                return
            
            if len(img_pts_list) < 4:
                logger.error(f"Insufficient points: need at least 4, got {len(img_pts_list)}")
                return
            
            img_pts = np.array(img_pts_list, dtype=np.float32)
            world_pts = np.array(world_pts_list, dtype=np.float32)
            
            # Fix #1: Validate 2D shape
            if img_pts.ndim != 2 or img_pts.shape[1] != 2:
                logger.error(f"Invalid image_points shape: {img_pts.shape}, expected (N, 2)")
                return
            if world_pts.ndim != 2 or world_pts.shape[1] != 2:
                logger.error(f"Invalid world_points shape: {world_pts.shape}, expected (N, 2)")
                return
            
            # Fix #5: Detect and handle normalized image points [0, 1]
            if img_pts.max() <= 1.0 and img_pts.max() > 0:
                resolution = calibration_cfg.get("resolution")
                if resolution is None:
                    logger.error("Image points appear normalized, but no 'resolution' provided in calibration config. Aborting homography calculation.")
                    return
                
                logger.warning(f"Image points appear normalized (max={img_pts.max():.3f}). Scaling by {resolution}")
                img_pts[:, 0] *= resolution[0]
                img_pts[:, 1] *= resolution[1]
            
            # Fix #7: Capture and log RANSAC inlier mask
            self.homography_matrix, mask = cv2.findHomography(img_pts, world_pts)
            
            if self.homography_matrix is None:
                logger.error("findHomography returned None - points may be collinear or degenerate")
                return
            
            # Log inlier count from RANSAC
            if mask is not None:
                inlier_count = np.sum(mask)
                if inlier_count < len(img_pts):
                    logger.warning(f"RANSAC outliers detected: {inlier_count}/{len(img_pts)} points are inliers")
            else:
                inlier_count = len(img_pts)
            
            # Fix #2: Use SVD-based inversion for numerical stability
            success, self.inv_homography_matrix = cv2.invert(self.homography_matrix, cv2.DECOMP_SVD)
            if not success:
                logger.error("Matrix inversion failed - homography may be singular")
                self.inv_homography_matrix = None
                return
            
            logger.info(f"Homography computed successfully from {len(img_pts)} points (inliers: {inlier_count})")
            
        except Exception as e:
            logger.error(f"Failed to calculate homography: {type(e).__name__}: {e}")
            self.homography_matrix = None
            self.inv_homography_matrix = None

    def pixel_to_ground(self, points: np.ndarray) -> Optional[np.ndarray]:
        """Transforms array of image pixel coordinates to real-world ground coordinates (meters).
        
        Args:
            points: Array of shape (N, 2) with pixel coordinates (x, y).
            
        Returns:
            Array of shape (N, 2) with ground coordinates (x, y) in meters, or None if not calibrated.
        """
        if self.homography_matrix is None:
            return None
        
        # Fix #3 & #9: Validate input shape before reshape
        if points.size == 0:
            return np.array([], dtype=np.float32).reshape(0, 2)
        
        # Ensure 2D array
        if points.ndim == 1:
            if len(points) % 2 != 0:
                logger.error(f"Invalid 1D points length {len(points)} - must be even for (x, y) pairs")
                return None
            points = points.reshape(-1, 2)
        elif points.ndim == 2:
            if points.shape[1] != 2:
                logger.error(f"Invalid points shape {points.shape} - expected (N, 2)")
                return None
        else:
            logger.error(f"Invalid points dimensions {points.ndim} - expected 1D or 2D array")
            return None
        
        # Reshape to (N, 1, 2) as required by perspectiveTransform
        pts = points.reshape(-1, 1, 2).astype(np.float32)
        
        if not np.isfinite(pts).all():
            logger.error("Input points contain NaN or Inf values. Skipping transformation.")
            return None
            
        transformed = cv2.perspectiveTransform(pts, self.homography_matrix)
        return transformed.reshape(-1, 2)

    def update_calibration(self, calibration_cfg: Dict):
        """Public method to update calibration from config.
        
        This is the public API for dynamic recalibration.
        """
        self._update_homography(calibration_cfg)
    
    def ground_to_pixel(self, gx: float, gy: float) -> Optional[Tuple[float, float]]:
        """Transforms real-world ground coordinates back to image pixel coordinates.
        
        For batch operations, use ground_to_pixel_batch instead.
        """
        if self.inv_homography_matrix is None:
            return None
        
        point = np.array([[[gx, gy]]], dtype=np.float32)
        transformed = cv2.perspectiveTransform(point, self.inv_homography_matrix)
        return float(transformed[0][0][0]), float(transformed[0][0][1])
    
    def ground_to_pixel_batch(self, ground_points: np.ndarray) -> Optional[np.ndarray]:
        """Batch version of ground_to_pixel.
        
        Args:
            ground_points: Array of shape (N, 2) with ground coordinates (x, y) in meters.
            
        Returns:
            Array of shape (N, 2) with pixel coordinates (x, y), or None if not calibrated.
        """
        if self.inv_homography_matrix is None:
            return None
        
        pts = ground_points.reshape(-1, 1, 2).astype(np.float32)
        
        if not np.isfinite(pts).all():
            logger.error("Input ground points contain NaN or Inf values. Skipping transformation.")
            return None
            
        transformed = cv2.perspectiveTransform(pts, self.inv_homography_matrix)
        return transformed.reshape(-1, 2)
    
    @property
    def is_calibrated(self) -> bool:
        """Fix #10: Check if transformer is calibrated and ready to use."""
        return self.homography_matrix is not None and self.inv_homography_matrix is not None
    
    def get_status(self) -> Dict:
        """Fix #10: Return diagnostic status for health checks."""
        return {
            "is_calibrated": self.is_calibrated,
            "homography_valid": self.homography_matrix is not None,
            "inverse_valid": self.inv_homography_matrix is not None,
        }
