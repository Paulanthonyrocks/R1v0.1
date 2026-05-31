from starlette.types import ASGIApp, Scope, Receive, Send

class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            # WebSocket or lifespan – pass through untouched
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                
                # Use setdefault to avoid overwriting explicit custom headers
                # Headers in ASGI are bytes
                headers.setdefault(b"x-content-type-options", b"nosniff")
                headers.setdefault(b"x-frame-options", b"DENY")
                headers.setdefault(b"referrer-policy", b"strict-origin-when-cross-origin")
                headers.setdefault(b"content-security-policy", b"default-src 'self'")
                
                # HSTS only over HTTPS
                if scope.get("scheme") == "https":
                    headers.setdefault(b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                
                message["headers"] = list(headers.items())
            await send(message)

        await self.app(scope, receive, send_wrapper)
