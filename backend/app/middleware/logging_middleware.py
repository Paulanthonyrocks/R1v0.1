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
        start_time = time.time()

        # Log request details
        logger.info(
            f"Request: {request.method} {request.url.path} from {request.client.host}"
        )

        response = await call_next(request)

        process_time = time.time() - start_time
        # Log response details
        logger.info(
            f"Response: {request.method} {request.url.path} Status: {response.status_code} - Processed in {process_time:.4f}s"
        )

        return response
