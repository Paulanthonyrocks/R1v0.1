import time
import logging
from starlette.requests import Request
from starlette.types import ASGIApp, Scope, Receive, Send

logger = logging.getLogger("app.middleware")


class LoggingMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            # WebSocket or lifespan – pass through untouched
            await self.app(scope, receive, send)
            return

        start_time = time.monotonic()
        request = Request(scope, receive)
        request_id = request.headers.get("x-request-id", "N/A")

        # Log request details
        logger.info(
            f"Request: {request.method} {request.url.path} from {request.client.host if request.client else 'unknown'} [ReqID: {request_id}]"
        )

        # Capture the response status
        response_started = False
        status_code = 0

        async def send_wrapper(message):
            nonlocal response_started, status_code
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            elapsed = time.monotonic() - start_time
            logger.warning(
                f"Request failed: {request.method} {request.url.path} after {elapsed:.4f}s [ReqID: {request_id}]"
            )
            raise
        else:
            elapsed = time.monotonic() - start_time
            logger.info(
                f"Response: {request.method} {request.url.path} Status: {status_code} - {elapsed:.4f}s [ReqID: {request_id}]"
            )
