"""Bounded, non-blocking Firebase authentication with current-account policy."""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from threading import BoundedSemaphore
from typing import Any, Dict

import firebase_admin
from firebase_admin import auth
from fastapi import HTTPException, status

logger = logging.getLogger("firebase_auth")
_FIREBASE_WORKERS = 4
_FIREBASE_TIMEOUT_SECONDS = 10.0
_FIREBASE_EXECUTOR = ThreadPoolExecutor(max_workers=_FIREBASE_WORKERS, thread_name_prefix="firebase-auth")
_FIREBASE_SLOTS = BoundedSemaphore(_FIREBASE_WORKERS)


async def _firebase_call(function, *args, **kwargs):
    # Hold capacity until the actual thread ends, not merely until its await times out.
    if not _FIREBASE_SLOTS.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="Authentication service busy.")
    try:
        future = _FIREBASE_EXECUTOR.submit(partial(function, *args, **kwargs))
    except BaseException:
        _FIREBASE_SLOTS.release()
        raise
    future.add_done_callback(lambda _: _FIREBASE_SLOTS.release())
    wrapped = asyncio.wrap_future(future)
    wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    try:
        return await asyncio.wait_for(asyncio.shield(wrapped), _FIREBASE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Authentication service timed out.") from None


async def _get_account(user_id: str):
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid identity.")
    try:
        record = await _firebase_call(auth.get_user, user_id)
    except HTTPException:
        raise
    except auth.UserDisabledError:
        raise HTTPException(status_code=403, detail="User account is disabled.") from None
    except Exception:
        logger.warning("Current account lookup unavailable.")
        raise HTTPException(status_code=503, detail="Authentication service unavailable.") from None
    if getattr(record, "disabled", False):
        raise HTTPException(status_code=403, detail="User account is disabled.")
    return record


async def verify_firebase_token(token: str) -> Dict[str, Any]:
    """Verify revocation and replace stale authorization claims with server truth."""
    if firebase_admin._DEFAULT_APP_NAME not in firebase_admin._apps:
        raise HTTPException(status_code=503, detail="Firebase authentication service not available.")
    try:
        decoded = await _firebase_call(auth.verify_id_token, token, check_revoked=True, clock_skew_seconds=5)
        record = await _get_account(decoded.get("sub") or decoded.get("uid"))
        claims = getattr(record, "custom_claims", None) or {}
        decoded["role"] = claims.get("role") or "user"
        # Authorization scopes are never inherited from a stale token.
        decoded["signal_ids"] = claims.get("signal_ids", [])
        return decoded
    except HTTPException:
        raise
    except auth.UserDisabledError:
        raise HTTPException(status_code=403, detail="User account is disabled.") from None
    except (auth.RevokedIdTokenError, auth.InvalidIdTokenError):
        logger.info("Firebase credential rejected.")
        raise HTTPException(status_code=401, detail="Invalid or expired token.", headers={"WWW-Authenticate": "Bearer"}) from None
    except Exception:
        logger.warning("Firebase authentication unavailable.")
        raise HTTPException(status_code=503, detail="Authentication service unavailable.") from None


async def get_server_role(user_id: str, fallback: str = "user") -> str:
    """Current role; fallback retained for compatibility but never grants privilege."""
    record = await _get_account(user_id)
    return (getattr(record, "custom_claims", None) or {}).get("role") or "user"
