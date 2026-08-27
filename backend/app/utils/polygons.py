"""Canonical ROI / exclusion-zone polygon coercion.

The wire contract (dashboard WS + persisted FeedConfigInfo) carries polygons as
a list of normalized [0,1] dicts [{x: float, y: float}, ...] OR normalized
pairs [[x, y], ...]. Legacy configs may carry pixel-space pairs.

Every consumer previously assumed a DIFFERENT format and none handled dicts:
- core_module._initialize_roi_mask: raw pixel pairs (np.array(..., int32))
- core_module._preprocess_frame: pixel pairs, no scaling
- detection.initialize_roi / _apply_exclusion_zones: normalized pairs, scaled
  internally

A saved ROI therefore crashed the whole feed (TypeError `int() argument must be
... not 'dict'` at mask fill; IndexError `pts[:, 0]` on the 1-D dict object
array at crop). This module is the single coercion point: input any accepted
shape, output float32 (N,2) normalized coordinates (or int32 pixel points for
cv2), or None if the polygon is unusable.
"""
from typing import List, Optional, Union

import numpy as np

Polygon = Union[List[dict], List[List[float]], List[tuple]]


def normalize_polygon(polygon, w: int, h: int) -> Optional[np.ndarray]:
    """Coerce any accepted polygon shape to float32 (N,2) normalized [0,1].

    Accepts [{x,y}, ...], [[x,y], ...], [(x,y), ...], and legacy PIXEL-space
    pairs (any |value| > 1.5 is treated as pixels and divided by frame dims).
    Returns None for empty input, non-pair items, or polygons with < 3 points.
    """
    if not polygon:
        return None
    pts: List[List[float]] = []
    for p in polygon:
        if isinstance(p, dict):
            if "x" not in p or "y" not in p:
                return None
            pts.append([p["x"], p["y"]])
        elif isinstance(p, (list, tuple)) and len(p) >= 2:
            pts.append([p[0], p[1]])
        else:
            return None
    if len(pts) < 3:
        return None
    arr = np.asarray(pts, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[1] != 2:
        return None
    if np.max(np.abs(arr)) > 1.5:
        # Legacy pixel-space polygon; convert to normalized [0,1].
        arr = arr / np.asarray([w, h], dtype=np.float32)
    return arr


def pixel_polygon(polygon, w: int, h: int) -> Optional[np.ndarray]:
    """normalize_polygon() -> int32 (N,2) pixel points, ready for cv2.fillPoly."""
    norm = normalize_polygon(polygon, w, h)
    if norm is None:
        return None
    return (norm * np.asarray([w, h], dtype=np.float32)).round().astype(np.int32)
