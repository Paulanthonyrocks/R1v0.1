"""Camera health as safety (feature 4).

Frame-level tamper/frozen/dark/fog signals from cheap gray-frame stats.
FeedWatchdog owns restart policy; this module only classifies so a blind
camera never reads as a quiet road. All thresholds live under
`camera_health:` in config.
"""
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("app.services.camera_health")


def analyze_gray(
    prev_mean: float,
    curr_mean: float,
    mean_abs_diff: float,
    laplacian_var: float,
    dark_thresh: float = 15.0,
    frozen_diff_thresh: float = 0.6,
    fog_blur_thresh: float = 25.0,
) -> Dict[str, Any]:
    frozen = mean_abs_diff < frozen_diff_thresh
    dark = curr_mean < dark_thresh
    foggy = (not dark) and (laplacian_var < fog_blur_thresh)
    # Sudden blackout / cover: large mean drop between consecutive frames.
    tamper = (prev_mean - curr_mean) > 80.0
    healthy = not (frozen or dark or foggy or tamper)
    return {
        "healthy": healthy,
        "frozen": frozen,
        "dark": dark,
        "foggy": foggy,
        "tamper_suspected": tamper,
        "brightness": round(curr_mean, 2),
        "blur_score": round(laplacian_var, 2),
    }


def frame_stats(gray) -> Dict[str, float]:
    """Compute mean + Laplacian variance for a single-channel numpy frame."""
    import cv2

    mean_val = float(gray.mean())
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return {"mean": mean_val, "blur": blur}


class CameraHealthService:
    def __init__(self, config: Dict[str, Any]):
        cfg = config.get("camera_health", {})
        self.enabled = cfg.get("enabled", True)
        self.dark_thresh = float(cfg.get("dark_thresh", 15.0))
        self.frozen_diff_thresh = float(cfg.get("frozen_diff_thresh", 0.6))
        self.fog_blur_thresh = float(cfg.get("fog_blur_thresh", 25.0))
        self._last_mean: Dict[str, float] = {}

    def check_feed(
        self,
        feed_id: str,
        curr_mean: float,
        mean_abs_diff: float,
        laplacian_var: float,
    ) -> Dict[str, Any]:
        prev = self._last_mean.get(feed_id, curr_mean)
        result = analyze_gray(
            prev, curr_mean, mean_abs_diff, laplacian_var,
            self.dark_thresh, self.frozen_diff_thresh, self.fog_blur_thresh,
        )
        self._last_mean[feed_id] = curr_mean
        if self.enabled and not result["healthy"]:
            logger.warning(f"Camera health [{feed_id}]: {result}")
        return result
