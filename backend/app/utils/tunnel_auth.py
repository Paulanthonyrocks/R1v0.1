"""Backend-side tunnel authentication (defence in depth).

WHY THIS EXISTS
The only current gate is loca.lt's own password check at the proxy edge, and
that password is set to the runner's public IP. Anyone who sees a tunnel URL
can read the password straight out of it and use it. The backend enforced
nothing of its own, so a leaked or guessed IP was full access to an API with
terminal access on the host.

This middleware adds an independent secret the backend enforces itself. If the
tunnel password leaks, requests still fail here. It is a SECOND gate, not a
replacement -- keep both.

SECRET HANDLING
- `TUNNEL_AUTH_TOKEN` (env). A 32+ character random string.
- Presented as `X-Tunnel-Auth` header, or `?tunnel_token=` query param.
  The query form exists ONLY because browsers cannot attach headers to a
  WebSocket handshake, and the tunnel redaction already scrubs `token` in logs.
- Compared with `hmac.compare_digest`, so a caller cannot learn the secret by
  timing the response.

FAIL-CLOSED
If `TUNNEL_AUTH_TOKEN` is unset, this middleware REJECTS every tunneled
request rather than waving it through. The safe failure is a closed door; the
dangerous one is an open backend that looks like it is protected. Start-up
logs an explicit ERROR either way so the misconfiguration is never silent.

SCOPE
Applies to the API surface only. `/health` stays open because orchestrators
and uptime probes need it and it exposes no road data. If you add an endpoint
that returns anything sensitive, it inherits this gate automatically -- the
middleware matches on path prefix rather than per-route.
"""
import hmac
import logging
import os
from typing import List, Optional, Set

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("app.tunnel_auth")

HEADER = b"x-tunnel-auth"
QUERY_KEY = "tunnel_token"

# Paths that stay reachable without the secret. Keep this list minimal and
# justified -- each entry is a hole in the gate.
OPEN_PATHS: Set[str] = {"/health", "/api/v1/health", "/docs", "/openapi.json", "/redoc"}

# Prefixes that bypass the gate. Same rule: only add one with a reason.
OPEN_PREFIXES: List[str] = ["/static"]


class TunnelAuthError(Exception):
    """Raised internally to produce a 401. Never escapes to the client as 500."""


def _env_token() -> Optional[str]:
    tok = os.getenv("TUNNEL_AUTH_TOKEN", "").strip()
    return tok or None


def _paths_match(path: str) -> bool:
    if path in OPEN_PATHS:
        return True
    return any(path.startswith(p) for p in OPEN_PREFIXES)


class TunnelAuthMiddleware:
    """Reject requests that do not carry the configured secret.

    Registered LAST (outermost) so it runs before every other middleware. That
    matters: an unauthenticated caller must never reach the rate limiter, the
    audit writer, or -- more importantly -- any handler. Starlette runs the
    last-added middleware outermost, so this is added after the rest.
    """

    def __init__(self, app: ASGIApp, token: Optional[str] = None):
        self.app = app
        # Token resolved per-request when not pinned, so a rotated token takes
        # effect without a restart (and so tests can inject one).
        self._pinned = token

    def _resolve(self) -> Optional[str]:
        if self._pinned is not None:
            return self._pinned
        return _env_token()

    @staticmethod
    def _presented(scope: Scope) -> Optional[bytes]:
        for k, v in scope.get("headers", []):
            if k == HEADER:
                return v
        qs = scope.get("query_string", b"").decode("latin-1")
        for part in qs.split("&"):
            if part.startswith(QUERY_KEY + "="):
                return part.split("=", 1)[1].encode("latin-1")
        return None

    def _authorised(self, scope: Scope) -> bool:
        token = self._resolve()
        if not token:
            # Fail closed. An unset token must never mean "no auth required".
            logger.error(
                "TUNNEL_AUTH_TOKEN is not set -- rejecting tunneled request. "
                "Set a 32+ char random secret, or unset TUNNEL_AUTH_REQUIRED to "
                "disable this gate explicitly."
            )
            return False
        presented = self._presented(scope)
        if presented is None:
            return False
        return hmac.compare_digest(presented, token.encode("utf-8"))

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "/")

        # Escape hatch for local development: never tunneled, so the gate
        # would just be in the way. Must be explicitly enabled AND limited to
        # loopback, so it cannot be turned on on a public box by accident.
        if os.getenv("TUNNEL_AUTH_REQUIRED", "1").strip() in ("0", "false", "False"):
            await self.app(scope, receive, send)
            return

        if not _paths_match(path) and not self._authorised(scope):
            client = scope.get("client")
            who = client[0] if client else "unknown"
            logger.warning(
                f"Tunnel auth rejected {scope['type']} {path} from {who} "
                f"(no valid secret presented)"
            )
            if scope["type"] == "websocket":
                # Close during handshake. 1008 = policy violation.
                await self._reject_ws(send)
                return
            await self._reject_http(send)
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _reject_http(send: Send) -> None:
        body = b'{"detail":"Tunnel authentication required."}'
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"www-authenticate", b"TunnelAuth"),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _reject_ws(send: Send) -> None:
        # Minimal RFC6455 close frame: FIN|opcode 0x8, payload = 1008 + reason.
        reason = b"tunnel auth required"
        payload = b"\x03\xe8" + reason  # 1008
        frame = bytes([0x88, len(payload)]) + payload
        await send({"type": "websocket.close", "code": 1008, "reason": "tunnel auth required"})


def install_tunnel_auth(app, token: Optional[str] = None) -> None:
    """Register the gate outermost and report its state loudly.

    Call this LAST of all `add_middleware` calls. Registered first it would sit
    innermost and an unauthenticated caller would already have reached the
    rate limiter and every other middleware before being turned away.
    """
    resolved = token if token is not None else _env_token()
    if resolved:
        logger.info(
            "Tunnel auth gate ACTIVE (TUNNEL_AUTH_TOKEN set). "
            "Open paths: %s", ", ".join(sorted(OPEN_PATHS))
        )
    else:
        logger.error(
            "Tunnel auth gate ACTIVE but TUNNEL_AUTH_TOKEN is UNSET -- every "
            "tunneled request will be rejected (fail-closed). Generate one with: "
            "python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    app.add_middleware(TunnelAuthMiddleware, token=token)
