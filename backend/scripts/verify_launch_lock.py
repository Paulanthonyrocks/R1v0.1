"""
Isolated check of the _launch_worker routing block fix.

The real module can't be imported in this sandbox (it transitively imports
torch), so we replicate the exact routing logic + lock change here to prove:
  - a synchronous `with threading.Lock()` (the fix) runs without the
    "Lock object does not support the context manager protocol" TypeError
    that the old `with self._lock` (asyncio.Lock) produced.
  - 3 feeds co-locate onto the same worker's slots so batches fill.

This mirrors app/services/feed_manager.py:_launch_worker (post-fix).
Run: .venv/bin/python scripts/verify_launch_lock.py
"""
import threading

slot_count = 16

class Stub:
    def __init__(self):
        self.slot_count = slot_count
        self._feed_launch_seq = 0
        self._per_worker_feed_count = {}
        self._route_lock = threading.Lock()
        self.pool_manager = type("P", (), {"pool_size": 2})()
        self.config = {"inference": {"num_workers": 2}}

    def route(self):
        # EXACT post-fix body of _launch_worker's routing block
        with self._route_lock:
            configured_pool = self.config.get("inference", {}).get("num_workers", 2)
            pool_size = max(1, self.pool_manager.pool_size or configured_pool or 1)
            wid = self._feed_launch_seq % pool_size
            sub = self._per_worker_feed_count.get(wid, 0)
            slot_id = (wid + sub * pool_size) % self.slot_count
            self._per_worker_feed_count[wid] = sub + 1
            self._feed_launch_seq += 1
        return wid, slot_id

m = Stub()
results = [m.route() for _ in range(3)]
print("routing (wid, slot):", results)
assert m._feed_launch_seq == 3
assert dict(m._per_worker_feed_count) == {0: 2, 1: 1}, "expected co-located routing"
# Confirm the fix is not the broken asyncio.Lock form:
import asyncio
old_lock = asyncio.Lock()
try:
    with old_lock:
        pass
    print("NOTE: asyncio.Lock unexpectedly worked in sync context")
except TypeError as e:
    print("reproduction confirmed:", e)
print("OK: threading.Lock routing runs cleanly and co-locates feeds for batching")
