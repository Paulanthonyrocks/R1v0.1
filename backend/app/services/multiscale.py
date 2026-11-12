"""Multi-scale detection helpers (#6c): re-scan expanded crops around
small/uncertain full-frame detections at native resolution.

Pure stdlib. The actual second detector pass lives in CoreModule (it owns
the model); this module owns region selection, box offsetting and NMS
merging, all unit-testable without the ML stack. Detection tuples are
(bbox, cls, conf, embedding).
"""
from typing import Any, List, Optional, Sequence, Tuple
import math

Det = Tuple[Any, Any, float, Any]


def _area(b: Sequence[float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def select_crop_regions(detections: List[Det], frame_w: int, frame_h: int,
                        max_crops: int = 4,
                        min_conf: float = 0.3,
                        max_area_frac: float = 0.05,
                        expand_frac: float = 0.25,
                        min_side_px: int = 24) -> List[Tuple[int, int, int, int]]:
    """Crop rects (x1, y1, x2, y2) around the smallest detections worth a
    high-res second pass. Detections below min_conf are skipped (too noisy
    to be worth re-scanning); tiny-but-confident boxes are the target."""
    frame_area = max(1.0, float(frame_w) * float(frame_h))
    cands = []
    for d in detections or []:
        conf = float(d[2])
        if conf < float(min_conf):
            continue
        if _area(d[0]) > float(max_area_frac) * frame_area:
            continue  # large/near-field: full-frame pass already resolves it
        cands.append((_area(d[0]), d[0]))
    cands.sort(key=lambda c: c[0])  # smallest first
    crops: List[Tuple[int, int, int, int]] = []
    for _, b in cands[: max(0, int(max_crops))]:
        w, h = b[2] - b[0], b[3] - b[1]
        ex, ey = w * float(expand_frac), h * float(expand_frac)
        x1 = max(0, int(b[0] - ex)); y1 = max(0, int(b[1] - ey))
        x2 = min(int(frame_w), int(math.ceil(b[2] + ex)))
        y2 = min(int(frame_h), int(math.ceil(b[3] + ey)))
        if (x2 - x1) >= int(min_side_px) and (y2 - y1) >= int(min_side_px):
            crops.append((x1, y1, x2, y2))
    return crops


def offset_box(bbox: Sequence[float], crop: Tuple[int, int, int, int]) -> List[float]:
    x1, y1, _, _ = crop
    return [float(bbox[0]) + x1, float(bbox[1]) + y1,
            float(bbox[2]) + x1, float(bbox[3]) + y1]


def iou(b1: Sequence[float], b2: Sequence[float]) -> float:
    x1 = max(b1[0], b2[0]); y1 = max(b1[1], b2[1])
    x2 = min(b1[2], b2[2]); y2 = min(b1[3], b2[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    a1 = max(0.0, b1[2] - b1[0]) * max(0.0, b1[3] - b1[1])
    a2 = max(0.0, b2[2] - b2[0]) * max(0.0, b2[3] - b2[1])
    union = a1 + a2 - inter
    return inter / union if union > 0 else 0.0


def nms(dets: List[Det], iou_thresh: float = 0.5) -> List[Det]:
    """Class-aware NMS: higher-confidence box suppresses overlapping
    same-class boxes."""
    out: List[Det] = []
    for d in sorted(dets or [], key=lambda d: -float(d[2])):
        if all(iou(d[0], o[0]) < float(iou_thresh) or o[1] != d[1] for o in out):
            out.append(d)
    return out


def merge_detections(full: List[Det], crop_dets: List[Det],
                     iou_thresh: float = 0.5) -> List[Det]:
    """Merge second-pass crop detections into the full-frame set."""
    return nms(list(full or []) + list(crop_dets or []), iou_thresh)
