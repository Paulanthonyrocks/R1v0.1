"""TDD tests for SharedFrameBuffer.acquire() timeout behaviour (Task B).

These tests verify that:
1. The default timeout is 50 ms (not 200 ms as before).
2. When the free pool is empty, acquire() returns None within ~50 ms.
3. An explicit timeout argument is respected.

We mock `_free_pool.get` to raise `queue.Empty` so we measure only the
acquire-side timeout without needing a real Redis instance.
"""
import queue
import time
from unittest.mock import MagicMock, patch

import pytest

from app.utils.shared_frame_buffer import SharedFrameBuffer


def _make_buffer_with_empty_pool():
    """Build a SharedFrameBuffer whose free pool is permanently empty."""
    # Bypass __init__ to avoid Redis /dev/shm side effects.
    buf = SharedFrameBuffer.__new__(SharedFrameBuffer)
    buf.pool_size = 1000
    buf._drop_count = 0
    buf._acquired_count = 0
    buf._release_count = 0
    buf._last_acquire_time = 0.0
    buf._last_release_time = 0.0
    buf._lock = __import__("threading").Lock()
    buf._in_flight = set()
    buf._segments = {}
    buf._free_pool = MagicMock()
    # Always raise Empty — simulates an exhausted pool.
    buf._free_pool.get = MagicMock(side_effect=queue.Empty)
    return buf


def test_acquire_default_timeout_is_50ms():
    """Default acquire() timeout should be 50 ms, not 200 ms."""
    import inspect
    sig = inspect.signature(SharedFrameBuffer.acquire)
    default = sig.parameters["timeout"].default
    assert default == 0.05, (
        f"Expected default timeout=0.05, got {default}. "
        "The default should be 50 ms so drops surface fast instead of blocking 200 ms."
    )


def test_acquire_returns_none_within_80ms_when_pool_empty():
    """When the pool is empty, acquire() should bail in ~50 ms (allow 30 ms overhead)."""
    buf = _make_buffer_with_empty_pool()
    t0 = time.monotonic()
    result = buf.acquire()
    elapsed = time.monotonic() - t0
    assert result is None, "Empty pool should return None, not block indefinitely"
    assert elapsed < 0.08, (
        f"acquire() took {elapsed * 1000:.0f} ms — should be <80 ms (50 ms timeout + overhead). "
        "If this is >150 ms, the default timeout is still 200 ms."
    )
    assert buf._drop_count == 1, "Drop counter should increment on failed acquire"


def test_acquire_respects_explicit_timeout():
    """An explicit timeout argument should be respected."""
    buf = _make_buffer_with_empty_pool()
    t0 = time.monotonic()
    result = buf.acquire(timeout=0.02)
    elapsed = time.monotonic() - t0
    assert result is None
    # Should bail in ~20 ms (allow 30 ms overhead)
    assert elapsed < 0.05, (
        f"acquire(timeout=0.02) took {elapsed * 1000:.0f} ms — should be <50 ms"
    )
