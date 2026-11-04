"""Pure-python placement query matching (backend mirror of hosting
lib/utils/feedSearch.ts) — used to filter recordings/history by road and
camera facing direction, so archive search speaks the same language as the
live map.

Query tokens (values lowercased):
  road:  road id, or road-name substring
  dir:   compass point (NE), degrees (90), with-road, against-road
"""
import math
from typing import Any, Dict, List, Optional, Sequence

from app.utils.geo_utils import snap_to_roads

COMPASS = {
    "N": 0.0, "NNE": 22.5, "NE": 45.0, "ENE": 67.5, "E": 90.0, "ESE": 112.5,
    "SE": 135.0, "SSE": 157.5, "S": 180.0, "SSW": 202.5, "SW": 225.0,
    "WSW": 247.5, "W": 270.0, "WNW": 292.5, "NW": 315.0, "NNW": 337.5,
}


def angle_delta_deg(a: float, b: float) -> float:
    """Smallest absolute difference between two compass bearings (deg)."""
    d = (a - b) % 360.0
    return d if d <= 180.0 else 360.0 - d


def _dir_target_deg(token: str) -> Optional[float]:
    up = token.upper()
    if up in COMPASS:
        return COMPASS[up]
    try:
        n = float(token)
    except (TypeError, ValueError):
        return None
    return n % 360.0


def heading_matches_dir(heading_deg: float, token: str) -> bool:
    """Absolute direction: |heading - target| <= 22.5 deg (one sector)."""
    target = _dir_target_deg(token)
    if target is None:
        return False
    return angle_delta_deg(heading_deg, target) <= 22.5


def resolve_road(query: Optional[str], roads: Sequence) -> Optional[Any]:
    """Exact road id, then name substring — mirror of TS resolveRoad."""
    if not query:
        return None
    q = query.lower()
    for r in roads:
        if str(getattr(r, "id", "")).lower() == q:
            return r
    for r in roads:
        if q in str(getattr(r, "name", "")).lower():
            return r
    return None


def heading_matches_road_dir(
    heading_deg: float,
    road_id: str,
    lat: float,
    lng: float,
    token: str,
    roads: Sequence,
) -> bool:
    """with-road: heading within +/-45 deg of the road segment bearing at the
    camera position; against-road: beyond 135 deg."""
    road = next((r for r in roads if str(getattr(r, "id", "")) == road_id), None)
    if road is None:
        return False
    snap = snap_to_roads(lat, lng, [road], 1e9)
    if not snap.get("found"):
        return False
    delta = angle_delta_deg(heading_deg, snap["segment_bearing_deg"])
    return delta <= 45.0 if token == "with-road" else delta >= 135.0


def feed_matches_placement(
    feed_cfg: Dict[str, Any],
    road_query: Optional[str],
    dir_query: Optional[str],
    roads: Sequence,
) -> bool:
    """feed_cfg: a FeedConfigInfo.model_dump()-style dict (road_id, heading_deg,
    latitude, longitude, ...). roads: Road model list."""
    if road_query:
        rid = feed_cfg.get("road_id")
        if not rid:
            return False
        road = resolve_road(road_query, roads)
        if road is not None:
            if str(getattr(road, "id", "")) != str(rid):
                return False
        elif road_query.lower() not in str(rid).lower():
            return False
    if dir_query:
        heading = feed_cfg.get("heading_deg")
        if heading is None:
            return False
        token = dir_query.lower()
        if token in ("with-road", "against-road"):
            lat = feed_cfg.get("latitude")
            lng = feed_cfg.get("longitude")
            rid = feed_cfg.get("road_id")
            if lat is None or lng is None or not rid:
                return False
            if not heading_matches_road_dir(float(heading), str(rid), lat, lng, token, roads):
                return False
        elif not heading_matches_dir(float(heading), token):
            return False
    return True
