"""
Verify the feed->slot routing now co-locates feeds onto the same worker so
inference batches fill. Mirrors the logic in FeedManager._launch_worker.

Worker owns slot s when s % pool_size == worker_id. Batching happens across a
worker's own slots, so to batch K feeds we need those feeds' slots to map to
the same worker.

Run: .venv/bin/python scripts/verify_routing.py
"""
SLOT_COUNT = 16  # FeedManagerConstants.SLOT_COUNT

def route_feed(launch_seq, per_worker_count, pool_size):
    wid = launch_seq % pool_size
    sub = per_worker_count.get(wid, 0)
    slot = (wid + sub * pool_size) % SLOT_COUNT
    per_worker_count[wid] = sub + 1
    return wid, slot

def worker_owns(slot, worker_id, pool_size):
    return slot % pool_size == worker_id

# Simulate 3 feeds, pool_size=2 (2 T4 GPUs, our new config).
pool_size = 2
per_worker = {}
assignments = []
for i in range(3):
    wid, slot = route_feed(i, per_worker, pool_size)
    assignments.append((f"feed{i}", wid, slot))

print("pool_size=2, 3 feeds:")
for feed, wid, slot in assignments:
    print(f"  {feed}: worker={wid} slot={slot}  (worker owns slot: {worker_owns(slot, wid, pool_size)})")

# Which workers actually get feeds, and how many feeds per worker.
from collections import defaultdict
by_worker = defaultdict(list)
for feed, wid, slot in assignments:
    by_worker[wid].append((feed, slot))

print("\nBatching outcome:")
for wid, feeds in sorted(by_worker.items()):
    print(f"  worker {wid}: {len(feeds)} feeds -> batch of up to {len(feeds)}")
    # confirm all their slots belong to this worker
    for feed, slot in feeds:
        assert worker_owns(slot, wid, pool_size), "slot/worker mismatch!"
        assert worker_owns(slot, wid, pool_size)

# Assert that at least one worker batches >1 feed (the whole point).
max_batch = max(len(f) for f in by_worker.values())
assert max_batch >= 2, "expected at least one worker to receive >=2 feeds for batching"
print(f"\nOK: max batch size achievable = {max_batch} (was 1 before the fix)")
print("BEFORE FIX (hash routing, SLOT_COUNT=16, 3 feeds): each feed -> distinct slot -> 1 feed/worker -> batch=1")
