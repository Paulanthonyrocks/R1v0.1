"""Road-keyed origin-destination analytics.

Re-keys vehicle transitions (feed -> feed per global vehicle) onto the road
registry: each placed feed resolves to "<road_id>@<chainage bucket>m", so
the OD matrix reads as corridor flow ("main-st@400m" -> "main-st@900m")
instead of camera-to-camera. Feeds without placement keep a "feed:<id>"
key so nothing is silently dropped.

Pure python (no numpy/pandas) so it is testable without the ML stack.
"""
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_BUCKET_M = 100.0


def road_key(snap: Optional[Dict[str, Any]], bucket_m: float = DEFAULT_BUCKET_M) -> Optional[str]:
    """'main-st@400m' style key for a describe_snap result; None if no road."""
    if not snap or not snap.get("found"):
        return None
    chain = snap.get("distance_along_m") or 0.0
    bucket = int(chain // bucket_m) * bucket_m
    return f"{snap.get('road_id')}@{bucket:g}m"


def _label(key: str, snap: Optional[Dict[str, Any]], feed_id: str, bucket_m: float) -> Dict[str, Any]:
    if snap and snap.get("found"):
        chain = snap.get("distance_along_m") or 0.0
        bucket = int(chain // bucket_m) * bucket_m
        return {
            "label": f"{snap.get('road_name')} @ {bucket:g}m",
            "road_id": snap.get("road_id"),
            "road_name": snap.get("road_name"),
            "chainage_m": round(chain, 1),
            "bucket_m": bucket,
        }
    return {"label": f"feed:{feed_id}", "feed_id": feed_id, "road_id": None, "bucket_m": None}


def build_road_od_matrix(
    transitions: Iterable[Tuple[str, str]],
    feed_geo: Dict[str, Optional[Dict[str, Any]]],
    bucket_m: float = DEFAULT_BUCKET_M,
) -> Dict[str, Any]:
    """
    transitions: (origin_feed_id, dest_feed_id) per vehicle step.
    feed_geo: {feed_id: describe_snap result or None}.

    Returns {"matrix": {origin_key: {dest_key: count}}, "labels": {key: {...}}}.
    """
    matrix: Dict[str, Dict[str, int]] = {}
    labels: Dict[str, Dict[str, Any]] = {}
    total = 0
    for origin_feed, dest_feed in transitions:
        if origin_feed == dest_feed:
            continue
        ko = road_key(feed_geo.get(origin_feed), bucket_m) or f"feed:{origin_feed}"
        kd = road_key(feed_geo.get(dest_feed), bucket_m) or f"feed:{dest_feed}"
        matrix.setdefault(ko, {})
        matrix[ko][kd] = matrix[ko].get(kd, 0) + 1
        if ko not in labels:
            labels[ko] = _label(ko, feed_geo.get(origin_feed), origin_feed, bucket_m)
        if kd not in labels:
            labels[kd] = _label(kd, feed_geo.get(dest_feed), dest_feed, bucket_m)
        total += 1
    return {"matrix": matrix, "labels": labels, "metadata": {"total_transitions": total, "bucket_m": bucket_m}}


def annotate_heatmap_point(
    lat: float,
    lng: float,
    road_service,
    max_distance_m: float = 150.0,
) -> Dict[str, Any]:
    """Attach road corridor fields (road_id, road_name, chainage_m,
    segment_bearing_deg) to a WGS84 heatmap point via the road registry."""
    snap = road_service.describe_snap(lat, lng, max_distance_m=max_distance_m)
    if snap.get("found"):
        return {
            "road_id": snap["road_id"],
            "road_name": snap["road_name"],
            "chainage_m": snap["distance_along_m"],
            "road_bearing_deg": snap["segment_bearing_deg"],
            "off_road_m": snap["distance_m"],
        }
    return {"road_id": None, "road_name": None, "chainage_m": None,
            "road_bearing_deg": None, "off_road_m": None}
