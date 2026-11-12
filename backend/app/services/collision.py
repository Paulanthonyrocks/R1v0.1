"""Collision detection: overlapping vehicle tracks with a crash signature.

Pure predicates over track dicts (bbox, speed km/h, acceleration m/s^2,
lane, class_id, status). A pair counts as a collision candidate when their
bboxes overlap beyond iou_threshold AND at least one track shows a hard
brake (acceleration <= brake_accel_mps2) or is (nearly) stopped. Emission
cooldowns and incident creation belong to the caller (inference_worker /
analytics_service maps "collision" anomalies to ACCIDENT incidents).
"""
from typing import Any, Dict, List, Optional, Tuple

from app.services.vru_service import vru_class_name


def iou(b1: List[float], b2: List[float]) -> float:
    x1 = max(b1[0], b2[0]); y1 = max(b1[1], b2[1])
    x2 = min(b1[2], b2[2]); y2 = min(b1[3], b2[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    a1 = max(0.0, b1[2] - b1[0]) * max(0.0, b1[3] - b1[1])
    a2 = max(0.0, b2[2] - b2[0]) * max(0.0, b2[3] - b2[1])
    union = a1 + a2 - inter
    return inter / union if union > 0 else 0.0


def _is_vehicle(track: Dict[str, Any]) -> bool:
    return vru_class_name(track.get("class_id", -1)) is None


def _num(track: Dict[str, Any], key: str) -> Optional[float]:
    try:
        v = track.get(key)
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def collision_event(a: Dict[str, Any], b: Dict[str, Any],
                    iou_thresh: float = 0.35,
                    brake_accel_mps2: float = -2.0,
                    stopped_speed_kmh: float = 5.0) -> Optional[Dict[str, Any]]:
    if a.get("status") != "active" or b.get("status") != "active":
        return None
    if not _is_vehicle(a) or not _is_vehicle(b):
        return None
    ba, bb = a.get("bbox"), b.get("bbox")
    if not ba or not bb or len(ba) < 4 or len(bb) < 4:
        return None
    ov = iou(ba, bb)
    if ov < float(iou_thresh):
        return None
    acc_a, acc_b = _num(a, "acceleration"), _num(b, "acceleration")
    sp_a, sp_b = _num(a, "speed"), _num(b, "speed")
    hard_brake = any(x is not None and x <= float(brake_accel_mps2)
                     for x in (acc_a, acc_b))
    stopped = any(s is not None and s < float(stopped_speed_kmh)
                  for s in (sp_a, sp_b))
    if not (hard_brake or stopped):
        return None
    return {
        "vehicles": [a.get("id"), b.get("id")],
        "iou": round(ov, 3),
        "lane": a.get("lane"),
        "accels_mps2": [acc_a, acc_b],
        "speeds_kmh": [sp_a, sp_b],
        "kind": "hard_brake" if hard_brake else "stopped",
    }


def frame_stats(tracks: Optional[Dict[str, Dict[str, Any]]],
                iou_thresh: float = 0.35,
                brake_accel_mps2: float = -2.0,
                stopped_speed_kmh: float = 5.0,
                max_events: int = 3) -> Dict[str, Any]:
    """All active vehicle track pairs; collision candidates sorted by IoU."""
    events: List[Dict[str, Any]] = []
    items: List[Tuple[str, Dict[str, Any]]] = []
    for tid, tr in (tracks or {}).items():
        if isinstance(tr, dict) and tr.get("status") == "active" and _is_vehicle(tr) and tr.get("bbox"):
            tr = dict(tr); tr["id"] = tid
            items.append((tid, tr))
    pairs = 0
    overlaps = 0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            pairs += 1
            ev = collision_event(items[i][1], items[j][1], iou_thresh, brake_accel_mps2, stopped_speed_kmh)
            if ev is not None:
                overlaps += 1
                events.append(ev)
    events.sort(key=lambda e: -e["iou"])
    return {
        "pairs": pairs,
        "overlaps": overlaps,
        "n": len(events),
        "collisions": events[:max_events],
    }
