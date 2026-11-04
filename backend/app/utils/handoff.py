"""Cross-camera handoff on the road graph.

Given a road, the camera that currently sees a vehicle, and the vehicle's
compass motion bearing, rank the other cameras on that road by how far ahead
they sit in the direction of travel — the drive-through order from item 7,
projected onto the vehicle's heading. Used for incident language
("hand off to <cam> 200 m ahead") and the /roads/{id}/handoff endpoint.

Pure python (no numpy) so it is testable without the ML stack.
"""
from typing import Any, Dict, List, Optional

from app.utils.geo_utils import snap_to_roads


def _angle_delta(a: float, b: float) -> float:
    d = (b - a) % 360.0
    if d > 180.0:
        d -= 360.0
    return d


def _road_polyline_points(road: Any) -> List:
    poly = getattr(road, "polyline", None)
    if poly is None and isinstance(road, dict):
        poly = road.get("polyline")
    out = []
    for p in poly or []:
        if hasattr(p, "lat"):
            out.append({"lat": p.lat, "lng": p.lng})
        elif isinstance(p, dict):
            out.append({"lat": p["lat"], "lng": p["lng"]})
        else:
            out.append({"lat": p[0], "lng": p[1]})
    return out


def _road_id_name(road: Any):
    if hasattr(road, "id"):
        return road.id, road.name
    return road.get("id"), road.get("name")


def handoff_candidates(
    road: Any,
    from_feed_id: str,
    motion_bearing_deg: Optional[float],
    cameras: List[Dict[str, Any]],
    max_results: int = 3,
    max_snap_m: float = 150.0,
) -> List[Dict[str, Any]]:
    """
    cameras: [{"feed_id", "name", "latitude", "longitude", "heading_deg",
    "road_id"}, ...] — typically the placed feeds from the feed registry.

    Returns candidates sorted by distance ahead of the source camera along
    the road in the direction of travel:
    [{"feed_id", "name", "distance_along_m", "distance_ahead_m",
      "heading_deg", "relation"}]. Empty when the road/from-feed can't be
    snapped or no other camera is on the road.
    """
    rid, _ = _road_id_name(road)
    road_dict = {"id": rid, "name": _road_id_name(road)[1], "polyline": _road_polyline_points(road)}

    from_cam = next((c for c in cameras if c.get("feed_id") == from_feed_id), None)
    if from_cam is None or from_cam.get("latitude") is None or from_cam.get("longitude") is None:
        return []
    from_snap = snap_to_roads(from_cam["latitude"], from_cam["longitude"], [road_dict], 1e12)
    if not from_snap.get("found"):
        return []
    chain0 = from_snap["distance_along_m"]
    seg_bearing = from_snap["segment_bearing_deg"]

    # +1 when motion agrees with the road's direction of travel, -1 when against
    if motion_bearing_deg is None:
        along_sign = 1.0
    else:
        along_sign = 1.0 if abs(_angle_delta(motion_bearing_deg, seg_bearing)) <= 90.0 else -1.0

    out: List[Dict[str, Any]] = []
    for c in cameras:
        if c.get("feed_id") == from_feed_id:
            continue
        if c.get("road_id") not in (None, rid):
            continue  # camera assigned to a different road
        if c.get("latitude") is None or c.get("longitude") is None:
            continue
        snap = snap_to_roads(c["latitude"], c["longitude"], [road_dict], max_snap_m)
        if not snap.get("found"):
            continue
        chain_i = snap["distance_along_m"]
        delta = (chain_i - chain0) * along_sign
        if delta <= 0:
            continue  # behind the vehicle's direction of travel
        heading = c.get("heading_deg")
        if heading is not None:
            d = abs(_angle_delta(heading, snap["segment_bearing_deg"]))
            relation = "with" if d <= 45 else ("against" if d >= 135 else "unknown")
        else:
            relation = "unknown"
        out.append({
            "feed_id": c.get("feed_id"),
            "name": c.get("name") or c.get("feed_id"),
            "distance_along_m": round(chain_i, 1),
            "distance_ahead_m": round(delta, 1),
            "heading_deg": heading,
            "relation": relation,
        })
    out.sort(key=lambda x: x["distance_ahead_m"])
    return out[:max_results]
