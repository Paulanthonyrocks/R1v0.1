
from fastapi import HTTPException, status, Query
from app.utils.auth_utils import verify_firebase_token
from app.models.user import User
import logging

logger = logging.getLogger(__name__)

async def get_current_user_ws(token: str = Query(...)) -> User:
    """
    FastAPI WebSocket dependency that extracts and verifies a Firebase ID token
    from the `token` query parameter. Returns a User object on success.
    Raises HTTPException (which becomes an HTTP 401/500 during handshake) on failure.
    """
    try:
        decoded_token = await verify_firebase_token(token)
    except Exception as e:
        logger.error("Firebase token verification failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    # Firebase ID tokens use 'sub' for the UID; custom tokens may use 'uid'.
    uid = decoded_token.get("sub") or decoded_token.get("uid")
    if not uid:
        logger.error("Firebase token missing 'sub' and 'uid' claims")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims",
        )

    user = User(
        # Using UID as the username; adjust if your User model separates them.
        username=uid,
        email=decoded_token.get("email", ""),
        full_name=decoded_token.get("name", uid),
        role=decoded_token.get("role", "user"),
    )
    logger.info(f"WebSocket authenticated: user={user.username}, role={user.role}")
    return user
