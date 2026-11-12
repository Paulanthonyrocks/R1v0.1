"""Composite per-feed health score (item 4).

Fuses the signals that already exist — feed status, processing fps, worker
metric drops / SHM pressure, speed-calibration state, and camera image
quality (blur/dark/frozen from camera_health_service when wired) — into one
0-100 score with a grade, surfaced on the dashboard's SystemHealthMonitor.

Pure stdlib; every input optional and defensive (.get) so a missing metric
never raises. Consumed by health_service.get_full_status.
"""
from typing import Any, Dict, Optional


def feed_health_score(*,
                      status: Optional[str] = None,
                      fps: Optional[float] = None,
                      expected_fps: Optional[float] = None,
                      latest_metrics: Optional[Dict[str, Any]] = None,
                      camera_health: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    score = 100.0
    factors: Dict[str, Any] = {}

    if status is not None and status != "running":
        score -= 60.0
        factors["status"] = status

    m = latest_metrics if isinstance(latest_metrics, dict) else {}

    if fps is not None and expected_fps:
        try:
            if float(fps) < 0.5 * float(expected_fps):
                score -= 20.0
                factors["low_fps"] = round(float(fps), 2)
        except (TypeError, ValueError):
            pass

    # Worker drop counters (monotonic): any drops at all means the pipeline
    # shed load at some point; cap the penalty so history can't sink a feed.
    drop_keys = ("frames_dropped", "drops_shm_recycled", "drops_output_full", "drops_other")
    drops = 0
    for k in drop_keys:
        v = m.get(k)
        if isinstance(v, (int, float)):
            drops += v
    if drops > 0:
        score -= min(15.0, 5.0 + drops * 0.01)
        factors["drops"] = drops

    shm_free = m.get("shm_free_pct")
    if isinstance(shm_free, (int, float)) and shm_free < 25.0:
        score -= 15.0
        factors["shm_free_pct"] = shm_free

    if m.get("speed_calibrated") is False:
        score -= 10.0
        factors["uncalibrated"] = True

    congestion = m.get("congestion_index", m.get("congestion_level_percent"))
    if isinstance(congestion, (int, float)) and congestion >= 80.0:
        score -= 5.0
        factors["congestion"] = congestion

    ch = camera_health if isinstance(camera_health, dict) else {}
    blur = ch.get("blur_score")
    if isinstance(blur, (int, float)) and blur < 25.0:
        score -= 15.0
        factors["blurry"] = blur
    if ch.get("dark"):
        score -= 15.0
        factors["dark"] = True
    if ch.get("frozen"):
        score -= 20.0
        factors["frozen"] = True

    score = max(0.0, min(100.0, round(score, 1)))
    grade = "OK" if score >= 80 else ("DEGRADED" if score >= 50 else "CRITICAL")
    return {"score": score, "grade": grade, "factors": factors}
