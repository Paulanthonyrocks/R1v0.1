# /content/drive/MyDrive/R1v0.1/backend/app/config.py

import logging
import logging.config # Import logging.config
from pathlib import Path
from typing import Dict, Any, Optional

# Assuming load_config is defined in utils.utils
from app.utils import load_config, ConfigError

logger = logging.getLogger("app.config") # Use specific logger name

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
        path_to_load = Path(__file__).parent.parent / "configs" / "config.yaml"
    else:
        path_to_load = Path(config_path)

    logger.info(f"Initializing configuration from: {path_to_load}")
    try:
        _config_instance = load_config(path_to_load)
        logger.info("Configuration initialized successfully via app.config.")

        # --- Reconfigure Logging Here (Centralized) ---
        # It's good practice to configure logging as soon as config is loaded
        print("Attempting to configure logging with dictConfig...")
        try:
            logging.config.dictConfig(_config_instance['logging'])
            print("Logging configured successfully using dictConfig.")
            logger.info("Logging configured successfully using dictConfig.")
        except Exception as e:
            print(f"Failed to configure logging with dictConfig: {e}")
            logger.error(f"Failed to configure logging with dictConfig: {e}", exc_info=True)
            # Fallback to basic config if dictConfig fails
            logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            print("Falling back to basic logging configuration.")
            logger.warning("Falling back to basic logging configuration.")
        # --- End Logging Reconfiguration ---

        return _config_instance
    except ConfigError as e:
        logger.critical(f"CRITICAL CONFIGURATION ERROR during initialization: {e}", exc_info=True)
        _config_instance = None # Ensure it's None on failure
        raise RuntimeError(f"Configuration loading failed: {e}") from e
    except Exception as e:
        logger.critical(f"Unexpected error initializing configuration: {e}", exc_info=True)
        _config_instance = None
        raise RuntimeError(f"Unexpected configuration error: {e}") from e

def get_current_config() -> Dict[str, Any]:
    """
    Returns the currently loaded configuration dictionary.
    Raises RuntimeError if configuration has not been initialized.
    """
    if _config_instance is None:
        logger.error("Configuration accessed before initialization!")
        raise RuntimeError("Configuration has not been initialized. Call initialize_config first.")
    return _config_instance

# Optional: Function to reload config (similar logic to router, but maybe called differently)
def reload_config(config_path: Optional[str] = None) -> Dict[str, Any]:
     """
     Forces a reload of the configuration. Use with caution, especially with multiple workers.
     Returns the newly loaded config.
     """
     global _config_instance
     logger.warning("Attempting configuration reload...")
     _config_instance = None # Clear current instance
     return initialize_config(config_path) # Reload