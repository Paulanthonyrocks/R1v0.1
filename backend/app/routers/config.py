# backend/app/routers/config.py

from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import get_config # Dependency to get config dict
from app.utils.utils import load_config, ConfigError # Import config loading function
from app.models.feeds import StandardResponse # Re-use standard response model
from pathlib import Path
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

# Basic filtering of sensitive keys (expand as needed)
SENSITIVE_KEYS = ["gemini_api_key", "db_password", "secret"]

def filter_sensitive_data(data: Any) -> Any:
    """Recursively filter sensitive keys from config dict."""
    if isinstance(data, dict):
        filtered_dict = {}
        for key, value in data.items():
            if key.lower() in SENSITIVE_KEYS:
                filtered_dict[key] = "********" if value else None
            else:
                filtered_dict[key] = filter_sensitive_data(value)
        return filtered_dict
    elif isinstance(data, list):
        return [filter_sensitive_data(item) for item in data]
    else:
        return data

@router.get(
    "/",
    response_model=Dict[str, Any], # Or define a Pydantic model for config structure
    summary="Get Current Configuration",
    description="Retrieves the currently loaded backend configuration, masking sensitive values.",
)
async def get_current_config(
    config: Dict[str, Any] = Depends(get_config)
) -> Dict[str, Any]:
    """
    Endpoint to retrieve the active configuration.
    Sensitive keys like API keys will be masked.
    """
    # IMPORTANT: Filter sensitive data before returning
    return filter_sensitive_data(config.copy())


@router.post(
    "/reload",
    response_model=StandardResponse,
    summary="Reload Configuration",
    description="Triggers the backend to reload its configuration from the config.yaml file.",
)
async def reload_configuration() -> StandardResponse:
    """
    Endpoint to trigger a configuration reload.
    Note: This reloads the config into memory; running processes
    may need restarting separately to use the new config.
    """
    global config # Need to potentially update the global config in main.py

    logger.info("Configuration reload requested via API.")
    try:
        # Ideally, load_config should be accessible and know its path
        # This assumes load_config can find the default path or a path is managed centrally
        config_file_path_obj = Path(__file__).parent.parent.parent / "configs" / "config.yaml"
        new_config = load_config(str(config_file_path_obj))

        # --- Update global config ---
        # This is tricky. If running multiple uvicorn workers, this only updates
        # the config for the *worker handling this request*. A better approach
        # might involve signaling other workers or using a shared config store (e.g., Redis).
        # For a single worker process, updating the global is okay.
        # from app.main import config as main_config # Import the global dict
        # main_config.clear()
        # main_config.update(new_config)
        # logger.info("In-memory configuration updated. Signal necessary services if needed.")
        # For simplicity now, just log the need to restart.
        # ---

        # Reconfigure logging level based on reloaded config
        log_level_str = new_config.get('logging', {}).get('level', 'INFO').upper()
        log_level = getattr(logging, log_level_str, logging.INFO)
        logging.getLogger().setLevel(log_level) # Update root logger
        logger.info(f"Logging level updated to: {log_level_str}")

        logger.info("Configuration reloaded successfully.")
        return StandardResponse(message="Configuration reload initiated. Restart feeds for changes to apply.")

    except ConfigError as e:
        logger.error(f"Configuration reload failed validation: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Configuration validation error: {e}")
    except Exception as e:
        logger.error(f"Configuration reload failed: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to reload configuration: {e}")