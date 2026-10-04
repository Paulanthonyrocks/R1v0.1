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
        # Say so in the log. This 503 is a CAPACITY refusal, not a Firebase
        # outage -- the two look identical in the browser and pull an operator
        # in completely opposite directions (wait vs. fix the credential). The
        # function name is enough to tell which call is hot: verify_id_token is
        # per-request, get_user only on the claims refresh.
        logger.warning(
            "Firebase concurrency pool saturated (%d slots); refusing %s. "
            "This is a capacity refusal, not an outage.",
            _FIREBASE_WORKERS, getattr(function, "__name__", str(function)),
        )
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
    except Exception as e:
        # Name the failure. "Authentication service unavailable" is three
        # different problems (credential rejected, network unreachable, project
        # id wrong) and the exception is the only thing that distinguishes
        # them -- but it was being discarded, so the 503 storm in the browser
        # gave an operator nothing to act on.
        logger.warning("Current account lookup unavailable: %s: %s",
                       type(e).__name__, e)
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
    except Exception as e:
        # Same reasoning as _get_account: the exception is the only evidence
        # that separates an unreachable Firebase from a bad credential from a
        # wrong project id. A generic warning line made the browser's 503
        # undiagnosable.
        logger.warning("Firebase authentication unavailable: %s: %s",
                       type(e).__name__, e)
        raise HTTPException(status_code=503, detail="Authentication service unavailable.") from None


async def get_server_role(user_id: str, fallback: str = "user") -> str:
    """Current role; fallback retained for compatibility but never grants privilege."""
    record = await _get_account(user_id)
    return (getattr(record, "custom_claims", None) or {}).get("role") or "user"
