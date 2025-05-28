import cv2
import numpy as np
import logging
from pathlib import Path
from typing import Tuple, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Default fallback ratio (this should be calibrated for accuracy)
DEFAULT_PIXEL_TO_MM_X = 0.5 # Example: 1 pixel = 0.5 mm horizontally
DEFAULT_PIXEL_TO_MM_Y = 0.5 # Example: 1 pixel = 0.5 mm vertically

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
    calibration_params: Optional[Dict[str, Any]] = None, # Made optional
    distance_to_surface: Optional[float] = None,
    point_in_image: Optional[Tuple[int, int]] = None, # Added for potential future use
    image_height: Optional[int] = None # Added for potential future use
) -> Tuple[float, float]:
    """
    Get the pixel to millimeter ratio for measurements
    
    Args:
        calibration_params: Calibration parameters dictionary
        distance_to_surface: Optional distance to pavement surface in mm
        point_in_image: Optional (x,y) coordinates of the point in the image.
        image_height: Optional height of the image.
        
    Returns:
        Tuple of (pixel_to_mm_x, pixel_to_mm_y)
    """
    try:
        # Use stored ratio if available and it's a pair
        if calibration_params and 'pixel_to_mm_ratio_x' in calibration_params and 'pixel_to_mm_ratio_y' in calibration_params:
            return float(calibration_params['pixel_to_mm_ratio_x']), float(calibration_params['pixel_to_mm_ratio_y'])
        # Use stored single ratio if available (apply to both axes)
        elif calibration_params and 'pixel_to_mm_ratio' in calibration_params:
            ratio = float(calibration_params['pixel_to_mm_ratio'])
            return ratio, ratio
            
        # Calculate from camera parameters if distance is provided (simplified)
        elif calibration_params and distance_to_surface is not None and 'camera_matrix' in calibration_params:
            focal_length_x = calibration_params['camera_matrix'][0,0]  # fx
            focal_length_y = calibration_params['camera_matrix'][1,1]  # fy
            # This is a very simplified model, assumes camera pointing straight down
            ratio_x = distance_to_surface / focal_length_x # mm/pixel if focal_length is in pixels and distance in mm
            ratio_y = distance_to_surface / focal_length_y # This needs careful unit checking.
                                                       # Assuming sensor_element_size / focal_length = pixel_size_in_world_units / distance_to_object
                                                       # So pixel_to_mm = (sensor_element_size_mm * distance_to_object_mm) / focal_length_pixels
                                                       # Let's stick to simpler default for now if this path is taken.
                                                       # For now, let's assume this calculation yields px/mm if set up.
                                                       # To be safe, using defaults if this path is chosen without proper unit validation
            logger.warning("Pixel to mm ratio from focal length and distance is experimental. Consider using direct calibration.")
            # Fallback to defaults if calculated ratios are zero or negative
            if focal_length_x > 0 and focal_length_y > 0:
                 # This needs to be inverted if focal_length is in pixels: mm_per_pixel = distance_mm / focal_length_pixels * sensor_pixel_size_mm
                 # For simplicity, we'll assume the calibration provides a direct way or we use defaults.
                 # Let's assume for now this is a placeholder for a more complex calculation
                 # and default to the global defaults if not perfectly configured.
                 # return focal_length / distance_to_surface, focal_length / distance_to_surface
                 pass # Pass to use defaults below if this logic path isn't fully fleshed out

        # Fallback to default values
        # Perspective scaling can be added here if point_in_image and image_height are used
        scale_factor = 1.0
        # Example of perspective scaling (adjust as needed, similar to pavement_analyzer)
        # if image_height and point_in_image:
        #     scale_factor = 1.0 + ( (image_height - point_in_image[1]) / image_height ) * 0.5

        logger.warning("Using default pixel to mm ratios. This should be calibrated for accuracy.")
        return DEFAULT_PIXEL_TO_MM_X * scale_factor, DEFAULT_PIXEL_TO_MM_Y * scale_factor
            
    except Exception as e:
        logger.error(f"Error getting pixel to mm ratio: {str(e)}")
        # Fallback to defaults in case of any error
        return DEFAULT_PIXEL_TO_MM_X, DEFAULT_PIXEL_TO_MM_Y
