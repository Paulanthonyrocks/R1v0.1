# /content/drive/MyDrive/R1v0.1/backend/app/utils/utils.py

import sys
import cv2
import psutil
import numpy as np
import logging
import sqlite3
import queue
import threading
import re
import yaml
import io
import time
from pathlib import Path
from collections import deque
from functools import lru_cache
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type, RetryError
import torch
from PIL import Image
import io
import pytesseract
import google.generativeai as genai
from pathlib import Path
import time
from multiprocessing import Queue as MPQueue
import yaml # <<< --- ADDED IMPORT --- >>>
from typing import Tuple, Dict, Any, List, Optional, Set

# Logging setup
# Configure logging properly in your main application (e.g., using FastAPI's setup)
# For standalone utils testing, use basic config:
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ----- System Resources -----
def check_system_resources(cpu_interval: float = 0.1) -> Tuple[float, float]:
    """Checks current CPU and Virtual Memory usage percentage."""
    try:
        cpu_percent = psutil.cpu_percent(interval=cpu_interval)
        memory_info = psutil.virtual_memory()
        memory_percent = memory_info.percent
        return cpu_percent, memory_percent
    except Exception as e:
        logger.error(f"Failed to get system resource usage: {e}", exc_info=True)
        return 0.0, 0.0

# --- Custom Exceptions ---
class ConfigError(Exception):
    """Custom exception for configuration loading/validation errors."""
    pass

class DatabaseError(Exception):
    """Custom exception for database operation errors."""
    pass

# --- Configuration Loading ---
DEFAULT_CONFIG: Dict[str, Any] = { # Define some sensible defaults
    "logging": {"level": "INFO", "log_path": "./logs/backend.log"},
    "database": {"db_path": "data/vehicle_data.db", "chunk_size": 100},
    "performance": {"gpu_acceleration": True, "memory_limit_percent": 85},
    "video_input": {"webcam_buffer_size": 2, "webcam_index": 0},
    "vehicle_detection": {
        "model_path": "models/yolov8n.pt", # Default model path
        "frame_resolution": [640, 480],
        "confidence_threshold": 0.5,
        "proximity_threshold": 50,
        "track_timeout": 5,
        "max_active_tracks": 50,
        "skip_frames": 1,
        "vehicle_class_ids": [2, 3, 5, 7], # COCO IDs: car, motorcycle, bus, truck
        "yolo_imgsz": 640
    },
    "ocr_engine": {
        "use_gpu_ocr": False, # Tesseract GPU usage is complex to set up
        "min_roi_size": 500, # Minimum area (pixels) for an ROI to be considered for OCR
        "ocr_interval": 10, # Minimum seconds between OCR attempts for the *same* track ID
        "gemini_api_key": None, # *** MUST BE SET IN USER CONFIG FILE ***
        "gemini_max_retries": 3,
        "gemini_cool_down_secs": 60, # Seconds to wait after certain API errors before retrying
        "roi_top_margin_factor": 0.5, # Factors to adjust ROI around detected vehicle bbox
        "roi_bottom_margin_factor": 0.1,
        "roi_left_margin_factor": 0.15,
        "roi_right_margin_factor": 0.15,
        "min_aspect_ratio": 2.0, # Min/Max aspect ratio for potential plate ROIs
        "max_aspect_ratio": 5.0,
        # Example kernels - adjust based on testing
        "sharpen_kernel": [[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]],
        "morph_kernel": [[1, 1, 1], [1, 1, 1], [1, 1, 1]], # 3x3 kernel
    },
    "lane_detection": {"num_lanes": 4, "lane_width": 160, "lane_change_buffer": 10},
    "perspective_calibration": {"matrix_path": ""}, # Path to saved perspective matrix
    "kalman_filter_params": { # Parameters for Kalman Filter (if used in tracking)
        "kf_sigma_px": 2.0, "kf_sigma_py": 2.0, "kf_sigma_pvx": 5.0, "kf_sigma_pvy": 5.0,
        "kf_sigma_mx": 0.5, "kf_sigma_my": 0.5, "kf_sigma_ax": 0.5, "kf_sigma_ay": 0.5
    },
    "pixels_per_meter": 40, # Estimate for speed calculation
    "speed_limit": 60, # km/h
    "stopped_speed_threshold_kmh": 5, # Threshold to consider a vehicle stopped
    "accel_threshold_mps2": 0.5, # Acceleration threshold for potential incidents
    "incident_detection": {"density_threshold": 10, "congestion_speed_threshold": 20},
    "interface": {"camera_warmup_time": 0.5}, # Seconds to wait for camera
    "vis_options_default": ["Tracked Vehicles", "Vehicle Data"], # Default visualization overlays
}

# --- Simple Deep Merge Helper ---
def merge_dicts(base: Dict, update: Dict) -> Dict:
    """Recursively merge update dict into base dict."""
    for key, value in update.items():
        if isinstance(value, dict) and key in base and isinstance(base[key], dict):
            merge_dicts(base[key], value)
        else:
            base[key] = value
    return base

def load_config(config_path: str) -> Dict[str, Any]:
    """Loads configuration from a YAML file, merges with defaults, validates."""
    path = Path(config_path)
    # Resolve path relative to the script's parent's parent (project root) if not absolute
    if not path.is_absolute():
         script_dir = Path(__file__).parent
         # Assumes utils.py is in backend/app/utils, so go up 3 levels
         project_root = script_dir.parent.parent.parent
         path = (project_root / config_path).resolve()

    logger.info(f"Attempting to load configuration from resolved path: {path}")

    if not path.is_file():
        logger.warning(f"Configuration file not found at {path}. Using default configuration ONLY.")
        config = DEFAULT_CONFIG.copy() # Use defaults if file is missing
    else:
        try:
            with open(path, 'r') as f:
                loaded_config = yaml.safe_load(f)
                if not isinstance(loaded_config, dict):
                    # Handle empty file case gracefully
                    if loaded_config is None:
                        logger.warning(f"Configuration file {path} is empty. Using default configuration.")
                        loaded_config = {}
                    else:
                        raise ConfigError(f"Configuration file {path} is not a valid YAML dictionary.")

                # Perform a deep merge: start with defaults, overwrite with loaded
                config = DEFAULT_CONFIG.copy()
                config = merge_dicts(config, loaded_config)

        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML configuration file {path}: {e}")
            raise ConfigError(f"Error parsing YAML file: {e}") from e
        except IOError as e:
            logger.error(f"Error reading configuration file {path}: {e}")
            raise ConfigError(f"Error reading config file: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred loading configuration file {path}: {e}", exc_info=True)
            raise ConfigError(f"Unexpected error loading configuration: {e}") from e

    # --- Comprehensive Validation (Applied to merged or default config) ---
    try:
        logger.debug("Validating configuration...")
        # Required sections and keys
        required_sections = ["database", "vehicle_detection", "ocr_engine"]
        required_keys = {
            "database": ["db_path"],
            "vehicle_detection": ["model_path"],
            "ocr_engine": ["gemini_api_key"] # Crucial, even if None
        }

        for section in required_sections:
            if section not in config:
                raise ConfigError(f"Missing required configuration section: '{section}'")
            for key in required_keys.get(section, []):
                 if key not in config[section]:
                     raise ConfigError(f"Missing required configuration key: '{section}.{key}'")

        # Specific Key Validations
        api_key = config["ocr_engine"]["gemini_api_key"]
        if api_key is not None and (not isinstance(api_key, str) or not api_key.strip()):
            raise ConfigError(f"Invalid 'ocr_engine.gemini_api_key'. Must be a non-empty string or null/None.")

        # Database path and directory
        db_path_str = config["database"]["db_path"]
        if not isinstance(db_path_str, str) or not db_path_str.strip():
             raise ConfigError(f"'database.db_path' must be a non-empty string. Got: '{db_path_str}'")
        db_file_path = Path(db_path_str)
        if not db_file_path.is_absolute(): # Resolve relative to project root if needed
            project_root = Path(__file__).parent.parent.parent
            db_file_path = (project_root / db_path_str).resolve()
            config["database"]["db_path"] = str(db_file_path) # Update config with absolute path

        if not db_file_path.parent.exists():
             try:
                 logger.info(f"Creating database directory: {db_file_path.parent}")
                 db_file_path.parent.mkdir(parents=True, exist_ok=True)
             except OSError as e:
                 raise ConfigError(f"Failed to create database directory {db_file_path.parent}: {e}") from e

        # Model path validation and resolution
        model_path_str = config["vehicle_detection"]["model_path"]
        model_file_path = Path(model_path_str)
        if not model_file_path.is_absolute():
            project_root = Path(__file__).parent.parent.parent
            model_file_path = (project_root / model_path_str).resolve()

        if not model_file_path.is_file():
             raise ConfigError(f"Vehicle detection model file not found at specified/resolved path: '{model_file_path}'")
        # Update config with resolved absolute path for consistency
        config["vehicle_detection"]["model_path"] = str(model_file_path)


        # Add other critical type/range validations as needed here...
        if not isinstance(config["vehicle_detection"]["confidence_threshold"], (int, float)) or not (0 <= config["vehicle_detection"]["confidence_threshold"] <= 1):
            raise ConfigError("'vehicle_detection.confidence_threshold' must be between 0 and 1.")

        if not isinstance(config["ocr_engine"]["gemini_max_retries"], int) or config["ocr_engine"]["gemini_max_retries"] < 0:
            raise ConfigError("'ocr_engine.gemini_max_retries' must be a non-negative integer.")
        if not isinstance(config["ocr_engine"]["gemini_cool_down_secs"], (int, float)) or config["ocr_engine"]["gemini_cool_down_secs"] < 0:
            raise ConfigError("'ocr_engine.gemini_cool_down_secs' must be a non-negative number.")

        logger.info("Configuration loaded and validated successfully.")
        return config

    except ConfigError as e: # Re-raise validation errors directly
        logger.error(f"Configuration validation failed: {e}")
        raise
    except Exception as e:
        logger.error(f"An unexpected error occurred during configuration validation: {e}", exc_info=True)
        raise ConfigError(f"Unexpected error validating configuration: {e}") from e
# --- END CONFIG LOADING ---

# --- Timers ---
class FrameTimer:
    """Simple class to track timings of different stages in a processing loop."""
    def __init__(self, maxlen: int = 100):
        self.timings: Dict[str, deque] = {
            'read': deque(maxlen=maxlen),
            'detect_track': deque(maxlen=maxlen),
            'ocr': deque(maxlen=maxlen), # Add OCR timing if needed
            'monitor': deque(maxlen=maxlen), # Add traffic monitor timing
            'visualize': deque(maxlen=maxlen),
            'db_save': deque(maxlen=maxlen), # Add DB save timing
            'queue_put': deque(maxlen=maxlen), # e.g., putting frame for output
            'loop_total': deque(maxlen=maxlen)
        }
        self._lock = threading.Lock() # Protect access if used across threads

    def log_time(self, stage: str, duration: float):
        """Logs the duration for a specific stage."""
        with self._lock:
            if stage in self.timings:
                self.timings[stage].append(duration)
            else:
                logger.warning(f"FrameTimer: Unknown stage '{stage}'")

    def get_avg(self, stage: str) -> float:
        """Gets the average duration for a stage."""
        with self._lock:
            if stage in self.timings and self.timings[stage]:
                return np.mean(self.timings[stage])
            return 0.0

    def get_fps(self, stage: str = 'loop_total') -> float:
        """Calculates Frames Per Second based on the average time of a stage."""
        avg_time = self.get_avg(stage)
        return 1.0 / avg_time if avg_time > 0 else 0.0

    def update_from_dict(self, timings_dict: Dict[str, List[float]]):
        """Updates timings from a dictionary (e.g., from another process/thread)."""
        with self._lock:
            for stage, times in timings_dict.items():
                if stage in self.timings and isinstance(times, (list, deque)):
                    self.timings[stage].extend(times)

# --- FrameReader ---
class FrameReader:
    """
    Reads frames from a video source (file or webcam) in a separate thread
    and provides them via a queue.
    """
    def __init__(self, source: Any, buffer_size: int = 2, target_fps: Optional[int] = None):
        self.source_name = str(source)
        self.target_fps = target_fps
        self.is_webcam = False
        capture_source: Any = source

        try:
            # Try converting source to int for webcam index
            capture_source = int(source)
            self.is_webcam = True
        except ValueError:
            # If not an int, assume it's a file path or URL
            if str(source).lower() == "webcam":
                 capture_source = 0 # Default webcam index
                 self.is_webcam = True
            else:
                 # Check if file exists if it's not a URL (simple check)
                 if "://" not in str(source) and not Path(source).exists():
                     raise FileNotFoundError(f"Video file not found: {source}")

        self.cap = cv2.VideoCapture(capture_source)
        if not self.cap.isOpened():
            logger.error(f"FrameReader: Failed to open video source: {capture_source} (from {self.source_name})")
            raise RuntimeError(f"Cannot open video source: {capture_source}")
        logger.info(f"FrameReader: Successfully opened video source: {capture_source} (from {self.source_name})")

        # Get source properties
        self.source_fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.source_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.source_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        logger.info(f"Source properties: {self.source_width}x{self.source_height} @ {self.source_fps:.2f} FPS")

        if self.is_webcam:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, buffer_size)
            logger.info(f"Webcam buffer size set to: {buffer_size}")
            if self.target_fps:
                # Note: Setting FPS on webcams might not always be supported or precise
                self.cap.set(cv2.CAP_PROP_FPS, self.target_fps)
                logger.info(f"Attempting to set webcam FPS to: {self.target_fps}")
        elif self.target_fps and self.target_fps != self.source_fps:
            logger.warning(f"Target FPS {self.target_fps} differs from source FPS {self.source_fps}. Frame dropping/duplication may occur.")
            # Frame skipping/timing logic would be needed in the read loop for precise FPS control

        self.frame_queue = queue.Queue(maxsize=30) # Buffer for read frames
        self.stop_event = threading.Event()
        self._end_of_video_flag = False
        self.state_lock = threading.Lock() # Lock for accessing _end_of_video_flag
        self.thread = threading.Thread(target=self._update_loop, daemon=True, name=f"FrameReader-{self.source_name}")
        self.frame_index = 0
        self.thread.start()

    @property
    def end_of_video(self) -> bool:
        """Flag indicating if the end of the video source has been reached."""
        with self.state_lock:
            return self._end_of_video_flag

    @end_of_video.setter
    def end_of_video(self, value: bool):
        """Setter for the end_of_video flag."""
        with self.state_lock:
            self._end_of_video_flag = value

    def _update_loop(self):
        """Internal thread loop that reads frames from the source."""
        max_read_fails = 10 # Number of consecutive read failures before giving up
        consecutive_fails = 0
        last_read_time = time.monotonic()

        while not self.stop_event.is_set():
            try:
                if self.target_fps:
                    # Simple sleep logic to approximate target FPS (can be improved)
                    wait_time = (1.0 / self.target_fps) - (time.monotonic() - last_read_time)
                    if wait_time > 0:
                        time.sleep(wait_time)

                ret, frame = self.cap.read()
                last_read_time = time.monotonic()

                if ret:
                    consecutive_fails = 0 # Reset fail counter on success
                    if self.frame_queue.full():
                        try:
                            # Discard oldest frame if queue is full
                            self.frame_queue.get_nowait()
                            logger.warning(f"FrameReader queue full for {self.source_name}. Discarding oldest frame.")
                        except queue.Empty:
                            pass # Should not happen if full, but handle anyway

                    try:
                        # Put tuple (index, frame) into the queue
                        self.frame_queue.put((self.frame_index, frame.copy()), timeout=0.1) # Copy frame
                        self.frame_index += 1
                    except queue.Full:
                        logger.warning(f"FrameReader queue still full after check. Frame {self.frame_index} lost.")

                else:
                    # Frame read failed
                    consecutive_fails += 1
                    logger.warning(f"FrameReader {self.source_name}: cv2.read() returned False (Fail {consecutive_fails}/{max_read_fails}).")
                    if consecutive_fails >= max_read_fails:
                        logger.error(f"FrameReader {self.source_name}: Reached max read failures. Assuming end of video or hardware issue.")
                        self.end_of_video = True
                        break # Exit loop

                    # Small sleep before retrying read
                    time.sleep(0.05)

                    # Check if the source is likely finished (especially for files)
                    if not self.is_webcam and self.cap.get(cv2.CAP_PROP_POS_FRAMES) >= self.cap.get(cv2.CAP_PROP_FRAME_COUNT):
                        logger.info(f"FrameReader {self.source_name}: Reached end of video file.")
                        self.end_of_video = True
                        break


            except Exception as e:
                logger.error(f"FrameReader thread encountered an error ({self.source_name}): {e}", exc_info=True)
                self.end_of_video = True # Assume fatal error
                break # Exit loop

        # --- Loop Finished ---
        logger.info(f"FrameReader thread stopping for {self.source_name}.")
        self.end_of_video = True # Ensure flag is set on exit
        if self.cap and self.cap.isOpened():
            self.cap.release()
            logger.info(f"FrameReader video capture released for {self.source_name}.")
        # Clear queue on exit?
        while not self.frame_queue.empty():
             try: self.frame_queue.get_nowait()
             except queue.Empty: break

    def read(self) -> Optional[Tuple[int, np.ndarray]]:
        """
        Reads the next available frame from the queue.
        Returns a tuple (frame_index, frame) or None if no frame is available.
        """
        try:
            # Get with a small timeout to avoid blocking indefinitely if producer thread dies
            return self.frame_queue.get(timeout=0.5)
        except queue.Empty:
             # Check if the thread is still running and if it's the end of the video
            if self.end_of_video and (not self.thread.is_alive() or self.frame_queue.empty()):
                 logger.debug(f"FrameReader {self.source_name}: Read called, but queue empty and end of video reached.")
                 return None # End of video and queue empty
            else:
                 # Queue is temporarily empty, but source might still produce frames
                 logger.debug(f"FrameReader {self.source_name}: Read called, queue temporarily empty.")
                 return None

    def stop(self):
        """Signals the reading thread to stop and releases resources."""
        logger.info(f"FrameReader {self.source_name}: Stop requested.")
        self.stop_event.set()

        # Wait briefly for the thread to finish
        if self.thread.is_alive():
            self.thread.join(timeout=2.0)
            if self.thread.is_alive():
                 logger.warning(f"FrameReader thread for {self.source_name} did not exit cleanly.")

        # Ensure capture is released (redundant if thread exits cleanly, but safe)
        if self.cap and self.cap.isOpened():
            self.cap.release()

        logger.info(f"FrameReader {self.source_name}: Stopped.")


# --- TrafficMonitor ---
class TrafficMonitor:
    """Calculates and maintains traffic metrics based on tracked vehicles."""
    def __init__(self, config: Dict):
        self.config = config
        self.tracked_vehicles: Dict[int, Dict] = {} # track_id -> vehicle_data dict
        self.lane_counts: Dict[int, int] = {} # lane_number -> count
        self.speed_limit_kmh = config.get('speed_limit', 60)
        self.density_threshold = config.get('incident_detection', {}).get('density_threshold', 10)
        self.stopped_threshold_kmh = config.get('stopped_speed_threshold_kmh', 5)
        self.congestion_speed_threshold = config.get('incident_detection', {}).get('congestion_speed_threshold', 20)
        # COCO class IDs mapped to names (adjust if using a different model/dataset)
        self.vehicle_type_map = {
             2: 'car', 3: 'motorcycle', 5: 'bus', 7: 'truck'
        }

    def update_vehicles(self, vehicles: Dict[int, Dict]):
        """Updates the monitor with the latest set of tracked vehicles."""
        start_time = time.time()
        self.tracked_vehicles = vehicles
        self.lane_counts.clear()

        for track_id, data in vehicles.items():
            lane = data.get('lane', -1) # Assume -1 if lane not assigned
            if lane != -1:
                self.lane_counts[lane] = self.lane_counts.get(lane, 0) + 1

        end_time = time.time()
        logger.debug(f"TrafficMonitor.update_vehicles execution time: {end_time - start_time:.6f} seconds")

    def get_metrics(self) -> Dict[str, Any]:
        """Calculates and returns current traffic metrics."""
        start_time = time.time()
        total_vehicles = len(self.tracked_vehicles)
        stopped_count = 0
        speeding_count = 0
        speeds_list_kmh = []
        vehicle_type_counts = {name: 0 for name in self.vehicle_type_map.values()}
        vehicle_type_counts['unknown'] = 0

        for data in self.tracked_vehicles.values():
            speed_kmh = float(data.get('speed', 0.0)) # Assuming speed is in km/h
            speeds_list_kmh.append(speed_kmh)

            if speed_kmh < self.stopped_threshold_kmh:
                stopped_count += 1
            if speed_kmh > self.speed_limit_kmh:
                speeding_count += 1

            class_id = data.get('class_id', -1)
            type_name = self.vehicle_type_map.get(class_id, 'unknown')
            vehicle_type_counts[type_name] += 1

        average_speed_kmh = float(np.mean(speeds_list_kmh)) if speeds_list_kmh else 0.0
        congestion_level_percent = float((stopped_count / total_vehicles * 100.0)) if total_vehicles > 0 else 0.0
        is_congested = average_speed_kmh < self.congestion_speed_threshold and total_vehicles > self.density_threshold # Example congestion condition

        high_density_lanes = [lane for lane, count in self.lane_counts.items() if count > self.density_threshold]

        metrics = {
            'total_vehicles': total_vehicles,
            'stopped_vehicles': stopped_count,
            'speeding_vehicles': speeding_count,
            'average_speed_kmh': round(average_speed_kmh, 1),
            'congestion_level_percent': round(congestion_level_percent, 1),
            'is_congested': is_congested,
            'vehicles_per_lane': self.lane_counts.copy(),
            'high_density_lanes': high_density_lanes,
            'vehicle_type_counts': vehicle_type_counts
        }
        end_time = time.time()
        logger.debug(f"TrafficMonitor.get_metrics execution time: {end_time - start_time:.6f} seconds")
        return metrics

# --- Visualization ---
# Cache variables for overlays to avoid recreating them every frame
cached_lane_overlay = None
cached_grid_overlay = None
overlay_cache_size = None # Store the (width, height) the cache was created for

def create_lane_overlay(shape: Tuple[int, int, int], num_lanes: int, lane_width: float, density_per_lane: Dict[int, int], config: Dict) -> np.ndarray:
    """Creates a semi-transparent overlay indicating lane density."""
    h, w = shape[:2]
    overlay = np.zeros((h, w, 4), dtype=np.uint8) # RGBA overlay

    # Define colors for different density levels (RGBA format)
    density_config = config.get('incident_detection', {})
    threshold_high = density_config.get('density_threshold', 10)
    threshold_medium = threshold_high // 2 # Example: medium is half of high

    levels = {
        'low': (0, 255, 0, 60),       # Green, low alpha
        'medium': (255, 165, 0, 80),  # Orange, medium alpha
        'high': (255, 0, 0, 100)      # Red, higher alpha
    }

    for lane_num in range(1, num_lanes + 1):
        x1 = int((lane_num - 1) * lane_width)
        x2 = int(lane_num * lane_width)
        density = density_per_lane.get(lane_num, 0)

        color = levels['low'] # Default to low
        if density >= threshold_high:
            color = levels['high']
        elif density >= threshold_medium:
            color = levels['medium']

        # Draw the colored rectangle for the lane
        cv2.rectangle(overlay, (x1, 0), (x2, h), color, -1) # Fill the rectangle

    return overlay

def create_grid_overlay(shape: Tuple[int, int, int], config: Dict) -> np.ndarray:
    """Creates a semi-transparent grid overlay based on config."""
    h, w = shape[:2]
    overlay = np.zeros((h, w, 4), dtype=np.uint8) # RGBA overlay
    ppm = config.get('pixels_per_meter', 50)
    lanes = config.get('lane_detection', {}).get('num_lanes', 0) # Use num_lanes from config

    grid_interval_meters = 10 # Draw grid lines every 10 meters
    grid_interval_pixels = int(grid_interval_meters * ppm) if ppm > 0 else 100

    grid_color = (100, 100, 100, 80) # Gray, low alpha
    text_color = (200, 200, 200, 150) # Lighter gray, higher alpha

    # Draw horizontal lines
    for y in range(grid_interval_pixels, h, grid_interval_pixels):
        cv2.line(overlay, (0, y), (w, y), grid_color, 1, cv2.LINE_AA)
        # Optional: Add distance text
        # cv2.putText(overlay, f"{y // ppm}m", (5, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, text_color, 1, cv2.LINE_AA)

    # Draw vertical lane lines (if num_lanes is configured)
    if lanes > 0:
        lane_width_pixels = w / lanes # Assume lanes divide the width equally
        for i in range(1, lanes):
            x = int(i * lane_width_pixels)
            cv2.line(overlay, (x, 0), (x, h), grid_color, 1, cv2.LINE_AA)
            # Optional: Add lane number text
            # cv2.putText(overlay, f"L{i}", (x + 5, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.4, text_color, 1, cv2.LINE_AA)

    return overlay

def alpha_blend(foreground: np.ndarray, background: np.ndarray) -> np.ndarray:
    """Blends an RGBA foreground onto an BGR background."""
    if foreground.shape[:2] != background.shape[:2]:
        # Resize foreground to match background if needed (e.g., overlay cache mismatch)
        foreground = cv2.resize(foreground, (background.shape[1], background.shape[0]), interpolation=cv2.INTER_NEAREST)

    # Split foreground RGBA channels
    fg_b, fg_g, fg_r, fg_a = cv2.split(foreground)

    # Normalize alpha channel to range [0, 1]
    alpha = fg_a.astype(float) / 255.0

    # Prepare foreground BGR channels (already weighted by alpha)
    fg_b_w = (fg_b * alpha).astype(background.dtype)
    fg_g_w = (fg_g * alpha).astype(background.dtype)
    fg_r_w = (fg_r * alpha).astype(background.dtype)

    # Prepare background BGR channels (weighted by 1 - alpha)
    bg_b, bg_g, bg_r = cv2.split(background)
    inv_alpha = 1.0 - alpha
    bg_b_w = (bg_b * inv_alpha).astype(background.dtype)
    bg_g_w = (bg_g * inv_alpha).astype(background.dtype)
    bg_r_w = (bg_r * inv_alpha).astype(background.dtype)

    # Combine weighted channels
    out_b = cv2.add(fg_b_w, bg_b_w)
    out_g = cv2.add(fg_g_w, bg_g_w)
    out_r = cv2.add(fg_r_w, bg_r_w)

    # Merge back into a BGR image
    return cv2.merge((out_b, out_g, out_r))


def visualize_data(frame: Optional[np.ndarray], tracked_vehicles: Dict[int, Dict],
                  traffic_metrics: Dict[str, Any], visualization_options: Set[str],
                  config: Dict, feed_id: str = "") -> Optional[np.ndarray]:
    """Draws visualizations onto the frame based on tracked data and options."""
    global cached_lane_overlay, cached_grid_overlay, overlay_cache_size
    if frame is None:
        return None

    try:
        vis_frame = frame.copy()
        h, w = vis_frame.shape[:2]
        current_size = (w, h)

        # Check if overlay cache needs reset (frame size changed)
        if overlay_cache_size != current_size:
            logger.debug(f"[{feed_id}] Frame size changed to {current_size}. Resetting visualization overlays.")
            cached_lane_overlay = None
            cached_grid_overlay = None
            overlay_cache_size = current_size

        lane_config = config.get('lane_detection', {})
        num_lanes = lane_config.get('num_lanes', 0)
        lane_width = w / num_lanes if num_lanes > 0 else w # Avoid division by zero

        # --- Apply Overlays ---
        if "Grid Overlay" in visualization_options:
            if cached_grid_overlay is None:
                cached_grid_overlay = create_grid_overlay(vis_frame.shape, config)
            if cached_grid_overlay is not None:
                vis_frame = alpha_blend(cached_grid_overlay, vis_frame)

        if "Lane Density Overlay" in visualization_options and num_lanes > 0:
            # Get density data from metrics
            density_per_lane = traffic_metrics.get('vehicles_per_lane', {})
            # Recreate lane overlay only if density changes significantly? For now, recreate always if option enabled.
            lane_overlay = create_lane_overlay(vis_frame.shape, num_lanes, lane_width, density_per_lane, config)
            vis_frame = alpha_blend(lane_overlay, vis_frame)

        # --- Draw Tracked Vehicles ---
        if "Tracked Vehicles" in visualization_options or "Vehicle Data" in visualization_options:
            speed_limit = config.get('speed_limit', 60)
            # Define colors for speed indication
            color_normal = (0, 255, 0) # Green
            color_warning = (0, 255, 255) # Yellow
            color_speeding = (0, 0, 255) # Red

            for vehicle_id, data in tracked_vehicles.items():
                bbox = data.get('bbox')
                if bbox:
                    x1, y1, x2, y2 = map(int, bbox)
                    speed = data.get('speed', 0.0) # Assuming speed is in km/h
                    class_id = data.get('class_id', -1)
                    class_name = TrafficMonitor.vehicle_type_map.get(class_id, '?') # Get name
                    plate = data.get('license_plate', '')

                    # Determine color based on speed
                    color = color_normal
                    if speed > speed_limit:
                        color = color_speeding
                    elif speed > speed_limit * 0.8: # Example: warning above 80% of limit
                        color = color_warning

                    # Draw bounding box
                    if "Tracked Vehicles" in visualization_options:
                         cv2.rectangle(vis_frame, (x1, y1), (x2, y2), color, 2)

                    # Draw vehicle info text
                    if "Vehicle Data" in visualization_options:
                        text_lines = [f"ID: {vehicle_id} ({class_name})"]
                        text_lines.append(f"Spd: {speed:.1f} km/h")
                        if plate:
                             text_lines.append(f"LP: {plate}")
                        # Add other data like lane, accel if needed

                        # Calculate text position (above the box, ensuring it stays within frame)
                        text_y = y1 - 7
                        line_height = 15
                        if text_y < line_height * len(text_lines): # If text goes off top
                           text_y = y2 + line_height # Put below box

                        # Draw multiple lines of text
                        for i, line in enumerate(text_lines):
                            cv2.putText(vis_frame, line, (x1 + 5, text_y + i * line_height),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1, cv2.LINE_AA)

        # --- Draw Top Banner Info ---
        time_str = time.strftime("%Y-%m-%d %H:%M:%S")
        veh_count = traffic_metrics.get('total_vehicles', 0)
        avg_spd = traffic_metrics.get('average_speed_kmh', 0.0)
        congested = traffic_metrics.get('is_congested', False)

        banner_text = f"{time_str} | Feed: {feed_id} | Vehicles: {veh_count} | Avg Spd: {avg_spd:.1f} km/h"
        if congested: banner_text += " | CONGESTED"

        banner_h = 25
        cv2.rectangle(vis_frame, (0, 0), (w, banner_h), (0, 0, 0, 180), -1) # Semi-transparent black banner
        cv2.putText(vis_frame, banner_text, (10, banner_h - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA) # White text

        return vis_frame

    except Exception as e:
        logger.error(f"[{feed_id}] Visualization error: {e}", exc_info=True)
        return frame # Return original frame on error

# --- LicensePlatePreprocessor ---
class LicensePlatePreprocessor:
    """Handles preprocessing of license plate ROIs and performing OCR."""
    def __init__(self, config: Dict, perspective_matrix: Optional[np.ndarray] = None):
        # ... (init logic - unchanged) ...
        self.config = config
        self.gemini_api_key = config.get("ocr_engine", {}).get("gemini_api_key", None)
        genai.configure(api_key=self.gemini_api_key)
        self.model = genai.GenerativeModel('gemini-pro-vision')
        self.cool_down_time = 5
        self.last_error_time = 0
    def calculate_threshold_params(self, image): # ... (logic unchanged) ...
        pass
    def _retry_settings(self): # ... (logic unchanged) ...
        pass
    
    @retry(wait=wait_exponential(multiplier=1, min=1, max=10), 
           stop=stop_after_attempt(3),
           retry=retry_if_exception_type((genai.types.BlockedPromptException, genai.types.InvalidAPIKeyError, genai.types.RateLimitExceededError, genai.types.PermissionDeniedError, genai.types.GenerativeModelError, ConnectionError, TimeoutError))
           )
    def _call_gemini_ocr(self, image: np.ndarray) -> str:
        start_time = time.time()

        if not self.model or not self.gemini_api_key:
             return "" # Exit early if model not initialized

        current_time = time.time()
        if current_time - self.last_error_time < self.cool_down_time:
             logger.info(f"Cool-down period active. Waiting before making another OCR request.")
             time.sleep(self.cool_down_time)
        end_time = time.time()
        logger.debug(f"LicensePlatePreprocessor._call_gemini_ocr execution time: {end_time - start_time:.6f} seconds")
        logger.debug(f"Calling gemini API.")
        return ""
    def preprocess_license_plate(self, roi, skip_rotation=False, min_contour_area=100):
        start_time = time.time()
        if roi is None or roi.size == 0: return None
        if roi.shape[0] < 10 or roi.shape[1] < 10: return None # Too small

        try:
            logger.debug(f"Preprocessing ROI of size {roi.shape}")

            # 1. Grayscale
            if len(roi.shape) == 3 and roi.shape[2] == 3:
                gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            elif len(roi.shape) == 2:
                gray = roi.copy()
            else:
                logger.warning("Invalid ROI shape for preprocessing.")
                return None

            # 2. Increase Contrast/Brightness (Optional - Careful tuning needed)
            # alpha = 1.5 # Contrast control
            # beta = 10    # Brightness control
            # adjusted = cv2.convertScaleAbs(gray, alpha=alpha, beta=beta)

            # 3. Noise Reduction (Gaussian Blur or Median Blur)
            blurred = cv2.GaussianBlur(gray, (3, 3), 0) # Smaller kernel for plates?
            # blurred = cv2.medianBlur(gray, 3) # Median better for salt-and-pepper noise

            # 4. Thresholding (Adaptive is often good for varying light)
            thresh = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                           cv2.THRESH_BINARY_INV, 19, 9) # Adjust block size and C
            # Or Otsu's
            # _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

            # 5. Morphological Operations (Closing/Opening to clean up)
            # kernel = self.morph_kernel
            # closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=1)
            # opened = cv2.morphologyEx(closed, cv2.MORPH_OPEN, kernel, iterations=1)
            # processed_roi = opened

            # Let's try just dilation first on inverted binary
            dilated = cv2.dilate(thresh, self.morph_kernel, iterations=1)
            processed_roi = dilated # Return this for Tesseract

            # --- Advanced Steps (Placeholder) ---
            # - Contour detection to find exact plate boundary
            # - Rotation correction (deskewing) based on contours or Hough lines
            # - Perspective warping if matrix is available
            # - Character segmentation (usually handled by OCR engine like Tesseract)
            # - Sharpening (cv2.filter2D(processed_roi, -1, self.sharpen_kernel)) - apply carefully

            end_time = time.time()
            logger.debug(f"LicensePlatePreprocessor.preprocess_license_plate execution time: {end_time - start_time:.6f} seconds")
            return processed_roi

        except Exception as e:
            logger.error(f"Error during license plate preprocessing: {e}", exc_info=True)
            return None


    def preprocess_and_ocr(self, roi: np.ndarray) -> str:
        """Preprocesses the ROI and then calls the appropriate OCR engine."""
        start_time = time.time()
        ocr_result = ""

        # --- Prefer Gemini if available and configured ---
        if self.model and self.gemini_api_key:
            logger.debug("Attempting OCR using Gemini...")
            # Gemini generally prefers less pre-processed, original color images
            ocr_result = self._call_gemini_ocr(roi)
            if ocr_result: # If Gemini succeeded, return its result
                 logger.debug(f"Gemini OCR successful: {ocr_result}")
                 return ocr_result
            else:
                 logger.warning("Gemini OCR failed or returned empty. Falling back to Tesseract if available.")
                 # Fall through to Tesseract if Gemini fails

        # --- Fallback to Tesseract ---
        if pytesseract:
            logger.debug("Attempting OCR using Tesseract...")
            processed_roi = self.preprocess_license_plate(roi)
            if processed_roi is not None:
                try:
                    # Tesseract configuration:
                    # --oem 3: Default LSTM engine
                    # --psm 6: Assume a single uniform block of text (good for plates)
                    # -c tessedit_char_whitelist: Restrict characters
                    custom_config = r'--oem 3 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
                    text = pytesseract.image_to_string(processed_roi, config=custom_config)

                    # Post-process Tesseract result
                    ocr_result = ''.join(filter(str.isalnum, text)).upper()
                    logger.info(f"Tesseract OCR Raw: '{text.strip()}', Processed: '{ocr_result}'")

                except Exception as e:
                    logger.error(f"Tesseract OCR encountered an error: {e}", exc_info=True)
                    ocr_result = ""
            else:
                logger.warning("Preprocessing failed, cannot perform Tesseract OCR.")
        else:
            if not (self.model and self.gemini_api_key): # Only log if no OCR was attempted
                 logger.warning("No OCR engine (Gemini or Tesseract) is available/configured.")

        end_time = time.time()
        logger.debug(f"LicensePlatePreprocessor.preprocess_and_ocr total execution time: {end_time - start_time:.6f} seconds")
        return ocr_result

# --- DatabaseManager (Simplified for SQLite) ---
class DatabaseManager:
    """Handles all database interactions using SQLite."""
    def __init__(self, config: Dict):
        self.db_config = config.get("database", {})
        self.db_path = self.db_config.get("db_path")
        if not self.db_path:
            raise ConfigError("Database path ('database.db_path') not found in configuration.")

        self.chunk_size = self.db_config.get("chunk_size", 100)
        # Use threading.RLock if methods might call each other while holding the lock
        self.lock = threading.Lock()

        logger.info(f"Initializing DatabaseManager with db path: {self.db_path}")
        self._initialize_database()

    def _get_connection(self) -> sqlite3.Connection:
        """Establishes a new SQLite connection with WAL mode and Row factory."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0) # Add timeout
            conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrency
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;") # Often paired with WAL
            except sqlite3.Error as e:
                # Don't fail init if WAL fails, just log warning
                logger.warning(f"Could not set WAL mode on {self.db_path} (may be busy or unsupported): {e}")
            return conn
        except sqlite3.Error as e:
            logger.error(f"Failed to connect to database {self.db_path}: {e}", exc_info=True)
            raise DatabaseError(f"Failed to connect to database: {e}") from e

    def _initialize_database(self):
        """Ensures the database file exists and creates tables if necessary."""
        logger.info(f"Initializing database schema at {self.db_path}...")
        try:
            # The connection context manager handles commit/rollback and close
            with self._get_connection() as conn:
                cursor = conn.cursor()
                self._create_tables(cursor)
            logger.info("Database schema initialization check complete.")
        except sqlite3.Error as e:
            logger.error(f"Database initialization error: {e}", exc_info=True)
            raise DatabaseError(f"Database schema initialization failed: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error during database initialization: {e}", exc_info=True)
            raise DatabaseError(f"Unexpected error initializing database: {e}") from e

    def _create_tables(self, cursor: sqlite3.Cursor):
        """Defines and creates necessary tables if they don't exist."""
        logger.debug("Creating/Ensuring 'vehicle_tracks' table exists...")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS vehicle_tracks (
                feed_id TEXT NOT NULL,
                track_id INTEGER NOT NULL,
                timestamp REAL NOT NULL,
                class_id INTEGER,
                confidence REAL,
                bbox_x1 REAL, bbox_y1 REAL, bbox_x2 REAL, bbox_y2 REAL, -- Use REAL for potential float coords
                center_x REAL, center_y REAL,
                speed REAL,
                acceleration REAL,
                lane INTEGER,
                direction REAL, -- Angle or vector component
                license_plate TEXT,
                ocr_confidence REAL,
                flags TEXT, -- Comma-separated string of flags
                -- Unique constraint on feed, track, and timestamp to prevent exact duplicates
                PRIMARY KEY (feed_id, track_id, timestamp)
            )
        ''')
        # Indexes for common query patterns
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vt_timestamp ON vehicle_tracks(timestamp DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vt_feed_track ON vehicle_tracks(feed_id, track_id);")

        logger.debug("Creating/Ensuring 'alerts' table exists...")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL DEFAULT (unixepoch('now', 'subsec')), -- Use standard unixepoch
                severity TEXT NOT NULL CHECK(severity IN ('INFO', 'WARNING', 'CRITICAL')),
                feed_id TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT, -- Optional JSON string or similar
                acknowledged INTEGER DEFAULT 0 NOT NULL CHECK(acknowledged IN (0, 1)) -- Boolean flag (0=false, 1=true)
            )
        ''')
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_feed_severity ON alerts(feed_id, severity);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_acknowledged ON alerts(acknowledged);")
        logger.debug("Table creation check finished.")


    # --- Retry Decorator for DB Writes ---
    db_write_retry_decorator = retry(
        wait=wait_exponential(multiplier=0.2, min=0.2, max=3), # Faster retry for DB locks
        stop=stop_after_attempt(4),
        retry=retry_if_exception_type(sqlite3.OperationalError) # Retry specifically on OperationalError (e.g., "database is locked")
    )

    @db_write_retry_decorator
    def save_vehicle_data(self, vehicle_data: Dict) -> bool:
        """Saves a single vehicle track record with retry logic."""
        start_time = time.time()
        sql = ''' INSERT OR REPLACE INTO vehicle_tracks
                  (feed_id, track_id, timestamp, class_id, confidence,
                   bbox_x1, bbox_y1, bbox_x2, bbox_y2, center_x, center_y,
                   speed, acceleration, lane, direction, license_plate, ocr_confidence, flags)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '''
        try:
            bbox = vehicle_data.get('bbox', [None]*4)
            center = vehicle_data.get('center', [None]*2)
            flags_str = ','.join(sorted(list(vehicle_data.get('flags', set())))) # Ensure consistent order

            params = (
                vehicle_data.get('feed_id', 'unknown'), vehicle_data.get('track_id'),
                vehicle_data.get('timestamp', time.time()), vehicle_data.get('class_id'),
                vehicle_data.get('confidence'), bbox[0], bbox[1], bbox[2], bbox[3],
                center[0], center[1], vehicle_data.get('speed'), vehicle_data.get('acceleration'),
                vehicle_data.get('lane'), vehicle_data.get('direction'),
                vehicle_data.get('license_plate'), vehicle_data.get('ocr_confidence'),
                flags_str
            )
            # Use lock for thread safety if manager instance is shared
            with self.lock:
                # Connection context manager handles commit/rollback and close
                with self._get_connection() as conn:
                    conn.execute(sql, params)
            logger.debug(f"Saved track data: Feed={params[0]}, Track={params[1]}, Time={params[2]:.2f}")
            return True

        except RetryError as e: # Log after retries fail
             logger.error(f"DB save_vehicle_data failed after retries: {e}. Data: {vehicle_data.get('track_id')}")
             return False # Indicate failure
        except sqlite3.Error as e: # Catch specific DB errors not retried
            logger.error(f"Database error saving vehicle data: {e} - Track ID: {vehicle_data.get('track_id')}", exc_info=True)
            # Don't raise DatabaseError here if retry handles OperationalError, only for others
            if not isinstance(e, sqlite3.OperationalError):
                 raise DatabaseError(f"Failed to save vehicle data: {e}") from e
            return False # Should be caught by RetryError ideally
        except Exception as e: # Catch other unexpected errors (e.g., type errors in data)
            logger.error(f"Unexpected error saving vehicle data: {e} - Track ID: {vehicle_data.get('track_id')}", exc_info=True)
            raise DatabaseError(f"Unexpected error saving vehicle data: {e}") from e
        finally:
            end_time = time.time()
            logger.debug(f"DatabaseManager.save_vehicle_data execution time: {end_time - start_time:.6f} seconds")

    @db_write_retry_decorator
    def save_vehicle_data_batch(self, vehicle_data_list: List[Dict]) -> bool:
        """Saves a batch of vehicle track records with retry logic."""
        start_time = time.time()
        if not vehicle_data_list: return True

        sql = ''' INSERT OR REPLACE INTO vehicle_tracks
                  (feed_id, track_id, timestamp, class_id, confidence,
                   bbox_x1, bbox_y1, bbox_x2, bbox_y2, center_x, center_y,
                   speed, acceleration, lane, direction, license_plate, ocr_confidence, flags)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '''
        prepared_data = []
        try:
            for vd in vehicle_data_list:
                bbox = vd.get('bbox', [None]*4)
                center = vd.get('center', [None]*2)
                flags_str = ','.join(sorted(list(vd.get('flags', set()))))
                prepared_data.append((
                    vd.get('feed_id', 'unknown'), vd.get('track_id'),
                    vd.get('timestamp', time.time()), vd.get('class_id'),
                    vd.get('confidence'), bbox[0], bbox[1], bbox[2], bbox[3],
                    center[0], center[1], vd.get('speed'), vd.get('acceleration'),
                    vd.get('lane'), vd.get('direction'), vd.get('license_plate'),
                    vd.get('ocr_confidence'), flags_str
                ))

            if not prepared_data: return True # Avoid empty execute many

            with self.lock:
                with self._get_connection() as conn:
                    conn.executemany(sql, prepared_data)
            logger.debug(f"Saved batch of {len(prepared_data)} vehicle records.")
            return True

        except RetryError as e:
             logger.error(f"DB save_vehicle_data_batch failed after retries: {e}.")
             return False
        except sqlite3.Error as e:
            logger.error(f"Database error saving vehicle batch: {e}", exc_info=True)
            if not isinstance(e, sqlite3.OperationalError):
                 raise DatabaseError(f"Failed to save vehicle batch: {e}") from e
            return False
        except Exception as e:
            logger.error(f"Unexpected error saving vehicle batch: {e}", exc_info=True)
            raise DatabaseError(f"Unexpected error saving vehicle batch: {e}") from e
        finally:
            end_time = time.time()
            logger.debug(f"DatabaseManager.save_vehicle_data_batch execution time: {end_time - start_time:.6f} seconds")

    # --- Read Operations ---
    def get_recent_tracks(self, feed_id: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """Retrieves the most recent track records, optionally filtered by feed_id."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if feed_id:
                    cursor.execute("SELECT * FROM vehicle_tracks WHERE feed_id = ? ORDER BY timestamp DESC LIMIT ?", (feed_id, limit))
                else:
                    cursor.execute("SELECT * FROM vehicle_tracks ORDER BY timestamp DESC LIMIT ?", (limit,))
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Database error getting recent tracks (feed={feed_id}): {e}", exc_info=True)
            return [] # Return empty list on error

    def get_track_history(self, feed_id: str, track_id: int, limit: int = 50) -> List[Dict]:
         """Retrieves the recent history for a specific track."""
         try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM vehicle_tracks
                    WHERE feed_id = ? AND track_id = ?
                    ORDER BY timestamp DESC LIMIT ?
                """, (feed_id, track_id, limit))
                # Return in chronological order for easy plotting/analysis
                return [dict(row) for row in reversed(cursor.fetchall())]
         except sqlite3.Error as e:
            logger.error(f"Database error getting track history (feed={feed_id}, track={track_id}): {e}", exc_info=True)
            return []

    # --- Statistics Methods (Consider caching these if frequently called) ---
    # @lru_cache(maxsize=4) # Example caching decorator
    def get_vehicle_stats(self, time_window_secs: int = 300) -> Dict:
        """Calculates overall vehicle statistics in a recent time window."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                min_timestamp = time.time() - time_window_secs
                cursor.execute("""
                    SELECT
                        COUNT(*) as total_vehicles,
                        AVG(speed) as average_speed_kmh, -- Assuming speed stored in km/h
                        SUM(CASE WHEN speed < ? THEN 1 ELSE 0 END) as stopped_vehicles
                    FROM vehicle_tracks
                    WHERE timestamp > ?
                """, (DEFAULT_CONFIG['stopped_speed_threshold_kmh'], min_timestamp)) # Use configured threshold
                result = cursor.fetchone()
                stats = dict(result) if result else {'total_vehicles': 0, 'average_speed_kmh': 0.0, 'stopped_vehicles': 0}
                # Ensure values are not None
                stats['total_vehicles'] = stats.get('total_vehicles') or 0
                stats['average_speed_kmh'] = stats.get('average_speed_kmh') or 0.0
                stats['stopped_vehicles'] = stats.get('stopped_vehicles') or 0
                return stats
        except sqlite3.Error as e:
            logger.error(f"Database error getting vehicle stats: {e}", exc_info=True)
            return {} # Return empty dict on error

    # @lru_cache(maxsize=4)
    def get_vehicle_counts_by_type(self, time_window_secs: int = 300) -> Dict[str, int]:
        """Counts distinct vehicles by type in a recent time window."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                min_timestamp = time.time() - time_window_secs
                # Query to get the latest record for each track_id within the window
                # Then count types from those latest records
                cursor.execute("""
                    WITH LatestTracks AS (
                        SELECT feed_id, track_id, class_id, MAX(timestamp) as max_ts
                        FROM vehicle_tracks
                        WHERE timestamp > ?
                        GROUP BY feed_id, track_id
                    )
                    SELECT vt.class_id, COUNT(DISTINCT lt.track_id) as count
                    FROM vehicle_tracks vt
                    JOIN LatestTracks lt ON vt.feed_id = lt.feed_id AND vt.track_id = lt.track_id AND vt.timestamp = lt.max_ts
                    GROUP BY vt.class_id
                """, (min_timestamp,))

                results = cursor.fetchall()
                type_map = TrafficMonitor.vehicle_type_map # Use same map as monitor
                counts = {name: 0 for name in type_map.values()}
                counts['unknown'] = 0
                for row in results:
                    type_name = type_map.get(row['class_id'], 'unknown')
                    counts[type_name] = row['count']
                return counts
        except sqlite3.Error as e:
            logger.error(f"Database error getting vehicle counts by type: {e}", exc_info=True)
            return {}

    # --- Alert Management ---
    def get_alerts_filtered(self, filters: Dict, limit: int = 100, offset: int = 0) -> List[Dict]:
        """Retrieves alerts based on filters, limit, and offset."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                base_query = "SELECT id, timestamp, severity, feed_id, message, details, acknowledged FROM alerts WHERE 1=1"
                params = []
                conditions = []

                allowed_filters = {"severity", "feed_id", "acknowledged"}
                for key, value in filters.items():
                    if key in allowed_filters:
                        # Ensure acknowledged is 0 or 1 if provided
                        if key == 'acknowledged':
                             value = 1 if value else 0
                        conditions.append(f"{key} = ?")
                        params.append(value)
                    elif key == "search" and isinstance(value, str) and value.strip():
                        conditions.append("message LIKE ?")
                        params.append(f"%{value.strip()}%")
                    elif key == "start_time" and isinstance(value, (int, float)):
                         conditions.append("timestamp >= ?")
                         params.append(value)
                    elif key == "end_time" and isinstance(value, (int, float)):
                         conditions.append("timestamp <= ?")
                         params.append(value)

                if conditions:
                    base_query += " AND " + " AND ".join(conditions)

                # Apply ordering, limit, and offset
                query = f"{base_query} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])

                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]

        except sqlite3.Error as e:
            logger.error(f"Database error in get_alerts_filtered: {e}", exc_info=True)
            return []

    @db_write_retry_decorator
    def save_alert(self, severity: str, feed_id: str, message: str, details: Optional[str] = None) -> bool:
        """Saves a new alert to the database with retry logic."""
        sql = ''' INSERT INTO alerts (severity, feed_id, message, details)
                  VALUES (?, ?, ?, ?) '''
        # Validate severity
        if severity not in ('INFO', 'WARNING', 'CRITICAL'):
            logger.error(f"Invalid alert severity: {severity}. Must be INFO, WARNING, or CRITICAL.")
            return False
        try:
            params = (severity, feed_id, message, details)
            with self.lock:
                with self._get_connection() as conn:
                    conn.execute(sql, params)
            logger.info(f"Saved alert: Sev={severity}, Feed={feed_id}, Msg='{message[:60]}...'")
            return True
        except RetryError as e:
             logger.error(f"DB save_alert failed after retries: {e}.")
             return False
        except sqlite3.Error as e:
            logger.error(f"Database error saving alert: {e}", exc_info=True)
            if not isinstance(e, sqlite3.OperationalError):
                 raise DatabaseError(f"Failed to save alert: {e}") from e
            return False
        except Exception as e:
            logger.error(f"Unexpected error saving alert: {e}", exc_info=True)
            raise DatabaseError(f"Unexpected error saving alert: {e}") from e

    @db_write_retry_decorator
    def acknowledge_alert(self, alert_id: int, acknowledge: bool = True) -> bool:
        """Sets the acknowledged status of an alert."""
        sql = "UPDATE alerts SET acknowledged = ? WHERE id = ?"
        try:
            ack_value = 1 if acknowledge else 0
            with self.lock:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(sql, (ack_value, alert_id))
                    conn.commit()
                    # Check if any row was actually updated
                    if cursor.rowcount == 0:
                         logger.warning(f"Alert ID {alert_id} not found for acknowledgement.")
                         return False
            logger.info(f"Alert ID {alert_id} acknowledged status set to {acknowledge}.")
            return True
        except RetryError as e:
            logger.error(f"DB acknowledge_alert failed after retries: {e}.")
            return False
        except sqlite3.Error as e:
            logger.error(f"Database error acknowledging alert {alert_id}: {e}", exc_info=True)
            if not isinstance(e, sqlite3.OperationalError):
                 raise DatabaseError(f"Failed to acknowledge alert: {e}") from e
            return False
        except Exception as e:
            logger.error(f"Unexpected error acknowledging alert {alert_id}: {e}", exc_info=True)
            raise DatabaseError(f"Unexpected error acknowledging alert: {e}") from e

    def close(self):
        """Placeholder for closing resources (less critical for simple SQLite)."""
        logger.info("DatabaseManager close called (no action needed for simple SQLite).")
        # If using connection pooling, close the pool here.


# --- Example Usage (Optional: for testing utils directly) ---
if __name__ == "__main__":
    print("Running utils.py directly (for testing purposes)...")

    # 1. Load Configuration
    try:
        # Assumes config.yaml is in the project root (where you run python from)
        # Or adjust the relative path if running from backend/app/utils directly
        config_file = "../../../config.yaml" # Example relative path
        if not Path(config_file).exists():
             config_file = "config.yaml" # Try project root

        config = load_config(config_file)
        print("\n--- Configuration Loaded ---")
        # print(config) # Print loaded config (can be verbose)
        print(f"Database path: {config.get('database',{}).get('db_path')}")
        print(f"Model path: {config.get('vehicle_detection',{}).get('model_path')}")
        print(f"Gemini Key Set: {'Yes' if config.get('ocr_engine',{}).get('gemini_api_key') else 'No'}")

    except ConfigError as e:
        print(f"\n--- CONFIGURATION ERROR ---")
        print(e)
        sys.exit(1)
    except Exception as e:
        print(f"\n--- UNEXPECTED ERROR DURING CONFIG LOAD ---")
        print(e)
        sys.exit(1)


    # 2. Test DatabaseManager (optional)
    try:
        print("\n--- Testing DatabaseManager ---")
        db_manager = DatabaseManager(config)

        # Test saving an alert
        # success = db_manager.save_alert("WARNING", "TestFeed", "This is a test alert message.", '{"detail": 123}')
        # print(f"Save alert success: {success}")

        # Test retrieving alerts
        alerts = db_manager.get_alerts_filtered({}, limit=5)
        print(f"Retrieved {len(alerts)} recent alerts:")
        for alert in alerts:
            print(f"- ID: {alert['id']}, Time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(alert['timestamp']))}, Sev: {alert['severity']}, Feed: {alert['feed_id']}, Ack: {alert['acknowledged']}")

        # # Test acknowledging an alert (replace 1 with a valid ID if needed)
        # if alerts:
        #      alert_to_ack = alerts[0]['id']
        #      print(f"Attempting to acknowledge alert ID: {alert_to_ack}")
        #      ack_success = db_manager.acknowledge_alert(alert_to_ack, True)
        #      print(f"Acknowledge success: {ack_success}")
        #      # Verify
        #      updated_alerts = db_manager.get_alerts_filtered({'id': alert_to_ack}, limit=1) # Assuming filter by ID works
        #      if updated_alerts: print(f"Alert {alert_to_ack} acknowledged status: {updated_alerts[0]['acknowledged']}")


        db_manager.close() # Good practice, though less critical here

    except DatabaseError as e:
        print(f"\n--- DATABASE ERROR ---")
        print(e)
    except Exception as e:
        print(f"\n--- UNEXPECTED ERROR DURING DB TEST ---")
        print(e)

    # 3. Test LicensePlatePreprocessor (optional - requires an image and API key)
    # try:
    #     print("\n--- Testing LicensePlatePreprocessor ---")
    #     lp_preprocessor = LicensePlatePreprocessor(config)
    #     if lp_preprocessor.model: # Check if Gemini model loaded
    #          # Load a test image (replace with your image path)
    #          test_image_path = "path/to/your/license_plate.jpg"
    #          if Path(test_image_path).exists():
    #               test_img = cv2.imread(test_image_path)
    #               if test_img is not None:
    #                    print(f"Attempting OCR on {test_image_path}...")
    #                    ocr_text = lp_preprocessor.preprocess_and_ocr(test_img)
    #                    print(f"OCR Result: '{ocr_text}'")
    #               else: print(f"Failed to load image: {test_image_path}")
    #          else: print(f"Test image not found: {test_image_path}")
    #     else: print("Gemini model not loaded, skipping OCR test.")
    # except Exception as e:
    #      print(f"\n--- UNEXPECTED ERROR DURING OCR TEST ---")
    #      print(e)

    print("\n--- Utils.py Tests Finished ---")