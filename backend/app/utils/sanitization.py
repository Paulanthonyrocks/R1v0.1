import bleach
import numpy as np
from typing import Any, Dict, List, Union

def sanitize_html(text: str) -> str:
    """
    Sanitize HTML content using bleach.
    Removes all tags by default to prevent XSS.
    """
    if not text:
        return text
    return bleach.clean(text, tags=[], attributes={}, strip=True)

def sanitize_input(data: Any) -> Any:
    """
    Recursively sanitize input data (strings, lists, dicts).
    Also converts NumPy types (bool_, int_, float_, ndarray) to Python types
    to ensure Pydantic JSON serialization works correctly.
    """
    # 1. Handle NumPy types (convert to native Python types)
    if isinstance(data, np.bool_):
        return bool(data)
    elif isinstance(data, np.integer):
        return int(data)
    elif isinstance(data, np.floating):
        return float(data)
    elif isinstance(data, np.ndarray):
        return data.tolist()

    # 2. Handle standard containers and types
    if isinstance(data, str):
        return sanitize_html(data)
    elif isinstance(data, list):
        return [sanitize_input(item) for item in data]
    elif isinstance(data, dict):
        return {k: sanitize_input(v) for k, v in data.items()}
    return data
