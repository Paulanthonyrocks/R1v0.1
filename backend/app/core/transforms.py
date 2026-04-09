
import cv2
import numpy as np
import logging
from typing import Tuple, Optional, List, Dict

logger = logging.getLogger(__name__)

class CoordinateTransformer:
    """
    Handles coordinate transformations between pixel space (image) and
    real-world ground plane space using a homography matrix.
    """
    def __init__(self, calibration_cfg: Optional[Dict] = None):
        """
        Initializes the transformer with an optional calibration configuration.

        Args:
            calibration_cfg (Dict, optional): A dictionary containing '''image_points''' and '''world_points'''.
        """
        self.homography_matrix: Optional[np.ndarray] = None
        if calibration_cfg:
            self.update_calibration(calibration_cfg)

    def update_calibration(self, calibration_cfg: Dict):
        """
        Updates the homography matrix from a new calibration configuration.

        Args:
            calibration_cfg (Dict): A dictionary containing '''image_points''' and '''world_points'''.
                                    Both should be lists of at least 4 points.
        """
        if not calibration_cfg or "image_points" not in calibration_cfg or "world_points" not in calibration_cfg:
            self.homography_matrix = None
            logger.info("Homography not updated: configuration is missing required keys.")
            return

        img_pts = np.array(calibration_cfg["image_points"], dtype=np.float32)
        world_pts = np.array(calibration_cfg["world_points"], dtype=np.float32)

        if len(img_pts) >= 4 and len(world_pts) >= 4:
            self.homography_matrix, status = cv2.findHomography(img_pts, world_pts, cv2.RANSAC, 5.0)
            if self.homography_matrix is not None:
                logger.info("Homography matrix successfully updated.")
            else:
                logger.error("Homography matrix calculation failed.")
        else:
            self.homography_matrix = None
            logger.warning("Homography not updated: not enough points in configuration.")

    def pixel_to_ground(self, x: float, y: float) -> Optional[Tuple[float, float]]:
        """
        Transforms a single point from pixel coordinates to ground plane coordinates.

        Args:
            x (float): The x-coordinate in the image.
            y (float): The y-coordinate in the image.

        Returns:
            Optional[Tuple[float, float]]: The (x, y) coordinates on the ground plane, or None if the
                                           transformation is not possible (e.g., no homography matrix).
        """
        if self.homography_matrix is None:
            return None
        
        pixel_coords = np.array([[[x, y]]], dtype=np.float32)
        
        try:
            ground_coords = cv2.perspectiveTransform(pixel_coords, self.homography_matrix)
            if ground_coords is not None:
                return (float(ground_coords[0][0][0]), float(ground_coords[0][0][1]))
        except cv2.error as e:
            logger.error(f"Error during perspective transform: {e}")

        return None

class CameraMotionEstimator:
    """
    Estimates camera motion (shake/pan) between consecutive frames using optical flow.
    This helps in stabilizing measurements and tracking.
    """
    def __init__(self, config: Optional[Dict] = None):
        """
        Initializes the motion estimator.

        Args:
            config (Dict, optional): Configuration dictionary. Can specify '''lk_params''' and '''max_features'''.
        """
        cfg = config or {}
        # Parameters for Lucas-Kanade optical flow
        self.lk_params = cfg.get("lk_params", dict(
            winSize=(21, 21),
            maxLevel=3,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03),
        ))
        
        # Parameters for corner detection
        self.max_features = cfg.get("max_features", 200)
        self.feature_params = cfg.get("feature_params", dict(
             maxCorners=self.max_features, qualityLevel=0.01, minDistance=10, blockSize=7
        ))
        
        self.prev_gray: Optional[np.ndarray] = None
        self.prev_pts: Optional[np.ndarray] = None
        self.last_feature_update = 0

    def estimate_motion(self, frame: np.ndarray) -> Tuple[float, float]:
        """
        Estimates the camera's translation (dx, dy) relative to the previous frame.

        Args:
            frame (np.ndarray): The current video frame (in BGR format).

        Returns:
            Tuple[float, float]: The estimated horizontal and vertical shift in pixels.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        dx, dy = 0.0, 0.0

        # Refresh features every second
        if self.prev_pts is None or len(self.prev_pts) < self.max_features * 0.25 or (self.last_feature_update % 30 == 0):
             self.prev_pts = cv2.goodFeaturesToTrack(gray, **self.feature_params)
        self.last_feature_update += 1

        if self.prev_gray is not None and self.prev_pts is not None and len(self.prev_pts) > 0:
            next_pts, status, _ = cv2.calcOpticalFlowPyrLK(
                self.prev_gray, gray, self.prev_pts, None, **self.lk_params
            )

            if next_pts is not None and status is not None:
                good_new = next_pts[status.ravel() == 1]
                good_old = self.prev_pts[status.ravel() == 1]

                if len(good_new) > 10:
                    diff = good_new - good_old
                    # Use median to be robust to outliers (e.g., moving objects)
                    mdx, mdy = np.median(diff, axis=0)
                    
                    # Filter out unrealistic jumps (e.g., >10% of frame dimension)
                    if abs(mdx) < w * 0.1 and abs(mdy) < h * 0.1:
                        dx, dy = mdx, mdy

                # Update points for the next frame
                self.prev_pts = good_new.reshape(-1, 1, 2)
            else:
                 self.prev_pts = cv2.goodFeaturesToTrack(gray, **self.feature_params)
        
        self.prev_gray = gray
        return float(dx), float(dy)
