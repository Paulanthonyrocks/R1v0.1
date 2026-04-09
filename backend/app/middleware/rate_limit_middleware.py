import time
import asyncio
from collections import deque
from typing import Dict, Tuple, Optional, List, Any
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
        # In-memory storage: {(user_id, path_pattern): deque([timestamps])}
        self.request_counts: Dict[Tuple[str, str], deque] = {}
        self.lock = asyncio.Lock()
        
        # User tiers limits (requests, window_seconds)
        self.tier_limits = {
            "anonymous": (limit, window),
            "authenticated": (limit * 2, window),
            "premium": (limit * 5, window)
        }

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
        
        # Fix: Do not completely bypass rate limits for heavy I/O endpoints like snapshots.
        # Instead of returning immediately, we let them proceed to a specific config 
        # or use a generous default limit to prevent resource exhaustion.
        # (The logic below now allows them to be rate limited if not explicitly whitelisted).
        
        # Identify user
        # Fix: Never trust X-User-ID header directly as it can be spoofed to bypass rate limits.
        # Use authenticated user state or fallback to client host.
        user_id = getattr(request.state, "user_id", request.client.host if request.client else "unknown")
        user_tier = getattr(request.state, "user_tier", "anonymous")

        # Determine limits based on tier or path-specific config
        pattern, config = self._get_config_for_path(path)
        
        # If path specific config is default, use tier limits logic
        if pattern == "default":
            limit, window = self.tier_limits.get(user_tier, self.tier_limits["anonymous"])
        else:
            limit, window = config.limit, config.window

        key = (user_id, pattern)
        now = time.time()
        
        async with self.lock:
            if key not in self.request_counts:
                self.request_counts[key] = deque()

            # Efficiently prune stale timestamps from the left (O(1) per pop)
            window_start = now - window
            while self.request_counts[key] and self.request_counts[key][0] < window_start:
                self.request_counts[key].popleft()

            if len(self.request_counts[key]) >= limit:
                logger.warning(f"Rate limit exceeded for User: {user_id} ({user_tier}) on path: {path}")
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": "Rate limit exceeded. Please try again later."},
                    headers={"Retry-After": str(window)}
                )

            self.request_counts[key].append(now)
            current_count = len(self.request_counts[key])

        # Periodic cleanup of totally empty keys to prevent unbounded memory growth
        if len(self.request_counts) > 10000:
            keys_to_del = [k for k, v in self.request_counts.items() if not v]
            for k in keys_to_del:
                del self.request_counts[k]

        response = await call_next(request)
        
        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(limit - current_count)
        response.headers["X-RateLimit-Reset"] = str(int(now + window))
        
        return response
