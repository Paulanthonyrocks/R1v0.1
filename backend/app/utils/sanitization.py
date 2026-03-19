import bleach
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
    """
    if isinstance(data, str):
        return sanitize_html(data)
    elif isinstance(data, list):
        return [sanitize_input(item) for item in data]
    elif isinstance(data, dict):
        return {k: sanitize_input(v) for k, v in data.items()}
    return data
