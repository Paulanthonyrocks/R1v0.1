import cv2
import numpy as np
import logging
from typing import Dict, Tuple, Optional

# Adjusted import path for backend structure
from ..utils.camera_calibration import get_pixel_to_mm_ratio

logger = logging.getLogger(__name__)

def measure_pothole_contour(contour: np.ndarray, image_shape: Tuple[int, int], calibration_params: Optional[Dict] = None) -> Dict[str, float]:
    """
    Measures a pothole based on its contour.
    Returns a dictionary with estimated measurements in pixels and mm.
    """
    if contour is None or image_shape is None:
        logger.warning("measure_pothole_contour received None contour or image_shape.")
        return {}

    measurements = {}
    try:
        area_pixels = cv2.contourArea(contour)
        perimeter_pixels = cv2.arcLength(contour, True)

        x, y, w, h = cv2.boundingRect(contour)
        
        estimated_diameter_pixels = (w + h) / 2.0

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

        area_sq_mm = area_pixels * px_to_mm_x * px_to_mm_y
        area_sq_m = area_sq_mm / (1000 * 1000)
        
        # Use average ratio for general dimensions from bounding box
        avg_px_to_mm = (px_to_mm_x + px_to_mm_y) / 2.0
        width_mm_bbox = w * avg_px_to_mm
        height_mm_bbox = h * avg_px_to_mm
        estimated_diameter_mm = estimated_diameter_pixels * avg_px_to_mm

        measurements = {
            'area_pixels': float(area_pixels),
            'perimeter_pixels': float(perimeter_pixels),
            'width_pixels_bbox': float(w),
            'height_pixels_bbox': float(h),
            'estimated_diameter_pixels': float(estimated_diameter_pixels),
            'area_sq_mm': round(area_sq_mm, 2),
            'area_sq_m': round(area_sq_m, 4),
            'width_mm_bbox': round(width_mm_bbox, 2),
            'height_mm_bbox': round(height_mm_bbox, 2),
            'estimated_diameter_mm': round(estimated_diameter_mm, 2)
            # TODO: Add depth measurement if possible (e.g., from 3D data or advanced analysis)
        }
        logger.debug(f"Pothole measurements: {measurements}")
        return measurements

    except Exception as e:
        logger.error(f"Error measuring pothole contour: {e}", exc_info=True)
        return {} 