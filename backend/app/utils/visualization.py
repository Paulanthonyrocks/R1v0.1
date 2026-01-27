import cv2
import numpy as np
import logging
import time  # Used in visualize_data for banner timestamp
from typing import Dict, Any, Optional, Set, Tuple, List
from .monitoring import TrafficMonitor
from .lane_detection import process_frame_for_lanes, get_lane_boundaries_from_lines

logger = logging.getLogger(__name__)
# No longer need the placeholder class TrafficMonitor here

# Global variables for caching visualization overlays
cached_lane_overlay: Optional[np.ndarray] = None
cached_grid_overlay: Optional[np.ndarray] = None
overlay_cache_size: Optional[Tuple[int, int]] = None


def create_lane_overlay(
    shape: Tuple[int, int, int],
    lane_boundaries: List[int],
    density_per_lane: Dict[int, int],
    config: Dict[str, Any],
) -> np.ndarray:
    h, w = shape[:2]
    overlay = np.zeros((h, w, 4), dtype=np.uint8)  # Ensure 4 channels for alpha

    density_config = config.get("incident_detection", {})
    threshold_high = density_config.get("density_threshold", 10)
    threshold_medium = threshold_high // 2  # Default medium threshold

    # Define colors with alpha (R, G, B, Alpha)
    levels = {
        "low": (0, 255, 0, 60),  # Greenish with some transparency
        "medium": (255, 165, 0, 80),  # Orangeish with more transparency
        "high": (255, 0, 0, 100),  # Reddish with even more transparency
    }

    if len(lane_boundaries) < 2:
        logger.warning("Not enough lane boundaries to draw overlay.")
        return overlay

    for i in range(len(lane_boundaries) - 1):
        x1 = lane_boundaries[i]
        x2 = lane_boundaries[i+1]
        lane_num = i + 1 # 1-indexed lane number
        density = density_per_lane.get(lane_num, 0)

        color = (
            levels["high"]
            if density >= threshold_high
            else (levels["medium"] if density >= threshold_medium else levels["low"])
        )

        cv2.rectangle(overlay, (x1, 0), (x2, h), color, -1)  # Fill rectangle
    return overlay


def create_grid_overlay(
    shape: Tuple[int, int, int], config: Dict[str, Any]
) -> np.ndarray:
    h, w = shape[:2]
    overlay = np.zeros((h, w, 4), dtype=np.uint8)  # 4 channels for alpha

    ppm = config.get("pixels_per_meter", 50)  # Default pixels per meter
    lanes_config = config.get("lane_detection", {})
    num_lanes = lanes_config.get("num_lanes", 0)  # Default number of lanes

    grid_interval_pixels = (
        int(10 * ppm) if ppm > 0 else 100
    )  # Grid every 10 meters, or 100px default
    grid_color = (100, 100, 100, 80)  # Light gray with transparency

    # Draw horizontal grid lines
    for y_coord in range(grid_interval_pixels, h, grid_interval_pixels):
        cv2.line(overlay, (0, y_coord), (w, y_coord), grid_color, 1, cv2.LINE_AA)

    # Draw vertical lane lines if num_lanes is specified
    if num_lanes > 0:
        lane_width_pixels = w / num_lanes
        for i in range(1, num_lanes):
            cv2.line(
                overlay,
                (int(i * lane_width_pixels), 0),
                (int(i * lane_width_pixels), h),
                grid_color,
                1,
                cv2.LINE_AA,
            )

    return overlay


def alpha_blend(foreground: np.ndarray, background: np.ndarray) -> np.ndarray:
    """Alpha blends the foreground image (with an alpha channel) onto the background image."""
    if foreground.shape[:2] != background.shape[:2]:
        foreground = cv2.resize(
            foreground,
            (background.shape[1], background.shape[0]),
            interpolation=cv2.INTER_NEAREST,
        )

    if foreground.shape[2] != 4:
        logger.warning("Foreground image for alpha blending does not have an alpha channel.")
        return background

    # Extract alpha and BGR
    fg_bgr = foreground[:, :, :3]
    alpha = foreground[:, :, 3:4].astype(np.float32) / 255.0

    # Ensure background is 3-channel
    if background.shape[2] == 4:
        background = background[:, :, :3]

    # Vectorized blending: out = fg * alpha + bg * (1 - alpha)
    blended = (fg_bgr.astype(np.float32) * alpha + background.astype(np.float32) * (1.0 - alpha)).astype(np.uint8)
    return blended


def visualize_data(
    frame: Optional[np.ndarray],
    tracked_vehicles: Dict[int, Dict[str, Any]],
    traffic_metrics: Dict[str, Any],
    visualization_options: Set[str],
    config: Dict[str, Any],
    feed_id: str = "",
    lane_boundaries: Optional[List[int]] = None,
    lane_lines: Optional[List[Tuple[int, int, int, int]]] = None
) -> Optional[np.ndarray]:
    global cached_lane_overlay, cached_grid_overlay, overlay_cache_size

    if frame is None:
        return None

    try:
        # Avoid unnecessary copying and color conversions if possible
        # We'll work on a 3-channel frame and only use alpha blending for specific overlays
        vis_frame = frame.copy()
        h, w = vis_frame.shape[:2]
        current_size = (w, h)

        # --- Draw ROI Polygon ---
        roi_cfg = config.get("roi_processing", {})
        roi_enabled = roi_cfg.get("enabled", False)
        roi_polygon_points = roi_cfg.get("polygon_points", None)

        if roi_enabled and roi_polygon_points:
            # The ROI polygon is no longer drawn on the video frame.
            # It is still drawn on debug images generated separately.
            logger.debug(f"[{feed_id}] ROI polygon drawing skipped for video frame.")
        # --- End Draw ROI Polygon ---

        # Reset cached overlays if frame size changes
        if overlay_cache_size != current_size:
            logger.debug(
                f"[{feed_id}] Frame size changed from {overlay_cache_size} to {current_size}. Resetting visualization overlays."
            )
            cached_lane_overlay = None
            cached_grid_overlay = None
            overlay_cache_size = current_size

        # Use passed boundaries or fallback to static ones if not provided
        if not lane_boundaries:
            lane_cfg = config.get("lane_detection", {})
            num_lanes = lane_cfg.get("num_lanes", 0)
            lane_width = w / num_lanes if num_lanes > 0 else w
            if num_lanes > 0:
                lane_boundaries = [int(i * lane_width) for i in range(num_lanes + 1)]
            else:
                lane_boundaries = []
            logger.debug(f"[{feed_id}] Using static lane boundaries: {lane_boundaries}")
        else:
            logger.debug(f"[{feed_id}] Using provided lane boundaries: {lane_boundaries}")


        if "Grid Overlay" in visualization_options:
            if cached_grid_overlay is None or overlay_cache_size != current_size:  # Recreate if size changes
                cached_grid_overlay = create_grid_overlay(vis_frame.shape, config)
            if cached_grid_overlay is not None:
                vis_frame = alpha_blend(cached_grid_overlay, vis_frame)

        if "Lane Density Overlay" in visualization_options and lane_boundaries:
            density_per_lane = traffic_metrics.get("vehicles_per_lane", {})
            lane_overlay = create_lane_overlay(
                vis_frame.shape, lane_boundaries, density_per_lane, config
            )
            vis_frame = alpha_blend(lane_overlay, vis_frame)

        # Draw lane lines if provided
        if lane_lines and "Lane Lines" in visualization_options:
            for line in lane_lines:
                x1, y1, x2, y2 = line
                cv2.line(vis_frame, (x1, y1), (x2, y2), (0, 255, 255), 2)

        if (
            "Tracked Vehicles" in visualization_options
            or "Vehicle Data" in visualization_options
        ):
            logger.debug(f"[{feed_id}] 'Tracked Vehicles' or 'Vehicle Data' option is active.")
            logger.debug(f"[{feed_id}] tracked_vehicles content: {tracked_vehicles}")

            # Define colors based on behavior
            color_map = {
                "moving": (0, 255, 0),  # Green
                "stopped": (0, 0, 255),  # Red
                "speeding": (255, 0, 0),  # Blue
                "accelerating": (255, 255, 0),  # Yellow
                "decelerating": (0, 255, 255),  # Cyan
                "lane_changing": (255, 0, 255),  # Magenta
                "unknown": (128, 128, 128),  # Gray
            }

            for veh_id, data in tracked_vehicles.items():
                bbox = data.get("bbox")
                speed = data.get("speed", 0.0)
                plate = data.get("license_plate", "")
                class_id = data.get("class_id", -1)
                class_name = TrafficMonitor.vehicle_type_map.get(class_id, "?")
                behavior = data.get("behavior", "unknown")

                logger.debug(f"[{feed_id}] Processing vehicle {veh_id}. Bbox: {bbox}, Behavior: {behavior}")

                if bbox:
                    x1, y1, x2, y2 = map(int, bbox)
                    color = color_map.get(behavior, (128, 128, 128))

                    if "Tracked Vehicles" in visualization_options:
                        cv2.rectangle(vis_frame, (x1, y1), (x2, y2), color, 2) # Increased thickness

                    if "Vehicle Data" in visualization_options:
                        lines = [f"ID:{veh_id}({class_name})", f"Spd:{speed:.1f}km/h"]
                        if plate:
                            lines.append(f"LP:{plate}")

                        font_scale = 0.5 # Increased font size
                        font_thickness = 2 # Increased font thickness
                        line_height = 35 # Increased line height for more spacing

                        # Position text: above bbox if space, else below
                        text_y_start = (
                            y1 - 5 - (len(lines) * line_height)
                            if y1 - 5 - (len(lines) * line_height) >= 0
                            else y2 + line_height
                        )

                        for i, line_text in enumerate(lines):
                            (text_width, _), baseline = cv2.getTextSize(
                                line_text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, font_thickness
                            )
                            text_x_center = x1 + (x2 - x1 - text_width) // 2 # Center horizontally
                            cv2.putText(
                                vis_frame,
                                line_text,
                                (text_x_center, text_y_start + i * line_height + baseline), # Add baseline for correct vertical alignment
                                cv2.FONT_HERSHEY_SIMPLEX,
                                font_scale,
                                color,
                                font_thickness,
                                cv2.LINE_AA,
                            )

        # Banner for general info
        banner_height = 30 # Increased height slightly for better fit
        banner_text = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | Feed: {feed_id} | Vehicles: {traffic_metrics.get('total_vehicles', 0)} | Avg Speed: {traffic_metrics.get('average_speed_kmh', 0.0):.1f} km/h"
        if traffic_metrics.get("is_congested", False):
            banner_text += " | CONGESTED"

        # Semi-transparent banner background
        cv2.rectangle(
            vis_frame, (0, 0), (w, banner_height), (0, 0, 0, 180), -1
        )  # Black with alpha

        # Calculate text size and position for centering
        (text_width, text_height), baseline = cv2.getTextSize(
            banner_text, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1
        )
        text_x = (w - text_width) // 2 # Center horizontally
        text_y = (banner_height + text_height) // 2 # Center vertically

        cv2.putText(
            vis_frame,
            banner_text,
            (text_x, text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4, # Slightly reduced font size
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )  # White text

        if vis_frame.shape[2] == 4:
            vis_frame = cv2.cvtColor(vis_frame, cv2.COLOR_BGRA2BGR)

        return vis_frame

    except Exception as e:
        logger.error(f"[{feed_id}] Visualization error: {e}", exc_info=True)
        if frame is not None and frame.shape[2] == 4:
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        return frame  # Return original frame on error
