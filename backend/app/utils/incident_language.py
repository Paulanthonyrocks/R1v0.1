"""Direction-aware incident language for the Chief Traffic Warden layer.

Turns placement metadata (feed road_id, road polyline, camera/vehicle
bearing) into operator-readable phrases:

    "Vehicle 42 traveling SW on NE-bound Main St, 300 m along, 80 m from
     intersection 34.0000,-118.0000"

Pure python (no numpy) so it is testable without the ML stack.
"""
from typing import Any, Dict, Optional

COMPASS16 = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]


def compass_label(bearing_deg: float) -> str:
    """16-point compass label for a bearing in degrees [0, 360)."""
    b = float(bearing_deg) % 360.0
    return COMPASS16[int(round(b / 22.5)) % 16]


def road_direction_label(segment_bearing_deg: float) -> str:
    """'NE-bound' style label for a road segment's direction of travel."""
    return f"{compass_label(segment_bearing_deg)}-bound"


def describe_geo_context(
    snap: Optional[Dict[str, Any]],
    motion_bearing_deg: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Build the geo context dict for an incident.

    snap: RoadService.describe_snap result ({"found": True, "road_id",
    "road_name", "distance_along_m", "segment_bearing_deg",
    "nearest_intersection_id", "distance_to_intersection_m", ...}).
    motion_bearing_deg: compass bearing of the vehicle's motion (from its
    pixel velocity through the feed homography), if known.

    Returns None when there is no road context; otherwise a dict with a
    human-readable "phrase" plus the structured fields.
    """
    if not snap or not snap.get("found"):
        return None
    road_name = snap.get("road_name") or snap.get("road_id") or "unknown road"
    seg = snap.get("segment_bearing_deg")
    chainage = snap.get("distance_along_m")
    inter = snap.get("nearest_intersection_id")
    inter_d = snap.get("distance_to_intersection_m")

    road_phrase = (
        f"on {road_direction_label(seg)} {road_name}" if seg is not None else f"on {road_name}"
    )
    pos_parts = []
    if chainage is not None:
        pos_parts.append(f"{int(round(chainage))} m along")
    if inter is not None:
        if inter_d is not None:
            pos_parts.append(f"{int(round(inter_d))} m from intersection {inter}")
        else:
            pos_parts.append(f"near intersection {inter}")
    motion = ""
    if motion_bearing_deg is not None:
        motion = f"traveling {compass_label(motion_bearing_deg)} "
    phrase = motion + road_phrase + (", " + ", ".join(pos_parts) if pos_parts else "")
    return {
        "road_id": snap.get("road_id"),
        "road_name": snap.get("road_name"),
        "chainage_m": chainage,
        "road_bearing_deg": seg,
        "road_direction": road_direction_label(seg) if seg is not None else None,
        "motion_bearing_deg": motion_bearing_deg,
        "motion_compass": compass_label(motion_bearing_deg) if motion_bearing_deg is not None else None,
        "nearest_intersection_id": inter,
        "distance_to_intersection_m": inter_d,
        "phrase": phrase,
    }
