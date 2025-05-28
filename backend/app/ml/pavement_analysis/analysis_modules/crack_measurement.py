import cv2
import numpy as np
import logging
from typing import Dict, Tuple, Optional

# Adjusted import path for backend structure
from ..utils.camera_calibration import get_pixel_to_mm_ratio 

logger = logging.getLogger(__name__)

def measure_crack_contour(contour: np.ndarray, image_shape: Tuple[int, int], calibration_params: Optional[Dict] = None) -> Dict[str, float]:
    """
    Measures a crack based on its contour.
    Returns a dictionary with estimated measurements in pixels and mm.
    """
    if contour is None or image_shape is None:
        logger.warning("measure_crack_contour received None contour or image_shape.")
        return {}

    measurements = {}
    try:
        area_pixels = cv2.contourArea(contour)
        perimeter_pixels = cv2.arcLength(contour, True)
        
        x, y, w, h = cv2.boundingRect(contour)
        length_pixels_bbox = float(max(w, h))
        # width_pixels_bbox = float(min(w, h)) # This might not be the best width proxy
        
        # Estimate average width using area and length (simplified)
        average_width_pixels = (area_pixels / length_pixels_bbox) if length_pixels_bbox > 0 else 0.0

        # Use the centroid or a point on the contour to estimate pixel-to-mm ratio
        M = cv2.moments(contour)
        point_in_image: Optional[Tuple[int, int]] = None
        if M['m00'] != 0:
            cx = int(M['m10'] / M['m00'])
            cy = int(M['m01'] / M['m00'])
            point_in_image = (cx, cy)
        else:
             point_in_image = (x + w // 2, y + h // 2)

        px_to_mm_x, px_to_mm_y = get_pixel_to_mm_ratio(
            calibration_params=calibration_params,
            point_in_image=point_in_image, 
            image_height=image_shape[0]
        )
        
        length_mm = length_pixels_bbox * px_to_mm_y 
        width_mm = average_width_pixels * px_to_mm_x 
        area_sq_mm = area_pixels * px_to_mm_x * px_to_mm_y
        area_sq_m = area_sq_mm / (1000 * 1000)

        measurements = {
            'area_pixels': float(area_pixels),
            'perimeter_pixels': float(perimeter_pixels),
            'length_pixels': float(length_pixels_bbox),
            'width_pixels': float(average_width_pixels),
            'length_mm': round(length_mm, 2),
            'width_mm': round(width_mm, 2),
            'area_sq_mm': round(area_sq_mm, 2),
            'area_sq_m': round(area_sq_m, 4)
        }
        logger.debug(f"Crack measurements: {measurements}")
        return measurements

    except Exception as e:
        logger.error(f"Error measuring crack contour: {e}", exc_info=True)
        return {}

# TODO: Add functions for specific crack types (transverse, alligator) if their 
# measurement logic differs significantly (e.g., alligator crack density).
# For now, measure_crack_contour can be used for any general linear crack contour. 