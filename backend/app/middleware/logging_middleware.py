import time
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

logger = logging.getLogger("app.middleware")


class LoggingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        start_time = time.monotonic()
        request_id = request.headers.get("x-request-id", "N/A")

        # Log request details
        logger.info(
            f"Request: {request.method} {request.url.path} from {request.client.host} [ReqID: {request_id}]"
        )

        try:
            response = await call_next(request)
        except Exception:
            elapsed = time.monotonic() - start_time
            logger.warning(
                f"Request failed: {request.method} {request.url.path} after {elapsed:.4f}s [ReqID: {request_id}]"
            )
            raise

        process_time = time.monotonic() - start_time
        # Log response details
        logger.info(
            f"Response: {request.method} {request.url.path} Status: {response.status_code} - {process_time:.4f}s [ReqID: {request_id}]"
        )

        return response
