"""Isolated tests for safety-hub features 1-9.

Loads service modules directly by file path to avoid
app/services/__init__.py pulling the full backend chain (feed_manager,
msgpack, yaml, ...). Same code under test, no venv needed.
"""
import importlib.util
import unittest
from pathlib import Path

SVC = Path(__file__).resolve().parents[2] / "services"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SVC / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


evidence = load("evidence_service")
escalation = load("escalation_service")
forensic = load("forensic_service")
vru = load("vru_service")
anpr = load("anpr_service")
workzone = load("workzone_service")
camhealth = load("camera_health_service")
v2x = load("v2x_service")


class TestF1Evidence(unittest.TestCase):
    def test_manifest_marks_clip_unavailable(self):
        m = evidence.build_manifest({"incident_id": "i1", "type": "ACCIDENT"}, [], None)
        self.assertTrue(m["clip_unavailable"])
        self.assertEqual(m["snapshots"], [])

    def test_save_and_get_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            svc = evidence.EvidenceService(config={"evidence": {"enabled": True, "dir": d}})
            saved = svc.save_bundle({"incident_id": "abc123", "type": "ACCIDENT",
                                     "severity": "HIGH"}, [], None)
            self.assertIsNotNone(saved)
            self.assertEqual(svc.get_bundle("abc123")["incident_id"], "abc123")

    def test_disabled_returns_none(self):
        svc = evidence.EvidenceService(config={"evidence": {"enabled": False, "dir": "/tmp/x"}})
        self.assertIsNone(svc.save_bundle({"id": "z"}, [], None))


class TestF2Escalation(unittest.TestCase):
    def test_ok_due_overdue_acked(self):
        self.assertEqual(escalation.escalation_state(0, None, 10), "OK")
        self.assertEqual(escalation.escalation_state(0, None, 400), "DUE")
        self.assertEqual(escalation.escalation_state(0, None, 1000), "OVERDUE")
        self.assertEqual(escalation.escalation_state(0, 50, 10000), "ACKED")

    def test_next_level(self):
        lvls = [{"name": "operator", "after_sec": 0}, {"name": "supervisor", "after_sec": 300}]
        self.assertEqual(escalation.next_level(400, lvls), "supervisor")


class TestF3Privacy(unittest.TestCase):
    def test_disabled_passthrough(self):
        import numpy as np
        svc = load("privacy_service").PrivacyService(config={"privacy": {"enabled": False}})
        img = np.zeros((10, 10, 3), dtype=np.uint8)
        out, masked = svc.mask_image(img, [(0, 0, 5, 5)])
        self.assertFalse(masked)

    def test_blur_changes_roi(self):
        import numpy as np
        privacy = load("privacy_service")
        rng = np.random.default_rng(0)
        img = rng.integers(0, 255, size=(40, 40, 3), dtype=np.uint8)
        before = img.copy()
        privacy.blur_boxes_bgr(img, [(5, 5, 35, 35)], ksize=15)
        self.assertFalse((img == before).all())


class TestF4CameraHealth(unittest.TestCase):
    def test_frozen(self):
        r = camhealth.analyze_gray(100, 100, 0.1, 200.0)
        self.assertTrue(r["frozen"])
        self.assertFalse(r["healthy"])

    def test_healthy(self):
        r = camhealth.analyze_gray(100, 102, 5.0, 200.0)
        self.assertTrue(r["healthy"])

    def test_dark(self):
        r = camhealth.analyze_gray(100, 5, 90.0, 5.0)
        self.assertTrue(r["dark"])


class TestF5Forensic(unittest.TestCase):
    def test_filter(self):
        incs = [
            {"type": "ACCIDENT", "severity": "HIGH", "source_feed_id": "f1", "timestamp": 100},
            {"type": "CONGESTION", "severity": "LOW", "source_feed_id": "f2", "timestamp": 200},
        ]
        self.assertEqual(len(forensic.filter_incidents(incs, {"severity": "HIGH"})), 1)
        self.assertTrue(forensic.match_incident(incs[0], {"q": "acci"}))
        self.assertFalse(forensic.match_incident(incs[1], {"q": "acci"}))


class TestF6VRU(unittest.TestCase):
    def test_is_vru(self):
        self.assertTrue(vru.is_vru("person"))
        self.assertFalse(vru.is_vru("car"))

    def test_near_miss(self):
        self.assertTrue(vru.near_miss(1.0, 20.0))
        self.assertFalse(vru.near_miss(10.0, 500.0))
        self.assertFalse(vru.near_miss(-1.0, 5.0))

    def test_event(self):
        self.assertTrue(vru.vru_event("person", 1.0, 20.0)["alert"])


class TestF7V2XInbound(unittest.TestCase):
    def test_parse_valid(self):
        import json
        d = json.dumps({"v2x_msg": "BSM", "station_id": "veh1"}).encode()
        self.assertEqual(v2x.parse_inbound_datagram(d)["station_id"], "veh1")

    def test_parse_garbage_none(self):
        self.assertIsNone(v2x.parse_inbound_datagram(b"not json"))
        self.assertIsNone(v2x.parse_inbound_datagram(b'{"v2x_msg":"NOPE"}'))

    def test_handle_counts(self):
        import json
        svc = v2x.V2XService(config={"v2x": {"enabled": False}})
        svc.handle_inbound(json.dumps({"v2x_msg": "ACK", "station_id": "rsu1"}).encode())
        self.assertEqual(svc.inbound_stats()["rx_count"], 1)
        self.assertEqual(svc.inbound_stats()["stations_seen"], 1)


class TestF8ANPR(unittest.TestCase):
    def test_lists(self):
        r = anpr.check_plate_lists("ab-123", ["AB123"], ["XY999"])
        self.assertTrue(r["allowed"])
        self.assertFalse(r["blocked"])

    def test_disabled_honest(self):
        svc = anpr.ANPRService(config={"anpr": {"enabled": False}})
        self.assertTrue(svc.check_plate("AB123")["unconfigured"])
        self.assertEqual(svc.lists(), {"allow": [], "block": []})


class TestF9WorkZone(unittest.TestCase):
    def test_active_window(self):
        z = {"id": "w1", "starts_at": 100, "ends_at": 200}
        self.assertTrue(workzone.is_zone_active(z, 150))
        self.assertFalse(workzone.is_zone_active(z, 250))
        self.assertEqual(workzone.active_zones([z], 150), [z])

    def test_speed_override(self):
        svc = workzone.WorkZoneService(config={"work_zones": {"enabled": True, "zones": [
            {"id": "w1", "feed_id": "f1", "lane": "0", "speed_limit": 20,
             "starts_at": 0, "ends_at": 9999999999}]}})
        self.assertEqual(svc.speed_limit_for("f1", 0, 60), 20)
        self.assertEqual(svc.speed_limit_for("f2", 0, 60), 60)


if __name__ == "__main__":
    unittest.main()
