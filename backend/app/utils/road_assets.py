"""Join traffic signals and work-zones onto the road graph.

signals_on_road: match each signal's location to a road polyline (within
match_radius_m) and to the nearest registered intersection node.
workzones_on_road: work-zones are feed-scoped (config work_zones:), so a
zone lands on a road via its feed's placement snap (chainage).

Pure python (no numpy) so it is testable without the ML stack.
"""
from typing import Any, Dict, List, Optional

from app.utils.geo_utils import snap_to_roads, haversine_m


def _road_dict(road: Any) -> Dict[str, Any]:
    if hasattr(road, "model_dump"):
        return road.model_dump()
    return road


def _signal_latlng(sig: Dict[str, Any]):
    loc = sig.get("location") or {}
    lat = loc.get("latitude", sig.get("latitude"))
    lng = loc.get("longitude", sig.get("longitude"))
    if lat is None or lng is None:
        return None
    return float(lat), float(lng)


def signals_on_road(
    signals: List[Dict[str, Any]],
    road: Any,
    match_radius_m: float = 80.0,
    intersection_radius_m: float = 60.0,
) -> List[Dict[str, Any]]:
    """Signals whose location lies within match_radius_m of the road.

    Each result gains chainage_m, distance_m, and intersection_id (nearest
    registered intersection node within intersection_radius_m, else None).
    """
    rd = _road_dict(road)
    rid = rd.get("id")
    rname = rd.get("name")
    poly = [
        {"lat": p["lat"], "lng": p["lng"]} if isinstance(p, dict) else {"lat": p.lat, "lng": p.lng}
        for p in rd.get("polyline") or []
    ]
    if len(poly) < 2:
        return []
    intersections = []
    for iid in rd.get("intersection_ids") or []:
        try:
            la, ln = (float(x) for x in str(iid).split(","))
            intersections.append((iid, la, ln))
        except (ValueError, TypeError):
            continue

    out: List[Dict[str, Any]] = []
    for sig in signals:
        ll = _signal_latlng(sig)
        if ll is None:
            continue
        snap = snap_to_roads(ll[0], ll[1], [{"id": rid, "name": rname, "polyline": poly}], match_radius_m)
        if not snap.get("found"):
            continue
        nearest_inter = None
        nearest_d = None
        for iid, la, ln in intersections:
            d = haversine_m(ll[0], ll[1], la, ln)
            if nearest_d is None or d < nearest_d:
                nearest_inter, nearest_d = iid, d
        if nearest_d is not None and nearest_d > intersection_radius_m:
            nearest_inter = None
        out.append({
            "signal_id": sig.get("signal_id"),
            "phase": sig.get("current_phase"),
            "operational_status": sig.get("operational_status"),
            "latitude": ll[0],
            "longitude": ll[1],
            "road_id": rid,
            "road_name": rname,
            "chainage_m": snap.get("distance_along_m"),
            "distance_m": snap.get("distance_m"),
            "intersection_id": nearest_inter,
        })
    out.sort(key=lambda s: (s["chainage_m"] if s["chainage_m"] is not None else 0))
    return out


def workzones_on_road(
    zones: List[Dict[str, Any]],
    road_id: str,
    feed_geo: Dict[str, Optional[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Active work-zones whose feed is placed on this road, with chainage."""
    out: List[Dict[str, Any]] = []
    for z in zones:
        fid = z.get("feed_id")
        snap = feed_geo.get(fid) if fid else None
        if not snap or not snap.get("found") or snap.get("road_id") != road_id:
            continue
        out.append({
            "id": z.get("id"),
            "feed_id": fid,
            "lane": z.get("lane"),
            "speed_limit": z.get("speed_limit"),
            "starts_at": z.get("starts_at"),
            "ends_at": z.get("ends_at"),
            "chainage_m": snap.get("distance_along_m"),
            "road_name": snap.get("road_name"),
        })
    out.sort(key=lambda z: (z["chainage_m"] if z["chainage_m"] is not None else 0))
    return out
