"""Tunnel auth gate contract tests.

The gate is the only thing standing between a leaked loca.lt password (which is
the runner's public IP, readable from any tunnel URL) and full access to an API
with terminal access on the host. These tests pin the properties that matter:

  - an unauthenticated request is rejected
  - a WRONG secret is rejected (the IP password is not sufficient)
  - no secret configured means REJECT, never "open"
  - health stays reachable for probes
  - WebSockets are closed, not left hanging

RUNNER NOTE (2026-10-01): these were previously bare module-level `def test_*`
functions with a hand-rolled `__main__` runner. That works under pytest, but
`python -m unittest` collects ZERO of them and still exits 0 -- so a developer
without a virtualenv sees a green "Ran 0 tests / OK" and concludes the gate is
covered when nothing ran. They are now `unittest.TestCase` methods, which both
`pytest` and `python -m unittest` collect, so the suite is green only when
these assertions actually executed.
"""
import asyncio
import importlib.util
import os
import sys
import types
import unittest
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


def _hdrs(sent):
    """Collect response headers case-insensitively (ASGI header names are
    bytes and this middleware emits some in mixed case)."""
    out = {}
    for m in sent:
        if m.get('type') == 'http.response.start':
            for k, v in m['headers']:
                out[k.decode().lower()] = v.decode()
    return out


class TunnelAuthContractTests(unittest.TestCase):
    def test_rejects_without_secret(self):
        inner = App()
        mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
        status, _ = run(mw, make_scope())
        self.assertEqual(status, 401, f'expected 401, got {status}')
        self.assertEqual(inner.reached, 0, 'request must NOT reach the inner app')

    def test_accepts_with_matching_header(self):
        inner = App()
        mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
        status, _ = run(mw, make_scope(headers=[(ta.HEADER, SECRET.encode())]))
        self.assertEqual(status, 200, 'valid secret should pass through to the inner app')
        self.assertEqual(inner.reached, 1)

    def test_rejects_wrong_secret(self):
        """The loca.lt password alone must NOT be enough."""
        inner = App()
        mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
        # Simulates an attacker who has read the public IP out of the tunnel URL.
        status, _ = run(mw, make_scope(headers=[(ta.HEADER, b'34.48.223.113')]))
        self.assertEqual(status, 401, f'public-IP password must be rejected, got {status}')
        self.assertEqual(inner.reached, 0)

    def test_rejects_prefix_of_secret(self):
        """A truncated secret must not pass (guards against a prefix compare)."""
        inner = App()
        mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
        status, _ = run(mw, make_scope(headers=[(ta.HEADER, SECRET[:8].encode())]))
        self.assertEqual(status, 401)

    def test_accepts_via_query_param(self):
        """WebSockets can only carry the secret in the query string."""
        inner = App()
        mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
        qs = f'tunnel_token={SECRET}'.encode()
        status, _ = run(mw, make_scope(qs=qs))
        self.assertEqual(status, 200)
        self.assertEqual(inner.reached, 1)

    def test_fails_closed_when_no_token_configured(self):
        inner = App()
        mw = ta.TunnelAuthMiddleware(inner, token=None)
        os.environ.pop('TUNNEL_AUTH_TOKEN', None)
        # The file form resolves on every request, so it has to be cleared too --
        # otherwise a developer who has correctly configured it locally sees this
        # "fail-closed" test pass for the wrong reason (200) and the regression it
        # guards goes unnoticed on exactly the machine that would catch it.
        os.environ.pop('TUNNEL_AUTH_TOKEN_FILE', None)
        status, _ = run(mw, make_scope(headers=[(ta.HEADER, b'anything')]))
        self.assertEqual(status, 401, 'unset token must reject, never wave requests through')
        self.assertEqual(inner.reached, 0)

    def test_token_read_from_file(self):
        """TUNNEL_AUTH_TOKEN_FILE is the durable form (2026-09-30)."""
        tok_file = BACKEND / 'keys' / 'tunnel_auth.token'
        try:
            tok_file.parent.mkdir(parents=True, exist_ok=True)
            tok_file.write_text(SECRET + '\n')  # trailing newline on purpose
            os.environ.pop('TUNNEL_AUTH_TOKEN', None)
            os.environ['TUNNEL_AUTH_TOKEN_FILE'] = str(tok_file)
            try:
                self.assertEqual(
                    ta._env_token(), SECRET,
                    'file value must be read AND stripped -- a trailing newline from '
                    '`> file` is the likeliest cause of a two-sides-dont-match 403',
                )
                inner = App()
                mw = ta.TunnelAuthMiddleware(inner, token=None)
                status, _ = run(mw, make_scope(qs=f'tunnel_token={SECRET}'.encode()))
                self.assertEqual(status, 200, 'secret from file must authenticate')
                self.assertEqual(inner.reached, 1)
            finally:
                os.environ.pop('TUNNEL_AUTH_TOKEN_FILE', None)
        finally:
            if tok_file.exists():
                tok_file.unlink()

    def test_inline_token_wins_over_file(self):
        """Precedence is inline first, so a pinned prod value is never shadowed."""
        tok_file = BACKEND / 'keys' / 'tunnel_auth.token'
        try:
            tok_file.parent.mkdir(parents=True, exist_ok=True)
            tok_file.write_text('value-from-file')
            os.environ['TUNNEL_AUTH_TOKEN'] = 'value-inline'
            os.environ['TUNNEL_AUTH_TOKEN_FILE'] = str(tok_file)
            self.assertEqual(ta._env_token(), 'value-inline')
        finally:
            os.environ.pop('TUNNEL_AUTH_TOKEN', None)
            os.environ.pop('TUNNEL_AUTH_TOKEN_FILE', None)
            if tok_file.exists():
                tok_file.unlink()

    def test_unreadable_token_file_fails_closed(self):
        """A configured-but-broken file must reject, not fall back to open."""
        os.environ.pop('TUNNEL_AUTH_TOKEN', None)
        os.environ['TUNNEL_AUTH_TOKEN_FILE'] = '/nonexistent/path/tunnel_auth.token'
        try:
            self.assertIsNone(ta._env_token())
            inner = App()
            mw = ta.TunnelAuthMiddleware(inner, token=None)
            status, _ = run(mw, make_scope(headers=[(ta.HEADER, b'anything')]))
            self.assertEqual(status, 401, 'unreadable secret file must reject, not open')
            self.assertEqual(inner.reached, 0)
        finally:
            os.environ.pop('TUNNEL_AUTH_TOKEN_FILE', None)

    def test_resolution_reports_which_file_it_read(self):
        """The start-up log must name the path, not just say "ACTIVE".

        Two production failures of this gate were both "the value is not reaching
        the process", and the second one was a `$PWD`-relative path that was valid
        from the repo root and broken from backend/ -- the cwd the backend is
        actually started from. A log line saying only "ACTIVE" cannot distinguish
        wrong-path from unset-var from unreadable-file, which is what sent the
        debugging in circles. The detail string is the fix; this pins it.
        """
        tok_file = BACKEND / 'keys' / 'tunnel_auth.token'
        try:
            tok_file.parent.mkdir(parents=True, exist_ok=True)
            tok_file.write_text(SECRET)

            os.environ.pop('TUNNEL_AUTH_TOKEN', None)
            os.environ.pop('TUNNEL_AUTH_TOKEN_FILE', None)
            tok, src, detail = ta._resolve_token()
            self.assertIsNone(tok)
            self.assertEqual(src, 'none')
            self.assertIn('unset', detail, f'unset state must say so, got {detail!r}')

            os.environ['TUNNEL_AUTH_TOKEN_FILE'] = '/nonexistent/path/tunnel_auth.token'
            tok, src, detail = ta._resolve_token()
            self.assertIsNone(tok)
            self.assertEqual(src, 'unreadable')
            self.assertEqual(
                detail, '/nonexistent/path/tunnel_auth.token',
                'the configured path must be echoed verbatim -- that path IS the '
                'diagnostic',
            )

            os.environ['TUNNEL_AUTH_TOKEN_FILE'] = str(tok_file)
            tok, src, detail = ta._resolve_token()
            self.assertEqual(tok, SECRET)
            self.assertEqual(src, 'TUNNEL_AUTH_TOKEN_FILE')
            self.assertEqual(detail, str(tok_file), 'must report the file it actually read')
        finally:
            os.environ.pop('TUNNEL_AUTH_TOKEN', None)
            os.environ.pop('TUNNEL_AUTH_TOKEN_FILE', None)
            if tok_file.exists():
                tok_file.unlink()

    def test_empty_token_file_fails_closed(self):
        """An empty file is a silent misconfiguration, not an open door."""
        tok_file = BACKEND / 'keys' / 'tunnel_auth.token'
        try:
            tok_file.parent.mkdir(parents=True, exist_ok=True)
            tok_file.write_text('   \n')
            os.environ.pop('TUNNEL_AUTH_TOKEN', None)
            os.environ['TUNNEL_AUTH_TOKEN_FILE'] = str(tok_file)
            self.assertIsNone(ta._env_token(), 'whitespace-only file must resolve to None')
            inner = App()
            mw = ta.TunnelAuthMiddleware(inner, token=None)
            status, _ = run(mw, make_scope(headers=[(ta.HEADER, b'anything')]))
            self.assertEqual(status, 401)
        finally:
            os.environ.pop('TUNNEL_AUTH_TOKEN_FILE', None)
            if tok_file.exists():
                tok_file.unlink()

    def test_health_is_open_for_probes(self):
        inner = App()
        mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
        status, _ = run(mw, make_scope(path='/health'))
        self.assertEqual(status, 200, '/health must stay reachable without the secret')
        self.assertEqual(inner.reached, 1)

    def test_websocket_rejected_with_close_frame(self):
        inner = App()
        mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
        sent = []

        async def _send(msg):
            sent.append(msg)

        async def _receive():
            return {'type': 'websocket.connect'}

        scope = make_scope(path='/api/v1/ws/abc', stype='websocket')
        asyncio.run(mw(scope, _receive, _send))
        self.assertEqual(inner.reached, 0, 'unauthenticated WS must not reach the handler')
        self.assertTrue(
            any(m.get('type') == 'websocket.close' for m in sent),
            'WS must be actively closed, not left pending',
        )

    def test_websocket_allowed_with_token(self):
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
        self.assertEqual(inner.reached, 1, 'authenticated WS must reach the handler')

    def test_disabled_escape_hatch_bypasses(self):
        """TUNNEL_AUTH_REQUIRED=0 opens the gate (local dev only)."""
        inner = App()
        mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
        os.environ['TUNNEL_AUTH_REQUIRED'] = '0'
        try:
            status, _ = run(mw, make_scope())
            self.assertEqual(status, 200, 'escape hatch should let the request through')
            self.assertEqual(inner.reached, 1)
        finally:
            os.environ.pop('TUNNEL_AUTH_REQUIRED', None)

    def test_evidence_paths_are_gated(self):
        """The sensitive routes must be behind the gate, not in the open list."""
        for p in ('/api/v1/incidents/x/evidence', '/api/v1/vehicles/global/list',
                  '/api/v1/feeds', '/api/v1/analysis'):
            self.assertNotIn(p, ta.OPEN_PATHS, f'{p} must not be open')
            self.assertFalse(
                any(p.startswith(o) for o in ta.OPEN_PREFIXES), f'{p} bypassed',
            )

    def test_rejection_carries_cors_headers(self):
        """A 401 from the OUTERMOST middleware would otherwise reach the browser
        with no Access-Control-Allow-Origin and surface as an opaque CORS error
        rather than the 401 it is. Same defect that hid the 503 behind CORS."""
        inner = App()
        mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
        origin = 'https://3000-firebase-x.cluster-abc.cloudworkstations.dev'
        scope = make_scope(headers=[(b'origin', origin.encode())])
        _, sent = run(mw, scope)
        hdrs = _hdrs(sent)
        self.assertEqual(
            hdrs.get('access-control-allow-origin'), origin,
            f"401 must echo the allowed Origin, got {hdrs}",
        )
        self.assertEqual(hdrs.get('vary'), 'Origin')
        self.assertEqual(hdrs.get('access-control-allow-credentials'), 'true')

    def test_rejection_omits_cors_for_disallowed_origin(self):
        """An origin outside the allowlist must NOT be reflected."""
        inner = App()
        mw = ta.TunnelAuthMiddleware(inner, token=SECRET)
        scope = make_scope(headers=[(b'origin', b'https://evil.example.com')])
        _, sent = run(mw, scope)
        hdrs = _hdrs(sent)
        self.assertNotIn(
            'access-control-allow-origin', hdrs,
            'must not reflect a disallowed origin',
        )
        self.assertNotIn(
            'access-control-allow-credentials', hdrs,
            'must not offer credentialed access to a disallowed origin',
        )


if __name__ == '__main__':
    unittest.main()
