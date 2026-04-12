from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # Fix: Remove X-XSS-Protection as it is deprecated and can be exploitable. 
        # The CSP header below is the modern replacement.
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # Content Security Policy (Defense-in-depth)
        # Fix: Restrict connect-src to prevent exfiltration of data to unknown domains.
        # Allowed: self, websockets (wss), and common mapping providers (e.g., Mapbox/OpenStreetMap)
        csp_policy = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: *.openstreetmap.org *.arcgisonline.com *.cartocdn.com unpkg.com; "
            "connect-src 'self' wss: https://api.mapbox.com https://tile.openstreetmap.org; "
            "font-src 'self' data:; "
            "frame-ancestors 'none';"
        )
        response.headers["Content-Security-Policy"] = csp_policy
        
        return response
