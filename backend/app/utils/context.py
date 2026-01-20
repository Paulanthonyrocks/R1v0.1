from contextvars import ContextVar, copy_context
import functools
from typing import Optional

# Define context variables
current_user: ContextVar[Optional[str]] = ContextVar('current_user', default=None)
current_trace_id: ContextVar[Optional[str]] = ContextVar('current_trace_id', default=None)

def preserve_context(func):
    """Decorator to preserve context variables in async tasks."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        ctx = copy_context()
        return await ctx.run(func, *args, **kwargs)
    return wrapper

def set_request_context(trace_id: str, user: Optional[str] = None):
    """Set the context for the current request."""
    current_trace_id.set(trace_id)
    if user:
        current_user.set(user)
