import time
import asyncio
from typing import Dict, Tuple, Optional, List
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, JSONResponse
import logging
from pydantic import BaseModel

logger = logging.getLogger("app.middleware.rate_limit")

class RateLimitConfig(BaseModel):
    limit: int
    window: int

class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    In-memory rate limiting middleware.
    
    NOTE: This implementation uses local process memory and is NOT suitable for 
    distributed deployments or multi-worker setups (e.g., Uvicorn with --workers > 1).
    For production environments with multiple workers, use a shared backend like Redis.
    """
    def __init__(
        self, 
        app, 
        limit: int = 100, 
        window: int = 60,
        rate_limits: Optional[Dict[str, RateLimitConfig]] = None
    ):
        """
        :param limit: Default number of requests allowed
        :param window: Default window size in seconds
        :param rate_limits: Optional dict mapping paths to RateLimitConfig
        """
        super().__init__(app)
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

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip rate limiting for OPTIONS requests (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        
        # Skip rate limiting for static files, specific paths, and WebSocket connections
        if path.startswith("/snapshots") or path.startswith("/static") or path.startswith("/api/v1/snapshots") or path.startswith("/api/v1/ws"):
            return await call_next(request)

        # Identify user: Never trust X-User-ID header for rate limiting as it is trivially spoofable.
        # Use client IP as the primary identifier.
        user_id = request.client.host if request.client else "unknown"
        user_tier = getattr(request.state, "user_tier", "anonymous")

        # Determine limits based on tier or path-specific config
        pattern, config = self._get_config_for_path(path)
        
        # If path specific config is default, use tier limits logic
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
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": "Rate limit exceeded. Please try again later."},
                    headers={"Retry-After": str(window)}
                )

            self.request_counts[key] = self.request_counts[key] + [now]
            
            # Calculate reset time for the sliding window (earliest timestamp + window)
            reset_time = now + window
            if self.request_counts[key]:
                reset_time = self.request_counts[key][0] + window
        
        response = await call_next(request)
        
        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(limit - len(self.request_counts.get(key, [])))
        response.headers["X-RateLimit-Reset"] = str(int(reset_time))
        
        return response
