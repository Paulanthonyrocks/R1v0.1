"""Regression test: POST /api/v1/feeds/ crashed on `request.name`.

Live shape of the bug (audit, Oct-20):
  - FeedCreateRequest (app/models/feeds.py) declared `name_hint`.
  - routers/feeds.py:add_and_start_feed read `request.name` (twice:
    name_hint=... at :152 and the response message at :158).
  - The dashboard's AddFeedDialog sends {"source", "name"}.
  So EVERY valid add-feed request raised AttributeError -> 500. The field is
  now `name` on the wire (the FeedManager's internal name_hint kwarg is a
  different layer and is unchanged).

The first test drives the REAL model; the source-shape test pins the router
so the wrong attribute cannot come back. Runs under pytest AND unittest.
"""
import importlib.util
import re
import sys
import types
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
MODEL = BACKEND / "app" / "models" / "feeds.py"
ROUTER = BACKEND / "app" / "routers" / "feeds.py"


def _load_feeds_models():
    """Load app/models/feeds.py by path (its deps are stdlib + pydantic)."""
    spec = importlib.util.spec_from_file_location("feeds_models_under_test", MODEL)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


feeds_models = _load_feeds_models()
FeedCreateRequest = feeds_models.FeedCreateRequest


class FeedCreateRequestContractTests(unittest.TestCase):
    def test_dashboard_payload_validates_and_exposes_name(self):
        """The exact AddFeedDialog body {"source", "name"} must work."""
        req = FeedCreateRequest(source="rtsp://cam/1", name="North Gate")
        self.assertEqual(req.name, "North Gate")
        self.assertEqual(req.source, "rtsp://cam/1")

    def test_name_optional(self):
        self.assertIsNone(FeedCreateRequest(source="/tmp/v.mp4").name)

    def test_name_hint_is_not_a_wire_field(self):
        # Exactly one field for the display name: no duplicate alias that can
        # drift from what the router reads again.
        self.assertNotIn("name_hint", FeedCreateRequest.model_fields)

    def test_lat_lon_still_accepted(self):
        req = FeedCreateRequest(source="x", latitude=34.05, longitude=-118.24)
        self.assertEqual(req.latitude, 34.05)


class RouterSourceShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = ROUTER.read_text()

    def test_router_reads_request_name_not_name_hint(self):
        self.assertIn("name_hint=request.name", self.src)
        self.assertIsNone(
            re.search(r"request\.name_hint", self.src),
            "router must read request.name -- the model field is `name`",
        )


if __name__ == "__main__":
    unittest.main()
