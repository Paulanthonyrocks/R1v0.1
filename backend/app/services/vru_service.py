"""Vulnerable road users (feature 6).

Near-miss / crosswalk-dwell helpers for peds and cyclists. Geometry stays
in pixels until homography lands; thresholds are conservative so this path
cannot storm. SafetyMonitor owns emission; these are pure predicates.
"""
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("app.services.vru")

VRU_CLASSES = {"PERSON", "PEDESTRIAN", "BICYCLE", "CYCLIST", "MOTORCYCLE"}

# COCO class ids for vulnerable road users (YOLOv8 class list).
VRU_CLASS_IDS = {0: "person", 1: "bicycle"}


def is_vru(class_name: str) -> bool:
    return str(class_name).upper() in VRU_CLASSES


def vru_class_name(cls_id: Any) -> Optional[str]:
    """Map a COCO class id to its VRU class name, or None if not a VRU class."""
    try:
        return VRU_CLASS_IDS.get(int(cls_id))
    except (TypeError, ValueError):
        return None


def near_miss(
    ttc_sec: float,
    min_dist_px: float,
    ped_in_crosswalk: bool = False,
    ttc_thresh: float = 2.0,
    dist_thresh_px: float = 60.0,
) -> bool:
    """True when a vehicle-VRU pair is on a collision course right now."""
    if ttc_sec < 0:
        return False
    if ped_in_crosswalk:
        return ttc_sec <= ttc_thresh * 1.5 and min_dist_px <= dist_thresh_px * 1.5
    return ttc_sec <= ttc_thresh and min_dist_px <= dist_thresh_px


def crosswalk_dwell(dwell_sec: float, dwell_thresh: float = 5.0) -> bool:
    return dwell_sec >= dwell_thresh


def vru_event(
    vru_class: str,
    ttc_sec: float,
    min_dist_px: float,
    ped_in_crosswalk: bool = False,
    dwell_sec: float = 0.0,
    ttc_thresh: float = 2.0,
    dist_thresh_px: float = 60.0,
) -> Dict[str, Any]:
    if not is_vru(vru_class):
        return {"vru": False}
    nm = near_miss(ttc_sec, min_dist_px, ped_in_crosswalk, ttc_thresh, dist_thresh_px)
    dwell = crosswalk_dwell(dwell_sec) if ped_in_crosswalk else False
    return {"vru": True, "near_miss": nm, "crosswalk_dwell": dwell,
            "alert": bool(nm or dwell)}


def estimate_ttc(
    dist_px: float,
    vx: float,
    vy: float,
    dx: float,
    dy: float,
    min_closing_px_s: float = 0.5,
) -> float:
    """Time-to-collision in seconds for a vehicle with pixel velocity (vx, vy)
    (px/s) heading toward a point (dx, dy) away from it, dist_px currently away.
    Returns -1.0 when the vehicle is not closing on the point."""
    if dist_px <= 0:
        return 0.0
    mag = math.hypot(dx, dy)
    if mag <= 0:
        return -1.0
    ux, uy = dx / mag, dy / mag
    closing = vx * ux + vy * uy  # >0 when velocity points at the target
    if closing < min_closing_px_s:
        return -1.0
    return dist_px / closing


def _box_center(bbox) -> Optional[Tuple[float, float]]:
    try:
        if bbox is None or len(bbox) != 4:
            return None
        return ((float(bbox[0]) + float(bbox[2])) / 2.0,
                (float(bbox[1]) + float(bbox[3])) / 2.0)
    except (TypeError, ValueError, IndexError):
        return None


def frame_stats(
    vru_dets: List[Tuple],
    vehicle_tracks: Optional[Dict[str, Dict[str, Any]]],
    ttc_thresh: float = 2.0,
    dist_thresh_px: float = 60.0,
) -> Dict[str, Any]:
    """Per-frame VRU analysis: counts by class plus vehicle-VRU near-miss pairs.

    vru_dets: [(bbox, cls_id, conf), ...] (VRU detections only).
    vehicle_tracks: {track_id: track dict} with bbox / vx / vy / class_id.
    Returns {"counts": {name: n}, "near_miss": n_pairs, "events": [vru_event, ...]}.
    Pure predicate path — emission/cooldowns belong to the caller.
    """
    counts: Dict[str, int] = {}
    events: List[Dict[str, Any]] = []
    near_miss_count = 0
    for det in vru_dets or []:
        try:
            bbox, cls_id = det[0], det[1]
        except (TypeError, IndexError):
            continue
        name = vru_class_name(cls_id)
        if name is None:
            continue
        counts[name] = counts.get(name, 0) + 1
        vc = _box_center(bbox)
        if vc is None:
            continue
        for tid, tr in (vehicle_tracks or {}).items():
            if not isinstance(tr, dict):
                continue
            if vru_class_name(tr.get("class_id", -1)) is not None:
                continue  # don't pair VRU against VRU
            tc = _box_center(tr.get("bbox"))
            if tc is None:
                continue
            dx, dy = vc[0] - tc[0], vc[1] - tc[1]
            dist = math.hypot(dx, dy)
            try:
                vx = float(tr.get("vx") or 0.0)
                vy = float(tr.get("vy") or 0.0)
            except (TypeError, ValueError):
                vx = vy = 0.0
            ttc = estimate_ttc(dist, vx, vy, dx, dy)
            ev = vru_event(name, ttc, dist,
                           ttc_thresh=ttc_thresh, dist_thresh_px=dist_thresh_px)
            if not ev.get("vru"):
                continue
            ev["vru_class"] = name
            ev["vehicle_id"] = tid
            ev["ttc_sec"] = round(ttc, 2) if ttc >= 0 else None
            ev["min_dist_px"] = round(dist, 1)
            events.append(ev)
            if ev.get("near_miss"):
                near_miss_count += 1
    return {"counts": counts, "near_miss": near_miss_count, "events": events}
