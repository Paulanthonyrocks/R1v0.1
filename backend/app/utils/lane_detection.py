import cv2
import numpy as np
import logging
from typing import List, Tuple, Dict, Any, Optional
from ..utils.polygons import pixel_polygon

logger = logging.getLogger(__name__)

def region_of_interest(img: np.ndarray, vertices: np.ndarray) -> np.ndarray:
    """
    Applies an image mask.

    Only keeps the region of the image defined by the polygon
    formed from `vertices`. The rest of the image is set to black.
    """
    mask = np.zeros_like(img)
    if len(img.shape) > 2:
        channel_count = img.shape[2]  # i.e. 3 or 4 depending on your image
        ignore_mask_color = (255,) * channel_count
    else:
        ignore_mask_color = 255
    
    cv2.fillPoly(mask, vertices, ignore_mask_color)
    masked_image = cv2.bitwise_and(img, mask)
    return masked_image

def draw_lines(img: np.ndarray, lines: List[np.ndarray], color: Tuple[int, int, int] = (0, 255, 0), thickness: int = 5) -> np.ndarray:
    """
    Draws lines on an image.
    """
    line_img = np.zeros((img.shape[0], img.shape[1], 3), dtype=np.uint8)
    if lines is None:
        return img
    for line in lines:
        for x1, y1, x2, y2 in line:
            cv2.line(line_img, (x1, y1), (x2, y2), color, thickness)
    return cv2.addWeighted(img, 0.8, line_img, 1.0, 0.0)

def process_frame_for_lanes(frame: Optional[np.ndarray], config: Dict[str, Any]) -> Optional[List[Tuple[int, int, int, int]]]:
    """
    Processes a single frame to detect lane lines.
    """
    if frame is None or frame.size == 0:
        logger.warning("Received empty or None frame for lane detection.")
        return None

    lane_cfg = config.get("lane_detection", {})
    
    # 1. Grayscale
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # 2. Gaussian Blur
    kernel_size = lane_cfg.get("gaussian_blur_kernel_size", 5)
    blur_gray = cv2.GaussianBlur(gray, (kernel_size, kernel_size), 0)

    # 3. Canny Edge Detection
    low_threshold = lane_cfg.get("canny_low_threshold", 50)
    high_threshold = lane_cfg.get("canny_high_threshold", 150)
    edges = cv2.Canny(blur_gray, low_threshold, high_threshold)

    # 4. Region of Interest
    imshape = frame.shape
    roi_cfg = config.get("roi_processing", {})
    
    vertices = None
    
    # Check if frontend ROI is enabled and available
    if roi_cfg.get("enabled", False):
        if "roi_points_normalized" in roi_cfg and roi_cfg["roi_points_normalized"]:
            # Use normalized points from frontend
            norm_points = roi_cfg["roi_points_normalized"]
            vertices = np.array([
                [(int(imshape[1] * p[0]), int(imshape[0] * p[1])) for p in norm_points]
            ], dtype=np.int32)
            logger.debug("Using normalized ROI points from frontend for lane detection.")
            
        elif "polygon_points" in roi_cfg and roi_cfg["polygon_points"]:
            # Wire polygon ({x,y} dicts, normalized) or legacy pixel pairs.
            # Coerce through the canonical normalizer so dicts don't reach
            # np.array(..., int32) (TypeError) and normalized coords aren't
            # treated as pixels.
            poly_points = pixel_polygon(roi_cfg["polygon_points"], imshape[1], imshape[0])
            if poly_points is not None:
                vertices = np.array([poly_points], dtype=np.int32)
                logger.debug("Using normalised ROI polygon points from frontend for lane detection.")

    # Fallback to default trapezoid if no frontend ROI used
    if vertices is None:
        roi_vertices_percent = lane_cfg.get("roi_vertices_percent", [
            (0.1, 1.0), (0.45, 0.6), (0.55, 0.6), (0.9, 1.0)
        ])
        vertices = np.array([
            [(int(imshape[1] * p[0]), int(imshape[0] * p[1])) for p in roi_vertices_percent]
        ], dtype=np.int32)

    masked_edges = region_of_interest(edges, vertices)

    # 5. Hough Line Transform
    rho = lane_cfg.get("hough_rho", 2)  # distance resolution in pixels of the Hough grid
    theta = np.pi / lane_cfg.get("hough_theta_divisor", 180)  # angular resolution in radians of the Hough grid
    threshold = lane_cfg.get("hough_threshold", 15)  # minimum number of votes (points) in a line bin
    min_line_length = lane_cfg.get("hough_min_line_length", 40)  # minimum number of pixels making up a line
    max_line_gap = lane_cfg.get("hough_max_line_gap", 20)  # maximum gap in pixels between connectable line segments
    
    lines = cv2.HoughLinesP(masked_edges, rho, theta, threshold, np.array([]), 
                            minLineLength=min_line_length, maxLineGap=max_line_gap)

    if lines is None or len(lines) == 0:
        logger.debug("No lines detected by Hough Transform.")
        return None

    # 6. Average and Extrapolate Lines (simplified for basic implementation)
    # Separate left and right lines based on slope
    left_lines = []
    right_lines = []
    
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if x2 == x1: # Avoid division by zero for vertical lines
            continue
        slope = (y2 - y1) / (x2 - x1)
        
        # Filter based on slope and position (positive slope for right, negative for left)
        # Adjust these thresholds based on typical camera view
        if 0.15 < abs(slope) < 1.5: # Filter out near-horizontal and near-vertical lines
            line_mid_x = (x1 + x2) / 2
            if slope < 0 and line_mid_x < imshape[1] / 2: # Left lane lines (negative slope, left half of image)
                left_lines.append(line[0])
            elif slope > 0 and line_mid_x > imshape[1] / 2: # Right lane lines (positive slope, right half of image)
                right_lines.append(line[0])

    # Average the lines to get a single representative line for left and right
    # This is a very basic averaging. More robust solutions involve RANSAC or polynomial fitting.
    avg_lines = []
    if left_lines:
        left_line = np.mean(left_lines, axis=0, dtype=np.int32)
        avg_lines.append(left_line)
    if right_lines:
        right_line = np.mean(right_lines, axis=0, dtype=np.int32)
        avg_lines.append(right_line)

    # Convert averaged lines to the desired format (list of tuples)
    return [tuple(line) for line in avg_lines]

def get_lane_boundaries_from_lines(frame_width: int, detected_lines: List[Tuple[int, int, int, int]], config: Dict[str, Any]) -> List[int]:
    """
    Estimates lane boundaries (x-coordinates) based on detected lane lines.
    This is a simplified approach assuming two main lane lines (left and right).
    """
    if not detected_lines:
        return []

    # Sort lines by their x-coordinates to distinguish left from right
    # We'll use the midpoint of the line for sorting
    sorted_lines = sorted(detected_lines, key=lambda line: (line[0] + line[2]) / 2)

    lane_cfg = config.get("lane_detection", {})
    num_lanes_fallback = lane_cfg.get("num_lanes", 4)
    lane_width_fallback = lane_cfg.get("lane_width", 120) # pixels

    lane_boundaries = []
    if len(sorted_lines) >= 2:
        # Assuming the two outermost lines define the primary road width
        left_line_x_avg = (sorted_lines[0][0] + sorted_lines[0][2]) / 2
        right_line_x_avg = (sorted_lines[-1][0] + sorted_lines[-1][2]) / 2
        
        # Calculate approximate lane width based on detected outer lines
        approx_road_width = right_line_x_avg - left_line_x_avg
        if approx_road_width > lane_width_fallback: # Check if detected width is plausible
            dynamic_lane_width = approx_road_width / num_lanes_fallback
            for i in range(num_lanes_fallback + 1):
                lane_boundaries.append(int(left_line_x_avg + i * dynamic_lane_width))
        else:
            # Fallback if lines are too close or inverted
            logger.warning("Detected lines are too close or inverted. Falling back to configured lane boundaries.")
            for i in range(num_lanes_fallback + 1):
                lane_boundaries.append(int(i * lane_width_fallback))
    elif len(sorted_lines) == 1:
        # Expected degradation path (sparse / occluded lane markings), not an
        # error: the caller already falls back to a sensible boundary. Logging
        # this at WARNING per-frame spammed the log (100+ lines/run) and buried
        # real errors, so it is DEBUG.
        logger.debug("Only one lane line detected. Extrapolating from single line.")
        line_x_avg = (sorted_lines[0][0] + sorted_lines[0][2]) / 2
        
        # Check if the detected line is likely a left or right boundary
        if line_x_avg < frame_width / 2:
            # Assume it's the left-most line
            logger.debug(f"Single line at x={line_x_avg} assumed to be left boundary.")
            for i in range(num_lanes_fallback + 1):
                lane_boundaries.append(int(line_x_avg + i * lane_width_fallback))
        else:
            # Assume it's the right-most line
            logger.debug(f"Single line at x={line_x_avg} assumed to be right boundary.")
            for i in range(num_lanes_fallback + 1):
                lane_boundaries.append(int(line_x_avg - i * lane_width_fallback))
            lane_boundaries.reverse() # Ensure ascending order
    else:
        logger.warning("No lane lines detected. Falling back to configured lane boundaries.")
        for i in range(num_lanes_fallback + 1):
            lane_boundaries.append(int(i * lane_width_fallback))

    return lane_boundaries
