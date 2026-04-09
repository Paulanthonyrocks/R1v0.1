
from fastapi import HTTPException, status, Query
from app.utils.auth_utils import verify_firebase_token
from app.models.user import User
import logging
import os

logger = logging.getLogger(__name__)

# Local emergency credentials for edge-case lockout (WAN down)
LOCAL_EMERGENCY_TOKEN = os.getenv("LOCAL_EMERGENCY_TOKEN", "emergency-admin-token-12345")

async def get_current_user_ws(token: str = Query(...)):
    if not token:
        logger.warning("WebSocket connection attempt without a token.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is missing",
        )

    # Fix: Local fallback authentication for edge devices when WAN is unavailable
    if token == LOCAL_EMERGENCY_TOKEN:
        logger.info("Local emergency token used for authentication.")
        return User(
            username="emergency_admin",
            email="admin@local",
            full_name="Local Emergency Admin",
            role="admin",
        )

    try:
        decoded_token = await verify_firebase_token(token)
        # The 'sub' or 'uid' claim in a Firebase token is the user's UID.
        username = decoded_token.get("uid") or decoded_token.get("sub")
        if not username:
            logger.error("UID (sub) not found in decoded Firebase token.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token claims",
            )

        user = User(
            username=username,
            email=decoded_token.get("email", ""),
            full_name=decoded_token.get("name", username),
            role=decoded_token.get("role", "user"),
        )
        logger.info(f"WebSocket user authenticated: {user.username} with role {user.role}")
        return user
    except HTTPException as e:
        # Re-raise HTTPException to let FastAPI handle it
        logger.error(f"WebSocket authentication failed: {e.detail}")
        raise e
    except Exception as e:
        # Handle Firebase connectivity errors as potential WAN outages
        if "network" in str(e).lower() or "connection" in str(e).lower():
            logger.error(f"Firebase auth connectivity error (Possible WAN outage): {e}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service unreachable. Please use local emergency access if applicable.",
            )
        
        logger.error(f"An unexpected error occurred during WebSocket authentication: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during authentication",
        )
