"""Temporal detection fusion (#6a): vote/average overlapping raw detections
across the last N frames before they reach the tracker.

Detections are the detector's 4-tuples (bbox, cls, conf, embedding).
High-confidence detections (>= high_conf_pass_through) pass through
untouched; lower-confidence ones survive only with >= min_support matches
(same class, IoU >= iou_threshold) across the history window, and are
emitted as the mean box/conf. This suppresses single-frame false positives
from motion blur / compression artifacts without a better model.

Pure stdlib; history is a plain list the caller owns.
"""
from typing import Any, Dict, List, Optional, Sequence, Tuple

Det = Tuple[Any, Any, float, Any]  # (bbox, cls, conf, embedding)


def iou(b1: Sequence[float], b2: Sequence[float]) -> float:
    x1 = max(b1[0], b2[0]); y1 = max(b1[1], b2[1])
    x2 = min(b1[2], b2[2]); y2 = min(b1[3], b2[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    a1 = max(0.0, b1[2] - b1[0]) * max(0.0, b1[3] - b1[1])
    a2 = max(0.0, b2[2] - b2[0]) * max(0.0, b2[3] - b2[1])
    union = a1 + a2 - inter
    return inter / union if union > 0 else 0.0


def fuse_detections(detections: List[Det],
                    history: List[List[Det]],
                    iou_thresh: float = 0.5,
                    window: int = 3,
                    min_support: int = 2,
                    high_conf: float = 0.6
                    ) -> Tuple[List[Det], List[List[Det]], Dict[str, int]]:
    """Fuse current-frame detections against up to `window`-1 past frames.

    Returns (fused, new_history, stats). new_history keeps at most `window`
    frames including the current one."""
    fused: List[Det] = []
    for d in detections or []:
        bbox, cls, conf = d[0], d[1], float(d[2])
        emb = d[3] if len(d) > 3 else None
        if conf >= float(high_conf):
            fused.append(d)
            continue
        support = 0
        boxes = [list(map(float, bbox))]
        confs = [conf]
        for past in history or []:
            best_iou, best = 0.0, None
            for p in past:
                if p[1] != cls:
                    continue
                v = iou(bbox, p[0])
                if v > best_iou:
                    best_iou, best = v, p
            if best is not None and best_iou >= float(iou_thresh):
                support += 1
                boxes.append(list(map(float, best[0])))
                confs.append(float(best[2]))
        if support >= int(min_support):
            n = len(boxes)
            avg = [round(sum(b[k] for b in boxes) / n, 2) for k in range(4)]
            fused.append((avg, cls, round(sum(confs) / len(confs), 4), emb))
        # else: unsupported low-conf detection -> dropped
    new_history = (list(history or []) + [list(detections or [])])[-max(1, int(window)):]
    stats = {"in": len(detections or []), "kept": len(fused),
             "dropped": len(detections or []) - len(fused)}
    return fused, new_history, stats
