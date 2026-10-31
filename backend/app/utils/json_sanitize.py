"""Make pandas/numpy-derived structures JSON-serializable for FastAPI responses."""
from typing import Any

try:
    import numpy as _np
except Exception:  # numpy absent (e.g. minimal unit-test env) -- duck-type instead
    _np = None

try:
    import pandas as _pd
except Exception:
    _pd = None


def to_jsonable(obj: Any) -> Any:
    """Recursively convert numpy scalars / pandas Timestamps to plain Python types.

    ``DataFrame.to_dict()`` / ``pd.crosstab(...).to_dict()`` produce
    ``numpy.int64``/``float64`` values (and ``pd.Timestamp`` index entries),
    which FastAPI's ``jsonable_encoder`` cannot serialize -- it raises, the
    unhandled-exception handler fires, and the client gets HTTP 500. Seen
    live on GET /api/v1/analytics/od-matrix once ReID produced cross-feed
    transitions; the empty-matrix early returns cast with ``int()`` and
    stayed 200, which is why this only fires when there is actual data.
    """
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if _pd is not None and isinstance(obj, _pd.Timestamp):
        return obj.isoformat()
    if _np is not None and isinstance(obj, _np.generic):
        return obj.item()
    if not isinstance(obj, (str, bytes)) and hasattr(obj, "item") and callable(obj.item):
        # Duck-typed numpy scalar fallback when numpy itself isn't importable.
        try:
            return obj.item()
        except Exception:
            pass
    return obj
