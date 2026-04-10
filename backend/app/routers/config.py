# backend/app/routers/config.py

from typing import Dict, Any
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException

from app.dependency_injection import (
    get_config,
    get_current_admin,
    get_feed_manager,
    get_container,
    DependencyContainer as Container,
    FeedManager,
)
from app.models.common import APIResponse  # Re-use standard response model
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
    response_model=APIResponse[
        Dict[str, Any]
    ],  # Or define a Pydantic model for config structure
    summary="Get Current Configuration",
    description="Retrieves the currently loaded backend configuration, masking sensitive values.",
)
async def get_current_config(
    config: Dict[str, Any] = Depends(get_config),
    current_user: dict = Depends(get_current_admin),  # Protected
) -> Dict[str, Any]:
    logger.info(
        f"GET /config endpoint called by admin user: {current_user.get('uid', 'unknown_admin_uid')}"
    )
    """
    Endpoint to retrieve the active configuration. Requires authentication.
    Sensitive keys like API keys will be masked.
    """
    logger.info(
        f"Admin user {current_user.get('uid', 'unknown_admin_uid')} retrieved configuration."
    )
    # IMPORTANT: Filter sensitive data before returning
    return APIResponse.success(
        data=filter_sensitive_data(config.copy()),
        message="Configuration retrieved successfully.",
    )


@router.post(
    "/reload",
    summary="Reload Configuration",
    description="Triggers a reload of the config.yaml file and propagates changes to active services.",
)
async def reload_server_config(
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_admin),
    feed_manager: FeedManager = Depends(get_feed_manager),
    container: Container = Depends(get_container),
) -> Dict[str, Any]:
    try:
        from app.config import reload_config
        new_config = reload_config()
        # Update the dependency injection container
        container.set_config(new_config)
        
        # Notify FeedManager to update its internal config and workers
        # We use a background task to avoid blocking the response
        background_tasks.add_task(feed_manager.update_global_config, new_config)
        
        return APIResponse.success(
            data=None,
            message="Configuration reloaded and propagated to workers."
        )
    except Exception as e:
        logger.error(f"Config reload failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to reload configuration: {str(e)}")


# Architectural Note: Configuration Reload
# Reloading configuration dynamically in a multi-worker FastAPI application (e.g., with Uvicorn workers)
# is complex. Direct reloading of `config.yaml` via an API endpoint will only affect the specific
# worker process that handles the request.
# For this implementation, we rely on the FeedManager's ability to broadcast updates to
# the inference workers.
