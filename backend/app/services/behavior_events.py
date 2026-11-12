"""Per-vehicle behavior events: speeding, illegal parking, lane changes.

Pure predicates over track dicts (same shape as tailgating/vru_service:
`bbox`, `speed` in km/h or None, `vx`/`vy`, `lane`, `class_id`, `status`).
Emission cooldowns and incident creation belong to the caller
(inference_worker / analytics_service), same pattern as tailgating.py.

Speed units are km/h as produced by CoreModule (homography ground-plane
when calibrated, pixels-per-meter approximation otherwise; None when
uncalibrated -- events requiring speed simply do not fire then).
"""
from typing import Any, Dict, List, Optional

from app.services.vru_service import vru_class_name


def _is_vehicle(track: Dict[str, Any]) -> bool:
    # VRU detections never reach the vehicle tracker, but a track dict can
    # still carry a VRU class id at the boundary -- never judge them.
    return vru_class_name(track.get("class_id", -1)) is None


def _speed_kmh(track: Dict[str, Any]) -> Optional[float]:
    try:
        s = track.get("speed")
        return None if s is None else float(s)
    except (TypeError, ValueError):
        return None


def speeding_event(track: Dict[str, Any], limit_kmh: float,
                   margin_kmh: float = 5.0) -> Optional[Dict[str, Any]]:
    """Per-vehicle speeding: speed above limit + margin. None speed (an
    uncalibrated feed) never fires."""
    speed = _speed_kmh(track)
    if speed is None:
        return None
    thr = float(limit_kmh) + float(margin_kmh)
    if speed <= thr:
        return None
    return {
        "vehicle_id": track.get("id"),
        "speed_kmh": round(speed, 1),
        "limit_kmh": float(limit_kmh),
        "margin_kmh": float(margin_kmh),
        "lane": track.get("lane"),
    }


def parking_event(track: Dict[str, Any], now: float,
                  min_stationary_sec: float = 300.0,
                  stopped_speed_kmh: float = 5.0) -> Optional[Dict[str, Any]]:
    """Illegal-parking candidate: an active vehicle holding a committed lane
    while stationary for >= min_stationary_sec. State lives on the track dict
    itself (`stationary_since`), so the SAME dict object must be passed every
    frame (the worker passes the tracker's persistent dicts). Moving again
    resets the timer; uncalibrated (speed None) leaves the timer untouched."""
    if track.get("status") != "active" or not _is_vehicle(track):
        return None
    if track.get("lane") is None or track.get("bbox") is None:
        return None
    speed = _speed_kmh(track)
    if speed is None:
        return None
    if speed >= float(stopped_speed_kmh):
        track.pop("stationary_since", None)
        return None
    since = track.get("stationary_since")
    if since is None:
        track["stationary_since"] = now
        return None
    dur = now - float(since)
    if dur < float(min_stationary_sec):
        return None
    return {
        "vehicle_id": track.get("id"),
        "parked_sec": round(dur, 1),
        "lane": track.get("lane"),
        "speed_kmh": round(speed, 1),
    }


def lane_change_event(track: Dict[str, Any],
                      prev_lane: Optional[int]) -> Optional[Dict[str, Any]]:
    """Committed-lane transition. prev_lane is the lane committed on the
    previous frame (None on first sighting -> no event)."""
    lane = track.get("lane")
    if track.get("status") != "active" or not _is_vehicle(track):
        return None
    if lane is None or prev_lane is None:
        return None
    if int(lane) == int(prev_lane):
        return None
    return {
        "vehicle_id": track.get("id"),
        "from_lane": prev_lane,
        "to_lane": lane,
    }


def frame_stats(
    tracks: Optional[Dict[str, Dict[str, Any]]],
    now: float,
    state: Dict[str, Dict[str, Any]],
    limit_kmh: float,
    margin_kmh: float = 5.0,
    min_stationary_sec: float = 300.0,
    stopped_speed_kmh: float = 5.0,
    lane_change_min_seen: int = 10,
    max_events: int = 5,
) -> Dict[str, Any]:
    """Per-frame behavior analysis over active vehicle tracks.

    `state` is a per-feed dict {track_id: {"lane": last committed lane,
    "seen": frames seen}} mutated in place; entries for vanished tracks are
    pruned. Pass the tracker's OWN track dicts (not copies) so
    `stationary_since` persists across frames.

    Returns worst-first capped event lists plus frame counts:
    {"speeding", "parking", "lane_changes", "parked_now",
     "lane_changes_by_lane", "n_speeding", "n_parking", "n_lane_changes"}.
    """
    speeding: List[Dict[str, Any]] = []
    parking: List[Dict[str, Any]] = []
    lane_changes: List[Dict[str, Any]] = []
    parked_now = 0
    cur_ids = set()
    for tid, tr in (tracks or {}).items():
        if not isinstance(tr, dict):
            continue
        if tr.get("status") != "active" or not _is_vehicle(tr):
            continue
        cur_ids.add(tid)
        if "id" not in tr:
            tr["id"] = tid
        st = state.setdefault(tid, {"lane": None, "seen": 0})
        st["seen"] += 1
        se = speeding_event(tr, limit_kmh, margin_kmh)
        if se is not None:
            speeding.append(se)
        pe = parking_event(tr, now, min_stationary_sec, stopped_speed_kmh)
        if pe is not None:
            parking.append(pe)
        sp = _speed_kmh(tr)
        if tr.get("stationary_since") is not None and sp is not None and sp < float(stopped_speed_kmh):
            parked_now += 1
        if st["seen"] >= int(lane_change_min_seen):
            lce = lane_change_event(tr, st["lane"])
            if lce is not None:
                lane_changes.append(lce)
        st["lane"] = tr.get("lane")
    for tid in list(state.keys()):
        if tid not in cur_ids:
            del state[tid]
    speeding.sort(key=lambda e: -e["speed_kmh"])
    parking.sort(key=lambda e: -e["parked_sec"])
    by_lane: Dict[str, int] = {}
    for e in lane_changes:
        k = str(e["to_lane"])
        by_lane[k] = by_lane.get(k, 0) + 1
    return {
        "speeding": speeding[:max_events],
        "parking": parking[:max_events],
        "lane_changes": lane_changes[:max_events],
        "parked_now": parked_now,
        "lane_changes_by_lane": by_lane,
        "n_speeding": len(speeding),
        "n_parking": len(parking),
        "n_lane_changes": len(lane_changes),
    }
