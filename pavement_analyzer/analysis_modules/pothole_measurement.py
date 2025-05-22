# analysis_modules/pothole_measurement.py
import cv2
import numpy as np
import logging
from ..utils.camera_calibration import get_pixel_to_mm_ratio

logger = logging.getLogger(__name__)

def measure_pothole(contour, image_shape):
    """
    Measures a pothole based on its contour.
    Returns a dictionary with estimated measurements in pixels and mm.
    """
    if contour is None or image_shape is None:
        return {}

    try:
        area_pixels = cv2.contourArea(contour)
        perimeter_pixels = cv2.arcLength(contour, True)

        # Bounding box dimensions
        x, y, w, h = cv2.boundingRect(contour)
        
        # Estimate diameter/size from bounding box or area
        estimated_diameter_pixels = (w + h) / 2.0 # Simple average of width and height
        # Could also use: np.sqrt(area_pixels / np.pi) * 2 # Diameter from area assuming circle

        # Use the centroid or a point on the contour to estimate pixel-to-mm ratio
        M = cv2.moments(contour)
        if M['m00'] != 0:
            cx = int(M['m10'] / M['m00'])
            cy = int(M['m01'] / M['m00'])
            point_in_image = (cx, cy)
        else:
             point_in_image = (x + w // 2, y + h // 2)

        px_to_mm_x, px_to_mm_y = get_pixel_to_mm_ratio(point_in_image=point_in_image, image_height=image_shape[0])

        # Convert pixel measurements to mm
        # Assuming pixel-to-mm is roughly constant over the small area of a pothole contour
        # A more accurate approach would consider perspective distortion within the contour

        area_sq_mm = area_pixels * px_to_mm_x * px_to_mm_y
        area_sq_m = area_sq_mm / (1000 * 1000) # Convert sq mm to sq meters
        
        # Estimate dimensions in mm based on bounding box and average pixel-to-mm
        width_mm = w * ((px_to_mm_x + px_to_mm_y) / 2.0) # Use average ratio for a general dimension
        height_mm = h * ((px_to_mm_x + px_to_mm_y) / 2.0)
        estimated_diameter_mm = estimated_diameter_pixels * ((px_to_mm_x + px_to_mm_y) / 2.0)

        measurements = {
            'area_pixels': area_pixels,
            'perimeter_pixels': perimeter_pixels,
            'width_pixels_bbox': w,
            'height_pixels_bbox': h,
            'estimated_diameter_pixels': estimated_diameter_pixels,
            'area_sq_mm': area_sq_mm,
            'area_sq_m': area_sq_m,
            'width_mm_bbox': width_mm,
            'height_mm_bbox': height_mm,
            'estimated_diameter_mm': estimated_diameter_mm
        }
        return measurements

    except Exception as e:
        logger.error(f"Error measuring pothole contour: {e}")
        return {}