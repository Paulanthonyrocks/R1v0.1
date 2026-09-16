"""Vulnerable road users (feature 6).

Near-miss / crosswalk-dwell helpers for peds and cyclists. Geometry stays
in pixels until homography lands; thresholds are conservative so this path
cannot storm. SafetyMonitor owns emission; these are pure predicates.
"""
import logging
from typing import Any, Dict

logger = logging.getLogger("app.services.vru")

VRU_CLASSES = {"PERSON", "PEDESTRIAN", "BICYCLE", "CYCLIST", "MOTORCYCLE"}


def is_vru(class_name: str) -> bool:
    return str(class_name).upper() in VRU_CLASSES


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
) -> Dict[str, Any]:
    if not is_vru(vru_class):
        return {"vru": False}
    nm = near_miss(ttc_sec, min_dist_px, ped_in_crosswalk)
    dwell = crosswalk_dwell(dwell_sec) if ped_in_crosswalk else False
    return {"vru": True, "near_miss": nm, "crosswalk_dwell": dwell,
            "alert": bool(nm or dwell)}
