"""Resolve the real client IP behind a reverse proxy / tunnel.

Behind a tunnel (cloudflared quick tunnel, loca.lt, nginx, ...) the TCP peer
seen by uvicorn is the PROXY's IP, so ``request.client.host`` is the same
edge IP for every browser. Rate limiting keyed on it aggregates all real
users into one bucket (observed 2026-11-01: every tunneled request seen as
105.127.11.243, the /api/v1/analytics 10/60 bucket exhausted -> 429 for the
whole deployment at once).

Header trust order:
  1. CF-Connecting-IP — Cloudflare overwrites this on proxied requests, so
     it cannot be spoofed by clients coming through Cloudflare.
  2. X-Forwarded-For, first hop (the original client) — generic proxy form.
  3. request.client.host — direct connection, no proxy in between.

All inbound traffic already passes the outermost TunnelAuthMiddleware, so a
direct (non-tunneled) caller forging these headers already holds the tunnel
secret; the headers only refine identity, they don't grant access.
"""

from starlette.requests import Request


def resolve_client_ip(request: Request) -> str:
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip and cf_ip.strip():
        return cf_ip.strip()

    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first

    return request.client.host if request.client else "unknown"
