"""
Standalone verification of the SHM in-flight accounting fix.

No torch/redis needed: we stub get_redis_client with an in-memory set and
RedisQueue with a queue.Queue. This proves:
  1. release() now clears the segment from the acquired set (so it is no
     longer treated as a permanent leak).
  2. prune_stale_segments() will NOT reclaim a segment that is still in the
     acquired set (in flight), even if its last_used is ancient.
  3. A genuinely stale segment (present in pool, not in acquired set, old
     timestamp) IS reclaimed.

Run: .venv/bin/python scripts/verify_shm_fix.py
"""
import sys, os, time, struct, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import queue as _queue

# --- Stubs -------------------------------------------------------------
class FakeRedisSet:
    """Minimal stand-in for the acquired-set key stored in Redis."""
    def __init__(self):
        self._s = set()
    def sadd(self, name):
        self._s.add(name)
    def srem(self, name):
        self._s.discard(name)
    def smembers(self):
        return set(self._s)

class FakeRedisClient:
    def __init__(self):
        self.acquired = FakeRedisSet()
    def sadd(self, key, name):
        if key == 'shm_acquired_pool':
            self.acquired.sadd(name)
    def srem(self, key, name):
        if key == 'shm_acquired_pool':
            self.acquired.srem(name)
    def smembers(self, key):
        if key == 'shm_acquired_pool':
            return self.acquired.smembers()
        return set()
    def delete(self, key):
        pass

_FAKE_REDIS = FakeRedisClient()

import app.utils.shared_frame_buffer as sfb_mod
def _fake_get_redis_client():
    return _FAKE_REDIS
sfb_mod.get_redis_client = _fake_get_redis_client

# RedisQueue stub: just needs put/get/qsize/clear/maxsize for the free pool.
class FakeRedisQueue:
    def __init__(self, name, maxsize=0):
        self.q = _queue.Queue(maxsize=maxsize)
    def put(self, item, block=True, timeout=None):
        self.q.put(item, block=block, timeout=timeout)
    def put_nowait(self, item):
        self.q.put_nowait(item)
    def get(self, block=True, timeout=None):
        return self.q.get(block=block, timeout=timeout)
    def get_nowait(self):
        return self.q.get_nowait()
    def qsize(self):
        return self.q.qsize()
    def clear(self):
        while not self.q.empty():
            try: self.q.get_nowait()
            except _queue.Empty: break
sfb_mod.RedisQueue = FakeRedisQueue

# Build a tiny frame buffer WITHOUT creating real /dev/shm segments.
# We monkeypatch SharedMemory so no OS SHM is allocated.
import numpy as np
class FakeSharedMemory:
    def __init__(self, name=None, create=False, size=0):
        self.name = name
        self.size = size
        self._buf = bytearray(size)
        # zero-init header: version=0, size=0, w=0,h=0,c=0, feed_hash=0, last_used=0
        self._buf[0:32] = struct.pack('<I iiii I d', 0, 0, 0, 0, 0, 0, 0.0)
    @property
    def buf(self):
        # Return a writable memoryview over the backing bytearray. Supports
        # slice get/set and struct.pack_into via the underlying buffer.
        return memoryview(self._buf)
    def close(self): pass
    def unlink(self): pass
sfb_mod.shared_memory.SharedMemory = FakeSharedMemory
# Force a tiny pool so we don't allocate thousands of bytearrays.
sfb_mod.shared_memory.SharedMemory  # noqa

FB = sfb_mod.SharedFrameBuffer
buf = FB(pool_size=4, max_frame_size=1024, owner=True)

print("acquired set after init:", _FAKE_REDIS.acquired._s)
assert _FAKE_REDIS.acquired._s == set(), "acquired set must start empty"

# --- Test 1: acquire then release clears the acquired set ----------
name = buf.acquire(timeout=1.0)
assert name is not None, "acquire failed"
assert name in _FAKE_REDIS.acquired._s, "acquire must add to acquired set"
# write a valid even-version frame
buf.write(name, b"\x01\x02\x03", feed_id="feedA")
buf.release(name)
assert name not in _FAKE_REDIS.acquired._s, "release MUST clear the acquired set"
print("TEST 1 OK: release() clears acquired set")

# --- Test 2: in-flight segment is NOT pruned even if ancient ----------
name2 = buf.acquire(timeout=1.0)
buf.write(name2, b"\x0a\x0b", feed_id="feedB")
# make its last_used ancient so prune WOULD reclaim it if not in-flight
raw = buf._segments[name2].buf
struct.pack_into('<d', raw, 24, time.time() - 99999.0)
# it is in acquired set (acquired via acquire above). prune must skip it.
buf.prune_stale_segments(timeout_seconds=30, odd_timeout=10.0)
assert name2 in _FAKE_REDIS.acquired._s, "in-flight segment must remain acquired"
# And it must NOT have been pushed back to the free pool while still acquired.
free_names = set()
while True:
    try: free_names.add(buf._free_pool.get_nowait())
    except _queue.Empty: break
# name2 should not be in the free pool because it's still logically in flight.
assert name2 not in free_names, "prune must NOT reclaim an in-flight segment"
print("TEST 2 OK: prune_stale_segments skips in-flight segments")

# --- Test 3: a genuinely stale segment (not acquired) IS reclaimed -----
name3 = "frame_buffer_stale"
# craft a stale segment not tracked by acquire
seg = FakeSharedMemory(name=name3, create=True, size=1024)
buf._segments[name3] = seg
# write an old even-version frame
seg.buf[0:32] = struct.pack('<I iiii I d', 2, 5, 0, 0, 0, 0, time.time() - 99999.0)
buf.prune_stale_segments(timeout_seconds=30, odd_timeout=10.0)
# name3 should now be reclaimed into the free pool and NOT in acquired set
reclaimed = set()
while True:
    try: reclaimed.add(buf._free_pool.get_nowait())
    except _queue.Empty: break
assert name3 in reclaimed, "genuinely stale segment must be reclaimed"
assert name3 not in _FAKE_REDIS.acquired._s
print("TEST 3 OK: genuinely stale segment is reclaimed")

# --- Test 4: get_stats orphan_count behaves ---------------------------
stats = buf.get_stats()
print("stats:", stats)
print("\nALL SHM FIX VERIFICATIONS PASSED")
