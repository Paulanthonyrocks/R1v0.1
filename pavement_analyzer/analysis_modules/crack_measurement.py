# analysis_modules/crack_measurement.py
import cv2
import numpy as np
import logging
from ..utils.camera_calibration import get_pixel_to_mm_ratio

logger = logging.getLogger(__name__)

def measure_crack(contour, image_shape):
    """
    Measures a crack based on its contour.
    This is a simplified approach. More advanced methods might use skeletonization
    or fitting lines/curves for better length/width estimation.
    
    Returns a dictionary with estimated measurements in pixels and mm.
    """
    if contour is None or image_shape is None:
        return {}

    try:
        area_pixels = cv2.contourArea(contour)
        perimeter_pixels = cv2.arcLength(contour, True)
        
        # Simple bounding box based measurements
        x, y, w, h = cv2.boundingRect(contour)
        length_pixels_bbox = max(w, h)
        width_pixels_bbox = min(w, h)
        
        # More potentially accurate, but complex: minimum area rectangle
        # rect = cv2.minAreaRect(contour)
        # (center_x, center_y), (width, height), angle = rect
        # length_pixels_min_area = max(width, height)
        # width_pixels_min_area = min(width, height)
        
        # Estimate average width using area and length (simplified)
        # This assumes a somewhat consistent width, which is often not true for cracks
        # A better approach involves analyzing points along the skeleton or perpendicular profiles.
        average_width_pixels = area_pixels / length_pixels_bbox if length_pixels_bbox > 0 else 0

        # Use the centroid or a point on the contour to estimate pixel-to-mm ratio
        M = cv2.moments(contour)
        if M['m00'] != 0:
            cx = int(M['m10'] / M['m00'])
            cy = int(M['m01'] / M['m00'])
            point_in_image = (cx, cy)
        else:
             # Fallback to bounding box center if moment is zero (e.g., single point contour)
             point_in_image = (x + w // 2, y + h // 2)

        px_to_mm_x, px_to_mm_y = get_pixel_to_mm_ratio(point_in_image=point_in_image, image_height=image_shape[0])
        
        # Convert pixel measurements to mm
        # This conversion is an estimation and highly depends on accurate camera calibration
        # and potentially perspective correction.
        length_mm = length_pixels_bbox * px_to_mm_y # Assuming length aligns more with Y-axis
        width_mm = average_width_pixels * px_to_mm_x # Assuming width aligns more with X-axis
        area_sq_mm = area_pixels * px_to_mm_x * px_to_mm_y
        area_sq_m = area_sq_mm / (1000 * 1000) # Convert sq mm to sq meters

        measurements = {
            'area_pixels': area_pixels,
            'perimeter_pixels': perimeter_pixels,
            'length_pixels': length_pixels_bbox,
            'width_pixels': average_width_pixels, # Using average width
            'length_mm': length_mm,
            'width_mm': width_mm,
            'area_sq_mm': area_sq_mm,
            'area_sq_m': area_sq_m
        }
        return measurements

    except Exception as e:
        logger.error(f"Error measuring crack contour: {e}")
        return {}

# TODO: Add functions for other crack types (transverse, alligator) if their measurement logic differs
# For now, measure_crack can be used for any general linear crack contour.