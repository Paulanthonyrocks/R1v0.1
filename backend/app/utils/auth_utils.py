from typing import Dict, Any
import firebase_admin
from firebase_admin import auth
import logging

logger = logging.getLogger("firebase_auth")


async def verify_firebase_token(token: str) -> Dict[str, Any]:
    """
    Verify Firebase ID token and return decoded token data.
    Raises ValueError for any authentication failures.
    """
    logger.info(f"Verifying Firebase token: {token[:40]}... (truncated)")
    if firebase_admin._DEFAULT_APP_NAME not in firebase_admin._apps:
        msg = "Firebase authentication service not available. No default app initialized."
        logger.error(msg)
        raise ValueError(msg)
    try:
        # Using default clock skew is generally safer.
        decoded_token = auth.verify_id_token(token, check_revoked=True)
        logger.info(f"Successfully decoded token for UID: {decoded_token.get('uid')}")
        return decoded_token
    except auth.RevokedIdTokenError as e:
        logger.warning(f"Authentication failed: Token has been revoked. UID: {e.uid}")
        raise ValueError("Token has been revoked.")
    except auth.InvalidIdTokenError as e:
        logger.warning(f"Authentication failed: Invalid ID token. Details: {e}")
        raise ValueError(f"Invalid ID token: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred during token verification: {e}", exc_info=True)
        raise ValueError(f"An unexpected authentication error occurred: {e}")
