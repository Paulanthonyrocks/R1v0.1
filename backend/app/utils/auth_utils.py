from typing import Dict, Any
import firebase_admin
from firebase_admin import auth
from fastapi import HTTPException, status
import logging

logger = logging.getLogger("firebase_auth")


async def verify_firebase_token(token: str) -> Dict[str, Any]:
    """Verify Firebase ID token and return decoded token data."""
    # SECURITY: never log the raw token or full claims. The JWT is a bearer
    # secret and the decoded claims contain PII (email, uid, role). Logging
    # either at INFO leaks credentials/PII into server logs on EVERY auth
    # call (REST + WS). Log only a non-secret debug hint with no token/PII.
    logger.debug("Verifying Firebase ID token.")
    if firebase_admin._DEFAULT_APP_NAME not in firebase_admin._apps:
        logger.error("Firebase authentication service not available. No default app initialized.")
        logger.error("Firebase authentication service not available.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firebase authentication service not available.",
        )
    try:
        decoded = auth.verify_id_token(token, check_revoked=True, clock_skew_seconds=5)
        # SECURITY (crack #1): a valid ID token's claims are Google-signed and
        # trustworthy, BUT Firebase's verify_id_token does NOT reject disabled
        # accounts (only revoked tokens). Reject disabled users explicitly.
        # Also resolve the role SERVER-SIDE from the Firebase user record rather
        # than trusting the token claim verbatim, as defense-in-depth against
        # claim drift between token issuance and now.
        uid = decoded.get("sub") or decoded.get("uid")
        try:
            user_rec = auth.get_user(uid)
            if getattr(user_rec, "disabled", False):
                logger.warning(f"Firebase user {uid} is disabled.")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User account is disabled.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            server_role = (getattr(user_rec, "custom_claims", None) or {}).get("role")
            if server_role:
                decoded["role"] = server_role
        except HTTPException:
            raise
        except Exception as e:
            # User-record lookup unavailable (network/Firebase) -- fall back to
            # the signed token claim rather than denying all auth.
            logger.warning(f"Could not resolve role server-side for {uid}: {e}")

        # Log only non-secret identity (sub + role), never email or the token.
        logger.debug(
            f"Firebase token verified for sub={decoded.get('sub')} "
            f"role={decoded.get('role', 'user')}."
        )
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
        logger.error(f"Invalid ID token: {e}. Full error details: {e}", exc_info=True)
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


async def get_server_role(user_id: str, fallback: str = "user") -> str:
    """Resolve a user's role SERVER-SIDE from the Firebase user record.

    Used to defeat role stickiness on long-lived WebSocket sessions: a
    client's cached role (set at AUTHENTICATE time) would otherwise stay
    valid until the token expires even if the account was demoted. Callers
    pass the cached uid and get the current server truth, falling back to
    the cached role if the Firebase user API is unavailable (so a transient
    outage never silently grants or denies access).
    """
    if not user_id:
        return fallback
    try:
        user_rec = auth.get_user(user_id)
        role = (getattr(user_rec, "custom_claims", None) or {}).get("role")
        return role or fallback
    except Exception as e:
        logger.warning(f"Could not resolve server role for {user_id}: {e}")
        return fallback
