# backend/app/routers/config.py

from typing import Dict, Any
from fastapi import APIRouter, Depends

from app.dependency_injection import (
    get_config,
    get_current_admin,
)  # Added get_current_active_user
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
    current_user: User = Depends(get_current_viewer),  # Protected for viewers and admins
) -> Dict[str, Any]:
    logger.info(
        f"GET /config endpoint called by user: {current_user.username}"
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


# Architectural Note: Configuration Reload
# Reloading configuration dynamically in a multi-worker FastAPI application (e.g., with Uvicorn workers)
# is complex. Directly reloading `config.yaml` via an API endpoint will only affect the specific
# worker process that handles the request, leading to inconsistent behavior across workers.
# A robust solution requires a centralized configuration store (e.g., Redis, Consul) or
# a signaling mechanism (e.g., SIGHUP to the master process) to ensure all workers update.
# For this reason, the /reload endpoint has been removed to prevent partial updates.
 to prevent partial updates.
