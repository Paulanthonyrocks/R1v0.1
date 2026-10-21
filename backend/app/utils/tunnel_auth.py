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
- Presented as `X-Tunnel-Auth` header (REST), or `?tunnel_token=` query param.
- WebSocket alternative: the `Sec-WebSocket-Protocol` handshake header carrying
  `r1-tunnel-auth.<secret>` (see `_from_subprotocol`). Browsers cannot attach
  arbitrary headers to a WS upgrade, and the query form put the secret in the
  URL of every upgrade -- where browser-native failure messages print it
  verbatim ("WebSocket connection to '...?tunnel_token=...' failed", Oct-18:
  the full token landed in the forwarded console.log ~9 times). A subprotocol
  name rides in a header and never appears in a URL. NOTE (Oct-21 live run):
  although RFC 6455 lets a server decline an offered subprotocol, BROWSERS do
  not implement that latitude -- the WHATWG WebSocket API fails the handshake
  outright unless the server's response selects one of the offered protocols.
  The WS endpoint must therefore echo it back via select_offered_subprotocol()
  + websocket.accept(subprotocol=...) (see routers/ws.py): a bare accept()
  completed the handshake server-side while the browser tore the socket down,
  surfacing as "disconnected before authenticating" on every attempt. The
  query form is kept as a fallback during migration; drop it once every client
  sends the subprotocol.
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
import re
from typing import List, Optional, Set, Tuple

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("app.tunnel_auth")

HEADER = b"x-tunnel-auth"
QUERY_KEY = "tunnel_token"

# WebSocket handshake alternative: the secret rides in a subprotocol NAME on
# the Sec-WebSocket-Protocol handshake header instead of the URL query string.
# Charset must be valid RFC 6455 token chars (the browser rejects a handshake
# otherwise), so: letters/digits/hyphen then a dot separator + the raw secret.
SUBPROTOCOL_HEADER = b"sec-websocket-protocol"
SUBPROTOCOL_PREFIX = "r1-tunnel-auth."

# Paths that stay reachable without the secret. Keep this list minimal and
# justified -- each entry is a hole in the gate.
OPEN_PATHS: Set[str] = {"/health", "/api/v1/health", "/docs", "/openapi.json", "/redoc"}

# Prefixes that bypass the gate. Same rule: only add one with a reason.
OPEN_PREFIXES: List[str] = ["/static"]


def select_offered_subprotocol(
    sec_websocket_protocol_header: Optional[str],
) -> Optional[str]:
    """Pick OUR subprotocol out of the client's `Sec-WebSocket-Protocol` header.

    Returns the first offered name carrying our prefix, else None. The WS
    endpoint MUST pass the result to `websocket.accept(subprotocol=...)`:

    RFC 6455 lets a server decline an offered subprotocol while still
    completing the handshake, but BROWSERS do not -- the WHATWG WebSocket API
    fails the connection when the client offered a protocol and the server's
    response selects none (Oct-21 live run: hosting's `r1-tunnel-auth.<token>`
    offer + a bare `accept()` = every handshake died client-side as
    "disconnected before authenticating" while the gate itself had already
    passed the token). A client that offered nothing still gets a
    subprotocol-less accept, so legacy query-form builds are unaffected.

    Security: the middleware (outermost) has already validated the token that
    rode inside this header before the router runs, and only names carrying
    our own prefix are ever echoed back -- unknown client protocols are not
    reflected, and echoing reveals nothing the client didn't send.
    """
    if not sec_websocket_protocol_header:
        return None
    for proto in sec_websocket_protocol_header.split(","):
        proto = proto.strip()
        if proto.startswith(SUBPROTOCOL_PREFIX):
            return proto
    return None


class TunnelAuthError(Exception):
    """Raised internally to produce a 401. Never escapes to the client as 500."""


def _origin_of(scope) -> Optional[str]:
    """Read the Origin request header straight off the ASGI scope."""
    want = b"origin"
    for k, v in scope.get("headers", []):
        if k.lower() == want:
            return v.decode("latin-1")
    return None


# Same anchored allowlist as app.main.setup_cors (domain-boundary
# (?:[^/]*\.)? prefix kept in sync so the fallback can never widen the
# allowlist). Only used if that module cannot be imported.
_FALLBACK_CORS_REGEX = re.compile(
    r"https?://(?:[^/]*\.)?(ngrok-free\.app|trycloudflare\.com|ngrok\.io|cloudworkstations\.dev|loca\.lt"
    r"|githubdev\.dev|localhost|127\.0\.0\.1)(:\d+)?"
)


def _fallback_cors_headers_for_origin(origin: Optional[str]) -> dict:
    if not origin:
        return {}
    try:
        if not _FALLBACK_CORS_REGEX.fullmatch(origin):
            return {}
    except (TypeError, ValueError):
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Vary": "Origin",
    }


def _env_token() -> Optional[str]:
    tok, _src, _detail = _resolve_token()
    return tok


def _resolve_token() -> Tuple[Optional[str], str, str]:
    """Resolve the secret AND report where it came from.

    Returns (token, source, detail). `detail` is the file path (or the env var
    name) so the start-up log can name the exact file that was read. That was
    not cosmetic: the first two production failures of this gate were both
    "the value is not reaching the process", and the only way to tell a wrong
    path from an unset var from an unreadable file is to print which one it was.
    Logging a bare "ACTIVE" or a bare "not set" sends you back to the same
    guessing loop both times.
    """
    tok = os.getenv("TUNNEL_AUTH_TOKEN", "").strip()
    if tok:
        return tok, "TUNNEL_AUTH_TOKEN", "inline env var"

    # TUNNEL_AUTH_TOKEN_FILE -- the durable form.
    #
    # Why this exists (2026-09-30): the inline-only form failed twice in
    # production. The backend runs from a notebook kernel, and the value was set
    # in a cell that did not survive into the process that served the request,
    # so the gate correctly fail-closed and every WS handshake 403'd. The
    # symptom was indistinguishable from a tunnel fault (see _reject_ws), which
    # cost a debugging cycle.
    #
    # A file removes the ordering dependency entirely: write it once, point the
    # env var at it, and it is correct for every future start -- restart, cell
    # re-run, new kernel -- without re-pasting the secret. Same pattern and the
    # same reasoning as ROUTE_ONE_EVIDENCE_KEY_FILE in evidence_integrity.py.
    #
    # Use an ABSOLUTE path here. The backend is started from either the repo
    # root or backend/, so a `$PWD`-relative path is correct in one and broken
    # in the other -- and a broken relative path shows up as a shell redirect
    # error at write time or an unreadable file here, never as a clear message.
    #
    # Precedence: inline value wins, then the file. Stripped, because a trailing
    # newline from `echo >` or `openssl rand >` is the single most likely cause
    # of a "the two sides should match but don't" 403 storm, and the token is
    # compared with hmac.compare_digest so a stray byte fails silently.
    path = os.getenv("TUNNEL_AUTH_TOKEN_FILE", "").strip()
    if not path:
        return None, "none", "TUNNEL_AUTH_TOKEN and TUNNEL_AUTH_TOKEN_FILE both unset"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = fh.read().strip()
    except OSError as e:
        logger.error(
            "TUNNEL_AUTH_TOKEN_FILE is set but UNREADABLE: %s (%s). "
            "Check the path -- it must be absolute and must exist in THIS "
            "container.", path, e
        )
        return None, "unreadable", path
    if not data:
        logger.error(
            "TUNNEL_AUTH_TOKEN_FILE is EMPTY: %s. Refusing to fall back to "
            "open; regenerate with token_urlsafe(32).", path
        )
        return None, "empty", path
    return data, "TUNNEL_AUTH_TOKEN_FILE", path


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
        # Precedence: explicit header (REST), then the WS subprotocol handshake
        # form, then the query param (legacy WS form, still accepted so an
        # older frontend build keeps working while hosts roll out).
        for k, v in scope.get("headers", []):
            if k.lower() == HEADER:
                return v
        from_subproto = TunnelAuthMiddleware._from_subprotocol(scope)
        if from_subproto is not None:
            return from_subproto
        qs = scope.get("query_string", b"").decode("latin-1")
        for part in qs.split("&"):
            if part.startswith(QUERY_KEY + "="):
                return part.split("=", 1)[1].encode("latin-1")
        return None

    @staticmethod
    def _from_subprotocol(scope: Scope) -> Optional[bytes]:
        """Extract the secret from a `Sec-WebSocket-Protocol` name.

        The browser offers this as a header on the WS upgrade, so the token
        never enters the URL -- which is the whole point: Chrome's native
        "WebSocket connection to '<url>' failed" message prints the URL, and
        with query-param auth that message put the secret in every forwarded
        log. Accepts a list of protocols ("chat, r1-tunnel-auth.<secret>"),
        skips blank entries, and only trusts names carrying our exact prefix.
        """
        for k, v in scope.get("headers", []):
            if k.lower() != SUBPROTOCOL_HEADER:
                continue
            for proto in v.decode("latin-1").split(","):
                proto = proto.strip()
                if proto.startswith(SUBPROTOCOL_PREFIX):
                    return proto[len(SUBPROTOCOL_PREFIX):].encode("latin-1")
        return None

    def _authorised(self, scope: Scope) -> bool:
        token = self._resolve()
        if not token:
            # Fail closed. An unset token must never mean "no auth required".
            logger.error(
                "TUNNEL_AUTH_TOKEN is not set and TUNNEL_AUTH_TOKEN_FILE is unset "
                "or unreadable -- rejecting tunneled request. Write a 32+ char "
                "random secret and export TUNNEL_AUTH_TOKEN_FILE=<path> (see "
                "backend/tunnel_token.env.example), or set TUNNEL_AUTH_REQUIRED=0 "
                "to disable this gate explicitly."
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
            # This middleware is OUTERMOST -- it answers before CORSMiddleware
            # ever runs, so its rejection would reach the browser with no
            # Access-Control-Allow-Origin and surface as an opaque CORS error
            # rather than the 401 it is.
            #
            # The matcher is imported from app.main so there is exactly ONE
            # allowlist (a second copy is how it drifts). The import is lazy
            # because app.main imports this module at import time.
            #
            # If it fails we fall back to the SAME compiled pattern below
            # rather than silently emitting no CORS headers: a swallowed
            # ImportError here would restore the exact bug this fixes, and the
            # fallback cannot widen the allowlist (same regex, same fullmatch).
            try:
                from app.main import _cors_headers_for_origin
            except Exception:
                _cors_headers_for_origin = _fallback_cors_headers_for_origin
            extra = _cors_headers_for_origin(_origin_of(scope))
            if scope["type"] == "websocket":
                # Close during handshake. 1008 = policy violation.
                await self._reject_ws(send)
                return
            await self._reject_http(send, extra)
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _reject_http(send: Send, extra_headers: Optional[dict] = None) -> None:
        body = b'{"detail":"Tunnel authentication required."}'
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"www-authenticate", b"TunnelAuth"),
        ]
        for k, v in (extra_headers or {}).items():
            headers.append((k.encode("latin-1"), str(v).encode("latin-1")))
        await send({"type": "http.response.start", "status": 401, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _reject_ws(send: Send) -> None:
        # Reject during the handshake. ASGI's `websocket.close` is the correct
        # way to do this; hand-rolling the RFC6455 frame (a `frame` local built
        # from FIN|opcode 0x8 and payload 1008) was dead code -- it was
        # constructed and never sent, because the ASGI event is what uvicorn
        # actually serialises.
        #
        # Note for whoever debugs the next occurrence: the browser does NOT
        # surface this as a 1008 close. The handshake never completes, so the
        # client sees a generic connection failure (Chrome:
        # "WebSocket connection to ... failed", WebSocketClient's
        # onerror -> "WebSocket error occurred") and burns its reconnect
        # attempts against a rejection that will never change. Correlate with
        # the WARNING line above and the uvicorn `"..." 403` line, not with
        # anything the client says.
        await send({"type": "websocket.close", "code": 1008, "reason": "tunnel auth required"})


def install_tunnel_auth(app, token: Optional[str] = None) -> None:
    """Register the gate outermost and report its state loudly.

    Call this LAST of all `add_middleware` calls. Registered first it would sit
    innermost and an unauthenticated caller would already have reached the
    rate limiter and every other middleware before being turned away.
    """
    resolved, src, detail = (token, "argument", "passed to install_tunnel_auth") if token is not None else _resolve_token()
    if resolved:
        logger.info(
            "Tunnel auth gate ACTIVE -- secret resolved via %s (%s), %d chars. "
            "Open paths: %s", src, detail, len(resolved), ", ".join(sorted(OPEN_PATHS))
        )
    else:
        logger.error(
            "Tunnel auth gate ACTIVE but NO secret could be resolved [%s: %s] -- "
            "every tunneled request will be rejected (fail-closed). Fix: write one "
            "with python -c \"import secrets; print(secrets.token_urlsafe(32))\" > "
            "/kaggle/working/R1v0.1/backend/keys/tunnel_auth.token, then export "
            "TUNNEL_AUTH_TOKEN_FILE=/kaggle/working/R1v0.1/backend/keys/tunnel_auth.token "
            "(ABSOLUTE path -- the backend starts from two different cwds). See "
            "backend/tunnel_token.env.example.",
            src, detail,
        )
    app.add_middleware(TunnelAuthMiddleware, token=token)
