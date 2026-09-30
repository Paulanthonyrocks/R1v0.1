"""Tunnel auth gate contract tests.

The gate is the only thing standing between a leaked loca.lt password (which is
the runner's public IP, readable from any tunnel URL) and full access to an API
with terminal access on the host. These tests pin the properties that matter:

  - an unauthenticated request is rejected
  - a WRONG secret is rejected (the IP password is not sufficient)
  - no secret configured means REJECT, never "open"
  - health stays reachable for probes
  - WebSockets are closed, not left hanging
"""
import asyncio
import importlib.util
import os
import sys
import types
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
TARGET = BACKEND / 'app/utils/tunnel_auth.py'

# starlette is absent locally (deps live on Kaggle) but is imported ONLY for
# ASGI type hints. Stub the names so the real module source loads unmodified.
if 'starlette' not in sys.modules:
    st = types.ModuleType('starlette')
    st_types = types.ModuleType('starlette.types')
    for _n in ('ASGIApp', 'Receive', 'Scope', 'Send'):
        setattr(st_types, _n, object)
    setattr(st, 'types', st_types)
    sys.modules['starlette'] = st
    sys.modules['starlette.types'] = st_types

spec = importlib.util.spec_from_file_location('tunnel_auth_under_test', TARGET)
ta = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ta
spec.loader.exec_module(ta)

SECRET = 'correct-horse-battery-staple-32chars'


class App:
    """Records whether the inner app was reached, and replies 200 like the
    real ASGI stack would — so the harness can distinguish "authorised
    (passed through, inner app answered)" from "rejected (middleware 401)".
    """
    def __init__(self):
        self.reached = 0

    async def __call__(self, scope, receive, send):
        self.reached += 1
        if scope['type'] == 'websocket':
            await send({'type': 'websocket.accept'})
            return
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'ok'})


def make_scope(path='/api/v1/vehicles/global/list', headers=None, qs=b'',
               stype='http'):
    return {
        'type': stype,
        'path': path,
        'query_string': qs,
        'headers': headers or [],
        'client': ('1.2.3.4', 5555),
    }


async def drain(send, stype='http'):
    events = []

    async def _send(msg):
        events.append(msg)

    return events


def run(middleware, scope):
    """Drive the middleware. Returns (status_or_None, sent).

    status is None when the request reached the inner app, which sends its own
    200 — that is the "authorised" outcome. A rejected request gets a 401 from
    the middleware itself.
    """
    sent = []

    async def _send(msg):
        sent.append(msg)

    async def _receive():
        return {'type': 'http.request'}

    asyncio.run(middleware(scope, _receive, _send))
    status = None
    for m in sent:
        if m.get('type') == 'http.response.start':
            status = m.get('status')
    return status, sent


def test_rejects_without_secret():
    inner = App()
    mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
    status, _ = run(mw, make_scope())
    assert status == 401, f'expected 401, got {status}'
    assert inner.reached == 0, 'request must NOT reach the inner app'


def test_accepts_with_matching_header():
    inner = App()
    mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
    status, _ = run(mw, make_scope(headers=[(ta.HEADER, SECRET.encode())]))
    assert status == 200, 'valid secret should pass through to the inner app'
    assert inner.reached == 1


def test_rejects_wrong_secret():
    """The loca.lt password alone must NOT be enough."""
    inner = App()
    mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
    # Simulates an attacker who has read the public IP out of the tunnel URL.
    status, _ = run(mw, make_scope(headers=[(ta.HEADER, b'34.48.223.113')]))
    assert status == 401, f'public-IP password must be rejected, got {status}'
    assert inner.reached == 0


def test_rejects_prefix_of_secret():
    """A truncated secret must not pass (guards against a prefix compare)."""
    inner = App()
    mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
    status, _ = run(mw, make_scope(headers=[(ta.HEADER, SECRET[:8].encode())]))
    assert status == 401


def test_accepts_via_query_param():
    """WebSockets can only carry the secret in the query string."""
    inner = App()
    mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
    qs = f'tunnel_token={SECRET}'.encode()
    status, _ = run(mw, make_scope(qs=qs))
    assert status == 200
    assert inner.reached == 1


def test_fails_closed_when_no_token_configured():
    inner = App()
    mw = ta.TunnelAuthMiddleware(inner, token=None)
    os.environ.pop('TUNNEL_AUTH_TOKEN', None)
    status, _ = run(mw, make_scope(headers=[(ta.HEADER, b'anything')]))
    assert status == 401, 'unset token must reject, never wave requests through'
    assert inner.reached == 0


def test_health_is_open_for_probes():
    inner = App()
    mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
    status, _ = run(mw, make_scope(path='/health'))
    assert status == 200, '/health must stay reachable without the secret'
    assert inner.reached == 1


def test_websocket_rejected_with_close_frame():
    inner = App()
    mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
    sent = []

    async def _send(msg):
        sent.append(msg)

    async def _receive():
        return {'type': 'websocket.connect'}

    scope = make_scope(path='/api/v1/ws/abc', stype='websocket')
    asyncio.run(mw(scope, _receive, _send))
    assert inner.reached == 0, 'unauthenticated WS must not reach the handler'
    assert any(m.get('type') == 'websocket.close' for m in sent), \
        'WS must be actively closed, not left pending'


def test_websocket_allowed_with_token():
    inner = App()
    mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
    sent = []

    async def _send(msg):
        sent.append(msg)

    async def _receive():
        return {'type': 'websocket.connect'}

    scope = make_scope(
        path='/api/v1/ws/abc', stype='websocket',
        qs=f'tunnel_token={SECRET}'.encode(),
    )
    asyncio.run(mw(scope, _receive, _send))
    assert inner.reached == 1, 'authenticated WS must reach the handler'


def test_disabled_escape_hatch_bypasses():
    """TUNNEL_AUTH_REQUIRED=0 opens the gate (local dev only)."""
    inner = App()
    mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
    os.environ['TUNNEL_AUTH_REQUIRED'] = '0'
    try:
        status, _ = run(mw, make_scope())
        assert status == 200, 'escape hatch should let the request through'
        assert inner.reached == 1
    finally:
        os.environ.pop('TUNNEL_AUTH_REQUIRED', None)


def test_evidence_paths_are_gated():
    """The sensitive routes must be behind the gate, not in the open list."""
    for p in ('/api/v1/incidents/x/evidence', '/api/v1/vehicles/global/list',
              '/api/v1/feeds', '/api/v1/analysis'):
        assert p not in ta.OPEN_PATHS, f'{p} must not be open'
        assert not any(p.startswith(o) for o in ta.OPEN_PREFIXES), f'{p} bypassed'


def _hdrs(sent):
    """Collect response headers case-insensitively (ASGI header names are
    bytes and this middleware emits some in mixed case)."""
    out = {}
    for m in sent:
        if m.get('type') == 'http.response.start':
            for k, v in m['headers']:
                out[k.decode().lower()] = v.decode()
    return out


def test_rejection_carries_cors_headers():
    """A 401 from the OUTERMOST middleware would otherwise reach the browser
    with no Access-Control-Allow-Origin and surface as an opaque CORS error
    rather than the 401 it is. Same defect that hid the 503 behind CORS."""
    inner = App()
    mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
    origin = 'https://3000-firebase-x.cluster-abc.cloudworkstations.dev'
    scope = make_scope(headers=[(b'origin', origin.encode())])
    _, sent = run(mw, scope)
    hdrs = _hdrs(sent)
    assert hdrs.get('access-control-allow-origin') == origin, \
        f"401 must echo the allowed Origin, got {hdrs}"
    assert hdrs.get('vary') == 'Origin'
    assert hdrs.get('access-control-allow-credentials') == 'true'


def test_rejection_omits_cors_for_disallowed_origin():
    """An origin outside the allowlist must NOT be reflected."""
    inner = App()
    mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
    scope = make_scope(headers=[(b'origin', b'https://evil.example.com')])
    _, sent = run(mw, scope)
    hdrs = _hdrs(sent)
    assert 'access-control-allow-origin' not in hdrs, \
        'must not reflect a disallowed origin'
    assert 'access-control-allow-credentials' not in hdrs, \
        'must not offer credentialed access to a disallowed origin'


if __name__ == '__main__':
    failures = []
    for name, fn in sorted(list(globals().items())):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print(f'  PASS  {name}')
            except AssertionError as e:
                failures.append((name, e))
                print(f'  FAIL  {name}: {e}')
            except Exception as e:  # noqa: BLE001
                failures.append((name, e))
                print(f'  ERROR {name}: {type(e).__name__}: {e}')
    print()
    print(f'{len(failures)} failure(s)')
    raise SystemExit(1 if failures else 0)
