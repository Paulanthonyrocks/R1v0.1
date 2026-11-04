"""
Pure-python road geometry helpers (no numpy/cv2 on purpose, same constraint as
geo_calibration.py: imported at startup and unit-tested without the ML stack).

All projections use a local equirectangular approximation in meters around the
query point, which is exact enough at road scale (< a few km): 1 deg lat =
~111.2 km, 1 deg lng = ~111.2 km * cos(lat).
"""

import math
from typing import Dict, List, Optional, Sequence, Tuple

EARTH_RADIUS_M = 6371008.8

try:  # normal package import
    from app.utils.geo_calibration import bearing_deg as _bearing_deg
except ImportError:  # loaded standalone by path in tests
    def _bearing_deg(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        p1 = math.radians(lat1)
        p2 = math.radians(lat2)
        dl = math.radians(lng2 - lng1)
        x = math.sin(dl) * math.cos(p2)
        y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
        return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


LatLng = Tuple[float, float]


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def _to_xy(lat: float, lng: float, lat0: float, lng0: float) -> Tuple[float, float]:
    x = math.radians(lng - lng0) * math.cos(math.radians(lat0)) * EARTH_RADIUS_M
    y = math.radians(lat - lat0) * EARTH_RADIUS_M
    return x, y


def _to_ll(x: float, y: float, lat0: float, lng0: float) -> LatLng:
    lat = lat0 + math.degrees(y / EARTH_RADIUS_M)
    lng = lng0 + math.degrees(x / (EARTH_RADIUS_M * math.cos(math.radians(lat0))))
    return lat, lng


def polyline_length_m(polyline: Sequence[LatLng]) -> float:
    return sum(
        haversine_m(polyline[i][0], polyline[i][1], polyline[i + 1][0], polyline[i + 1][1])
        for i in range(len(polyline) - 1)
    )


def project_point_to_polyline(lat: float, lng: float, polyline: Sequence[LatLng]) -> Optional[Dict]:
    """
    Closest point on a polyline to (lat, lng).

    Returns {"segment_index", "t", "point": (lat, lng), "distance_m",
    "distance_along_m", "segment_bearing_deg"} or None for a degenerate
    polyline. distance_along_m is the arc length from the polyline start to
    the projected point; segment_bearing_deg is the compass heading of the
    segment the point landed on (the road's local direction of travel).
    """
    if not polyline or len(polyline) < 2:
        return None
    best = None  # (distance_m, segment_index, t, proj_ll)
    along = 0.0
    for i in range(len(polyline) - 1):
        a, b = polyline[i], polyline[i + 1]
        seg_len = haversine_m(a[0], a[1], b[0], b[1])
        if seg_len < 1e-9:
            proj, t = a, 0.0
            dist = haversine_m(lat, lng, a[0], a[1])
        else:
            ax, ay = _to_xy(a[0], a[1], lat, lng)
            bx, by = _to_xy(b[0], b[1], lat, lng)
            px, py = 0.0, 0.0  # query point is the origin in this frame
            dx, dy = bx - ax, by - ay
            t = ((px - ax) * dx + (py - ay) * dy) / (seg_len * seg_len)
            t = max(0.0, min(1.0, t))
            proj_xy = (ax + t * dx, ay + t * dy)
            proj = _to_ll(proj_xy[0], proj_xy[1], lat, lng)
            dist = math.hypot(proj_xy[0], proj_xy[1])
        if best is None or dist < best[0]:
            best = (dist, i, t, proj, along)
        along += seg_len
    if best is None:
        return None
    dist, i, t, proj, along_before = best
    a, b = polyline[i], polyline[i + 1]
    seg_len = haversine_m(a[0], a[1], b[0], b[1])
    return {
        "segment_index": i,
        "t": t,
        "point": proj,
        "distance_m": dist,
        "distance_along_m": along_before + t * seg_len,
        "segment_bearing_deg": _bearing_deg(a[0], a[1], b[0], b[1]),
    }


def _polyline_of(road) -> List[LatLng]:
    """Accepts Road models (polyline of {lat,lng} points) or plain dicts."""
    pts = road.polyline if hasattr(road, "polyline") else road["polyline"]
    out: List[LatLng] = []
    for p in pts:
        if hasattr(p, "lat"):
            out.append((float(p.lat), float(p.lng)))
        else:
            out.append((float(p["lat"]), float(p["lng"])))
    return out


def _id_name_of(road) -> Tuple[str, str]:
    if hasattr(road, "id"):
        return road.id, road.name
    return road["id"], road["name"]


def snap_to_roads(lat: float, lng: float, roads: Sequence, max_distance_m: float) -> Dict:
    """
    Nearest road to (lat, lng) across all roads. Returns a RoadSnap-shaped
    dict: {"found", "road_id", "road_name", "point": {"lat","lng"},
    "distance_m", "distance_along_m", "segment_bearing_deg"}.
    """
    best = None  # (distance_m, road, proj)
    for road in roads:
        proj = project_point_to_polyline(lat, lng, _polyline_of(road))
        if proj is None:
            continue
        if best is None or proj["distance_m"] < best[0]:
            best = (proj["distance_m"], road, proj)
    if best is None or best[0] > max_distance_m:
        return {"found": False, "road_id": None, "road_name": None, "point": None,
                "distance_m": None, "distance_along_m": None, "segment_bearing_deg": None}
    dist, road, proj = best
    rid, rname = _id_name_of(road)
    plat, plng = proj["point"]
    return {
        "found": True,
        "road_id": rid,
        "road_name": rname,
        "point": {"lat": plat, "lng": plng},
        "distance_m": round(dist, 2),
        "distance_along_m": round(proj["distance_along_m"], 1),
        "segment_bearing_deg": round(proj["segment_bearing_deg"], 1),
    }
