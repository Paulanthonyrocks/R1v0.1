
import logging
import logging.config
import os
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict

# Assuming load_config is defined in utils.utils
from app.utils.config import load_config, ConfigError
from app.utils.secrets_manager import secrets

logger = logging.getLogger("app.config")

class LoggingConfig(BaseModel):
    version: int = 1
    disable_existing_loggers: bool = False
    formatters: Dict[str, Any] = {}
    handlers: Dict[str, Any] = {}
    loggers: Dict[str, Any] = {}

class DatabaseConfig(BaseModel):
    db_path: str = "backend/data/vehicle_data.db"
    connection_timeout: int = 30
    query_timeout: int = 60
    cache_size: int = 128
    chunk_size: int = 100
    schema_: Dict[str, str] = Field(..., alias='schema')
    timescaledb: Dict[str, Any] = {}
    model_config = ConfigDict(populate_by_name=True)

class RedisConfig(BaseModel):
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None
    enabled: bool = True

class MongoDBConfig(BaseModel):
    uri: str = "mongodb://localhost:27017/"
    database_name: str = "traffic_hub"
    enabled: bool = True

class PerformanceConfig(BaseModel):
    gpu_acceleration: bool = True
    video_gpu_acceleration: bool = False  # For HW accelerated decoding/encoding
    image_gpu_acceleration: bool = False  # For GPU-based image ops (resizing, etc.)
    inference_pool_size: int = 2
    memory_limit_percent: int = 80
    max_concurrent_feeds: int = 10
    auto_optimize: bool = True
    batch_size: int = 1
    cpu_limit_percent: int = 95
    cpu_threshold_for_skip_increase: int = 85
    inference_timeout: float = 0.05
    long_sleep_after_increase: int = 10
    max_global_skip_factor: float = 2.0
    min_global_skip_factor: float = 1.0
    queue_fullness_threshold_for_skip_increase: float = 0.8
    queue_max_size: int = 500
    skip_factor_adjustment_interval: float = 360.0
    skip_factor_decrease_step: float = 0.05
    skip_factor_increase_step: float = 0.1
    use_shared_memory: bool = True
    use_shm: bool = True


class WebSocketRateLimitConfig(BaseModel):
    rate: float = 5.0  # tokens per second
    capacity: float = 10.0  # max tokens

class AppConfig(BaseSettings):
    project_root_dir: str = str(Path(__file__).resolve().parent.parent.parent)
    data_dir: str = os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))
    
    logging: Dict[str, Any] = {}
    database: DatabaseConfig = DatabaseConfig(schema={})
    redis: RedisConfig = RedisConfig()
    mongodb: MongoDBConfig = MongoDBConfig()
    performance: PerformanceConfig = PerformanceConfig()
    websocket: Dict[str, Any] = {
        "max_connections": 1000,
        "rate_limit": {"rate": 5.0, "capacity": 10.0}
    }
    firebase_admin: Dict[str, Any] = {"auth_enabled": False}
    video_input: Dict[str, Any] = {"sample_videos": []}
    video_output: Dict[str, Any] = {"enabled": False, "output_directory": "recordings", "fps": 10}
    reid: Dict[str, Any] = {"similarity_threshold": 0.85, "persistence_path": "reid_gallery.pkl"}
    prediction_scheduler: Dict[str, Any] = {"enabled": True}
    auto_start_processing: bool = False
    file_watcher: Dict[str, Any] = {"enabled": False}
    post_startup_processing: Dict[str, Any] = {"enabled": False}
    cors: Dict[str, Any] = {"allowed_origins": []}
    
    # New settings for dynamic paths
    feeds_config_path: str = "feeds_config.json"
    snapshots_dir: str = "snapshots"
    accel_threshold_mps2: float = 0.5
    advanced_analytics: Dict[str, Any] = {}
    analytics_service: Dict[str, Any] = {}
    anomaly_detection: Dict[str, Any] = {}
    behavior_analysis: Dict[str, Any] = {}
    calibration: Dict[str, Any] = {}
    feed_manager: Dict[str, Any] = {}
    fps: int = 15
    github: Dict[str, Any] = {}
    incident_detection: Dict[str, Any] = {}
    inference: Dict[str, Any] = {}
    ingestion: Dict[str, Any] = {}
    interface: Dict[str, Any] = {}
    kalman_filter_params: Dict[str, Any] = {}
    lane_detection: Dict[str, Any] = {}
    llm: Dict[str, Any] = {}
    log_files: Dict[str, Any] = {}
    ml_model: Dict[str, Any] = {}
    ocr_engine: Dict[str, Any] = {}
    openvino: Dict[str, Any] = {}
    pavement_analysis: Dict[str, Any] = {}
    personalized_routing: Dict[str, Any] = {}
    perspective_calibration: Dict[str, Any] = {}
    pixels_per_meter: int = 30
    queue_log_interval: float = 120.0
    roi_processing: Dict[str, Any] = {}
    route_optimization: Dict[str, Any] = {}
    simulation: Dict[str, Any] = {}
    speed_limit: int = 60
    stopped_speed_threshold_kmh: int = 5
    storage: Dict[str, Any] = {}
    tracking: Dict[str, Any] = {}
    traffic_predictor_model_path: str = ""
    v2x: Dict[str, Any] = {}
    vehicle_detection: Dict[str, Any] = {}
    video_processing: Dict[str, Any] = {}
    vis_options_default: List[str] = []
    weather_service: Dict[str, Any] = {}
    
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="allow",
        populate_by_name=True
    )


def _resolve_paths(config: AppConfig) -> None:
    """
    Resolves relative paths in the configuration to absolute paths based on the project root.
    Ensures necessary directories exist.
    """
    root_dir = Path(config.project_root_dir).resolve()
    
    # 1. Resolve data_dir (absolute path)
    data_path = Path(config.data_dir).resolve()
    config.data_dir = str(data_path)
    data_path.mkdir(parents=True, exist_ok=True)
    
    # 2. Resolve database path (relative to project root)
    db_p = Path(config.database.db_path)
    if not db_p.is_absolute():
        config.database.db_path = str((root_dir / db_p).resolve())
    
    # 3. Resolve other paths relative to project root
    paths_to_resolve = {
        "video_output_dir": (config.video_output.get("output_directory"), "video_output"),
        "reid_path": (config.reid.get("persistence_path"), "reid"),
        "feeds_path": (config.feeds_config_path, None),
        "snapshots_path": (config.snapshots_dir, None),
    }
    
    # Special handling for nested dicts
    if not Path(config.video_output.get("output_directory", "")).is_absolute():
        out_dir = (root_dir / config.video_output.get("output_directory", "recordings")).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        config.video_output["output_directory"] = str(out_dir)
        
    if not Path(config.reid.get("persistence_path", "")).is_absolute():
        config.reid["persistence_path"] = str((root_dir / config.reid.get("persistence_path", "reid_gallery.pkl")).resolve())
        
    if not Path(config.feeds_config_path).is_absolute():
        config.feeds_config_path = str((root_dir / config.feeds_config_path).resolve())
        
    if not Path(config.snapshots_dir).is_absolute():
        snap_dir = (root_dir / config.snapshots_dir).resolve()
        snap_dir.mkdir(parents=True, exist_ok=True)
        config.snapshots_dir = str(snap_dir)

_config_lock = threading.Lock()
_config_instance: Optional[AppConfig] = None

def initialize_config(config_path: Optional[str] = None) -> AppConfig:
    """
    Loads and validates the configuration.
    """
    global _config_instance
    with _config_lock:
        if _config_instance is not None:
            return _config_instance

        # Load critical secrets from SecretsManager into environment
        for key in ["JWT_SECRET_KEY", "DATABASE_PASSWORD", "API_KEY", "FIREBASE_CREDENTIALS"]:
            val = secrets.get_secret(key)
            if val:
                os.environ[key] = val
                logger.info(f"Loaded secret '{key}' from SecretsManager.")

        if config_path is None:
            path_to_load = (Path(__file__).resolve().parent.parent / "configs" / "config.yaml")
        else:
            path_to_load = Path(config_path)

        try:
            raw_config = load_config(path_to_load)
            
            # Configure and validate logging
            if "logging" in raw_config:
                try:
                    # Validate logging structure with Pydantic
                    LoggingConfig(**raw_config["logging"])
                    
                    # Resolve log paths relative to the backend directory
                    backend_dir = Path(__file__).resolve().parent.parent
                    logging_config = raw_config["logging"]
                    if "handlers" in logging_config:
                        for handler_name, handler_config in logging_config["handlers"].items():
                            if "filename" in handler_config:
                                log_file = Path(handler_config["filename"])
                                if not log_file.is_absolute():
                                    log_file = backend_dir / log_file
                                log_file.parent.mkdir(parents=True, exist_ok=True)
                                handler_config["filename"] = str(log_file)
                    
                    logging.config.dictConfig(logging_config)
                except Exception as e:
                    logger.error(f"Logging configuration invalid: {e}")
            
            # Validate and create config instance
            _config_instance = AppConfig(**raw_config)
            
            # Resolve all relative paths to absolute paths
            _resolve_paths(_config_instance)
            
            logger.info(f"Configuration initialized. Data dir: {_config_instance.data_dir}")
            return _config_instance
        except Exception as e:
            logger.error(f"Config Init Failed: {e}")
            raise RuntimeError(f"Config Init Failed: {e}")

def set_config_instance(config_dict: Dict[str, Any]) -> AppConfig:
    """
    Sets the global configuration instance from a dictionary.
    Useful for workers that already have the config passed in.
    """
    global _config_instance
    with _config_lock:
        _config_instance = AppConfig(**config_dict)
        _resolve_paths(_config_instance)
        return _config_instance

def get_current_config() -> AppConfig:
    if _config_instance is None:
        raise RuntimeError("Config not initialized")
    return _config_instance


# Optional: Function to reload config (similar logic to router, but maybe called differently)
def reload_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Forces a reload of the configuration. Use with caution, especially with multiple workers.
    Returns the newly loaded config.
    """
    global _config_instance
    logger.warning("Attempting configuration reload...")
    _config_instance = None  # Clear current instance
    return initialize_config(config_path)  # Reload

