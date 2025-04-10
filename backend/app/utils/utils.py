# /content/drive/MyDrive/R1v0.1/backend/app/utils/utils.py

import os
import cv2
import psutil
import numpy as np
import logging
import sqlite3
import queue
import threading
import re
from typing import Optional, List, Dict, Set, Tuple, Any # Added Any
from collections import deque
from functools import lru_cache
from tenacity import retry, wait_exponential, stop_after_attempt, RetryError
import torch
from PIL import Image
import io
import pytesseract
import google.generativeai as genai
from pathlib import Path
import time
from multiprocessing import Queue as MPQueue
import yaml # <<< --- ADDED IMPORT --- >>>

# Logging setup
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
class ConfigError(Exception): pass

# --- Configuration Loading --- <<< --- ADDED FUNCTION --- >>>
DEFAULT_CONFIG = { # Define some sensible defaults
    "logging": {"level": "INFO", "log_path": "./logs/backend.log"},
    "database": {"db_path": "data/vehicle_data.db", "chunk_size": 100, "cache_size": 50},
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
        "vehicle_class_ids": [2, 3, 5, 7], # car, motorcycle, bus, truck
        "yolo_imgsz": 640
    },
    "ocr_engine": {
        "use_gpu_ocr": False,
        "min_roi_size": 500,
        "ocr_interval": 10, # Seconds
        "gemini_max_retries": 3,
        "gemini_retry_delay": 1.0,
        "roi_top_margin_factor": 0.5,
        "roi_bottom_margin_factor": 0.1,
        "roi_left_margin_factor": 0.15,
        "roi_right_margin_factor": 0.15,
        "min_aspect_ratio": 2.0,
        "max_aspect_ratio": 5.0,
        "sharpen_kernel": [[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]],
        "morph_kernel": [[1, 1, 1], [1, 1, 1], [1, 1, 1]],
    },
    "lane_detection": {"num_lanes": 4, "lane_width": 160, "lane_change_buffer": 10},
    "perspective_calibration": {"matrix_path": ""},
    "kalman_filter_params": {
        "kf_sigma_px": 2.0, "kf_sigma_py": 2.0, "kf_sigma_pvx": 5.0, "kf_sigma_pvy": 5.0,
        "kf_sigma_mx": 0.5, "kf_sigma_my": 0.5, "kf_sigma_ax": 0.5, "kf_sigma_ay": 0.5
    },
    "pixels_per_meter": 40,
    "speed_limit": 60,
    "stopped_speed_threshold_kmh": 5,
    "accel_threshold_mps2": 0.5,
    "incident_detection": {"density_threshold": 10, "congestion_speed_threshold": 20},
    "interface": {"camera_warmup_time": 0.5},
    "vis_options_default": ["Tracked Vehicles", "Vehicle Data"],
}

# --- Simple Deep Merge Helper ---
def merge_dicts(base, update):
    """Recursively merge update dict into base dict."""
    for key, value in update.items():
        if isinstance(value, dict) and key in base and isinstance(base[key], dict):
            merge_dicts(base[key], value)
        else:
            base[key] = value
    return base

def load_config(config_path: str) -> Dict[str, Any]:
    """Loads configuration from a YAML file, merges with defaults."""
    path = Path(config_path)
    # Resolve path relative to this utils.py file if it's not absolute
    if not path.is_absolute():
         script_dir = Path(__file__).parent
         path = (script_dir.parent.parent / config_path).resolve() # Assumes config relative to project root (backend/)

    if not path.is_file():
        logger.error(f"Configuration file not found at resolved path: {path}")
        raise ConfigError(f"Configuration file not found: {path}")

    try:
        with open(path, 'r') as f:
            loaded_config = yaml.safe_load(f)
            if not isinstance(loaded_config, dict):
                 raise ConfigError("Configuration file is not a valid YAML dictionary.")

            # Perform a deep merge with defaults
            config = merge_dicts(DEFAULT_CONFIG.copy(), loaded_config) # Start with defaults, update with loaded

            # --- Basic Validation Examples (Add more as needed) ---
            if not config.get('database') or not config['database'].get('db_path'):
                 raise ConfigError("Missing required 'database.db_path' configuration.")
            if not config.get('vehicle_detection') or not config['vehicle_detection'].get('model_path'):
                 raise ConfigError("Missing required 'vehicle_detection.model_path' configuration.")
            # Ensure resolution is a list/tuple of 2 ints
            res = config.get('vehicle_detection', {}).get('frame_resolution', [0,0])
            if not isinstance(res, (list, tuple)) or len(res) != 2 or not all(isinstance(x, int) for x in res):
                 logger.warning(f"Invalid 'frame_resolution' format: {res}. Using default [640, 480].")
                 config['vehicle_detection']['frame_resolution'] = [640, 480]

            logger.info(f"Configuration loaded and merged successfully from {path}")
            return config

    except yaml.YAMLError as e:
        logger.error(f"Error parsing YAML configuration file {path}: {e}")
        raise ConfigError(f"Error parsing YAML file: {e}") from e
    except IOError as e:
         logger.error(f"Error reading configuration file {path}: {e}")
         raise ConfigError(f"Error reading config file: {e}") from e
    except ConfigError: raise # Re-raise validation errors
    except Exception as e:
        logger.error(f"An unexpected error occurred loading configuration: {e}", exc_info=True)
        raise ConfigError(f"Unexpected error loading configuration: {e}") from e
# --- END CONFIG LOADING ---


# --- Timers ---
class FrameTimer:
    def __init__(self, maxlen=100):
        self.timings = {
            'read': deque(maxlen=maxlen), 'detect_track': deque(maxlen=maxlen),
            'visualize': deque(maxlen=maxlen), 'queue_put': deque(maxlen=maxlen),
            'loop_total': deque(maxlen=maxlen)
        }
        self._lock = threading.Lock()
    def log_time(self, stage, duration):
        with self._lock:
            if stage in self.timings: self.timings[stage].append(duration)
    def get_avg(self, stage):
        with self._lock:
            return np.mean(self.timings[stage]) if stage in self.timings and self.timings[stage] else 0
    def get_fps(self, stage='loop_total'):
        avg_time = self.get_avg(stage); return 1.0 / avg_time if avg_time > 0 else 0
    def update_from_dict(self, timings_dict: Dict[str, List[float]]):
        with self._lock:
            for stage, times in timings_dict.items():
                if stage in self.timings and isinstance(times, (list, deque)):
                    self.timings[stage].extend(times)

# --- FrameReader ---
class FrameReader:
    def __init__(self, source, fps=None, buffer_size=1):
        self.source = source
        is_webcam_str = str(source).lower() == "webcam"
        try: capture_source = int(source)
        except ValueError: capture_source = 0 if is_webcam_str else source

        self.cap = cv2.VideoCapture(capture_source)
        if not self.cap.isOpened():
            logger.error(f"FrameReader: cv2.VideoCapture failed: {capture_source} (from {source})")
            raise RuntimeError(f"Failed to open video source: {capture_source} (from {source})")
        logger.info(f"FrameReader: cv2.VideoCapture opened: {capture_source} (from {source})")

        try: self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)) if not isinstance(capture_source, int) else float('inf')
        except Exception: self.total_frames = float('inf'); logger.warning(f"Could not determine total frames for {source}.")
        logger.info(f"Source {source} reports {self.total_frames} frames.")

        if isinstance(capture_source, int):
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, buffer_size)
            if fps: self.cap.set(cv2.CAP_PROP_FPS, fps)

        self.frame_queue = queue.Queue(maxsize=30)
        self.stop_event = threading.Event()
        self._end_of_video_flag = False
        self.state_lock = threading.Lock()
        self.thread = threading.Thread(target=self.update, daemon=True, name=f"FrameReader-{source}")
        self.thread.start()
        self.frame_index = 0

    @property
    def end_of_video(self):
        with self.state_lock: return self._end_of_video_flag
    @end_of_video.setter
    def end_of_video(self, value):
        with self.state_lock: self._end_of_video_flag = value

    def update(self):
        max_retries = 3; read_successful = False
        try:
            while not self.stop_event.is_set():
                retries = 0; ret = False; frame = None
                while retries < max_retries:
                    try: ret, frame = self.cap.read();
                    except Exception as read_err: logger.error(f"FrameReader {self.source} read() Exception: {read_err}"); ret = False
                    if ret: read_successful = True; break
                    logger.warning(f"FrameReader {self.source} read() failed (retry {retries+1}/{max_retries})")
                    retries += 1; time.sleep(0.05)
                if not ret:
                    logger.warning(f"FrameReader: Failed read frame {self.frame_index} after {max_retries} retries from {self.source}. End likely.")
                    self.end_of_video = True; break
                if self.frame_queue.full():
                    try: self.frame_queue.get_nowait()
                    except queue.Empty: pass
                try: self.frame_queue.put((self.frame_index, frame), timeout=0.1); self.frame_index += 1
                except queue.Full: logger.warning(f"FrameReader queue full. Frame {self.frame_index} lost.")
        except Exception as e: logger.error(f"FrameReader thread ERROR {self.source}: {e}", exc_info=True); self.end_of_video = True
        finally:
            logger.debug(f"FrameReader update loop finished {self.source}. Releasing.")
            if self.cap and self.cap.isOpened(): self.cap.release()

    def read(self) -> Tuple[Optional[np.ndarray], Optional[int]]:
        if self.frame_queue.empty() and self.end_of_video and not self.thread.is_alive(): return None, None
        try: return self.frame_queue.get(timeout=0.1)
        except queue.Empty: return None, None

    def stop(self):
        logger.info(f"FrameReader {self.source}: Stop called.")
        self.stop_event.set()
        while not self.frame_queue.empty():
            try: self.frame_queue.get_nowait()
            except queue.Empty: break
        if self.thread.is_alive(): self.thread.join(timeout=1.0)
        if self.cap and self.cap.isOpened(): self.cap.release()
        logger.info(f"FrameReader {self.source}: Stopped.")

# --- TrafficMonitor ---
class TrafficMonitor:
    def __init__(self, config: Dict):
        self.tracked_vehicles = {}
        self.lane_counts = {}
        self.speed_limit = config.get('speed_limit', 60)
        self.density_threshold = config.get('incident_detection', {}).get('density_threshold', 15)
    def update_vehicles(self, vehicles: Dict):
        self.tracked_vehicles = vehicles; self.lane_counts.clear()
        for data in vehicles.values():
            lane = data.get('lane', -1)
            if lane != -1: self.lane_counts[lane] = self.lane_counts.get(lane, 0) + 1
    def get_metrics(self) -> Dict:
        total = len(self.tracked_vehicles); stopped = 0; speeding = 0; speeds = []
        for v in self.tracked_vehicles.values():
            speed = float(v.get('speed', 0.0)); speeds.append(speed)
            if speed < 2: stopped += 1
            if speed > self.speed_limit: speeding += 1
        avg_speed = float(np.mean(speeds)) if speeds else 0.0
        congestion = float((stopped / total * 100.0)) if total > 0 else 0.0
        high_density = sum(1 for c in self.lane_counts.values() if c > self.density_threshold)
        return {'total_vehicles': total, 'stopped_vehicles': stopped, 'speeding_vehicles': speeding,
                'avg_speed': round(avg_speed, 1), 'congestion_level': round(congestion, 1),
                'vehicles_per_lane': self.lane_counts.copy(), 'high_density_lanes': high_density,
                'vehicle_types': self._count_vehicle_types()}
    def _count_vehicle_types(self) -> Dict[str, int]:
        counts = {}; type_map = {2: 'car', 3: 'motorcycle', 5: 'bus', 7: 'truck'}
        for data in self.tracked_vehicles.values():
            type = type_map.get(data.get('class_id', -1), 'unknown')
            counts[type] = counts.get(type, 0) + 1
        return counts

# --- Visualization ---
cached_lane_overlay = None; cached_grid_overlay = None; overlay_cache_size = None
def create_lane_overlay(shape, count, width, density, config):
    h, w = shape[:2]; overlay = np.zeros((h, w, 4), dtype=np.uint8)
    levels = {'low': (0, 255, 0, 60), 'medium': (255, 165, 0, 60), 'high': (255, 0, 0, 60)}
    thresh = {'low': 0, 'medium': 5, 'high': config.get('incident_detection', {}).get('density_threshold', 15)}
    for lane in range(1, count + 1):
        x1, x2 = int((lane - 1) * width), int(lane * width)
        val = density.get(lane, 0); color = levels['low']
        if val >= thresh['high']: color = levels['high']
        elif val >= thresh['medium']: color = levels['medium']
        cv2.rectangle(overlay, (x1, 0), (x2, h), color, -1)
    return overlay
def create_grid_overlay(shape, config):
    h, w = shape[:2]; overlay = np.zeros((h, w, 4), dtype=np.uint8)
    ppm = config.get('pixels_per_meter', 50); lanes = config.get('lane_detection', {}).get('num_lanes', 6)
    p10m = int(10 * ppm) if ppm > 0 else 100; mpp = 1.0 / ppm if ppm > 0 else 0.1
    grid_c, text_c = (100, 100, 100, 80), (200, 200, 200, 150)
    for y in range(0, h, p10m): cv2.line(overlay, (0, y), (w, y), grid_c, 1, cv2.LINE_AA)
    if lanes > 0:
        lane_w = w / (lanes + 1)
        for i in range(1, lanes + 1): cv2.line(overlay, (int(i * lane_w), 0), (int(i * lane_w), h), grid_c, 1, cv2.LINE_AA)
    return overlay

def visualize_data(frame: Optional[np.ndarray], tracked_vehicles: Dict, density: Dict[int, float],
                  alerts_queue: Optional[MPQueue], visualization_options: Set[str], config: Dict,
                  debug_mode: bool = False, feed_id: str = "") -> Optional[np.ndarray]:
    global cached_lane_overlay, cached_grid_overlay, overlay_cache_size
    if frame is None: return None
    try:
        vis = frame.copy(); h, w = vis.shape[:2]; current_size = (w, h)
        if overlay_cache_size != current_size:
            logger.debug(f"[{feed_id}] Vis size changed. Resetting overlays."); cached_lane_overlay=None; cached_grid_overlay=None; overlay_cache_size=current_size
        lanes = config.get('lane_detection', {}).get('num_lanes', 6); lane_w = w/(lanes+1) if lanes > 0 else w/4

        if "Lane Dividers" in visualization_options and lanes > 0: # ... (lane divider drawing) ...
             pass
        if "Grid Overlay" in visualization_options:
             if cached_grid_overlay is None: cached_grid_overlay = create_grid_overlay(vis.shape, config)
             if cached_grid_overlay is not None: # ... (apply grid overlay) ...
                 pass
        if "Lane Density Overlay" in visualization_options and lanes > 0:
             density_overlay = create_lane_overlay(vis.shape, lanes, lane_w, density, config)
             # ... (apply density overlay) ...
             pass

        if "Tracked Vehicles" in visualization_options or "Vehicle Data" in visualization_options:
             # ... (vehicle drawing logic - unchanged) ...
             pass

        # Banner - unchanged
        time_str = time.strftime("%H:%M:%S"); count = len(tracked_vehicles)
        banner_h=25; cv2.rectangle(vis, (0,0),(w,banner_h), (0,0,0,180), -1)
        cv2.putText(vis, f"{time_str} | {feed_id} | Veh: {count}", (10, banner_h-8), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255,255,255), 1, cv2.LINE_AA)
        return vis
    except Exception as e: logger.error(f"[{feed_id}] Vis error: {e}", exc_info=debug_mode); return frame

# --- LicensePlatePreprocessor ---
class LicensePlatePreprocessor:
    def __init__(self, config: Dict, perspective_matrix: Optional[np.ndarray] = None):
        # ... (init logic - unchanged) ...
        pass
    def calculate_threshold_params(self, image): # ... (logic unchanged) ...
        pass
    def _retry_settings(self): # ... (logic unchanged) ...
        pass
    # NOTE: Tenacity retry must be applied directly to the method
    # Applying it dynamically based on instance state is complex.
    # Keep the decorator on _call_gemini_ocr as before.
    @retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(3))
    def _call_gemini_ocr(self, image: np.ndarray) -> str: # ... (logic unchanged) ...
        pass
    def preprocess_and_ocr(self, roi, skip_rotation=False, min_contour_area=100): # ... (logic unchanged) ...
        pass
    def preprocess_license_plate(self, roi, skip_rotation=False, min_contour_area=100): # ... (logic unchanged) ...
        pass

# --- DatabaseManager ---
class DatabaseManager:
    def __init__(self, config: Dict, db_path: Optional[str] = None):
        # ... (init logic - unchanged) ...
        pass
    def _create_tables(self): # ... (logic unchanged) ...
        pass
    def _validate_and_fix_schema(self): # ... (logic unchanged) ...
        pass
    def _db_retry_settings(self): # ... (logic unchanged) ...
        pass
    # NOTE: Apply retry directly here too
    @retry(wait=wait_exponential(multiplier=0.5, min=0.5, max=5), stop=stop_after_attempt(3))
    def save_vehicle_data(self, vehicle_data: Dict) -> bool: # ... (logic unchanged) ...
        pass
    @retry(wait=wait_exponential(multiplier=0.5, min=0.5, max=5), stop=stop_after_attempt(3))
    def save_vehicle_data_batch(self, vehicle_data_list: List[Dict]) -> bool: # ... (logic unchanged) ...
        pass
    def get_recent_tracks(self, limit: int = 10) -> List[Dict]: # ... (logic unchanged) ...
        pass
    def _get_vehicle_stats_uncached(self) -> Dict: # ... (logic unchanged) ...
        pass
    def _get_vehicle_counts_by_type_uncached(self) -> Dict[str, int]: # ... (logic unchanged) ...
        pass
    # Public methods calling cached versions (unchanged)
    def get_vehicle_stats(self) -> Dict:
        if not self.conn_pool: return {}; return self._get_vehicle_stats_uncached()
    def get_vehicle_counts_by_type(self) -> Dict[str, int]:
         if not self.conn_pool: return {}; return self._get_vehicle_counts_by_type_uncached()
    def close(self): # ... (logic unchanged) ...
        pass

# --- Main block for testing ---
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    logger.info("Running basic checks in utils.py...")
    # ... (instantiation checks - unchanged) ...
    logger.info("Basic checks in utils.py finished.")