"""Camera-motion compensation (#6b): estimate global frame motion with
sparse optical flow and subtract it from track velocities, so a shaking
pole-mounted camera doesn't inflate vehicle speed estimates.

cv2/numpy are imported LAZILY inside the estimator so this module loads and
its pure helpers test without the ML stack (local boxes have no cv2).
"""
import math
import statistics
from typing import List, Optional, Sequence, Tuple


def median_flow(flows: List[Tuple[float, float]]) -> Tuple[float, float]:
    """Median (dx, dy) over flow vectors — robust to outlier features."""
    if not flows:
        return (0.0, 0.0)
    return (statistics.median(f[0] for f in flows),
            statistics.median(f[1] for f in flows))


def adjust_velocity(vx: float, vy: float,
                    motion_px_per_frame: Optional[Tuple[float, float]],
                    fps: float,
                    min_motion_px: float = 0.5) -> Tuple[float, float]:
    """Subtract global motion (px/frame) from a track velocity (px/s).

    Below min_motion_px the motion is treated as sensor noise and the
    velocity is returned unchanged."""
    if motion_px_per_frame is None:
        return (vx, vy)
    mx, my = motion_px_per_frame
    if math.hypot(mx, my) < float(min_motion_px):
        return (vx, vy)
    return (vx - mx * float(fps), vy - my * float(fps))


class GlobalMotionEstimator:
    """Per-frame global motion from goodFeaturesToTrack + PyrLK.

    Holds the previous grayscale frame. update(frame_bgr) returns the
    estimated camera motion in px/frame, or None when it can't be estimated
    (first frame, too few features, cv2 missing)."""

    def __init__(self, max_points: int = 200, quality: float = 0.01,
                 min_features: int = 10, min_motion_px: float = 0.5):
        self.max_points = int(max_points)
        self.quality = float(quality)
        self.min_features = int(min_features)
        self.min_motion_px = float(min_motion_px)
        self._prev_gray = None

    def update(self, frame_bgr) -> Optional[Tuple[float, float]]:
        try:
            import cv2
            import numpy as np
        except Exception:
            return None
        try:
            if frame_bgr is None or frame_bgr.size == 0:
                return None
            gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr
            if self._prev_gray is None:
                self._prev_gray = gray
                return None
            p0 = cv2.goodFeaturesToTrack(self._prev_gray, maxCorners=self.max_points,
                                         qualityLevel=self.quality, minDistance=10)
            if p0 is None or len(p0) < self.min_features:
                self._prev_gray = gray
                return None
            p1, st, _err = cv2.calcOpticalFlowPyrLK(
                self._prev_gray, gray, p0, None,
                winSize=(21, 21), maxLevel=3,
                criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
            )
            self._prev_gray = gray
            if p1 is None or st is None:
                return None
            good = p1[st.reshape(-1) == 1] - p0[st.reshape(-1) == 1]
            if len(good) < self.min_features:
                return None
            flow = median_flow([(float(d[0]), float(d[1])) for d in good])
            if math.hypot(flow[0], flow[1]) < self.min_motion_px:
                return (0.0, 0.0)
            return flow
        except Exception:
            return None
