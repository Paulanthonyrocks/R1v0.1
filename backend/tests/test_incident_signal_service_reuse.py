"""Regression tests: the adaptive-signal step reuses the shared service.

Live-run evidence (2026-10-18, backend_services.log lines 128/334): every
CRITICAL incident re-printed "Initialized 2 mock signals." at the incident
timestamp, because IncidentManager.create_incident() constructed a fresh
TrafficSignalService per incident instead of using the singleton that
ServiceContainer had already built -- re-seeding the default signals and
opening (then abandoning) an httpx client each time.

These tests drive the REAL IncidentManager methods with fakes and also pin
the source-shape guarantees, so the per-incident construction cannot come
back silently. Runs under pytest AND `python -m unittest` (stdlib only --
the full app chain needs torch/numpy, which the load-by-path harness avoids).
"""
import asyncio
import importlib.util
import re
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

BACKEND = Path(__file__).resolve().parents[1]
TARGET = BACKEND / "app" / "services" / "incident_manager.py"


def _load_incident_manager():
    """Load the REAL incident_manager.py without its heavy siblings.

    The package __init__ drags in feed_manager -> numpy/torch, so the module
    is loaded by path with package scaffolding aliased into sys.modules and
    the first-party siblings it imports stubbed as MagicMocks. The code under
    test runs verbatim -- no copy, no paraphrase.
    """
    def pkg(name):
        mod = types.ModuleType(name)
        mod.__path__ = []  # mark as package so submodule imports resolve
        sys.modules.setdefault(name, mod)
        return mod

    app = pkg("app")
    app_models = pkg("app.models")
    app_websocket = pkg("app.websocket")
    app_services = pkg("app.services")
    setattr(app, "models", app_models)
    setattr(app, "websocket", app_websocket)
    setattr(app, "services", app_services)
    for name in (
        "app.models.traffic",
        "app.models.websocket",
        "app.websocket.connection_manager",
        "app.services.notification_service",
        "app.services.v2x_service",
    ):
        sys.modules.setdefault(name, MagicMock(name=name))
    spec = importlib.util.spec_from_file_location(
        "incident_manager_under_test", TARGET)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


incident_manager = _load_incident_manager()
IncidentManager = incident_manager.IncidentManager


class RecordingSignalService:
    """Stands in for the shared TrafficSignalService and records the call."""

    def __init__(self):
        self.calls = []

    async def suggest_signal_adjustment(self, incident_location, incident_severity):
        self.calls.append(
            {"position": incident_location, "severity": incident_severity})


def make_manager(traffic_signal_service=None):
    return IncidentManager(
        config={},
        db_manager=MagicMock(),
        connection_manager=MagicMock(),
        traffic_signal_service=traffic_signal_service,
    )


class AdaptiveSignalResponseTests(unittest.TestCase):
    def test_constructor_accepts_and_stores_shared_service(self):
        svc = RecordingSignalService()
        mgr = make_manager(traffic_signal_service=svc)
        self.assertIs(
            mgr._traffic_signal_service, svc,
            "the injected singleton must be kept, not copied or rebuilt",
        )

    def test_default_is_none_so_legacy_callers_still_construct(self):
        mgr = IncidentManager(
            config={}, db_manager=MagicMock(), connection_manager=MagicMock())
        self.assertIsNone(mgr._traffic_signal_service)

    def test_adaptive_response_calls_the_shared_service(self):
        svc = RecordingSignalService()
        mgr = make_manager(traffic_signal_service=svc)
        severity = MagicMock(name="IncidentSeverityEnum.CRITICAL")
        asyncio.run(
            IncidentManager._adaptive_signal_response(
                mgr,
                {"latitude": 34.0510, "longitude": 118.2430},
                severity,
            )
        )
        self.assertEqual(len(svc.calls), 1, "shared service must be called once")
        call = svc.calls[0]
        self.assertEqual(
            call["position"], {"latitude": 34.0510, "longitude": 118.2430})
        self.assertIs(call["severity"], severity, "severity must pass through")

    def test_adaptive_response_is_a_noop_without_service(self):
        mgr = make_manager(traffic_signal_service=None)
        # Must not raise: the None case is the legacy/unconfigured path.
        result = asyncio.run(
            IncidentManager._adaptive_signal_response(
                mgr, {"latitude": 1.0, "longitude": 2.0}, MagicMock()))
        self.assertIsNone(result)

    def test_service_failure_does_not_break_the_incident_flow(self):
        class Boom:
            async def suggest_signal_adjustment(self, incident_location, incident_severity):
                raise RuntimeError("controller down")

        mgr = make_manager(traffic_signal_service=Boom())
        # Same try/except posture the old inline block had: a signal-service
        # failure must never propagate into create_incident's flow.
        asyncio.run(
            IncidentManager._adaptive_signal_response(
                mgr, {"latitude": 1.0, "longitude": 2.0}, MagicMock()))


class SourceShapeTests(unittest.TestCase):
    """Pin the wiring so the per-incident construction cannot come back."""

    @classmethod
    def setUpClass(cls):
        cls.src = TARGET.read_text()
        cls.services_src = (BACKEND / "app" / "services" / "services.py").read_text()

    def test_no_per_incident_construction_in_source(self):
        for pattern in (
            r"TrafficSignalService\(",
            r"from app\.services\.traffic_signal_service import",
        ):
            self.assertIsNone(
                re.search(pattern, self.src),
                "IncidentManager must never construct TrafficSignalService "
                f"itself (per-incident churn); matched {pattern!r}",
            )

    def test_create_incident_delegates_to_the_helper(self):
        self.assertIn(
            "await self._adaptive_signal_response(location, severity)", self.src,
            "create_incident must go through the shared-service helper",
        )

    def test_service_container_injects_the_singleton(self):
        m = re.search(r"IncidentManager\((.*?)\)\n", self.services_src, re.S)
        self.assertIsNotNone(m, "could not locate the IncidentManager call")
        self.assertIn(
            "traffic_signal_service=", m.group(1),
            "services.py must inject the shared singleton into IncidentManager",
        )


if __name__ == "__main__":
    unittest.main()
