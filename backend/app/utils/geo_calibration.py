"""
Per-feed geo calibration: maps NORMALIZED image coordinates (u, v in [0,1],
the same space as the wire `bbox` [x1, y1, x2, y2]) to WGS84 (lat, lng) via a
homography.

Pure Python (no numpy/cv2) on purpose: this module is imported by
feed_registry/feed_manager at startup and unit-tested in environments without
the ML stack. Calibration is a rare, tiny computation (4-8 points), so an 8x8
Gaussian elimination is plenty and avoids a hard numpy dependency here.

Wire format: feed config `geo_calibration` is a list of
    {"u": float, "v": float, "lat": float, "lng": float}
with at least 4 points. `attach_geo_homography(entry)` computes the 3x3 matrix
and stores it on the process-registry entry as `geo_homography` (None when
uncalibrated). result_processor reads it to stamp `latitude`/`longitude`
onto every vehicle in the WS frame broadcast.
"""

import math
from typing import Dict, List, Optional, Sequence, Tuple

Point = Dict[str, float]
HMatrix = List[List[float]]


def _finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def validate_geo_points(points: Optional[Sequence[Point]]) -> Optional[str]:
    """Returns an error string, or None if the calibration points are usable."""
    if not points:
        return None  # empty/None = uncalibrated, not an error
    if len(points) < 4:
        return f"geo_calibration needs at least 4 points, got {len(points)}"
    for i, p in enumerate(points):
        for key in ("u", "v", "lat", "lng"):
            if key not in p or not _finite(p[key]):
                return f"point {i}: missing or non-finite '{key}'"
        u, v = float(p["u"]), float(p["v"])
        lat, lng = float(p["lat"]), float(p["lng"])
        # Tolerate slight overshoot (clicks just outside the frame edge).
        if not (-0.5 <= u <= 1.5 and -0.5 <= v <= 1.5):
            return f"point {i}: normalized image coord ({u}, {v}) out of range"
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
            return f"point {i}: (lat, lng) ({lat}, {lng}) out of range"
    return None


def _solve_linear(A: List[List[float]], b: List[float]) -> Optional[List[float]]:
    """Gaussian elimination with partial pivoting. Returns None if singular."""
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            return None
        M[col], M[piv] = M[piv], M[col]
        inv = 1.0 / M[col][col]
        for r in range(col + 1, n):
            f = M[r][col] * inv
            if f == 0.0:
                continue
            for c in range(col, n + 1):
                M[r][c] -= f * M[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = M[r][n] - sum(M[r][c] * x[c] for c in range(r + 1, n))
        x[r] = s / M[r][r]
    return x


def _normalization_transform(pts: List[Tuple[float, float]]) -> HMatrix:
    """Hartley normalization: translate to centroid, scale to mean dist sqrt(2)."""
    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    mean_dist = sum(math.hypot(p[0] - cx, p[1] - cy) for p in pts) / n
    s = math.sqrt(2.0) / mean_dist if mean_dist > 1e-12 else 1.0
    # T = [[s, 0, -s*cx], [0, s, -s*cy], [0, 0, 1]]
    return [[s, 0.0, -s * cx], [0.0, s, -s * cy], [0.0, 0.0, 1.0]]


def _mat_mul(A: HMatrix, B: HMatrix) -> HMatrix:
    return [
        [sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)]
        for i in range(3)
    ]


def _mat_inv3(T: HMatrix) -> HMatrix:
    a, b, c = T[0]
    d, e, f = T[1]
    g, h, i = T[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-15:
        raise ValueError("singular normalization matrix")
    inv_det = 1.0 / det
    return [
        [(e * i - f * h) * inv_det, (c * h - b * i) * inv_det, (b * f - c * e) * inv_det],
        [(f * g - d * i) * inv_det, (a * i - c * g) * inv_det, (c * d - a * f) * inv_det],
        [(d * h - e * g) * inv_det, (b * g - a * h) * inv_det, (a * e - b * d) * inv_det],
    ]


def compute_geo_homography(points: Sequence[Point]) -> Optional[HMatrix]:
    """
    DLT homography from >=4 normalized-image -> (lat, lng) correspondences.
    Uses least squares (normal equations) when more than 4 points are given.
    Returns a 3x3 matrix mapping [u, v, 1] -> [lat*w, lng*w, w], or None.
    """
    err = validate_geo_points(points)
    if err and points:  # validate returns None for empty input
        return None
    if not points or len(points) < 4:
        return None

    src = [(float(p["u"]), float(p["v"])) for p in points]
    dst = [(float(p["lat"]), float(p["lng"])) for p in points]

    Ts = _normalization_transform(src)
    Td = _normalization_transform(dst)
    src_n = [(Ts[0][0] * u + Ts[0][2], Ts[1][1] * v + Ts[1][2]) for u, v in src]
    dst_n = [(Td[0][0] * x + Td[0][2], Td[1][1] * y + Td[1][2]) for x, y in dst]

    # DLT with h33 fixed to 1: each correspondence gives 2 equations in the
    # 8 unknowns (h11..h32). Solve A h = b, then least squares via A^T A.
    rows: List[List[float]] = []
    rhs: List[float] = []
    for (u, v), (x, y) in zip(src_n, dst_n):
        rows.append([u, v, 1.0, 0.0, 0.0, 0.0, -u * x, -v * x])
        rhs.append(x)
        rows.append([0.0, 0.0, 0.0, u, v, 1.0, -u * y, -v * y])
        rhs.append(y)
    # Normal equations (8x8) for the overdetermined case.
    AtA = [[sum(r[i] * r[j] for r in rows) for j in range(8)] for i in range(8)]
    Atb = [sum(r[i] * b for r, b in zip(rows, rhs)) for i in range(8)]
    h = _solve_linear(AtA, Atb)
    if h is None:
        return None
    Hn = [
        [h[0], h[1], h[2]],
        [h[3], h[4], h[5]],
        [h[6], h[7], 1.0],
    ]
    # Denormalize: H = Td^-1 * Hn * Ts
    H = _mat_mul(_mat_inv3(Td), _mat_mul(Hn, Ts))
    return H


def project_geo(H: Optional[HMatrix], u: float, v: float) -> Optional[Tuple[float, float]]:
    """Project a normalized image point through H. Returns (lat, lng) or None."""
    if H is None or not (_finite(u) and _finite(v)):
        return None
    w = H[2][0] * u + H[2][1] * v + H[2][2]
    if abs(w) < 1e-12:
        return None
    lat = (H[0][0] * u + H[0][1] * v + H[0][2]) / w
    lng = (H[1][0] * u + H[1][1] * v + H[1][2]) / w
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return None  # projection flew off the globe = bad homography
    return lat, lng


def bbox_to_geo(H: Optional[HMatrix], bbox) -> Optional[Tuple[float, float]]:
    """
    Ground position of a wire bbox [x1, y1, x2, y2] (normalized): the
    bottom-center of the box is the tire contact point, which is what a
    homography calibrated on road points should map.
    """
    if not bbox or len(bbox) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(c) for c in bbox)
    except (TypeError, ValueError):
        return None
    return project_geo(H, (x1 + x2) / 2.0, y2)


def attach_geo_homography(entry: Dict) -> None:
    """Recompute entry['geo_homography'] from entry['config_info'].geo_calibration."""
    cfg = entry.get("config_info") if entry else None
    points = getattr(cfg, "geo_calibration", None) if cfg is not None else None
    entry["geo_homography"] = compute_geo_homography(points) if points else None
