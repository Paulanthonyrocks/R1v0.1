"""Regression tests: ConnectionManager.connect() must report rejection.

Before Oct-20, both rejection paths in connect() (server shutting down ->
close 1001; connection pool saturated -> close 4000) ended with a bare
`return` -- indistinguishable, from the caller's side, from the success path
falling off the end of the function. ws.py:message_receiver ignored the
verdict entirely and sent AUTH_SUCCESS over the already-closed socket, then
started the expiry watcher (a task that can sleep up to 1h) and the receive
loop against a connection that would never open.

These tests drive the REAL ConnectionManager.connect() with a fake websocket
and pin the True/False verdict for all three outcomes. Runs under pytest AND
unittest (stdlib + fastapi/starlette, which are installed locally; heavy
first-party siblings are stubbed, the module under test runs verbatim).
"""
import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

BACKEND = Path(__file__).resolve().parents[1]
TARGET = BACKEND / "app" / "websocket" / "connection_manager.py"


class FakeWebSocket:
    """Records close() calls the way the real starlette WebSocket would."""

    def __init__(self):
        self.closed = []

    async def close(self, code=1000, reason=None):
        self.closed.append((code, reason))


def _load_connection_manager():
    """Load the REAL connection_manager.py without app.models.websocket's
    package chain (pydantic is fine, but we keep the load hermetic)."""
    app = types.ModuleType("app")
    app.__path__ = []
    app_models = types.ModuleType("app.models")
    app_models.__path__ = []
    sys.modules.setdefault("app", app)
    sys.modules.setdefault("app.models", app_models)
    if "app.models.websocket" not in sys.modules:
        stub = types.ModuleType("app.models.websocket")
        stub.WebSocketMessage = MagicMock(name="WebSocketMessage")
        stub.WebSocketMessageTypeEnum = MagicMock(name="WebSocketMessageTypeEnum")
        stub.PingData = MagicMock(name="PingData")
        sys.modules["app.models.websocket"] = stub
    spec = importlib.util.spec_from_file_location(
        "connection_manager_under_test", TARGET)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


cm = _load_connection_manager()
ConnectionManager = cm.ConnectionManager


def make_manager():
    """A fresh manager instance (the singleton is bypassed via a new object)."""
    mgr = ConnectionManager(max_connections=10)
    return mgr


class ConnectVerdictTests(unittest.TestCase):
    def setUp(self):
        self.mgr = make_manager()
        # ConnectionManager is a process-wide singleton (__new__-cached), so
        # sanitize the fields these tests mutate -- otherwise one case's
        # max_connections=0 would reject every later case's connect().
        self.mgr.max_connections = 10
        self.mgr._shutting_down = False
        self.mgr.active_connections.clear()

    def tearDown(self):
        self.mgr._shutting_down = False

    def test_shutting_down_rejects_and_closes_1001(self):
        ws = FakeWebSocket()
        self.mgr._shutting_down = True
        registered = asyncio.run(
            self.mgr.connect(ws, "client_a", "user_a", "admin"))
        self.assertFalse(registered, "shutdown must report rejection")
        self.assertEqual(ws.closed, [(1001, "Server shutting down")])
        self.assertNotIn("client_a", self.mgr.active_connections)

    def test_pool_saturated_rejects_and_closes_4000(self):
        ws = FakeWebSocket()
        self.mgr.max_connections = 0  # {} pool of size 0 is always saturated
        registered = asyncio.run(
            self.mgr.connect(ws, "client_b", "user_b"))
        self.assertFalse(registered, "saturation must report rejection")
        self.assertEqual(len(ws.closed), 1)
        self.assertEqual(ws.closed[0][0], 4000)
        self.assertNotIn("client_b", self.mgr.active_connections)

    def test_success_registers_and_returns_true(self):
        ws = FakeWebSocket()
        registered = asyncio.run(
            self.mgr.connect(ws, "client_c", "user_c", "operator"))
        self.assertTrue(registered, "a normal admission must report success")
        self.assertIn("client_c", self.mgr.active_connections)
        self.assertEqual(ws.closed, [], "an admitted socket must not be closed")
        self.assertEqual(self.mgr.client_id_to_user_role["client_c"], "operator")


if __name__ == "__main__":
    unittest.main()
