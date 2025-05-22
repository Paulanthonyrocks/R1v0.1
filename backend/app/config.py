# /content/drive/MyDrive/R1v0.1/backend/app/config.py

import logging
from pathlib import Path
from typing import Dict, Any, Optional

# Assuming load_config is defined in utils.utils
from app.utils.utils import load_config, ConfigError

logger = logging.getLogger(__name__)

# Module-level variable to hold the loaded configuration
_config_instance: Optional[Dict[str, Any]] = None


def initialize_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Loads the configuration from the specified path or a default location.
    Stores it in the module-level variable.
    """
    global _config_instance
    if _config_instance is not None:
        logger.warning("Configuration already initialized. Skipping reload.")
        return _config_instance

    if config_path is None:
        # Default path relative to this file's parent's parent
        # (i.e., backend/configs/config.yaml)
        config_file_path_obj = (
            Path(__file__).parent.parent / "configs" / "config.yaml"
        )
        config_path = str(config_file_path_obj.resolve())
    else:
        config_file_path_obj = Path(config_path)  # Use provided path

    logger.info(f"Initializing configuration from: {config_path}")
    try:
        _config_instance = load_config(config_path)
        logger.info("Configuration initialized successfully via app.config.")

        # --- Reconfigure Logging Here (Centralized) ---
        # It's good practice to configure logging as soon as config is loaded
        logging_config = _config_instance.get('logging', {})
        log_level_str = logging_config.get('level', 'INFO').upper()
        log_level = getattr(logging, log_level_str, logging.INFO)

        # Configure root logger
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        logging.basicConfig(level=log_level, format=log_format, force=True)

        # Apply level to specific app loggers if needed
        logging.getLogger('app').setLevel(log_level)
        # Optional: Apply to uvicorn loggers
        # logging.getLogger('uvicorn.access').setLevel(log_level)
        # logging.getLogger('uvicorn.error').setLevel(log_level)
        logger.info(f"Logging level set to: {log_level_str} from config.")
        # --- End Logging Reconfiguration ---

        return _config_instance
    except ConfigError as e:
        logger.critical(f"CRITICAL CONFIG ERROR: {e}", exc_info=True)
        _config_instance = None  # Ensure it's None on failure
        raise RuntimeError(f"Configuration loading failed: {e}") from e
    except Exception as e:
        logger.critical(f"Unexpected config error: {e}", exc_info=True)
        _config_instance = None
        raise RuntimeError(f"Unexpected configuration error: {e}") from e


def get_current_config() -> Dict[str, Any]:
    """
    Returns the currently loaded configuration dictionary.
    Raises RuntimeError if configuration has not been initialized.
    """
    if _config_instance is None:
        logger.error("Config accessed before initialization!")
        raise RuntimeError("Initialize config first: call initialize_config.")
    return _config_instance

# Optional: Function to reload config


def reload_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Forces a reload of the configuration. Use with caution.
    Returns the newly loaded config.
    """
    global _config_instance
    logger.warning("Attempting configuration reload...")
    _config_instance = None  # Clear current instance
    return initialize_config(config_path)
