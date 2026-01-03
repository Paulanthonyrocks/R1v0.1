import time
from typing import Dict, Tuple
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
import logging

logger = logging.getLogger("app.middleware.rate_limit")

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limit: int = 100, window: int = 60):
        """
        :param limit: Number of requests allowed
        :param window: Window size in seconds
        """
        super().__init__(app)
        self.limit = limit
        self.window = window
        # In-memory storage: {ip: [timestamps]}
        # For production, use Redis.
        self.request_counts: Dict[str, list] = {}

    async def dispatch(self, request: Request, call_next) -> Response:
        client_ip = request.client.host if request.client else "unknown"
        
        # Skip rate limiting for static files or specific paths if needed
        if request.url.path.startswith("/snapshots") or request.url.path.startswith("/static"):
            return await call_next(request)

        now = time.time()
        
        if client_ip not in self.request_counts:
            self.request_counts[client_ip] = []

        # Filter out timestamps outside the window
        self.request_counts[client_ip] = [t for t in self.request_counts[client_ip] if now - t < self.window]

        if len(self.request_counts[client_ip]) >= self.limit:
            logger.warning(f"Rate limit exceeded for IP: {client_ip}")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later."
            )

        self.request_counts[client_ip].append(now)
        
        response = await call_next(request)
        
        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(self.limit)
        response.headers["X-RateLimit-Remaining"] = str(self.limit - len(self.request_counts[client_ip]))
        response.headers["X-RateLimit-Reset"] = str(int(now + self.window))
        
        return response
