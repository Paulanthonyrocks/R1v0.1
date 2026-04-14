
from fastapi import HTTPException, status, Query
from app.utils.auth_utils import verify_firebase_token
from app.models.user import User
import logging

logger = logging.getLogger(__name__)

async def get_current_user_ws(token: str = Query(...)):
    if not token:
        logger.warning("WebSocket connection attempt without a token.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is missing",
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
        logger.error(f"An unexpected error occurred during WebSocket authentication: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during authentication",
        )
