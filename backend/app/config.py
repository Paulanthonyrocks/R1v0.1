# /content/drive/MyDrive/R1v0.1/backend/app/config.py

import logging
import logging.config  # Import logging.config
from pathlib import Path
from typing import Dict, Any, Optional

# Assuming load_config is defined in utils.utils
from app.utils import load_config, ConfigError

logger = logging.getLogger("app.config")  # Use specific logger name

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
            logging.config.dictConfig(_config_instance["logging"])
            print("Logging configured successfully using dictConfig.")
            logger.info("Logging configured successfully using dictConfig.")
        except Exception as e:
            print(f"Failed to configure logging with dictConfig: {e}")
            logger.error(
                f"Failed to configure logging with dictConfig: {e}", exc_info=True
            )
            # Fallback to basic config if dictConfig fails
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            )
            print("Falling back to basic logging configuration.")
            logger.warning("Falling back to basic logging configuration.")
        # --- End Logging Reconfiguration ---

        # Resolve relative paths to absolute paths
        project_root = Path(
            __file__
        ).parent.parent.parent  # Assumes config.py is in backend/app/

        # Resolve sample_video path
        sample_video_path_str = _config_instance.get("video_input", {}).get(
            "sample_video"
        )
        if sample_video_path_str:
            resolved_sample_video_path = Path(sample_video_path_str)
            if not resolved_sample_video_path.is_absolute():
                # If it's not absolute, assume it's relative to the project root
                project_root = Path(__file__).parent.parent.parent
                resolved_sample_video_path = (project_root / sample_video_path_str).resolve()
            else:
                # If it's already absolute, just resolve it to get the canonical form
                resolved_sample_video_path = resolved_sample_video_path.resolve()

            _config_instance["video_input"]["sample_video"] = str(
                resolved_sample_video_path
            )
            logger.info(
                f"Resolved sample_video path to: {_config_instance['video_input']['sample_video']}"
            )

        # Resolve model_path for vehicle_detection
        model_path_relative = _config_instance.get("vehicle_detection", {}).get(
            "model_path"
        )
        if model_path_relative:
            model_path_absolute = (project_root / model_path_relative).resolve()
            _config_instance["vehicle_detection"]["model_path"] = str(
                model_path_absolute
            )
            logger.info(
                f"Resolved vehicle_detection model_path to: {_config_instance['vehicle_detection']['model_path']}"
            )

        # Resolve matrix_path for perspective_calibration
        matrix_path_relative = _config_instance.get("perspective_calibration", {}).get(
            "matrix_path"
        )
        if matrix_path_relative:
            matrix_path_absolute = (project_root / matrix_path_relative).resolve()
            _config_instance["perspective_calibration"]["matrix_path"] = str(
                matrix_path_absolute
            )
            logger.info(
                f"Resolved perspective_calibration matrix_path to: {_config_instance['perspective_calibration']['matrix_path']}"
            )

        # Resolve db_path for database
        db_path_relative = _config_instance.get("database", {}).get("db_path")
        if db_path_relative:
            db_path_absolute = (project_root / db_path_relative).resolve()
            _config_instance["database"]["db_path"] = str(db_path_absolute)
            logger.info(
                f"Resolved database db_path to: {_config_instance['database']['db_path']}"
            )

        return _config_instance

        


    except ConfigError as e:
        logger.error(f"Failed to load configuration from {path_to_load}: {e}", exc_info=True)
        raise RuntimeError(f"Failed to load configuration: {e}") from e
    except Exception as e:
        logger.error(f"An unexpected error occurred during configuration initialization: {e}", exc_info=True)
        raise RuntimeError(f"An unexpected error occurred during configuration initialization: {e}") from e

def get_current_config() -> Dict[str, Any]:
    """
    Returns the currently loaded configuration dictionary.
    Raises RuntimeError if configuration has not been initialized.
    """
    if _config_instance is None:
        logger.error("Configuration accessed before initialization!")
        raise RuntimeError(
            "Configuration has not been initialized. Call initialize_config first."
        )
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
