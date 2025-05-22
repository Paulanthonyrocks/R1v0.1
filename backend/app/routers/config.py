# backend/app/routers/config.py

from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status

# Added get_current_active_user
from app.dependencies import get_config, get_current_active_user
# Import config loading function
from app.utils.utils import load_config, ConfigError
from app.models.feeds import StandardResponse  # Re-use standard response model
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
    # Or define a Pydantic model for config structure
    response_model=Dict[str, Any],
    summary="Get Current Configuration",
    description="Retrieves current backend configuration, masking sensitive values.",
)
async def get_current_config_route( # Renamed to avoid conflict with imported get_current_config
    config: Dict[str, Any] = Depends(get_config),
    current_user: dict = Depends(get_current_active_user)  # Protected # noqa F841
) -> Dict[str, Any]:
    """
    Retrieves active configuration. Requires authentication.
    Sensitive keys (API keys, etc.) will be masked.
    """
    # current_user is used by Depends for authentication, so no F841 if # noqa is present
    # logger.info(f"User {current_user.get(\"email\")} retrieved configuration.")
    return filter_sensitive_data(config.copy())


@router.post(
    "/reload",
    response_model=StandardResponse,
    summary="Reload Configuration",
    description="Triggers backend to reload configuration from config.yaml.",
)
async def reload_configuration(
    current_user: dict = Depends(get_current_active_user) # Protected # noqa F841
) -> StandardResponse:
    """
    Triggers a configuration reload. Requires authentication.
    Note: Reloads config in memory. Running processes might need separate restarts.
    """
    # current_user used for auth. # noqa F841
    # global config # F824: 'global config' is unused as it's not assigned to in this scope.
    # The 'config' name here refers to the dependency, not a global to be updated.
    # If the intent was to update a global config object in main.py, that needs a different mechanism.

    logger.info("Configuration reload requested via API.")
    try:
        # Path to config relative to this file's location (app/routers/config.py)
        # -> app/ -> backend/ -> backend/configs/config.yaml
        config_file_path = Path(__file__).parent.parent.parent / "configs" / "config.yaml"
        new_config = load_config(str(config_file_path.resolve()))

        # Updating a global config dict directly from a router like this is problematic,
        # especially with multiple workers. The config should be managed centrally.
        # For now, this reload will only affect the config instance used by this worker
        # if the `get_config` dependency re-initializes or fetches fresh config.
        # The `initialize_config` function in `app.config` module should handle
        # making the new config available. Here, we just trigger that process.
        # This assumes `app.config.initialize_config` (if called again) or a similar
        # mechanism would update the shared config state.

        # Reconfigure logging based on the newly loaded config
        logging_settings = new_config.get('logging', {})
        log_level_str = logging_settings.get('level', 'INFO').upper()
        log_level = getattr(logging, log_level_str, logging.INFO)
        # Update root logger's level. Specific loggers might need individual updates
        # if they don't propagate to root or have overriding levels.
        logging.getLogger().setLevel(log_level)
        # Also update the logger for this module, in case its level was set differently
        logger.setLevel(log_level)
        logger.info(f"Logging level updated to: {log_level_str} based on reloaded config.")

        logger.info("Configuration reload process completed successfully via API call.")
        return StandardResponse(
            message="Configuration reload initiated. Restart services for changes to fully apply."
        )
    except ConfigError as e:
        logger.error(f"Config reload validation error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Configuration validation error: {e}")
    except Exception as e:
        logger.error(f"Config reload failed: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"Failed to reload configuration: {e}")
