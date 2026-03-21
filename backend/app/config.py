import logging
import logging.config
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

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

class RedisConfig(BaseModel):
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None
    enabled: bool = False

class MongoDBConfig(BaseModel):
    uri: str = "mongodb://localhost:27017/"
    database_name: str = "traffic_hub"
    enabled: bool = False

class PerformanceConfig(BaseModel):
    gpu_acceleration: bool = False
    video_gpu_acceleration: bool = False  # For HW accelerated decoding/encoding
    image_gpu_acceleration: bool = False  # For GPU-based image ops (resizing, etc.)
    inference_pool_size: int = 2
    memory_limit_percent: int = 80
    max_concurrent_feeds: int = 10

class AppConfig(BaseSettings):
    project_root_dir: str = str(Path(__file__).resolve().parent.parent.parent)
    data_dir: str = os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))
    
    logging: Dict[str, Any] = {}
    database: DatabaseConfig = DatabaseConfig()
    redis: RedisConfig = RedisConfig()
    mongodb: MongoDBConfig = MongoDBConfig()
    performance: PerformanceConfig = PerformanceConfig()
    websocket: Dict[str, Any] = {"max_connections": 1000}
    firebase_admin: Dict[str, Any] = {"auth_enabled": False}
    video_input: Dict[str, Any] = {"sample_videos": []}
    video_output: Dict[str, Any] = {"enabled": False, "output_directory": "recordings", "fps": 10}
    reid: Dict[str, Any] = {"similarity_threshold": 0.85, "persistence_path": "reid_gallery.pkl"}
    prediction_scheduler: Dict[str, Any] = {"enabled": True}
    auto_start_processing: bool = True
    file_watcher: Dict[str, Any] = {"enabled": False}
    post_startup_processing: Dict[str, Any] = {"enabled": False}
    cors: Dict[str, Any] = {"allowed_origins": []}
    
    # New settings for dynamic paths
    feeds_config_path: str = "feeds_config.json"
    snapshots_dir: str = "snapshots"
    
    # Snapshot management
    snapshot_format: str = "webp" # More efficient than jpg
    snapshot_quality: int = 80
    snapshot_max_width: int = 1280
    snapshot_retention_days: int = 7
    
    # Maintenance management
    maintenance_interval_hours: int = 24
    db_retention_days: int = 7
    
    # Data collection management
    hard_negative_quality: int = 70
    hard_negative_max_samples_per_feed: int = 1000
    hard_negative_retention_days: int = 3
    
    class Config:
        env_file = ".env"
        env_nested_delimiter = "__"
        env_file_encoding = "utf-8"
        extra = "allow"

# Module-level variable to hold the loaded configuration
_config_instance: Optional[AppConfig] = None

def initialize_config(config_path: Optional[str] = None) -> AppConfig:
    """
    Loads and validates the configuration.
    """
    global _config_instance
    if _config_instance is not None:
        return _config_instance

    # Load critical secrets from SecretsManager into environment
    # This ensures BaseSettings or other components can access them via os.environ
    for key in ["JWT_SECRET_KEY", "DATABASE_PASSWORD", "API_KEY", "FIREBASE_CREDENTIALS"]:
        val = secrets.get_secret(key)
        if val:
            os.environ[key] = val
            logger.info(f"Loaded secret '{key}' from SecretsManager.")

    if config_path is None:
        path_to_load = Path(__file__).parent.parent / "configs" / "config.yaml"
    else:
        path_to_load = Path(config_path)

    try:
        raw_config = load_config(path_to_load)
        
        # Configure logging
        if "logging" in raw_config:
            # Create directories for log files if they don't exist
            handlers = raw_config["logging"].get("handlers", {})
            for handler_name, handler_config in handlers.items():
                if "filename" in handler_config:
                    log_file = Path(handler_config["filename"])
                    # If filename is relative, make it relative to the parent of configs (backend root)
                    if not log_file.is_absolute():
                        log_file = (path_to_load.parent.parent / log_file).resolve()
                        # Update the raw_config with the absolute path so dictConfig knows where to write
                        handler_config["filename"] = str(log_file)
                    
                    log_file.parent.mkdir(parents=True, exist_ok=True)
            
            logging.config.dictConfig(raw_config["logging"])
        
        # Validate with Pydantic
        _config_instance = AppConfig(**raw_config)
        
        # Resolve paths dynamically
        data_path = Path(_config_instance.data_dir)
        data_path.mkdir(parents=True, exist_ok=True)
        
        # Update db_path if it's relative
        db_p = Path(_config_instance.database.db_path)
        if not db_p.is_absolute():
            _config_instance.database.db_path = str(data_path / db_p.name)
            
        # Update other paths
        if not Path(_config_instance.video_output["output_directory"]).is_absolute():
            rec_dir = data_path / _config_instance.video_output["output_directory"]
            rec_dir.mkdir(parents=True, exist_ok=True)
            _config_instance.video_output["output_directory"] = str(rec_dir)
            
        if not Path(_config_instance.reid["persistence_path"]).is_absolute():
            _config_instance.reid["persistence_path"] = str(data_path / _config_instance.reid["persistence_path"])
            
        if not Path(_config_instance.feeds_config_path).is_absolute():
            _config_instance.feeds_config_path = str(data_path / _config_instance.feeds_config_path)
            
        if not Path(_config_instance.snapshots_dir).is_absolute():
            snap_dir = data_path / _config_instance.snapshots_dir
            snap_dir.mkdir(parents=True, exist_ok=True)
            _config_instance.snapshots_dir = str(snap_dir)
        
        logger.info(f"Configuration initialized with data_dir: {data_path}")
        return _config_instance
    except Exception as e:
        logger.error(f"Config Init Failed: {e}")
        raise RuntimeError(f"Config Init Failed: {e}")

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
