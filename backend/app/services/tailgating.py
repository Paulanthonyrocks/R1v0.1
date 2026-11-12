"""Vehicle-vehicle tailgating / time-to-collision analysis (feature: same-lane
following-distance risk).

Pure predicates only — emission, cooldowns and incident creation belong to the
caller (inference_worker / analytics_service), same pattern as vru_service.
Geometry stays in pixels until homography lands; thresholds are conservative so
this path cannot storm.
"""
import math
from typing import Any, Dict, List, Optional

from app.services.vru_service import estimate_ttc, _box_center, vru_class_name


def _vec(track: Dict[str, Any]):
    try:
        return float(track.get("vx") or 0.0), float(track.get("vy") or 0.0)
    except (TypeError, ValueError):
        return 0.0, 0.0


def _is_vehicle(track: Dict[str, Any]) -> bool:
    # VRU detections are kept out of the vehicle tracker, but a track dict can
    # still carry a VRU class id at the boundary -- never pair them.
    return vru_class_name(track.get("class_id", -1)) is None


def tailgating_event(
    follower: Dict[str, Any],
    leader: Dict[str, Any],
    ttc_thresh: float = 1.5,
    min_closing_px_s: float = 0.5,
) -> Optional[Dict[str, Any]]:
    """One ordered (follower, leader) pair in the same lane.

    Returns an event dict when the follower is closing on the leader with
    TTC <= ttc_thresh, else None. Same-lane is decided by the caller (track
    "lane" band); this function only does the kinematics.
    """
    fb, lb = follower.get("bbox"), leader.get("bbox")
    fc, lc = _box_center(fb), _box_center(lb)
    if fc is None or lc is None:
        return None
    dx, dy = lc[0] - fc[0], lc[1] - fc[1]
    dist = math.hypot(dx, dy)
    fvx, fvy = _vec(follower)
    lvx, lvy = _vec(leader)
    # The leader must be AHEAD of the follower along the direction of travel,
    # otherwise this is a head-on/same-lane-opposite case (wrong-way
    # territory), not tailgating. Direction of motion: follower's velocity,
    # falling back to the leader's when the follower is stationary.
    if fvx or fvy:
        ahead = dx * fvx + dy * fvy
    else:
        ahead = dx * lvx + dy * lvy
    if ahead <= 0:
        return None
    # Relative closing speed along the follower->leader axis.
    mag = dist if dist > 0 else 0.0
    closing = ((fvx - lvx) * dx + (fvy - lvy) * dy) / mag if mag > 0 else 0.0
    if closing < min_closing_px_s:
        return None
    ttc = estimate_ttc(dist, fvx - lvx, fvy - lvy, dx, dy,
                       min_closing_px_s=min_closing_px_s)
    if ttc < 0 or ttc > ttc_thresh:
        return None
    return {
        "follower_id": follower.get("id"),
        "leader_id": leader.get("id"),
        "lane": follower.get("lane"),
        "ttc_sec": round(ttc, 2),
        "gap_px": round(dist, 1),
        "closing_px_s": round(closing, 1),
        "follower_speed_px_s": round(math.hypot(fvx, fvy), 1),
        "leader_speed_px_s": round(math.hypot(lvx, lvy), 1),
    }


def frame_stats(
    tracks: Optional[Dict[str, Dict[str, Any]]],
    ttc_thresh: float = 1.5,
    min_closing_px_s: float = 0.5,
    max_events: int = 5,
) -> Dict[str, Any]:
    """Per-frame same-lane tailgating analysis over active vehicle tracks.

    tracks: {track_id: track dict} with bbox / vx / vy / lane / class_id.
    Returns {"pairs": n_checked, "tailgating": n_events, "events": [worst first]}.
    Pure predicate path — emission/cooldowns belong to the caller.
    """
    events: List[Dict[str, Any]] = []
    pairs = 0
    items = [(tid, tr) for tid, tr in (tracks or {}).items()
             if isinstance(tr, dict)
             and tr.get("status", "active") == "active"
             and _is_vehicle(tr)
             and tr.get("lane") is not None
             and tr.get("bbox") is not None]
    # Same-lane only: group by lane band, then check ordered pairs within a band.
    by_lane: Dict[Any, List] = {}
    for tid, tr in items:
        tr = dict(tr)
        tr["id"] = tid
        by_lane.setdefault(tr.get("lane"), []).append(tr)
    for lane, group in by_lane.items():
        for f in group:
            for l in group:
                if f is l:
                    continue
                pairs += 1
                ev = tailgating_event(f, l, ttc_thresh, min_closing_px_s)
                if ev is not None:
                    events.append(ev)
    events.sort(key=lambda e: e["ttc_sec"])
    return {
        "pairs": pairs,
        "tailgating": len(events),
        "events": events[:max_events],
    }
