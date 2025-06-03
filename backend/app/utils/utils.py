# /content/drive/MyDrive/R1v0.1/backend/app/utils/utils.py

import asyncio # Added for to_thread
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
import torch # Although imported, torch is not directly used in this utils.py. Consider removing if not needed here.
from PIL import Image
import pytesseract
import google.generativeai as genai
from google.api_core import exceptions as google_api_exceptions
from multiprocessing import Queue as MPQueue
from typing import Tuple, Dict, Any, List, Optional, Set
from pymongo import MongoClient
from pymongo.database import Database as MongoDatabase # For type hinting
from pymongo.errors import ConnectionFailure, ConfigurationError as MongoConfigurationError

# Logging setup
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
DEFAULT_CONFIG: Dict[str, Any] = {
    "logging": {"level": "INFO", "log_path": "./logs/backend.log"},
    "database": {"db_path": "data/vehicle_data.db", "chunk_size": 100},
    "mongodb": {
        "uri": "mongodb://localhost:27017/",
        "database_name": "traffic_management_hub",
        "raw_traffic_collection": "raw_traffic_data",
        "processed_traffic_collection": "processed_traffic_data",
        "vehicle_tracks_collection": "vehicle_tracks"
    },
    "traffic_signal_control_service": {
        "base_url": None,
        "api_key": None,
        "timeout_seconds": 10
    },
    "performance": {"gpu_acceleration": True, "memory_limit_percent": 85},
    "video_input": {"webcam_buffer_size": 2, "webcam_index": 0},
    "vehicle_detection": {
        "model_path": "models/yolov8n.pt",
        "frame_resolution": [640, 480],
        "confidence_threshold": 0.5,
        "proximity_threshold": 50,
        "track_timeout": 5,
        "max_active_tracks": 50,
        "skip_frames": 1,
        "vehicle_class_ids": [2, 3, 5, 7],
        "yolo_imgsz": 640
    },
    "ocr_engine": {
        "use_gpu_ocr": False,
        "min_roi_size": 500,
        "ocr_interval": 10,
        "gemini_api_key": None,
        "gemini_max_retries": 3, # Note: tenacity @retry stop_after_attempt overrides this if different
        "gemini_cool_down_secs": 60,
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
    if not path.is_absolute():
         script_dir = Path(__file__).parent
         project_root = script_dir.parent.parent.parent
         path = (project_root / config_path).resolve()

    logger.info(f"Attempting to load configuration from resolved path: {path}")

    if not path.is_file():
        logger.warning(f"Configuration file not found at {path}. Using default configuration ONLY.")
        config = DEFAULT_CONFIG.copy()
    else:
        try:
            with open(path, 'r') as f:
                loaded_config = yaml.safe_load(f)
                if not isinstance(loaded_config, dict):
                    if loaded_config is None:
                        logger.warning(f"Configuration file {path} is empty. Using default configuration.")
                        loaded_config = {}
                    else:
                        raise ConfigError(f"Configuration file {path} is not a valid YAML dictionary.")
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

    try:
        logger.debug("Validating configuration...")
        required_sections = ["database", "vehicle_detection", "ocr_engine"]
        required_keys = {
            "database": ["db_path"],
            "vehicle_detection": ["model_path"],
            "ocr_engine": ["gemini_api_key"]
        }

        for section in required_sections:
            if section not in config:
                raise ConfigError(f"Missing required configuration section: '{section}'")
            for key in required_keys.get(section, []):
                 if key not in config[section]:
                     raise ConfigError(f"Missing required configuration key: '{section}.{key}'")

        api_key = config["ocr_engine"]["gemini_api_key"]
        if api_key is not None and (not isinstance(api_key, str) or not api_key.strip()):
            raise ConfigError(f"Invalid 'ocr_engine.gemini_api_key'. Must be a non-empty string or null/None.")

        db_path_str = config["database"]["db_path"]
        if not isinstance(db_path_str, str) or not db_path_str.strip():
             raise ConfigError(f"'database.db_path' must be a non-empty string. Got: '{db_path_str}'")
        db_file_path = Path(db_path_str)
        if not db_file_path.is_absolute():
            project_root = Path(__file__).parent.parent.parent
            db_file_path = (project_root / db_path_str).resolve()
            config["database"]["db_path"] = str(db_file_path)

        if not db_file_path.parent.exists():
             try:
                 logger.info(f"Creating database directory: {db_file_path.parent}")
                 db_file_path.parent.mkdir(parents=True, exist_ok=True)
             except OSError as e:
                 raise ConfigError(f"Failed to create database directory {db_file_path.parent}: {e}") from e

        model_path_str = config["vehicle_detection"]["model_path"]
        model_file_path = Path(model_path_str)
        if not model_file_path.is_absolute():
            project_root = Path(__file__).parent.parent.parent
            model_file_path = (project_root / model_path_str).resolve()

        if not model_file_path.is_file():
             raise ConfigError(f"Vehicle detection model file not found at specified/resolved path: '{model_file_path}'")
        config["vehicle_detection"]["model_path"] = str(model_file_path)

        if not isinstance(config["vehicle_detection"]["confidence_threshold"], (int, float)) or \
           not (0 <= config["vehicle_detection"]["confidence_threshold"] <= 1):
            raise ConfigError("'vehicle_detection.confidence_threshold' must be between 0 and 1.")

        if not isinstance(config["ocr_engine"]["gemini_max_retries"], int) or \
           config["ocr_engine"]["gemini_max_retries"] < 0:
            raise ConfigError("'ocr_engine.gemini_max_retries' must be a non-negative integer.")
        if not isinstance(config["ocr_engine"]["gemini_cool_down_secs"], (int, float)) or \
           config["ocr_engine"]["gemini_cool_down_secs"] < 0:
            raise ConfigError("'ocr_engine.gemini_cool_down_secs' must be a non-negative number.")

        logger.info("Configuration loaded and validated successfully.")
        return config

    except ConfigError as e:
        logger.error(f"Configuration validation failed: {e}")
        raise
    except Exception as e:
        logger.error(f"An unexpected error occurred during configuration validation: {e}", exc_info=True)
        raise ConfigError(f"Unexpected error validating configuration: {e}") from e

# --- Timers ---
class FrameTimer:
    """Simple class to track timings of different stages in a processing loop."""
    def __init__(self, maxlen: int = 100):
        self.timings: Dict[str, deque] = {
            'read': deque(maxlen=maxlen), 'detect_track': deque(maxlen=maxlen),
            'ocr': deque(maxlen=maxlen), 'monitor': deque(maxlen=maxlen),
            'visualize': deque(maxlen=maxlen), 'db_save': deque(maxlen=maxlen),
            'queue_put': deque(maxlen=maxlen), 'loop_total': deque(maxlen=maxlen)
        }
        self._lock = threading.Lock()

    def log_time(self, stage: str, duration: float):
        with self._lock:
            if stage in self.timings: self.timings[stage].append(duration)
            else: logger.warning(f"FrameTimer: Unknown stage '{stage}'")

    def get_avg(self, stage: str) -> float:
        with self._lock:
            return np.mean(self.timings[stage]) if stage in self.timings and self.timings[stage] else 0.0

    def get_fps(self, stage: str = 'loop_total') -> float:
        avg_time = self.get_avg(stage)
        return 1.0 / avg_time if avg_time > 0 else 0.0

    def update_from_dict(self, timings_dict: Dict[str, List[float]]):
        with self._lock:
            for stage, times in timings_dict.items():
                if stage in self.timings and isinstance(times, (list, deque)):
                    self.timings[stage].extend(times)

# --- FrameReader ---
class FrameReader:
    def __init__(self, source: Any, buffer_size: int = 2, target_fps: Optional[int] = None):
        self.source_name = str(source)
        self.target_fps = target_fps
        self.is_webcam = False
        capture_source: Any = source

        try:
            capture_source = int(source)
            self.is_webcam = True
        except ValueError:
            if str(source).lower() == "webcam":
                 capture_source = 0; self.is_webcam = True
            elif "://" not in str(source) and not Path(source).exists():
                 raise FileNotFoundError(f"Video file not found: {source}")

        self.cap = cv2.VideoCapture(capture_source)
        if not self.cap.isOpened():
            logger.error(f"FrameReader: Failed to open video source: {capture_source} (from {self.source_name})")
            raise RuntimeError(f"Cannot open video source: {capture_source}")
        logger.info(f"FrameReader: Successfully opened video source: {capture_source} (from {self.source_name})")

        self.source_fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.source_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.source_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        logger.info(f"Source properties: {self.source_width}x{self.source_height} @ {self.source_fps:.2f} FPS")

        if self.is_webcam:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, buffer_size)
            logger.info(f"Webcam buffer size set to: {buffer_size}")
            if self.target_fps:
                self.cap.set(cv2.CAP_PROP_FPS, self.target_fps)
                logger.info(f"Attempting to set webcam FPS to: {self.target_fps}")
        elif self.target_fps and self.target_fps != self.source_fps:
            logger.warning(f"Target FPS {self.target_fps} diffs from source {self.source_fps}. Frame drop/dup may occur.")

        self.frame_queue = queue.Queue(maxsize=30)
        self.stop_event = threading.Event()
        self._end_of_video_flag = False
        self.state_lock = threading.Lock()
        self.thread = threading.Thread(target=self._update_loop, daemon=True, name=f"FrameReader-{self.source_name}")
        self.frame_index = 0
        self.thread.start()

    @property
    def end_of_video(self) -> bool:
        with self.state_lock: return self._end_of_video_flag
    @end_of_video.setter
    def end_of_video(self, value: bool):
        with self.state_lock: self._end_of_video_flag = value

    def _update_loop(self):
        max_read_fails = 10; consecutive_fails = 0; last_read_time = time.monotonic()
        while not self.stop_event.is_set():
            try:
                if self.target_fps:
                    wait_time = (1.0 / self.target_fps) - (time.monotonic() - last_read_time)
                    if wait_time > 0: time.sleep(wait_time)
                ret, frame = self.cap.read(); last_read_time = time.monotonic()
                if ret:
                    consecutive_fails = 0
                    if self.frame_queue.full():
                        try: self.frame_queue.get_nowait(); logger.warning(f"FR queue full {self.source_name}. Discard oldest.")
                        except queue.Empty: pass
                    try: self.frame_queue.put((self.frame_index, frame.copy()), timeout=0.1); self.frame_index += 1
                    except queue.Full: logger.warning(f"FR queue still full. Frame {self.frame_index} lost.")
                else:
                    consecutive_fails += 1
                    logger.warning(f"FR {self.source_name}: cv2.read() False (Fail {consecutive_fails}/{max_read_fails}).")
                    if consecutive_fails >= max_read_fails:
                        logger.error(f"FR {self.source_name}: Max read fails. End of video/HW issue."); self.end_of_video = True; break
                    time.sleep(0.05)
                    if not self.is_webcam and self.cap.get(cv2.CAP_PROP_POS_FRAMES) >= self.cap.get(cv2.CAP_PROP_FRAME_COUNT):
                        logger.info(f"FR {self.source_name}: Reached end of video file."); self.end_of_video = True; break
            except Exception as e:
                logger.error(f"FR thread error ({self.source_name}): {e}", exc_info=True); self.end_of_video = True; break
        logger.info(f"FR thread stopping for {self.source_name}."); self.end_of_video = True
        if self.cap and self.cap.isOpened(): self.cap.release(); logger.info(f"FR video capture released for {self.source_name}.")
        while not self.frame_queue.empty():
             try: self.frame_queue.get_nowait()
             except queue.Empty: break

    def read(self) -> Optional[Tuple[int, np.ndarray]]:
        try: return self.frame_queue.get(timeout=0.5)
        except queue.Empty:
            if self.end_of_video and (not self.thread.is_alive() or self.frame_queue.empty()):
                 logger.debug(f"FR {self.source_name}: Read, queue empty & EOV."); return None
            logger.debug(f"FR {self.source_name}: Read, queue temp empty."); return None

    def stop(self):
        logger.info(f"FR {self.source_name}: Stop requested."); self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2.0)
            if self.thread.is_alive(): logger.warning(f"FR thread {self.source_name} did not exit cleanly.")
        if self.cap and self.cap.isOpened(): self.cap.release()
        logger.info(f"FR {self.source_name}: Stopped.")

# --- TrafficMonitor ---
class TrafficMonitor:
    vehicle_type_map = {2: 'car', 3: 'motorcycle', 5: 'bus', 7: 'truck'} # Class attribute
    def __init__(self, config: Dict):
        self.config = config; self.tracked_vehicles: Dict[int, Dict] = {}
        self.lane_counts: Dict[int, int] = {}
        self.speed_limit_kmh = config.get('speed_limit', 60)
        incident_cfg = config.get('incident_detection', {})
        self.density_threshold = incident_cfg.get('density_threshold', 10)
        self.congestion_speed_threshold = incident_cfg.get('congestion_speed_threshold', 20)
        self.stopped_threshold_kmh = config.get('stopped_speed_threshold_kmh', 5)

    def update_vehicles(self, vehicles: Dict[int, Dict]):
        self.tracked_vehicles = vehicles; self.lane_counts.clear()
        for track_id, data in vehicles.items():
            lane = data.get('lane', -1)
            if lane != -1: self.lane_counts[lane] = self.lane_counts.get(lane, 0) + 1

    def get_metrics(self) -> Dict[str, Any]:
        total_vehicles = len(self.tracked_vehicles); stopped_count = 0; speeding_count = 0
        speeds_list_kmh = []; vehicle_type_counts = {name: 0 for name in self.vehicle_type_map.values()}; vehicle_type_counts['unknown'] = 0
        for data in self.tracked_vehicles.values():
            speed_kmh = float(data.get('speed', 0.0)); speeds_list_kmh.append(speed_kmh)
            if speed_kmh < self.stopped_threshold_kmh: stopped_count += 1
            if speed_kmh > self.speed_limit_kmh: speeding_count += 1
            type_name = self.vehicle_type_map.get(data.get('class_id', -1), 'unknown'); vehicle_type_counts[type_name] += 1
        avg_speed_kmh = float(np.mean(speeds_list_kmh)) if speeds_list_kmh else 0.0
        congestion_lvl = float((stopped_count / total_vehicles * 100.0)) if total_vehicles > 0 else 0.0
        is_congested = avg_speed_kmh < self.congestion_speed_threshold and total_vehicles > self.density_threshold
        high_density_lanes = [lane for lane, count in self.lane_counts.items() if count > self.density_threshold]
        return {
            'total_vehicles': total_vehicles, 'stopped_vehicles': stopped_count, 'speeding_vehicles': speeding_count,
            'average_speed_kmh': round(avg_speed_kmh, 1), 'congestion_level_percent': round(congestion_lvl, 1),
            'is_congested': is_congested, 'vehicles_per_lane': self.lane_counts.copy(),
            'high_density_lanes': high_density_lanes, 'vehicle_type_counts': vehicle_type_counts
        }

# --- Visualization ---
cached_lane_overlay = None; cached_grid_overlay = None; overlay_cache_size = None
def create_lane_overlay(shape: Tuple[int, int, int], num_lanes: int, lane_width: float, density_per_lane: Dict[int, int], config: Dict) -> np.ndarray:
    h, w = shape[:2]; overlay = np.zeros((h, w, 4), dtype=np.uint8)
    density_config = config.get('incident_detection', {}); threshold_high = density_config.get('density_threshold', 10); threshold_medium = threshold_high // 2
    levels = {'low': (0, 255, 0, 60), 'medium': (255, 165, 0, 80), 'high': (255, 0, 0, 100)}
    for lane_num in range(1, num_lanes + 1):
        x1 = int((lane_num - 1) * lane_width); x2 = int(lane_num * lane_width); density = density_per_lane.get(lane_num, 0)
        color = levels['high'] if density >= threshold_high else (levels['medium'] if density >= threshold_medium else levels['low'])
        cv2.rectangle(overlay, (x1, 0), (x2, h), color, -1)
    return overlay

def create_grid_overlay(shape: Tuple[int, int, int], config: Dict) -> np.ndarray:
    h, w = shape[:2]; overlay = np.zeros((h, w, 4), dtype=np.uint8)
    ppm = config.get('pixels_per_meter', 50); lanes = config.get('lane_detection', {}).get('num_lanes', 0)
    grid_interval_pixels = int(10 * ppm) if ppm > 0 else 100; grid_color = (100, 100, 100, 80)
    for y_coord in range(grid_interval_pixels, h, grid_interval_pixels): cv2.line(overlay, (0, y_coord), (w, y_coord), grid_color, 1, cv2.LINE_AA)
    if lanes > 0:
        lane_width_pixels = w / lanes
        for i in range(1, lanes): cv2.line(overlay, (int(i * lane_width_pixels), 0), (int(i * lane_width_pixels), h), grid_color, 1, cv2.LINE_AA)
    return overlay

def alpha_blend(foreground: np.ndarray, background: np.ndarray) -> np.ndarray:
    if foreground.shape[:2] != background.shape[:2]: foreground = cv2.resize(foreground, (background.shape[1], background.shape[0]), interpolation=cv2.INTER_NEAREST)
    fg_b, fg_g, fg_r, fg_a = cv2.split(foreground); alpha = fg_a.astype(float) / 255.0
    fg_b_w = (fg_b * alpha).astype(background.dtype); fg_g_w = (fg_g * alpha).astype(background.dtype); fg_r_w = (fg_r * alpha).astype(background.dtype)
    bg_b, bg_g, bg_r = cv2.split(background); inv_alpha = 1.0 - alpha
    bg_b_w = (bg_b * inv_alpha).astype(background.dtype); bg_g_w = (bg_g * inv_alpha).astype(background.dtype); bg_r_w = (bg_r * inv_alpha).astype(background.dtype)
    return cv2.merge((cv2.add(fg_b_w, bg_b_w), cv2.add(fg_g_w, bg_g_w), cv2.add(fg_r_w, bg_r_w)))

def visualize_data(frame: Optional[np.ndarray], tracked_vehicles: Dict[int, Dict], traffic_metrics: Dict[str, Any], visualization_options: Set[str], config: Dict, feed_id: str = "") -> Optional[np.ndarray]:
    global cached_lane_overlay, cached_grid_overlay, overlay_cache_size
    if frame is None: return None
    try:
        vis_frame = frame.copy(); h, w = vis_frame.shape[:2]; current_size = (w, h)
        if overlay_cache_size != current_size: logger.debug(f"[{feed_id}] Frame size changed. Resetting viz overlays."); cached_lane_overlay=None; cached_grid_overlay=None; overlay_cache_size=current_size
        lane_cfg = config.get('lane_detection', {}); num_lanes = lane_cfg.get('num_lanes', 0); lane_width = w / num_lanes if num_lanes > 0 else w
        if "Grid Overlay" in visualization_options:
            if cached_grid_overlay is None: cached_grid_overlay = create_grid_overlay(vis_frame.shape, config)
            if cached_grid_overlay is not None: vis_frame = alpha_blend(cached_grid_overlay, vis_frame)
        if "Lane Density Overlay" in visualization_options and num_lanes > 0:
            density_per_lane = traffic_metrics.get('vehicles_per_lane', {})
            lane_overlay = create_lane_overlay(vis_frame.shape, num_lanes, lane_width, density_per_lane, config)
            vis_frame = alpha_blend(lane_overlay, vis_frame)
        if "Tracked Vehicles" in visualization_options or "Vehicle Data" in visualization_options:
            speed_limit = config.get('speed_limit', 60); color_normal=(0,255,0); color_warning=(0,255,255); color_speeding=(0,0,255)
            for veh_id, data in tracked_vehicles.items():
                bbox = data.get('bbox'); speed = data.get('speed',0.0); plate = data.get('license_plate','')
                class_name = TrafficMonitor.vehicle_type_map.get(data.get('class_id',-1),'?')
                if bbox:
                    x1,y1,x2,y2 = map(int,bbox); color = color_speeding if speed > speed_limit else (color_warning if speed > speed_limit*0.8 else color_normal)
                    if "Tracked Vehicles" in visualization_options: cv2.rectangle(vis_frame, (x1,y1), (x2,y2), color, 2)
                    if "Vehicle Data" in visualization_options:
                        lines=[f"ID:{veh_id}({class_name})",f"Spd:{speed:.1f}km/h"]; lh=15
                        if plate: lines.append(f"LP:{plate}")
                        text_y = y1-7 if y1-7 >= lh*len(lines) else y2+lh
                        for i,line_text in enumerate(lines): cv2.putText(vis_frame,line_text,(x1+5,text_y+i*lh),cv2.FONT_HERSHEY_SIMPLEX,0.4,color,1,cv2.LINE_AA)
        banner_h=25; banner_text = f"{time.strftime('%H:%M:%S')} | {feed_id} | Veh:{traffic_metrics.get('total_vehicles',0)} | AvgSpd:{traffic_metrics.get('average_speed_kmh',0.0):.1f}"
        if traffic_metrics.get('is_congested',False): banner_text += " | CONGESTED"
        cv2.rectangle(vis_frame,(0,0),(w,banner_h),(0,0,0,180),-1); cv2.putText(vis_frame,banner_text,(10,banner_h-8),cv2.FONT_HERSHEY_SIMPLEX,0.5,(255,255,255),1,cv2.LINE_AA)
        return vis_frame
    except Exception as e: logger.error(f"[{feed_id}] Viz error: {e}", exc_info=True); return frame

# --- LicensePlatePreprocessor ---
class LicensePlatePreprocessor:
    def __init__(self, config: Dict, perspective_matrix: Optional[np.ndarray] = None): # perspective_matrix not used
        self.config = config.get("ocr_engine", {})
        self.gemini_api_key = self.config.get("gemini_api_key")
        self.model = None
        if self.gemini_api_key:
            try:
                genai.configure(api_key=self.gemini_api_key)
                self.model = genai.GenerativeModel('gemini-pro-vision')
                logger.info("Gemini Pro Vision model initialized for OCR.")
            except Exception as e:
                logger.error(f"Failed to initialize Gemini Pro Vision model: {e}", exc_info=True)
                self.model = None
        else:
            logger.warning("Gemini API key not provided. Gemini OCR will not be available.")
        
        self.cool_down_secs = self.config.get("gemini_cool_down_secs", 60)
        self.last_api_error_time = 0
        
        self.morph_kernel = np.array(self.config.get("morph_kernel", [[1,1,1],[1,1,1],[1,1,1]]), dtype=np.uint8)
        self.sharpen_kernel = np.array(self.config.get("sharpen_kernel", [[-1,-1,-1],[-1,9,-1],[-1,-1,-1]]), dtype=np.float32)

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10), 
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((
            google_api_exceptions.PermissionDenied,     # Covers API key invalid, not enabled, permissions
            google_api_exceptions.ResourceExhausted,  # This generally covers rate limits / quota exceeded
            # genai.types.RateLimitExceededError, # <<<--- This was the problematic line
            # If google-generativeai has a more specific RateLimitExceededError, find its correct path.
            # For now, relying on google_api_exceptions.ResourceExhausted is safer.
            # Let's try to find specific genai errors if they exist and are different.
            # genai.types.DeadlineExceededError, # If this exists and is different from google_api_exceptions.DeadlineExceeded
            # genai.types.InternalServerError,   # If this exists
            # genai.types.ServiceUnavailableError, # If this exists
            # For now, using the google_api_core.exceptions for broader coverage:
            google_api_exceptions.DeadlineExceeded,
            google_api_exceptions.InternalServerError,
            google_api_exceptions.ServiceUnavailable,
            google_api_exceptions.Aborted,
            google_api_exceptions.Unknown,
            ConnectionError, 
            TimeoutError 
            # We will handle genai.types.BlockedPromptException and StopCandidateException inside the method
            # as they are usually not retryable in the same way (content safety).
        ))
    )
    def _call_gemini_ocr(self, image_roi: np.ndarray) -> str:
        if not self.model:
             logger.warning("Gemini model not available for _call_gemini_ocr.")
             return ""

        current_time = time.monotonic()
        if current_time - self.last_api_error_time < self.cool_down_secs:
            logger.info(f"Gemini API cool-down period active. Skipping OCR attempt. Wait {self.cool_down_secs - (current_time - self.last_api_error_time):.1f}s.")
            return ""

        logger.debug(f"Attempting Gemini OCR call for ROI of shape {image_roi.shape}")
        try:
            pil_image = Image.fromarray(cv2.cvtColor(image_roi, cv2.COLOR_BGR2RGB))
            img_byte_arr = io.BytesIO()
            pil_image.save(img_byte_arr, format='JPEG', quality=90)
            img_bytes = img_byte_arr.getvalue()

            image_part = {"mime_type": "image/jpeg", "data": img_bytes}
            prompt_parts = [
                image_part,
                "Identify and extract the license plate number from this image. Provide only the license plate characters, with no additional text or explanation. If multiple plates are visible, focus on the most prominent one. If no clear license plate is visible, respond with an empty string.",
            ]
            
            response = self.model.generate_content(prompt_parts)
            
            ocr_text = ""
            # Check response structure carefully based on actual Gemini API
            if response and hasattr(response, 'text'): # Common simple response
                ocr_text = response.text.strip()
            elif response.candidates and response.candidates[0].content and response.candidates[0].content.parts: # More complex structure
                ocr_text = response.candidates[0].content.parts[0].text.strip()
            
            ocr_text = ''.join(filter(str.isalnum, ocr_text)).upper()
            
            if ocr_text: logger.info(f"Gemini OCR Result: '{ocr_text}'")
            else: logger.debug("Gemini OCR: No plate found or empty result.")
            
            self.last_api_error_time = 0
            return ocr_text

        # Handle non-retryable (by tenacity decorator) GenAI safety exceptions first
        except (genai.types.BlockedPromptException, genai.types.StopCandidateException) as safety_error: # Assuming these paths are correct
            logger.warning(f"Gemini content safety issue: {safety_error}")
            self.last_api_error_time = time.monotonic()
            return ""
        # Handle API errors that tenacity should retry
        except (google_api_exceptions.PermissionDenied, 
                google_api_exceptions.ResourceExhausted, # General rate limit / quota
                google_api_exceptions.DeadlineExceeded,
                google_api_exceptions.InternalServerError,
                google_api_exceptions.ServiceUnavailable,
                google_api_exceptions.Aborted,
                google_api_exceptions.Unknown,
                ConnectionError, 
                TimeoutError) as retryable_error:
            logger.warning(f"Gemini API/network error (will be retried by tenacity): {type(retryable_error).__name__} - {retryable_error}")
            self.last_api_error_time = time.monotonic() # Set cool-down for after retries fail
            raise # Re-raise for tenacity to catch and retry
        except Exception as e:
            logger.error(f"Unexpected error during Gemini OCR call: {e}", exc_info=True)
            self.last_api_error_time = time.monotonic()
            return "" # Don't retry unknown errors by default here

    def _preprocess_for_tesseract(self, roi: np.ndarray) -> Optional[np.ndarray]:
        """Dedicated preprocessing for Tesseract."""
        if roi is None or roi.size == 0 or roi.shape[0] < 10 or roi.shape[1] < 10:
            logger.debug("ROI too small or empty for Tesseract preprocessing.")
            return None
        try:
            if len(roi.shape) == 3: gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            else: gray = roi.copy()
            
            # Experiment with different preprocessing steps for Tesseract
            # 1. Upscale for better OCR on small plates (optional, can be slow)
            # scale_factor = 2
            # gray = cv2.resize(gray, (0,0), fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_CUBIC)

            blurred = cv2.GaussianBlur(gray, (3, 3), 0)
            # Adaptive thresholding is often good
            thresh = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                           cv2.THRESH_BINARY_INV, 19, 9) # INV for white text on black
            # Or simple binary threshold if contrast is good
            # _, thresh = cv2.threshold(blurred, 120, 255, cv2.THRESH_BINARY_INV)

            # Morphological operations to clean up
            # kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3,3)) # Or use self.morph_kernel
            # opened = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, self.morph_kernel, iterations=1)
            # closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, self.morph_kernel, iterations=1)
            # For Tesseract, sometimes just dilation or erosion on binary inverted works
            # processed = cv2.dilate(thresh, self.morph_kernel, iterations=1) # Thicken text
            processed = thresh # Start with simple threshold for Tesseract

            return processed
        except Exception as e:
            logger.error(f"Error in _preprocess_for_tesseract: {e}", exc_info=True)
            return None

    def preprocess_and_ocr(self, roi: np.ndarray) -> str:
        """Preprocesses the ROI and then calls the appropriate OCR engine."""
        ocr_result = ""
        if roi is None or roi.size == 0: return ""

        # --- Prefer Gemini if available and configured ---
        if self.model and self.gemini_api_key:
            logger.debug("Attempting OCR using Gemini...")
            try:
                # Gemini prefers less pre-processed, original color images for its own internal processing.
                # The _call_gemini_ocr method is wrapped with @retry.
                ocr_result = self._call_gemini_ocr(roi) # Pass original ROI
            except RetryError as e: # Tenacity: All retries failed
                logger.error(f"Gemini OCR failed after all retries: {e.last_attempt.exception()}. ROI shape: {roi.shape}")
                ocr_result = "" # Ensure it's empty
            except Exception as e: # Should not happen if _call_gemini_ocr handles its exceptions
                logger.error(f"Unexpected error during Gemini OCR attempt sequence: {e}", exc_info=True)
                ocr_result = ""

            if ocr_result:
                 logger.info(f"Gemini OCR successful, result: '{ocr_result}'")
                 return ocr_result
            else:
                 logger.info("Gemini OCR did not yield a result or failed. Falling back to Tesseract if available.")
                 # Fall through to Tesseract

        # --- Fallback to Tesseract ---
        if not pytesseract: # Check if pytesseract module itself is available
            if not (self.model and self.gemini_api_key): # Only log if no OCR was even attempted
                 logger.warning("Pytesseract not available and Gemini not configured. No OCR will be performed.")
            return "" # Cannot proceed with Tesseract

        logger.debug("Attempting OCR using Tesseract...")
        processed_roi_for_tesseract = self._preprocess_for_tesseract(roi)
        if processed_roi_for_tesseract is not None:
            try:
                custom_config = r'--oem 3 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
                text = pytesseract.image_to_string(processed_roi_for_tesseract, config=custom_config, timeout=5) # Add timeout
                ocr_result = ''.join(filter(str.isalnum, text)).upper()
                if ocr_result: logger.info(f"Tesseract OCR Raw: '{text.strip()}', Processed: '{ocr_result}'")
                else: logger.debug("Tesseract OCR: No text found or empty result.")
            except RuntimeError as e: # Catches Tesseract not found or timeout
                logger.error(f"Tesseract runtime error (e.g., Tesseract not installed/found, or timeout): {e}", exc_info=False) # exc_info False as message is usually clear
                ocr_result = ""
            except Exception as e:
                logger.error(f"Tesseract OCR unexpected error: {e}", exc_info=True)
                ocr_result = ""
        else:
            logger.warning("Preprocessing for Tesseract failed, cannot perform Tesseract OCR.")
        
        return ocr_result

# --- DatabaseManager (Simplified for SQLite) ---
class DatabaseManager:
    def __init__(self, config: Dict):
        # SQLite configuration
        self.db_config = config.get("database", {})
        self.db_path = self.db_config.get("db_path")
        if not self.db_path: raise ConfigError("SQLite database path ('db_path') not found in config.")
        self.chunk_size = self.db_config.get("chunk_size", 100)
        self.lock = threading.RLock()
        logger.info(f"Initializing DatabaseManager with SQLite db path: {self.db_path}")
        self._initialize_sqlite_database()

        # MongoDB configuration
        self.mongo_config = config.get("mongodb", {})
        self.mongo_uri = self.mongo_config.get("uri")
        self.mongo_db_name = self.mongo_config.get("database_name")
        self.raw_traffic_collection_name = self.mongo_config.get("raw_traffic_collection", "raw_traffic_data")
        
        self.mongo_client: Optional[MongoClient] = None
        self.mongo_db: Optional[MongoDatabase] = None

        if self.mongo_uri and self.mongo_db_name:
            logger.info(f"MongoDB URI found. Initializing MongoDB connection to {self.mongo_db_name}...")
            self._initialize_mongodb()
        else:
            logger.warning("MongoDB URI or database_name not found in config. MongoDB will not be initialized.")

    def _get_sqlite_connection(self) -> sqlite3.Connection: # Renamed for clarity
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0); conn.row_factory = sqlite3.Row
            try: conn.execute("PRAGMA journal_mode=WAL;"); conn.execute("PRAGMA synchronous=NORMAL;")
            except sqlite3.Error as e: logger.warning(f"Could not set WAL mode on {self.db_path}: {e}")
            return conn
        except sqlite3.Error as e: logger.error(f"Failed to connect to DB {self.db_path}: {e}", exc_info=True); raise DatabaseError(f"DB connect fail: {e}") from e

    def _initialize_sqlite_database(self): # Renamed for clarity
        logger.info(f"Initializing SQLite DB schema at {self.db_path}...")
        try:
            with self._get_sqlite_connection() as conn: self._create_sqlite_tables(conn.cursor()) # Renamed for clarity
            logger.info("SQLite DB schema initialization check complete.")
        except sqlite3.Error as e: logger.error(f"DB init error: {e}", exc_info=True); raise DatabaseError(f"DB schema init fail: {e}") from e
        except Exception as e: logger.error(f"Unexpected DB init error: {e}", exc_info=True); raise DatabaseError(f"Unexpected DB init error: {e}") from e

    def _create_sqlite_tables(self, cursor: sqlite3.Cursor): # Renamed for clarity
        cursor.execute('''CREATE TABLE IF NOT EXISTS vehicle_tracks (
                feed_id TEXT NOT NULL, track_id INTEGER NOT NULL, timestamp REAL NOT NULL, class_id INTEGER, confidence REAL,
                bbox_x1 REAL, bbox_y1 REAL, bbox_x2 REAL, bbox_y2 REAL, center_x REAL, center_y REAL, speed REAL,
                acceleration REAL, lane INTEGER, direction REAL, license_plate TEXT, ocr_confidence REAL, flags TEXT,
                PRIMARY KEY (feed_id, track_id, timestamp))''')
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vt_timestamp ON vehicle_tracks(timestamp DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vt_feed_track ON vehicle_tracks(feed_id, track_id);")
        cursor.execute('''CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL DEFAULT (unixepoch('now', 'subsec')),
                severity TEXT NOT NULL CHECK(severity IN ('INFO', 'WARNING', 'CRITICAL')), feed_id TEXT NOT NULL,
                message TEXT NOT NULL, details TEXT, acknowledged INTEGER DEFAULT 0 NOT NULL CHECK(acknowledged IN (0, 1)))''')
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_feed_severity ON alerts(feed_id, severity);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_acknowledged ON alerts(acknowledged);")
        
        # Raw traffic data will now primarily go to MongoDB if configured.
        # This SQLite table can be kept for fallback or removed if MongoDB is mandatory.
        cursor.execute('''CREATE TABLE IF NOT EXISTS raw_traffic_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            timestamp TEXT NOT NULL, 
            sensor_id TEXT NOT NULL, 
            latitude REAL NOT NULL, 
            longitude REAL NOT NULL, 
            speed REAL, 
            occupancy REAL, 
            vehicle_count INTEGER
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS processed_traffic_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            segment_id TEXT NOT NULL, 
            timestamp TEXT NOT NULL, 
            congestion_level REAL NOT NULL
        )''')
        logger.debug("SQLite DB table creation check finished.")

    def _initialize_mongodb(self):
        try:
            self.mongo_client = MongoClient(self.mongo_uri, serverSelectionTimeoutMS=5000) # Timeout for connection
            # The ismaster command is cheap and does not require auth.
            self.mongo_client.admin.command('ismaster') # Verify connection
            self.mongo_db = self.mongo_client[self.mongo_db_name]
            logger.info(f"Successfully connected to MongoDB server. Database: '{self.mongo_db_name}'")
            # Optionally, create indexes here if needed for MongoDB collections, e.g.:
            # self.mongo_db[self.raw_traffic_collection_name].create_index([("timestamp", -1), ("sensor_id", 1)], background=True)
        except ConnectionFailure as e:
            logger.error(f"MongoDB connection failed to {self.mongo_uri}: {e}", exc_info=True)
            self.mongo_client = None
            self.mongo_db = None
            # Depending on policy, could raise DatabaseError or ConfigError
        except MongoConfigurationError as e:
            logger.error(f"MongoDB configuration error for {self.mongo_uri}: {e}", exc_info=True)
            self.mongo_client = None
            self.mongo_db = None
        except Exception as e:
            logger.error(f"An unexpected error occurred during MongoDB initialization for {self.mongo_uri}: {e}", exc_info=True)
            self.mongo_client = None
            self.mongo_db = None

    db_write_retry_decorator = retry(wait=wait_exponential(multiplier=0.2,min=0.2,max=3), stop=stop_after_attempt(4), retry=retry_if_exception_type(sqlite3.OperationalError))

    @db_write_retry_decorator
    def save_vehicle_data(self, vd: Dict) -> bool:
        sql = '''INSERT OR REPLACE INTO vehicle_tracks (feed_id,track_id,timestamp,class_id,confidence,bbox_x1,bbox_y1,bbox_x2,bbox_y2,center_x,center_y,speed,acceleration,lane,direction,license_plate,ocr_confidence,flags) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'''
        try:
            bbox=vd.get('bbox',[None]*4); center=vd.get('center',[None]*2); flags_str=','.join(sorted(list(vd.get('flags',set()))))
            params=(vd.get('feed_id','unknown'),vd.get('track_id'),vd.get('timestamp',time.time()),vd.get('class_id'),vd.get('confidence'),bbox[0],bbox[1],bbox[2],bbox[3],center[0],center[1],vd.get('speed'),vd.get('acceleration'),vd.get('lane'),vd.get('direction'),vd.get('license_plate'),vd.get('ocr_confidence'),flags_str)
            with self.lock:
                with self._get_sqlite_connection() as conn: conn.execute(sql, params)
            logger.debug(f"Saved track: Feed={params[0]},Track={params[1]},Time={params[2]:.2f}")
            return True
        except RetryError as e: logger.error(f"DB save_vehicle_data failed retries: {e}. TrackID: {vd.get('track_id')}"); return False
        except sqlite3.Error as e:
            logger.error(f"DB error saving vehicle: {e} - TrackID: {vd.get('track_id')}", exc_info=True)
            if not isinstance(e, sqlite3.OperationalError): raise DatabaseError(f"Failed save vehicle: {e}") from e
            return False
        except Exception as e: logger.error(f"Unexpected error saving vehicle: {e} - TrackID: {vd.get('track_id')}", exc_info=True); raise DatabaseError(f"Unexpected save vehicle: {e}") from e

    @db_write_retry_decorator
    def save_vehicle_data_batch(self, data_list: List[Dict]) -> bool:
        if not data_list: return True
        sql = '''INSERT OR REPLACE INTO vehicle_tracks (feed_id,track_id,timestamp,class_id,confidence,bbox_x1,bbox_y1,bbox_x2,bbox_y2,center_x,center_y,speed,acceleration,lane,direction,license_plate,ocr_confidence,flags) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'''
        prepared = []
        try:
            for vd in data_list:
                bbox=vd.get('bbox',[None]*4); center=vd.get('center',[None]*2); flags_str=','.join(sorted(list(vd.get('flags',set()))))
                prepared.append((vd.get('feed_id','unknown'),vd.get('track_id'),vd.get('timestamp',time.time()),vd.get('class_id'),vd.get('confidence'),bbox[0],bbox[1],bbox[2],bbox[3],center[0],center[1],vd.get('speed'),vd.get('acceleration'),vd.get('lane'),vd.get('direction'),vd.get('license_plate'),vd.get('ocr_confidence'),flags_str))
            if not prepared: return True
            with self.lock:
                with self._get_sqlite_connection() as conn: conn.executemany(sql, prepared)
            logger.debug(f"Saved batch of {len(prepared)} vehicle records.")
            return True
        except RetryError as e: logger.error(f"DB save_vehicle_data_batch failed retries: {e}."); return False
        except sqlite3.Error as e:
            logger.error(f"DB error saving vehicle batch: {e}", exc_info=True)
            if not isinstance(e, sqlite3.OperationalError): raise DatabaseError(f"Failed save vehicle batch: {e}") from e
            return False
        except Exception as e: logger.error(f"Unexpected error saving vehicle batch: {e}", exc_info=True); raise DatabaseError(f"Unexpected save vehicle batch: {e}") from e

    def get_recent_tracks(self, feed_id: Optional[str] = None, limit: int = 100) -> List[Dict]:
        try:
            with self._get_sqlite_connection() as conn:
                cursor=conn.cursor()
                if feed_id: cursor.execute("SELECT * FROM vehicle_tracks WHERE feed_id=? ORDER BY timestamp DESC LIMIT ?", (feed_id,limit))
                else: cursor.execute("SELECT * FROM vehicle_tracks ORDER BY timestamp DESC LIMIT ?", (limit,))
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e: logger.error(f"DB error get recent tracks (feed={feed_id}): {e}", exc_info=True); return []

    def get_track_history(self, feed_id: str, track_id: int, limit: int = 50) -> List[Dict]:
         try:
            with self._get_sqlite_connection() as conn:
                cursor=conn.cursor(); cursor.execute("SELECT * FROM vehicle_tracks WHERE feed_id=? AND track_id=? ORDER BY timestamp DESC LIMIT ?", (feed_id,track_id,limit))
                return [dict(row) for row in reversed(cursor.fetchall())]
         except sqlite3.Error as e: logger.error(f"DB error get track history (feed={feed_id},track={track_id}): {e}", exc_info=True); return []

    @lru_cache(maxsize=4)
    def get_vehicle_stats(self, time_window_secs: int = 300) -> Dict:
        try:
            with self._get_sqlite_connection() as conn:
                cursor=conn.cursor(); min_ts = time.time()-time_window_secs
                cursor.execute("SELECT COUNT(*) as total_vehicles, AVG(speed) as average_speed_kmh, SUM(CASE WHEN speed < ? THEN 1 ELSE 0 END) as stopped_vehicles FROM vehicle_tracks WHERE timestamp > ?", (DEFAULT_CONFIG['stopped_speed_threshold_kmh'], min_ts))
                res=cursor.fetchone(); stats=dict(res) if res else {'total_vehicles':0,'average_speed_kmh':0.0,'stopped_vehicles':0}
                stats['total_vehicles']=stats.get('total_vehicles') or 0; stats['average_speed_kmh']=stats.get('average_speed_kmh') or 0.0; stats['stopped_vehicles']=stats.get('stopped_vehicles') or 0
                return stats
        except sqlite3.Error as e: logger.error(f"DB error get vehicle stats: {e}", exc_info=True); return {}

    @lru_cache(maxsize=4)
    def get_vehicle_counts_by_type(self, time_window_secs: int = 300) -> Dict[str, int]:
        try:
            with self._get_sqlite_connection() as conn:
                cursor=conn.cursor(); min_ts = time.time()-time_window_secs
                cursor.execute("WITH LT AS (SELECT feed_id,track_id,class_id,MAX(timestamp) as mt FROM vehicle_tracks WHERE timestamp > ? GROUP BY feed_id,track_id) SELECT vt.class_id,COUNT(DISTINCT lt.track_id) as count FROM vehicle_tracks vt JOIN LT ON vt.feed_id=LT.feed_id AND vt.track_id=LT.track_id AND vt.timestamp=LT.mt GROUP BY vt.class_id", (min_ts,))
                results=cursor.fetchall(); type_map=TrafficMonitor.vehicle_type_map; counts={name:0 for name in type_map.values()}; counts['unknown']=0
                for row in results: counts[type_map.get(row['class_id'],'unknown')]=row['count']
                return counts
        except sqlite3.Error as e: logger.error(f"DB error get vehicle counts by type: {e}", exc_info=True); return {}

    def get_alerts_filtered(self, filters: Dict, limit: int = 100, offset: int = 0) -> List[Dict]:
        try:
            with self._get_sqlite_connection() as conn:
                cursor=conn.cursor(); base_q="SELECT id,timestamp,severity,feed_id,message,details,acknowledged FROM alerts WHERE 1=1"; params=[]; conds=[]
                allowed={"severity","feed_id","acknowledged"}
                for k,v in filters.items():
                    if k in allowed: conds.append(f"{k}=?"); params.append(1 if v else 0 if k=='acknowledged' else v)
                    elif k=="search" and isinstance(v,str) and v.strip(): conds.append("message LIKE ?"); params.append(f"%{v.strip()}%")
                    elif k=="start_time" and isinstance(v,(int,float)): conds.append("timestamp >= ?"); params.append(v)
                    elif k=="end_time" and isinstance(v,(int,float)): conds.append("timestamp <= ?"); params.append(v)
                if conds: base_q += " AND " + " AND ".join(conds)
                q = f"{base_q} ORDER BY timestamp DESC LIMIT ? OFFSET ?"; params.extend([limit,offset])
                cursor.execute(q,params); return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e: logger.error(f"DB error get_alerts_filtered: {e}", exc_info=True); return []

    def _execute_get_alerts_filtered(self, filters: Dict, limit: int, offset: int) -> List[Dict]:
        # Internal synchronous method for get_alerts_filtered
        base_q = "SELECT id, timestamp, severity, feed_id, message, details, acknowledged FROM alerts WHERE 1=1"
        params = []
        conds = []

        allowed_exact_match = {"feed_id"} # acknowledged and severity are handled separately

        if filters.get("acknowledged") is not None: # Handles True or False
            conds.append(f"acknowledged = ?")
            params.append(1 if filters["acknowledged"] else 0)

        if filters.get("severity"): # Single severity
            conds.append(f"severity = ?")
            params.append(filters["severity"])

        if filters.get("severity_in") and isinstance(filters["severity_in"], list) and len(filters["severity_in"]) > 0:
            placeholders = ", ".join("?" for _ in filters["severity_in"])
            conds.append(f"severity IN ({placeholders})")
            params.extend(filters["severity_in"])

        for k, v in filters.items():
            if k in allowed_exact_match and v is not None:
                conds.append(f"{k} = ?")
                params.append(v)
            elif k == "search" and isinstance(v, str) and v.strip():
                conds.append("message LIKE ?")
                params.append(f"%{v.strip()}%")
            elif k == "start_time" and isinstance(v, (int, float)):
                conds.append("timestamp >= ?")
                params.append(v)
            elif k == "end_time" and isinstance(v, (int, float)):
                conds.append("timestamp <= ?")
                params.append(v)

        if conds:
            base_q += " AND " + " AND ".join(conds)

        query = f"{base_q} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_sqlite_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    async def get_alerts_filtered(self, filters: Dict, limit: int = 100, offset: int = 0) -> List[Dict]:
        try:
            # Run the synchronous DB operation in a separate thread
            return await asyncio.to_thread(self._execute_get_alerts_filtered, filters, limit, offset)
        except sqlite3.Error as e:
            logger.error(f"DB error get_alerts_filtered: {e}", exc_info=True)
            return []
        except Exception as e: # Catch any other unexpected errors from the thread
            logger.error(f"Unexpected error in get_alerts_filtered via thread: {e}", exc_info=True)
            return []

    def _execute_count_alerts_filtered(self, filters: Dict) -> int:
        # Internal synchronous method for count_alerts_filtered
        base_q = "SELECT COUNT(*) FROM alerts WHERE 1=1"
        params = []
        conds = []

        allowed_exact_match = {"feed_id"}

        if filters.get("acknowledged") is not None:
            conds.append(f"acknowledged = ?")
            params.append(1 if filters["acknowledged"] else 0)

        if filters.get("severity"):
            conds.append(f"severity = ?")
            params.append(filters["severity"])

        if filters.get("severity_in") and isinstance(filters["severity_in"], list) and len(filters["severity_in"]) > 0:
            placeholders = ", ".join("?" for _ in filters["severity_in"])
            conds.append(f"severity IN ({placeholders})")
            params.extend(filters["severity_in"])

        for k, v in filters.items():
            if k in allowed_exact_match and v is not None:
                conds.append(f"{k} = ?")
                params.append(v)
            elif k == "search" and isinstance(v, str) and v.strip():
                conds.append("message LIKE ?")
                params.append(f"%{v.strip()}%")
            elif k == "start_time" and isinstance(v, (int, float)):
                conds.append("timestamp >= ?")
                params.append(v)
            elif k == "end_time" and isinstance(v, (int, float)):
                conds.append("timestamp <= ?")
                params.append(v)

        if conds:
            base_q += " AND " + " AND ".join(conds)

        with self._get_sqlite_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(base_q, params)
            count_result = cursor.fetchone()
            return count_result[0] if count_result else 0

    async def count_alerts_filtered(self, filters: Dict) -> int:
        try:
            return await asyncio.to_thread(self._execute_count_alerts_filtered, filters)
        except sqlite3.Error as e:
            logger.error(f"DB error count_alerts_filtered: {e}", exc_info=True)
            return 0
        except Exception as e:
            logger.error(f"Unexpected error in count_alerts_filtered via thread: {e}", exc_info=True)
            return 0

    @db_write_retry_decorator
    def save_alert(self, severity: str, feed_id: str, message: str, details: Optional[str]=None) -> bool:
        if severity not in ('INFO','WARNING','CRITICAL'): logger.error(f"Invalid alert sev: {severity}"); return False
        sql='INSERT INTO alerts (severity,feed_id,message,details) VALUES (?,?,?,?)'
        try:
            params=(severity,feed_id,message,details)
            with self.lock:
                with self._get_sqlite_connection() as conn: conn.execute(sql,params)
            logger.info(f"Saved alert: Sev={severity},Feed={feed_id},Msg='{message[:60]}...'")
            return True
        except RetryError as e: logger.error(f"DB save_alert failed retries: {e}."); return False
        except sqlite3.Error as e:
            logger.error(f"DB error saving alert: {e}", exc_info=True)
            if not isinstance(e, sqlite3.OperationalError): raise DatabaseError(f"Failed save alert: {e}") from e
            return False
        except Exception as e: logger.error(f"Unexpected error saving alert: {e}", exc_info=True); raise DatabaseError(f"Unexpected save alert: {e}") from e

    # Applying retry decorator to the async wrapper.
    # If the underlying sync method raises an OperationalError, it will be caught by the wrapper's
    # exception handling, and then the retry decorator on the async method will handle retrying the to_thread call.
    @retry(wait=wait_exponential(multiplier=0.2,min=0.2,max=3), stop=stop_after_attempt(4), retry=retry_if_exception_type(sqlite3.OperationalError))
    async def acknowledge_alert(self, alert_id: int, acknowledge: bool = True) -> bool:
        try:
            return await asyncio.to_thread(self._execute_acknowledge_alert, alert_id, acknowledge)
        except sqlite3.Error as e: # Catch errors from the thread
            logger.error(f"DB error ack alert {alert_id} (async wrapper): {e}", exc_info=True)
            if not isinstance(e, sqlite3.OperationalError): raise DatabaseError(f"Failed ack alert: {e}") from e
            return False # Should be caught by retry if OperationalError
        except Exception as e:
            logger.error(f"Unexpected error ack alert {alert_id} (async wrapper): {e}", exc_info=True)
            raise DatabaseError(f"Unexpected ack alert: {e}") from e

    def _execute_acknowledge_alert(self, alert_id: int, acknowledge: bool) -> bool:
        # Internal synchronous method
        sql="UPDATE alerts SET acknowledged = ? WHERE id = ?"
        ack_val = 1 if acknowledge else 0
        with self.lock:
            with self._get_sqlite_connection() as conn:
                cursor=conn.cursor(); cursor.execute(sql,(ack_val,alert_id)); conn.commit()
                if cursor.rowcount==0:
                    logger.warning(f"Alert ID {alert_id} not found for ack."); return False
        logger.info(f"Alert ID {alert_id} ack status set to {acknowledge}.")
        return True

    @retry(wait=wait_exponential(multiplier=0.2,min=0.2,max=3), stop=stop_after_attempt(4), retry=retry_if_exception_type(sqlite3.OperationalError))
    async def delete_alert(self, alert_id: int) -> bool:
        try:
            return await asyncio.to_thread(self._execute_delete_alert, alert_id)
        except sqlite3.Error as e:
            logger.error(f"DB error deleting alert ID {alert_id} (async wrapper): {e}", exc_info=True)
            if not isinstance(e, sqlite3.OperationalError): raise DatabaseError(f"Failed to delete alert ID {alert_id}: {e}") from e
            return False
        except Exception as e:
            logger.error(f"Unexpected error deleting alert ID {alert_id} (async wrapper): {e}", exc_info=True)
            raise DatabaseError(f"Unexpected error deleting alert ID {alert_id}: {e}") from e

    def _execute_delete_alert(self, alert_id: int) -> bool:
        # Internal synchronous method
        sql = "DELETE FROM alerts WHERE id = ?"
        with self.lock:
            with self._get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, (alert_id,))
                conn.commit()
                if cursor.rowcount > 0:
                    logger.info(f"Alert ID {alert_id} deleted successfully.")
                    return True
                else:
                    logger.warning(f"Alert ID {alert_id} not found for deletion.")
                    return False

    async def get_alert_by_id(self, alert_id: int) -> Optional[Dict]:
        try:
            return await asyncio.to_thread(self._execute_get_alert_by_id, alert_id)
        except sqlite3.Error as e:
            logger.error(f"DB error fetching alert ID {alert_id} (async wrapper): {e}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching alert ID {alert_id} (async wrapper): {e}", exc_info=True)
            raise DatabaseError(f"Unexpected error fetching alert ID {alert_id}: {e}") from e

    def _execute_get_alert_by_id(self, alert_id: int) -> Optional[Dict]:
        # Internal synchronous method
        sql = "SELECT id, timestamp, severity, feed_id, message, details, acknowledged FROM alerts WHERE id = ?"
        with self._get_sqlite_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (alert_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            else:
                logger.info(f"Alert ID {alert_id} not found.")
                return None

    @retry(wait=wait_exponential(multiplier=0.2,min=0.2,max=3), stop=stop_after_attempt(3), retry=retry_if_exception_type(Exception)) # Generic retry for Mongo
    def save_raw_traffic_data_mongo(self, data: Dict) -> bool:
        if not self.mongo_db:
            logger.error("MongoDB not initialized. Cannot save raw_traffic_data.")
            # Optionally, fall back to SQLite or raise an error
            # For now, trying SQLite if MongoDB is not available
            # return self.save_raw_traffic_data_sqlite(data) # Assuming such a method exists
            raise DatabaseError("MongoDB not available for saving traffic data.")
        
        try:
            collection = self.mongo_db[self.raw_traffic_collection_name]
            result = collection.insert_one(data)
            logger.debug(f"Saved raw traffic data to MongoDB with id: {result.inserted_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to save raw traffic data to MongoDB: {e}", exc_info=True)
            # Re-raise the exception to be caught by the retry decorator or calling function
            raise DatabaseError(f"Failed to save to MongoDB: {e}") from e

    def get_raw_traffic_data_mongo(self, query: Dict, limit: int = 1000, sort_criteria: Optional[List[Tuple[str, int]]] = None) -> List[Dict]:
        if not self.mongo_db:
            logger.warning("MongoDB not initialized. Cannot get raw_traffic_data.")
            return []
        try:
            collection = self.mongo_db[self.raw_traffic_collection_name]
            cursor = collection.find(query).limit(limit)
            if sort_criteria:
                cursor = cursor.sort(sort_criteria)
            return list(cursor)
        except Exception as e:
            logger.error(f"Failed to retrieve raw_traffic_data from MongoDB: {e}", exc_info=True)
            return []

    def close(self):
        with self.lock: # Protect shared resource access
            # Close SQLite (if it was ever initialized through its own connection pooling)
            # The current _get_sqlite_connection creates a new conn each time, so no global conn to close here.
            # If we had a self.sqlite_conn, we'd close it.
            logger.info("DatabaseManager close called. SQLite connections are per-call.")

            # Close MongoDB client
            if self.mongo_client:
                try:
                    self.mongo_client.close()
                    logger.info("MongoDB client connection closed.")
                except Exception as e:
                    logger.error(f"Error closing MongoDB client: {e}", exc_info=True)
                finally:
                    self.mongo_client = None
                    self.mongo_db = None
            else:
                logger.info("MongoDB client was not initialized or already closed.")

# --- Example Usage (Optional: for testing utils directly) ---
if __name__ == "__main__":
    print("Running utils.py directly (for testing purposes)...")
    try:
        # Adjust this path if your config.yaml is elsewhere relative to this utils.py
        # Assuming project root is two levels up from app/utils/
        project_root_dir = Path(__file__).resolve().parent.parent.parent
        config_file_path = project_root_dir / "config.yaml"
        
        if not config_file_path.exists():
            # Fallback for common alternative structures or running from different CWD
            alt_config_path1 = Path("config.yaml") # CWD is project root
            alt_config_path2 = Path("../../../config.yaml") # CWD is utils
            if alt_config_path1.exists(): config_file_path = alt_config_path1
            elif alt_config_path2.exists(): config_file_path = alt_config_path2.resolve()

        print(f"Attempting to load config from: {config_file_path}")
        config_data = load_config(str(config_file_path))
        
        print("\n--- Configuration Loaded ---")
        print(f"Database path: {config_data.get('database',{}).get('db_path')}")
        print(f"Model path: {config_data.get('vehicle_detection',{}).get('model_path')}")
        gemini_key = config_data.get('ocr_engine',{}).get('gemini_api_key')
        print(f"Gemini Key Set: {'Yes' if gemini_key and gemini_key.strip() else 'No'}")

        print("\n--- Testing DatabaseManager ---")
        db_manager = DatabaseManager(config_data)
        # success = db_manager.save_alert("INFO", "TestFeedCLI", "CLI test alert.", '{"details": "test"}')
        # print(f"Save alert success: {success}")
        alerts = db_manager.get_alerts_filtered({}, limit=3)
        print(f"Retrieved {len(alerts)} recent alerts:")
        for alert_item in alerts: print(f"  ID:{alert_item['id']} Time:{time.strftime('%H:%M:%S', time.localtime(alert_item['timestamp']))} Sev:{alert_item['severity']}")
        db_manager.close()

        if gemini_key and gemini_key.strip():
            print("\n--- Testing LicensePlatePreprocessor (Gemini) ---")
            lp_preprocessor = LicensePlatePreprocessor(config_data)
            # Create a dummy small black image for testing the API call structure (won't find a plate)
            dummy_roi = np.zeros((100, 300, 3), dtype=np.uint8) # HxWxC (BGR)
            cv2.putText(dummy_roi, "TEST", (50, 70), cv2.FONT_HERSHEY_SIMPLEX, 2, (255,255,255), 3)
            print("Attempting OCR on a dummy image with Gemini (expect empty or error if key invalid)...")
            ocr_text_gemini = lp_preprocessor.preprocess_and_ocr(dummy_roi)
            print(f"Gemini Dummy OCR Result: '{ocr_text_gemini}'")
        else:
            print("\n--- Skipping Gemini OCR test (API key not configured) ---")
            print("--- Testing LicensePlatePreprocessor (Tesseract Fallback) ---")
            lp_preprocessor = LicensePlatePreprocessor(config_data) # Init without Gemini
            dummy_roi_tess = np.zeros((100, 300, 3), dtype=np.uint8)
            cv2.rectangle(dummy_roi_tess, (10,10), (290,90), (50,50,50), -1) # Dark background
            cv2.putText(dummy_roi_tess, "TST123", (40, 70), cv2.FONT_HERSHEY_SIMPLEX, 2, (220,220,220), 5, cv2.LINE_AA) # Light text
            print("Attempting OCR on a dummy image with Tesseract (expect TST123 or similar if Tesseract installed)...")
            ocr_text_tesseract = lp_preprocessor.preprocess_and_ocr(dummy_roi_tess)
            print(f"Tesseract Dummy OCR Result: '{ocr_text_tesseract}'")


    except ConfigError as ce: print(f"\n--- CONFIGURATION ERROR ---\n{ce}"); sys.exit(1)
    except DatabaseError as dbe: print(f"\n--- DATABASE ERROR ---\n{dbe}"); sys.exit(1)
    except Exception as exc: print(f"\n--- UNEXPECTED ERROR IN MAIN TEST BLOCK ---\n{exc}"); logger.error("Main test block error", exc_info=True); sys.exit(1)
    print("\n--- Utils.py Tests Finished ---")