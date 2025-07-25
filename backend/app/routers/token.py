from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict
from app.dependencies import get_current_active_user
from app.models.common import APIResponse
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/token/status", response_model=APIResponse[Dict])
async def check_token_status(current_user: dict = Depends(get_current_active_user)):
    """
    Check the status of the current authentication token.
    Returns information about token validity and expiration.
    """
    try:
        if not current_user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

        # Extract relevant token information
        token_info = {
            "valid": True,
            "user_id": current_user.get("uid"),
            "email": current_user.get("email"),
            "role": current_user.get("role"),
            "auth_time": current_user.get("auth_time"),
            "exp": current_user.get("exp")
        }

        return APIResponse.success(
            data=token_info,
            message="Token status retrieved successfully"
        )

    except Exception as e:
        logger.error(f"Error checking token status: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
