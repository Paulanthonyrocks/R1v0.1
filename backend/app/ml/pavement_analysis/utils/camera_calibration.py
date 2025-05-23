import cv2
import numpy as np
import logging
from pathlib import Path
from typing import Tuple, Dict, Any, Optional

logger = logging.getLogger(__name__)

def load_calibration_params(config_path: str) -> Dict[str, Any]:
    """
    Load camera calibration parameters from a file
    
    Args:
        config_path: Path to calibration config file
        
    Returns:
        Dictionary containing calibration parameters
    """
    try:
        calibration_data = np.load(config_path)
        params = {
            'camera_matrix': calibration_data['camera_matrix'],
            'dist_coeffs': calibration_data['dist_coeffs'],
            'pixel_to_mm_ratio': calibration_data['pixel_to_mm_ratio']
        }
        return params
    except Exception as e:
        logger.error(f"Error loading calibration parameters: {str(e)}")
        raise

def undistort_image(
    image: np.ndarray,
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray
) -> np.ndarray:
    """
    Remove lens distortion from image
    
    Args:
        image: Input image
        camera_matrix: Camera intrinsic matrix
        dist_coeffs: Distortion coefficients
        
    Returns:
        Undistorted image
    """
    try:
        height, width = image.shape[:2]
        new_camera_matrix, roi = cv2.getOptimalNewCameraMatrix(
            camera_matrix, dist_coeffs, (width, height), 1, (width, height)
        )
        
        undistorted = cv2.undistort(
            image, camera_matrix, dist_coeffs, None, new_camera_matrix
        )
        
        # Crop the image to ROI
        x, y, w, h = roi
        undistorted = undistorted[y:y+h, x:x+w]
        
        return undistorted
    except Exception as e:
        logger.error(f"Error undistorting image: {str(e)}")
        raise

def get_pixel_to_mm_ratio(
    calibration_params: Dict[str, Any],
    distance_to_surface: Optional[float] = None
) -> float:
    """
    Get the pixel to millimeter ratio for measurements
    
    Args:
        calibration_params: Calibration parameters dictionary
        distance_to_surface: Optional distance to pavement surface in mm
        
    Returns:
        Ratio of pixels to millimeters
    """
    try:
        # Use stored ratio if available
        if 'pixel_to_mm_ratio' in calibration_params:
            return float(calibration_params['pixel_to_mm_ratio'])
            
        # Calculate from camera parameters if distance is provided
        elif distance_to_surface is not None:
            focal_length = calibration_params['camera_matrix'][0,0]  # fx
            ratio = focal_length / distance_to_surface
            return ratio
            
        else:
            # Default fallback ratio (this should be calibrated for accuracy)
            logger.warning("Using default pixel to mm ratio. This should be calibrated.")
            return 1.0
            
    except Exception as e:
        logger.error(f"Error getting pixel to mm ratio: {str(e)}")
        raise
