     1|import logging
     2|import logging.config
     3|import os
     4|from pathlib import Path
     5|from typing import Dict, Any, Optional, List
     6|from pydantic import BaseModel, Field
     7|from pydantic_settings import BaseSettings
     8|
     9|# Assuming load_config is defined in utils.utils
    10|from app.utils.config import load_config, ConfigError
    11|from app.utils.secrets_manager import secrets
    12|
    13|logger = logging.getLogger("app.config")
    14|
    15|class LoggingConfig(BaseModel):
    16|    version: int = 1
    17|    disable_existing_loggers: bool = False
    18|    formatters: Dict[str, Any] = {}
    19|    handlers: Dict[str, Any] = {}
    20|    loggers: Dict[str, Any] = {}
    21|
    22|class DatabaseConfig(BaseModel):
    23|    db_path: str = "backend/data/vehicle_data.db"
    24|    connection_timeout: int = 30
    25|    query_timeout: int = 60
    26|
    27|class RedisConfig(BaseModel):
    28|    host: str = "localhost"
    29|    port: int = 6379
    30|    db: int = 0
    31|    password: Optional[str] = None
    32|    enabled: bool = True
    33|
    34|class MongoDBConfig(BaseModel):
    35|    uri: str = "mongodb://localhost:27017/"
    36|    database_name: str = "traffic_hub"
    37|    enabled: bool = True
    38|
    39|class PerformanceConfig(BaseModel):
    40|    gpu_acceleration: bool = True
    41|    video_gpu_acceleration: bool = False  # For HW accelerated decoding/encoding
    42|    image_gpu_acceleration: bool = False  # For GPU-based image ops (resizing, etc.)
    43|    inference_pool_size: int = 2
    44|    memory_limit_percent: int = 80
    45|    max_concurrent_feeds: int = 10
    46|
    47|class AppConfig(BaseSettings):
    48|    project_root_dir: str = str(Path(__file__).resolve().parent.parent.parent)
    49|    data_dir: str = os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))
    50|    
    51|    logging: Dict[str, Any] = {}
    52|    database: DatabaseConfig = DatabaseConfig()
    53|    redis: RedisConfig = RedisConfig()
    54|    mongodb: MongoDBConfig = MongoDBConfig()
    55|    performance: PerformanceConfig = PerformanceConfig()
    56|    websocket: Dict[str, Any] = {"max_connections": 1000}
    57|    firebase_admin: Dict[str, Any] = {"auth_enabled": False}
    58|    video_input: Dict[str, Any] = {"sample_videos": []}
    59|    video_output: Dict[str, Any] = {"enabled": False, "output_directory": "recordings", "fps": 10}
    60|    reid: Dict[str, Any] = {"similarity_threshold": 0.85, "persistence_path": "reid_gallery.pkl"}
    61|    prediction_scheduler: Dict[str, Any] = {"enabled": True}
    62|    auto_start_processing: bool = True
    63|    file_watcher: Dict[str, Any] = {"enabled": False}
    64|    post_startup_processing: Dict[str, Any] = {"enabled": False}
    65|    cors: Dict[str, Any] = {"allowed_origins": []}
    66|    
    67|    # New settings for dynamic paths
    68|    feeds_config_path: str = "feeds_config.json"
    69|    snapshots_dir: str = "snapshots"
    70|    
    71|    class Config:
    72|        env_file = ".env"
    73|        extra = "allow"
    74|
    75|# Module-level variable to hold the loaded configuration
    76|_config_instance: Optional[AppConfig] = None
    77|
    78|def initialize_config(config_path: Optional[str] = None) -> AppConfig:
    79|    """
    80|    Loads and validates the configuration.
    81|    """
    82|    global _config_instance
    83|    if _config_instance is not None:
    84|        return _config_instance
    85|
    86|    # Load critical secrets from SecretsManager into environment
    87|    # This ensures BaseSettings or other components can access them via os.environ
    88|    for key in ["JWT_SECRET_KEY", "DATABASE_PASSWORD", "API_KEY", "FIREBASE_CREDENTIALS"]:
    89|        val = secrets.get_secret(key)
    90|        if val:
    91|            os.environ[key] = val
    92|            logger.info(f"Loaded secret '{key}' from SecretsManager.")
    93|
    94|    if config_path is None:
    95|        path_to_load = Path(__file__).parent.parent / "configs" / "config.yaml"
    96|    else:
    97|        path_to_load = Path(config_path)
    98|
    99|    try:
   100|        raw_config = load_config(path_to_load)
   101|        
   102|        # Configure logging
   103|        if "logging" in raw_config:
   104|            # Resolve log paths relative to the backend directory
   105|            backend_dir = Path(__file__).parent.parent
   106|            logging_config = raw_config["logging"]
   107|            if "handlers" in logging_config:
   108|                for handler_name, handler_config in logging_config["handlers"].items():
   109|                    if "filename" in handler_config:
   110|                        log_file = Path(handler_config["filename"])
   111|                        if not log_file.is_absolute():
   112|                            # Resolve relative to backend directory
   113|                            log_file = backend_dir / log_file
   114|                        
   115|                        # Ensure the log directory exists
   116|                        log_file.parent.mkdir(parents=True, exist_ok=True)
   117|                        handler_config["filename"] = str(log_file)
   118|            
   119|            logging.config.dictConfig(logging_config)
   120|        
   121|        # Validate with Pydantic
   122|        _config_instance = AppConfig(**raw_config)
   123|        
   124|        # Resolve paths dynamically
   125|        data_path = Path(_config_instance.data_dir)
   126|        data_path.mkdir(parents=True, exist_ok=True)
   127|        
   128|        # Update db_path if it's relative
   129|        db_p = Path(_config_instance.database.db_path)
   130|        if not db_p.is_absolute():
   131|            _config_instance.database.db_path = str(data_path / db_p.name)
   132|            
   133|        # Update other paths
   134|        if not Path(_config_instance.video_output["output_directory"]).is_absolute():
   135|            rec_dir = data_path / _config_instance.video_output["output_directory"]
   136|            rec_dir.mkdir(parents=True, exist_ok=True)
   137|            _config_instance.video_output["output_directory"] = str(rec_dir)
   138|            
   139|        if not Path(_config_instance.reid["persistence_path"]).is_absolute():
   140|            _config_instance.reid["persistence_path"] = str(data_path / _config_instance.reid["persistence_path"])
   141|            
   142|        if not Path(_config_instance.feeds_config_path).is_absolute():
   143|            _config_instance.feeds_config_path = str(data_path / _config_instance.feeds_config_path)
   144|            
   145|        if not Path(_config_instance.snapshots_dir).is_absolute():
   146|            snap_dir = data_path / _config_instance.snapshots_dir
   147|            snap_dir.mkdir(parents=True, exist_ok=True)
   148|            _config_instance.snapshots_dir = str(snap_dir)
   149|        
   150|        logger.info(f"Configuration initialized with data_dir: {data_path}")
   151|        return _config_instance
   152|    except Exception as e:
   153|        logger.error(f"Config Init Failed: {e}")
   154|        raise RuntimeError(f"Config Init Failed: {e}")
   155|
   156|def set_config_instance(config_dict: Dict[str, Any]) -> AppConfig:
   157|    \"\"\"
   158|    Sets the global configuration instance from a dictionary.
   159|    Useful for workers that already have the config passed in.
   160|    \"\"\"
   161|    global _config_instance
   162|    _config_instance = AppConfig(**config_dict)
   163|    return _config_instance
   164|
   165|def get_current_config() -> AppConfig:
   166|    if _config_instance is None:
        raise RuntimeError("Config not initialized")
   168|    return _config_instance
   169|
   170|
   171|# Optional: Function to reload config (similar logic to router, but maybe called differently)
   172|def reload_config(config_path: Optional[str] = None) -> Dict[str, Any]:
   173|    """
   174|    Forces a reload of the configuration. Use with caution, especially with multiple workers.
   175|    Returns the newly loaded config.
   176|    """
   177|    global _config_instance
   178|    logger.warning("Attempting configuration reload...")
   179|    _config_instance = None  # Clear current instance
   180|    return initialize_config(config_path)  # Reload
   181|