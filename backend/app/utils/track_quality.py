# backend/app/utils/track_quality.py
"""Per-track quality scoring and occlusion flags (perception-quality batch).

Pure stdlib so it loads standalone in tests (no numpy/cv2).

Quality model (0..1):
    quality = conf_weight * conf_ema + hit_weight * hit_score
where conf_ema is an EMA of detection confidence and
hit_score = min(1, hits / probation_threshold) rewards tracks that survived
probation. A miss decays quality by miss_decay so a track that stopped being
matched sinks toward 0 while it is only Kalman-predicted.

Occlusion: a track is "occluded" when another track's bbox covers at least
occlusion_cover_ratio of its area AND the occluder is the more confident /
larger detection. Flags are set on the track dicts as "occluded"/"occluded_by"
and surfaced per feed as occluded_tracks in the metrics.
"""
from typing import Any, Dict, Optional


def _clamp01(x: float) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0
    if x != x:  # NaN
        return 0.0
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def update_track_quality(track: Dict[str, Any], conf: Optional[float],
                         *, conf_weight: float = 0.6, hit_weight: float = 0.4,
                         ema_alpha: float = 0.3, probation_threshold: int = 3) -> float:
    """Fold a new detection into the track's quality score. Returns quality."""
    if conf is not None:
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            conf = None
        if conf is not None and conf != conf:  # NaN guard: never store/EMA a NaN
            conf = None
    if conf is not None:
        prev_ema = track.get("conf_ema")
        if prev_ema is None:
            track["conf_ema"] = conf
        else:
            track["conf_ema"] = (1.0 - ema_alpha) * float(prev_ema) + ema_alpha * conf
    conf_ema = track.get("conf_ema")
    conf_ema = float(conf_ema) if isinstance(conf_ema, (int, float)) and conf_ema == conf_ema else 0.0
    hits = int(track.get("hits", 0) or 0)
    denom = max(1, int(probation_threshold))
    hit_score = min(1.0, hits / denom)
    quality = _clamp01(conf_weight * conf_ema + hit_weight * hit_score)
    track["quality"] = round(quality, 4)
    return quality


def note_track_miss(track: Dict[str, Any], *, miss_decay: float = 0.95) -> float:
    """Decay quality for a frame where the track was not matched."""
    q = track.get("quality")
    q = float(q) if isinstance(q, (int, float)) and q == q else 1.0
    q = _clamp01(q * float(miss_decay))
    track["quality"] = round(q, 4)
    return q


def _box_area(bbox) -> float:
    try:
        w = float(bbox[2]) - float(bbox[0])
        h = float(bbox[3]) - float(bbox[1])
    except (TypeError, ValueError, IndexError):
        return 0.0
    return w * h if w > 0 and h > 0 else 0.0


def _intersection_over_smaller(a, b) -> float:
    """Intersection area / area of the SMALLER box (how covered a is by b)."""
    try:
        ix1 = max(float(a[0]), float(b[0]))
        iy1 = max(float(a[1]), float(b[1]))
        ix2 = min(float(a[2]), float(b[2]))
        iy2 = min(float(a[3]), float(b[3]))
    except (TypeError, ValueError, IndexError):
        return 0.0
    iw = ix2 - ix1
    ih = iy2 - iy1
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    smaller = min(_box_area(a), _box_area(b))
    if smaller <= 0:
        return 0.0
    return inter / smaller


def compute_occlusion_flags(tracks: Dict[str, Dict[str, Any]],
                            cover_ratio: float = 0.6) -> int:
    """Set track["occluded"]/track["occluded_by"] in place. Returns occluded count.

    O(n^2) pairwise over current bboxes; n is capped by max_active_tracks so
    this is bounded. A track is occluded when a MORE confident (tie-break:
    larger area) other track covers >= cover_ratio of its area.
    """
    items = []
    for tid, t in tracks.items():
        bbox = t.get("bbox")
        if bbox is None or len(bbox) != 4:
            t["occluded"] = False
            t.pop("occluded_by", None)
            continue
        try:
            conf = float(t.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        items.append((tid, t, bbox, conf, _box_area(bbox)))
        t["occluded"] = False
        t.pop("occluded_by", None)

    count = 0
    for i in range(len(items)):
        tid_a, t_a, box_a, conf_a, area_a = items[i]
        if area_a <= 0:
            continue
        for j in range(len(items)):
            if i == j:
                continue
            tid_b, t_b, box_b, conf_b, area_b = items[j]
            if area_b <= 0:
                continue
            # occluder must dominate: strictly higher confidence, or equal
            # confidence and strictly larger area (so ties don't both flag).
            if not (conf_b > conf_a or (conf_b == conf_a and area_b > area_a)):
                continue
            if _intersection_over_smaller(box_a, box_b) >= cover_ratio:
                t_a["occluded"] = True
                t_a["occluded_by"] = tid_b
                count += 1
                break
    return count
