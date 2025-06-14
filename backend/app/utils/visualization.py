import cv2
import numpy as np
import logging
import time # Used in visualize_data for banner timestamp
from typing import Dict, Any, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Attempt to import TrafficMonitor from where it's planned to be
from ..monitoring import TrafficMonitor
# No longer need the placeholder class TrafficMonitor here

# Global variables for caching visualization overlays
cached_lane_overlay: Optional[np.ndarray] = None
cached_grid_overlay: Optional[np.ndarray] = None
overlay_cache_size: Optional[Tuple[int, int]] = None


def create_lane_overlay(shape: Tuple[int, int, int], num_lanes: int, lane_width: float, density_per_lane: Dict[int, int], config: Dict[str, Any]) -> np.ndarray:
    h, w = shape[:2]
    overlay = np.zeros((h, w, 4), dtype=np.uint8) # Ensure 4 channels for alpha

    density_config = config.get('incident_detection', {})
    threshold_high = density_config.get('density_threshold', 10)
    threshold_medium = threshold_high // 2 # Default medium threshold

    # Define colors with alpha (R, G, B, Alpha)
    levels = {
        'low': (0, 255, 0, 60),      # Greenish with some transparency
        'medium': (255, 165, 0, 80), # Orangeish with more transparency
        'high': (255, 0, 0, 100)     # Reddish with even more transparency
    }

    for lane_num in range(1, num_lanes + 1):
        x1 = int((lane_num - 1) * lane_width)
        x2 = int(lane_num * lane_width)
        density = density_per_lane.get(lane_num, 0)

        color = levels['high'] if density >= threshold_high else \
                (levels['medium'] if density >= threshold_medium else levels['low'])

        cv2.rectangle(overlay, (x1, 0), (x2, h), color, -1) # Fill rectangle
    return overlay

def create_grid_overlay(shape: Tuple[int, int, int], config: Dict[str, Any]) -> np.ndarray:
    h, w = shape[:2]
    overlay = np.zeros((h, w, 4), dtype=np.uint8) # 4 channels for alpha

    ppm = config.get('pixels_per_meter', 50) # Default pixels per meter
    lanes_config = config.get('lane_detection', {})
    num_lanes = lanes_config.get('num_lanes', 0) # Default number of lanes

    grid_interval_pixels = int(10 * ppm) if ppm > 0 else 100 # Grid every 10 meters, or 100px default
    grid_color = (100, 100, 100, 80) # Light gray with transparency

    # Draw horizontal grid lines
    for y_coord in range(grid_interval_pixels, h, grid_interval_pixels):
        cv2.line(overlay, (0, y_coord), (w, y_coord), grid_color, 1, cv2.LINE_AA)

    # Draw vertical lane lines if num_lanes is specified
    if num_lanes > 0:
        lane_width_pixels = w / num_lanes
        for i in range(1, num_lanes):
            cv2.line(overlay, (int(i * lane_width_pixels), 0), (int(i * lane_width_pixels), h), grid_color, 1, cv2.LINE_AA)

    return overlay

def alpha_blend(foreground: np.ndarray, background: np.ndarray) -> np.ndarray:
    """Alpha blends the foreground image (with an alpha channel) onto the background image."""
    if foreground.shape[:2] != background.shape[:2]:
        # Resize foreground to match background if dimensions differ
        foreground = cv2.resize(foreground, (background.shape[1], background.shape[0]), interpolation=cv2.INTER_NEAREST)

    if foreground.shape[2] != 4:
        logger.warning("Foreground image for alpha blending does not have an alpha channel. Blending may not work as expected.")
        # Optionally, add an alpha channel here or return background
        return background # Or raise error

    # Ensure background is 3-channel BGR
    if background.shape[2] == 4: # If background also has alpha, remove it or handle appropriately
        background = cv2.cvtColor(background, cv2.COLOR_BGRA2BGR)

    fg_b, fg_g, fg_r, fg_a = cv2.split(foreground)

    # Normalize alpha channel to range 0-1
    alpha = fg_a.astype(float) / 255.0

    # Multiply foreground BGR channels by alpha
    fg_b_w = (fg_b * alpha).astype(background.dtype)
    fg_g_w = (fg_g * alpha).astype(background.dtype)
    fg_r_w = (fg_r * alpha).astype(background.dtype)

    # Multiply background BGR channels by (1 - alpha)
    bg_b, bg_g, bg_r = cv2.split(background)
    inv_alpha = 1.0 - alpha
    bg_b_w = (bg_b * inv_alpha).astype(background.dtype)
    bg_g_w = (bg_g * inv_alpha).astype(background.dtype)
    bg_r_w = (bg_r * inv_alpha).astype(background.dtype)

    # Add weighted channels
    out_b = cv2.add(fg_b_w, bg_b_w)
    out_g = cv2.add(fg_g_w, bg_g_w)
    out_r = cv2.add(fg_r_w, bg_r_w)

    return cv2.merge((out_b, out_g, out_r))


def visualize_data(
    frame: Optional[np.ndarray],
    tracked_vehicles: Dict[int, Dict[str, Any]],
    traffic_metrics: Dict[str, Any],
    visualization_options: Set[str],
    config: Dict[str, Any],
    feed_id: str = ""
) -> Optional[np.ndarray]:
    global cached_lane_overlay, cached_grid_overlay, overlay_cache_size

    if frame is None:
        logger.debug("visualize_data received None frame.")
        return None

    try:
        vis_frame = frame.copy()
        h, w = vis_frame.shape[:2]
        current_size = (w, h)

        # Reset cached overlays if frame size changes
        if overlay_cache_size != current_size:
            logger.debug(f"[{feed_id}] Frame size changed from {overlay_cache_size} to {current_size}. Resetting visualization overlays.")
            cached_lane_overlay = None
            cached_grid_overlay = None
            overlay_cache_size = current_size

        lane_cfg = config.get('lane_detection', {})
        num_lanes = lane_cfg.get('num_lanes', 0)
        lane_width = w / num_lanes if num_lanes > 0 else w # Avoid division by zero

        if "Grid Overlay" in visualization_options:
            if cached_grid_overlay is None: # Or if relevant config for grid changed
                cached_grid_overlay = create_grid_overlay(vis_frame.shape, config)
            if cached_grid_overlay is not None:
                vis_frame = alpha_blend(cached_grid_overlay, vis_frame)

        if "Lane Density Overlay" in visualization_options and num_lanes > 0:
            density_per_lane = traffic_metrics.get('vehicles_per_lane', {})
            # Re-create lane overlay dynamically as density changes, or cache if density is also stable
            # For now, let's assume it's dynamic enough to recreate or passed in if cached by caller
            lane_overlay = create_lane_overlay(vis_frame.shape, num_lanes, lane_width, density_per_lane, config)
            vis_frame = alpha_blend(lane_overlay, vis_frame)

        if "Tracked Vehicles" in visualization_options or "Vehicle Data" in visualization_options:
            speed_limit = config.get('speed_limit', 60) # Default speed limit
            color_normal = (0, 255, 0)    # Green
            color_warning = (0, 255, 255)  # Yellow
            color_speeding = (0, 0, 255)   # Red

            for veh_id, data in tracked_vehicles.items():
                bbox = data.get('bbox')
                speed = data.get('speed', 0.0)
                plate = data.get('license_plate', '')
                class_id = data.get('class_id', -1) # Use -1 for unknown if not present
                class_name = TrafficMonitor.vehicle_type_map.get(class_id, '?') # Default to '?'

                if bbox:
                    x1, y1, x2, y2 = map(int, bbox)
                    color = color_speeding if speed > speed_limit else \
                            (color_warning if speed > speed_limit * 0.8 else color_normal)

                    if "Tracked Vehicles" in visualization_options:
                        cv2.rectangle(vis_frame, (x1, y1), (x2, y2), color, 2)

                    if "Vehicle Data" in visualization_options:
                        lines = [f"ID:{veh_id}({class_name})", f"Spd:{speed:.1f}km/h"]
                        line_height = 15 # Approximate height for text
                        if plate:
                            lines.append(f"LP:{plate}")

                        # Position text: above bbox if space, else below
                        text_y = y1 - 7 if y1 - 7 >= line_height * len(lines) else y2 + line_height
                        for i, line_text in enumerate(lines):
                            cv2.putText(vis_frame, line_text, (x1 + 5, text_y + i * line_height),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1, cv2.LINE_AA)

        # Banner for general info
        banner_height = 25
        banner_text = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | Feed: {feed_id} | Vehicles: {traffic_metrics.get('total_vehicles',0)} | Avg Speed: {traffic_metrics.get('average_speed_kmh',0.0):.1f} km/h"
        if traffic_metrics.get('is_congested', False):
            banner_text += " | CONGESTED"

        # Semi-transparent banner background
        cv2.rectangle(vis_frame, (0,0), (w, banner_height), (0,0,0,180), -1) # Black with alpha
        cv2.putText(vis_frame, banner_text, (10, banner_height - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,255), 1, cv2.LINE_AA) # White text

        return vis_frame

    except Exception as e:
        logger.error(f"[{feed_id}] Visualization error: {e}", exc_info=True)
        return frame # Return original frame on error to prevent crash
