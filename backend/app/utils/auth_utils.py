from typing import Dict, Any
import firebase_admin
from firebase_admin import auth
from fastapi import HTTPException, status
import logging

logger = logging.getLogger("firebase_auth")


async def verify_firebase_token(token: str) -> Dict[str, Any]:
    """Verify Firebase ID token and return decoded token data."""
    logger.info(f"Verifying Firebase token: {token[:40]}... (truncated)")
    if firebase_admin._DEFAULT_APP_NAME not in firebase_admin._apps:
        logger.error("Firebase authentication service not available.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firebase authentication service not available.",
        )
    try:
        decoded = auth.verify_id_token(token, check_revoked=True, clock_skew_seconds=5)
        logger.info(f"Decoded token: {decoded}")
        return decoded
    except auth.RevokedIdTokenError:
        logger.error("Token has been revoked.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except auth.UserDisabledError:
        logger.error("User account is disabled.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except auth.InvalidIdTokenError as e:
        logger.error(f"Invalid ID token: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid ID token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        logger.error(f"Authentication error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Authentication error: {str(e)}",
        )
