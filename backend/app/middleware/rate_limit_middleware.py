import time
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
        path = request.url.path
        
        # Skip rate limiting for static files or specific paths if needed
        if path.startswith("/snapshots") or path.startswith("/static") or path.startswith("/api/v1/snapshots"):
            return await call_next(request)

        # Identify user
        user_id = request.headers.get("X-User-ID", request.client.host if request.client else "unknown")
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

        self.request_counts[key].append(now)
        
        response = await call_next(request)
        
        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(limit - len(self.request_counts[key]))
        response.headers["X-RateLimit-Reset"] = str(int(now + window))
        
        return response
