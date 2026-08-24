import time
import asyncio
from typing import Dict, Tuple, Optional, List
from fastapi import Request
from starlette.types import ASGIApp, Scope, Receive, Send
import logging
from pydantic import BaseModel

logger = logging.getLogger("app.middleware.rate_limit")

class RateLimitConfig(BaseModel):
    limit: int
    window: int

class RateLimitMiddleware:
    """
    In-memory rate limiting middleware.
    
    NOTE: This implementation uses local process memory and is NOT suitable for 
    distributed deployments or multi-worker setups (e.g., Uvicorn with --workers > 1).
    For production environments with multiple workers, use a shared backend like Redis.
    """
    def __init__(
        self, 
        app: ASGIApp, 
        limit: int = 100, 
        window: int = 60,
        rate_limits: Optional[Dict[str, RateLimitConfig]] = None
    ):
        """
        :param limit: Default number of requests allowed
        :param window: Default window size in seconds
        :param rate_limits: Optional dict mapping paths to RateLimitConfig
        """
        self.app = app
        self.default_config = RateLimitConfig(limit=limit, window=window)
        self.rate_limits = rate_limits or {}
        # In-memory storage: {(user_id, path_pattern): [timestamps]}
        self.request_counts: Dict[Tuple[str, str], List[float]] = {}
        self._lock = asyncio.Lock()
        
        # User tiers limits (requests, window_seconds)
        self.tier_limits = {
            "anonymous": (limit, window),
            "authenticated": (limit * 2, window),
            "premium": (limit * 5, window)
        }
        
        # Start periodic cleanup task
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

    async def _periodic_cleanup(self):
        """Periodically remove stale entries from request_counts to prevent memory leaks."""
        while True:
            try:
                await asyncio.sleep(300) # Clean every 5 minutes
                async with self._lock:
                    now = time.monotonic()
                    # Use list() to avoid 'dictionary changed size during iteration'
                    keys_to_delete = [
                        key for key, timestamps in self.request_counts.items()
                        if not timestamps or (now - timestamps[-1] > 3600) # Remove if inactive for 1 hour
                    ]
                    for key in keys_to_delete:
                        del self.request_counts[key]
                    if keys_to_delete:
                        logger.debug(f"RateLimitMiddleware: Cleaned up {len(keys_to_delete)} stale entries.")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error during rate limit cleanup: {e}")

    def _get_config_for_path(self, path: str) -> Tuple[str, RateLimitConfig]:
        """Find the most specific matching config for a given path."""
        for pattern, config in self.rate_limits.items():
            if path.startswith(pattern):
                return pattern, config
        return "default", self.default_config


    _tier_cache: Dict[str, tuple] = {}  # token-hash -> (tier, expiry_monotonic)

    async def _resolve_tier(self, request: Request) -> str:
        """Resolve the caller's tier from their Firebase bearer token.

        AUDIT FIX (2026-08-24): this used to read request.state.user_tier, which no
        auth layer ever populates before the middleware runs — every caller was
        silently 'anonymous' and the authenticated/premium tiers were dead code.

        Tier mapping (role claim from the verified Firebase ID token):
          admin  -> premium      (5x base limit)
          viewer/user/authenticated -> authenticated (2x)
          no/invalid token          -> anonymous    (base limit)

        Verification is cached per token (hash) for 5 minutes so we don't pay
        Firebase certificate checks on every request. On any verification error the
        caller is treated as anonymous — fail closed.
        """
        import hashlib
        import time as _time

        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return "anonymous"
        token = auth_header[7:].strip()
        if not token:
            return "anonymous"

        token_hash = hashlib.sha256(token.encode()).hexdigest()[:24]
        now = _time.monotonic()
        cached = self._tier_cache.get(token_hash)
        if cached and now < cached[1]:
            return cached[0]

        try:
            from app.utils.auth_utils import verify_firebase_token
            decoded = await asyncio.to_thread(verify_firebase_token, token)
            role = str(decoded.get("role", "")).lower()
            if role == "admin":
                tier = "premium"
            elif role in ("viewer", "user", "authenticated"):
                tier = "authenticated"
            else:
                tier = "authenticated"
        except Exception:
            tier = "anonymous"

        # Bound the cache so a flood of unique tokens can't grow it unbounded.
        if len(self._tier_cache) > 1000:
            self._tier_cache.clear()
        self._tier_cache[token_hash] = (tier, now + 300.0)
        return tier

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            # WebSocket or lifespan – pass through untouched
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)

        # Skip rate limiting for OPTIONS requests (CORS preflight)
        if request.method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        path = request.url.path
        
        # Skip rate limiting for static files, specific paths, and WebSocket connections
        if path.startswith("/snapshots") or path.startswith("/static") or path.startswith("/api/v1/snapshots") or path.startswith("/api/v1/ws"):
            await self.app(scope, receive, send)
            return

        # Identify user: Use client IP as the primary identifier.
        user_id = request.client.host if request.client else "unknown"
        user_tier = await self._resolve_tier(request)

        # Determine limits based on tier or path-specific config
        pattern, config = self._get_config_for_path(path)
        
        # If path specific is default, use tier limits logic
        if pattern == "default":
            limit, window = self.tier_limits.get(user_tier, self.tier_limits["anonymous"])
        else:
            limit, window = config.limit, config.window

        key = (user_id, pattern)
        now = time.monotonic()
        
        async with self._lock:
            if key not in self.request_counts:
                self.request_counts[key] = []

            # Filter out timestamps outside the window
            self.request_counts[key] = [t for t in self.request_counts[key] if now - t < window]

            if len(self.request_counts[key]) >= limit:
                logger.warning(f"Rate limit exceeded for User: {user_id} ({user_tier}) on path: {path}")
                await send({
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"retry-after", str(window).encode()),
                    ],
                })
                await send({
                    "type": "http.response.body",
                    "body": b'{"detail": "Rate limit exceeded. Please try again later."}',
                })
                return

            self.request_counts[key] = self.request_counts[key] + [now]
            
            # Calculate reset time for the sliding window (earliest timestamp + window)
            reset_time = self.request_counts[key][0] + window if self.request_counts[key] else now + window
        
        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-ratelimit-limit", str(limit).encode()))
                headers.append((b"x-ratelimit-remaining", str(limit - len(self.request_counts.get(key, []))).encode()))
                headers.append((b"x-ratelimit-reset", str(int(reset_time)).encode()))
                message["headers"] = headers
            await send(message)
            
        await self.app(scope, receive, send_wrapper)
