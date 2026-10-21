"""Regression tests: incident status changes attribute to the real user.

Before Oct-20, acknowledge_incident / resolve_incident took
`user_id: str = Query("operator")`. The frontend (incidentService.ts) calls
both endpoints without the parameter, so EVERY acknowledge/resolve was
written into the audit log as the literal string "operator" instead of the
authenticated admin -- the audit trail named a fictional account.

The fix: default to current_user.username, keep the explicit override.
These tests drive the REAL endpoint functions with fake manager/user
objects and assert the attribution passed to update_status(). Runs under
pytest AND unittest.
"""
import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

BACKEND = Path(__file__).resolve().parents[1]
TARGET = BACKEND / "app" / "routers" / "incidents.py"


class _DB:
    async def get_incident_by_id(self, incident_id):
        return {"id": incident_id}


class RecordingManager:
    """Stands in for IncidentManager.update_status and records the call."""

    def __init__(self):
        self.calls = []
        self._db_manager = _DB()

    async def update_status(self, incident_id, new_status, user_id=None, notes=None):
        self.calls.append({
            "incident_id": incident_id,
            "status": new_status,
            "user_id": user_id,
            "notes": notes,
        })
        return True


class FakeUser:
    def __init__(self, username="admin_bob"):
        self.username = username


def _load_incidents_router():
    """Load the REAL incidents.py with its first-party siblings stubbed.

    The router imports the services package (heavy: feed_manager -> numpy),
    so the siblings are MagicMocks; the functions under test run verbatim.
    """
    app = types.ModuleType("app"); app.__path__ = []
    app_models = types.ModuleType("app.models"); app_models.__path__ = []
    app_services = types.ModuleType("app.services"); app_services.__path__ = []
    app_dependencies = types.ModuleType("app.dependency_injection")
    sys.modules.setdefault("app", app)
    sys.modules.setdefault("app.models", app_models)
    sys.modules.setdefault("app.services", app_services)
    sys.modules.setdefault("app.dependency_injection", app_dependencies)
    # names the router pulls at def time -- REAL pydantic models + a real
    # str-Enum, because FastAPI validates response_model=List[Incident] and
    # the status query annotation at DECORATION time (a MagicMock there
    # raises "Invalid args for response field" during exec_module).
    from enum import Enum as _Enum
    from pydantic import BaseModel as _BaseModel

    class _Incident(_BaseModel):
        id: str = "inc-0"

    class _IncidentCreate(_BaseModel):
        latitude: float = 0.0
        longitude: float = 0.0
        type: str = "OTHER"
        severity: str = "LOW"
        description: str = ""
        feed_id: str | None = None

    class _IncidentUpdate(_BaseModel):
        pass

    class _IncidentStatus(str, _Enum):
        ACKNOWLEDGED = "acknowledged"
        RESOLVED = "resolved"

    incidents_models = types.ModuleType("app.models.incidents")
    incidents_models.Incident = _Incident
    incidents_models.IncidentCreate = _IncidentCreate
    incidents_models.IncidentUpdate = _IncidentUpdate
    incidents_models.IncidentStatus = _IncidentStatus
    sys.modules["app.models.incidents"] = incidents_models
    status_enum = _IncidentStatus
    services_mod = types.ModuleType("app.services.services")
    services_mod.get_incident_manager = MagicMock(name="get_incident_manager")
    services_mod.get_database_manager = MagicMock(name="get_database_manager")
    sys.modules["app.services.services"] = services_mod
    sys.modules["app.services.incident_manager"] = MagicMock(
        name="app.services.incident_manager")
    app_di = types.ModuleType("app.dependency_injection")
    app_di.get_current_active_user = MagicMock(name="get_current_active_user")
    app_di.get_current_admin = MagicMock(name="get_current_admin")
    sys.modules["app.dependency_injection"] = app_di
    sys.modules["app.models.user"] = MagicMock(name="app.models.user")

    spec = importlib.util.spec_from_file_location("incidents_router_under_test", TARGET)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod, status_enum


incidents, IncidentStatus = _load_incidents_router()


class AcknowledgementAttributionTests(unittest.TestCase):
    def test_defaults_to_authenticated_admin(self):
        manager = RecordingManager()
        asyncio.run(incidents.acknowledge_incident(
            "inc-1", user_id=None, manager=manager, current_user=FakeUser("admin_bob")))
        self.assertEqual(len(manager.calls), 1)
        self.assertEqual(
            manager.calls[0]["user_id"], "admin_bob",
            "an omitted user_id must attribute to the authenticated admin",
        )
        self.assertEqual(manager.calls[0]["status"], IncidentStatus.ACKNOWLEDGED)

    def test_explicit_override_still_passes_through(self):
        manager = RecordingManager()
        asyncio.run(incidents.acknowledge_incident(
            "inc-2", user_id="agent-7", manager=manager,
            current_user=FakeUser("admin_bob")))
        self.assertEqual(manager.calls[0]["user_id"], "agent-7")

    def test_never_attributes_to_literal_operator(self):
        manager = RecordingManager()
        asyncio.run(incidents.acknowledge_incident(
            "inc-3", user_id=None, manager=manager, current_user=FakeUser("admin_eve")))
        self.assertNotEqual(
            manager.calls[0]["user_id"], "operator",
            "the fictional 'operator' account must never be written again",
        )

    def test_resolve_also_attributes_to_admin(self):
        manager = RecordingManager()
        asyncio.run(incidents.resolve_incident(
            "inc-4", notes="cleared", user_id=None, manager=manager,
            current_user=FakeUser("admin_bob")))
        self.assertEqual(manager.calls[0]["user_id"], "admin_bob")
        self.assertEqual(manager.calls[0]["notes"], "cleared")


class SourceShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = TARGET.read_text()

    def test_no_operator_default_remains(self):
        self.assertIsNone(
            __import__("re").search(r'Query\("operator"', self.src),
            "the literal 'operator' default must not come back",
        )

    def test_user_id_defaults_to_none_and_falls_back(self):
        self.assertIn('user_id: Optional[str] = Query(None', self.src)
        self.assertIn("user_id or current_user.username", self.src)


if __name__ == "__main__":
    unittest.main()
