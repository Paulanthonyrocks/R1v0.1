# backend/app/utils/planning_metrics.py
"""Planning-grade shaping of the persisted feed_metrics record.

The inference worker already computes per-frame per-lane counts, per-class
counts, lane occupancy, queue lengths, VRU counts and speed-calibration
confidence in memory (TrafficMonitor.get_metrics + vru_service.frame_stats),
but the DB write site only stored a fixed column set -- so none of it was
retained. build_feed_metrics_record() maps the live metrics dict onto the
feed_metrics row: scalar columns where they exist, and everything else into
the JSON `extra` column (already serialized by save_feed_metrics_batch).

Pure stdlib so it loads standalone in tests (no numpy/yaml/sqlalchemy).
"""
import time
from typing import Any, Dict, List, Optional

# Live metric keys mirrored into feed_metrics.extra for planning queries.
_PLANNING_EXTRA_KEYS = (
    "vehicles_per_lane",
    "vehicle_type_counts",
    "lane_occupancy",
    "queue_lengths",
    "high_density_lanes",
    "vru",
    "tailgating",
    "behavior_events",
    "collision",
)


def _jsonable(value: Any) -> Any:
    """Recursively convert numpy-ish scalars and dict keys to JSON-safe types."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if not isinstance(k, (str, int, float, bool)):
                try:
                    k = int(k)
                except (TypeError, ValueError):
                    k = str(k)
            out[k] = _jsonable(v)
        return out
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    item = getattr(value, "item", None)  # numpy scalar -> python scalar
    if callable(item):
        try:
            return item()
        except Exception:
            pass
    return str(value)


def build_feed_metrics_record(
    feed_id: str,
    feed_metrics: Optional[Dict[str, Any]],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Shape the per-frame metrics dict into a feed_metrics DB record.

    Fills the fixed columns (vehicles_count / lanes_count / avg_speed /
    congestion_level) from the live metric names when absent, and mirrors the
    planning breakdowns (per-lane, per-class, occupancy, queues, VRU) plus the
    speed-calibration confidence block into `extra`.
    """
    m = feed_metrics if isinstance(feed_metrics, dict) else {}
    record = dict(m)
    record["feed_id"] = feed_id
    record["timestamp"] = now if now is not None else time.time()

    if record.get("vehicles_count") is None:
        record["vehicles_count"] = m.get("total_vehicles")
    if record.get("avg_speed") is None:
        record["avg_speed"] = m.get("average_speed_kmh")
    if record.get("congestion_level") is None:
        record["congestion_level"] = m.get("congestion_score")
    per_lane = m.get("vehicles_per_lane")
    if record.get("lanes_count") is None:
        record["lanes_count"] = len(per_lane) if isinstance(per_lane, dict) else None

    extra = record.get("extra")
    extra = dict(extra) if isinstance(extra, dict) else {}
    for key in _PLANNING_EXTRA_KEYS:
        if m.get(key) is not None and key not in extra:
            extra[key] = _jsonable(m.get(key))
    # Speed-calibration provenance: gate any published speed statistic with
    # this block ("high" = homography ground-plane speed; "low" = flat
    # pixels-per-meter approximation; "unknown" = no speed samples).
    extra["speed_calibration"] = _jsonable({
        "calibrated": m.get("speed_calibrated"),
        "confidence": m.get("speed_confidence", "unknown"),
    })
    record["extra"] = extra
    return record


def summarize_series_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Average a list of get_feed_metrics_range rows (vehicle_count /
    average_speed / congestion_score) plus the latest speed-calibration
    confidence found in any row's extra block."""
    counts = [r.get("vehicle_count") for r in rows if r.get("vehicle_count") is not None]
    speeds = [r.get("average_speed") for r in rows if r.get("average_speed") is not None]
    congestions = [r.get("congestion_score") for r in rows if r.get("congestion_score") is not None]

    def _mean(xs):
        return sum(xs) / len(xs) if xs else None

    speed_confidence = None
    speed_calibrated = None
    for r in reversed(rows):  # latest row wins
        extra = r.get("extra")
        if isinstance(extra, dict):
            sc = extra.get("speed_calibration")
            if isinstance(sc, dict) and sc.get("confidence"):
                speed_confidence = sc.get("confidence")
                speed_calibrated = sc.get("calibrated")
                break
    return {
        "average_vehicle_count": _mean(counts),
        "average_speed": _mean(speeds),
        "average_congestion_score": _mean(congestions),
        "samples": len(rows),
        "speed_confidence": speed_confidence,
        "speed_calibrated": speed_calibrated,
    }
