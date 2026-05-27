"""
This module provides semantic exception classes that extend FastAPI's HTTPException.

Usage Notes:
1. HTTP Cycle: These exceptions are intended for use during the HTTP request/response cycle 
   (e.g., during the WebSocket handshake). If raised inside an established WebSocket 
   connection, they will cause an unhandled server error. Use `websocket.close(code=...)` 
   for WebSocket-specific errors.
2. Security: When passing dynamic strings to the `detail` parameter, ensure they are 
   sanitized to prevent information disclosure or XSS if the error is rendered in a browser.
"""

from typing import Optional
from fastapi import HTTPException, status


class ResourceNotFound(HTTPException):
    def __init__(self, detail: str = "Resource not found.", headers: Optional[dict] = None):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail, headers=headers)


class OperationFailed(HTTPException):
    def __init__(self, detail: str = "Operation failed.", headers: Optional[dict] = None):
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail, headers=headers
        )


class BadRequest(HTTPException):
    def __init__(self, detail: str = "Bad request.", headers: Optional[dict] = None):
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail, headers=headers)


class Unauthorized(HTTPException):
    def __init__(self, detail: str = "Not authenticated.", headers: Optional[dict] = None):
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail, headers=headers)


class Forbidden(HTTPException):
    def __init__(self, detail: str = "Not authorized to perform this action.", headers: Optional[dict] = None):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail, headers=headers)


class RateLimitExceeded(HTTPException):
    def __init__(self, detail: str = "Rate limit exceeded.", headers: Optional[dict] = None):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail, headers=headers
        )


class Conflict(HTTPException):
    def __init__(self, detail: str = "Resource conflict occurred.", headers: Optional[dict] = None):
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail, headers=headers)


class ValidationError(HTTPException):
    def __init__(self, detail: str = "Validation error.", headers: Optional[dict] = None):
        super().__init__(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail, headers=headers)


class ServiceUnavailable(HTTPException):
    def __init__(self, detail: str = "Service temporarily unavailable.", headers: Optional[dict] = None):
        super().__init__(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail, headers=headers)
