import unittest

from app.core.core_module import CoreModule


def _core():
    core = CoreModule.__new__(CoreModule)
    core.lane_bands_enabled = True
    core.lane_bands_n = 6
    core.lane_hysteresis_frames = 3
    core._lane_votes = {}
    core.roi_polygon_points = None
    return core


def _track(cx):
    return {"bbox": [cx - 10, 400, cx + 10, 420]}


W, H = 600, 480  # 6 bands of 100px: cx=50 -> 0, cx=150 -> 1


class TestLaneHysteresis(unittest.TestCase):
    def test_first_sight_commits_immediately(self):
        core, t = _core(), _track(50)
        core._assign_lane_band(t, W, H, "v1")
        self.assertEqual(t["lane"], 0)

    def test_single_frame_flicker_does_not_commit(self):
        core, t = _core(), _track(50)
        core._assign_lane_band(t, W, H, "v1")
        t.update(_track(150))
        core._assign_lane_band(t, W, H, "v1")
        self.assertEqual(t["lane"], 0)
        t.update(_track(50))
        core._assign_lane_band(t, W, H, "v1")
        self.assertEqual(t["lane"], 0)

    def test_sustained_change_commits_after_threshold(self):
        core, t = _core(), _track(50)
        core._assign_lane_band(t, W, H, "v1")
        for _ in range(2):
            t.update(_track(150))
            core._assign_lane_band(t, W, H, "v1")
            self.assertEqual(t["lane"], 0)
        t.update(_track(150))
        core._assign_lane_band(t, W, H, "v1")
        self.assertEqual(t["lane"], 1)

    def test_alternating_flicker_never_commits(self):
        core, t = _core(), _track(50)
        core._assign_lane_band(t, W, H, "v1")
        for i in range(10):
            t.update(_track(150 if i % 2 else 50))
            core._assign_lane_band(t, W, H, "v1")
        self.assertEqual(t["lane"], 0)

    def test_missing_bbox_keeps_committed_lane(self):
        core, t = _core(), _track(50)
        core._assign_lane_band(t, W, H, "v1")
        t["bbox"] = None
        core._assign_lane_band(t, W, H, "v1")
        self.assertEqual(t["lane"], 0)

    def test_disabled_leaves_lane_alone(self):
        core, t = _core(), _track(50)
        core.lane_bands_enabled = False
        core._assign_lane_band(t, W, H, "v1")
        self.assertNotIn("lane", t)

    def test_no_tid_assigns_directly(self):
        core, t = _core(), _track(150)
        core._assign_lane_band(t, W, H)
        self.assertEqual(t["lane"], 1)

    def test_votes_scoped_per_track(self):
        core = _core()
        t1, t2 = _track(50), _track(50)
        core._assign_lane_band(t1, W, H, "v1")
        core._assign_lane_band(t2, W, H, "v2")
        t1.update(_track(150))
        for _ in range(3):
            core._assign_lane_band(t1, W, H, "v1")
        self.assertEqual(t1["lane"], 1)
        self.assertEqual(t2["lane"], 0)


if __name__ == "__main__":
    unittest.main()
