from __future__ import annotations
import logging
import numpy as np
from multiprocessing import shared_memory
from typing import Optional, Union, Tuple
import queue
import os
import time
import threading
import zlib
import struct
from app.utils.distributed_queue import RedisQueue
from app.utils.redis_client import get_redis_client

logger = logging.getLogger('app.utils.shared_frame_buffer')

class SharedFrameBuffer:
    """
    Manages a pool of shared memory segments for high-frequency frame transmission.
    Supports resolution-agnostic reads and orphan/stale segment pruning.
    """
    # Header: [version(i4), size(i4), width(i4), height(i4), channels(i4), feed_hash(i4), last_used(f8)]
    # 6*4 + 8 = 32 bytes
    HEADER_SIZE = 32 
    
    def __init__(self, pool_size: int = 1000, max_frame_size: int = 10 * 1024 * 1024, read_only: bool = False, owner: bool = False, odd_timeout: float = 10.0):
        # Pool size is sourced exclusively from config (see
        # configs/config.yaml -> performance.shm_pool_size). The previous
        # behaviour auto-bumped a literal-100 default to 1000, which made the
        # value inconvenient to override at any other size. Choose 1000 here
        # because, per SHM_POOL_FIX.md, 3 feeds @ 15 FPS need at least 1000
        # segments to keep ingestion ahead of the result-processor's recycling
        # window while leaving headroom for reader bursts.
        self.pool_size = int(pool_size) if pool_size is not None else 1000
        self.max_frame_size = max_frame_size
        self.read_only = read_only
        self._owner = owner
        self._odd_timeout = odd_timeout
        self._lock = threading.Lock()
        self._segments: dict[str, shared_memory.SharedMemory] = {}

        self._free_pool = None
        self._acquired_set_key = 'shm_acquired_pool'
        # Free-name set (audit #1B). Mirrors the free pool (a Redis LIST) with a
        # Redis SET so membership is O(1) and idempotent. prune_stale_segments
        # and release() SADD here; acquire() SREM here. The LIST is the actual
        # work queue; the SET exists purely to make "is this name already free?"
        # a fast, deduplicating check. Without it, an idle-but-released segment
        # (absent from the acquired set by definition) gets put_nowait'd into the
        # free pool again on every prune tick, forever, producing phantom
        # duplicates that let two producers acquire the same segment
        # concurrently (silent cross-feed corruption). Single writer = owner.
        self._free_set_key = 'shm_free_set'

        # Buffer tracking for diagnostics
        self._acquired_count = 0
        self._release_count = 0
        self._drop_count = 0
        self._last_acquire_time = 0.0
        self._last_release_time = 0.0
        # Track the names of segments currently held (acquired but not yet
        # released). ``release()`` consults this set so double-release is a
        # fast no-op and so the orphan count can be reported accurately.
        self._in_flight: set[str] = set()
        # Defense-in-depth (audit #1B): segment header version snapshotted at
        # acquire() time, keyed by name. release() compares the live header
        # version against this snapshot; a mismatch means the segment was
        # recycled to another producer and this caller must not free it.
        self._in_flight_version: dict[str, Optional[int]] = {}
        
        if not read_only:
            # Use RedisQueue for the free pool to ensure distributed access without MP Manager
            self._free_pool = RedisQueue('shm_free_pool', maxsize=pool_size)
            
            if owner:
                # Owner (main process) always creates the pool from scratch.
                # This handles crash restarts where Redis has stale entries
                # but /dev/shm segments are gone.
                self._free_pool.clear()
                get_redis_client().delete(self._acquired_set_key)
                get_redis_client().delete(self._free_set_key)
                self.prune_orphans()
                
                for i in range(pool_size):
                    name = f'frame_buffer_{i}'
                    try:
                        shm = shared_memory.SharedMemory(name=name, create=True, size=max_frame_size)
                        self._segments[name] = shm
                        self._free_pool.put(name)
                        get_redis_client().sadd(self._free_set_key, name)
                    except FileExistsError:
                        shm = shared_memory.SharedMemory(name=name)
                        self._segments[name] = shm
                        self._free_pool.put(name)
                        get_redis_client().sadd(self._free_set_key, name)
                    except Exception as e:
                        logger.error(f'Failed to allocate SHM segment {name}: {e}')
            else:
                # Worker (ingestion/inference) attaches to existing pool.
                # Never clears the Redis queue — avoids the race condition
                # where multiple workers each clear and re-populate.
                logger.info(f'SharedFrameBuffer: Attaching to existing free pool ({self._free_pool.qsize()} segments).')
                
                # Register SHM segment handles for read/write.
                # If a segment name is in the pool but /dev/shm is empty (stale),
                # create it so the free pool names are always valid.
                for i in range(pool_size):
                    name = f'frame_buffer_{i}'
                    shm = None
                    try:
                        shm = shared_memory.SharedMemory(name=name)
                    except FileNotFoundError:
                        # Segment name is in Redis but /dev/shm was cleared.
                        # Recreate the segment so the pool reference stays valid.
                        try:
                            shm = shared_memory.SharedMemory(name=name, create=True, size=max_frame_size)
                            logger.debug(f'Recreated missing SHM segment: {name}')
                        except FileExistsError:
                            # Recreated by another worker in the meantime
                            shm = shared_memory.SharedMemory(name=name)
                        except Exception as e:
                            logger.error(f'Failed to recreate SHM segment {name}: {e}')
                    except Exception as e:
                        logger.error(f'Unexpected error attaching to SHM segment {name}: {e}')
                    
                    if shm is not None:
                        self._segments[name] = shm

        logger.info(f'SharedFrameBuffer initialized with {len(self._segments)} segments. Header size: {self.HEADER_SIZE} bytes.')

    def prune_orphans(self):
        """Removes leaked SHM segments from /dev/shm that aren't in the current pool."""
        try:
            shm_dir = '/dev/shm'
            if os.path.exists(shm_dir):
                for filename in os.listdir(shm_dir):
                    if filename.startswith('frame_buffer_') and filename not in self._segments:
                        try:
                            temp_shm = shared_memory.SharedMemory(name=filename)
                            temp_shm.close()
                            temp_shm.unlink()
                            logger.debug(f'Pruned orphan SHM segment: {filename}')
                        except Exception:
                            pass
        except Exception as e:
            logger.warning(f'Orphan pruning failed: {e}')

    def prune_stale_segments(self, timeout_seconds: float, odd_timeout: float = None):
        """
        Returns abandoned segments (those not in the free pool and not recently accessed) 
        back to the free pool.
        """
        if self.read_only or self._free_pool is None:
            return

        if odd_timeout is None:
            odd_timeout = self._odd_timeout

        now = time.monotonic()
        stale_count = 0
        skipped_uninitialised = 0
        skipped_in_flight = 0

        # Segments still logically in flight (acquired but not yet released)
        # must NOT be reclaimed. The acquired set is now kept accurate by
        # release() (it srems the name), so any name still present here is a
        # segment genuinely pending in the pipeline. Reclaiming it would let
        # ingestion recycle it under the result reader, producing feed_hash
        # mismatches and dropped frames (the previous ~14% failure mode).
        # On a crash the acquired set is the source of truth for what is
        # still referenced; we only prune what is NOT in it.
        try:
            acquired_set = get_redis_client().smembers(self._acquired_set_key) or set()
        except Exception:
            acquired_set = set()

        # Zero-Corruption Protocol note: SHM segments created via
        # ``shared_memory.SharedMemory(create=True, ...)`` are zero-initialised,
        # so a freshly-allocated, never-written segment has ``version == 0`` and
        # ``last_used == 0`` (the Unix epoch). Subtracting ``now - 0.0`` yields
        # ~1.7e9 seconds, which trivially exceeds any sane timeout and would
        # otherwise cause every free-pool segment to be "pruned" and re-added
        # to the pool on every tick. Treat epoch timestamps as "uninitialised"
        # and skip both the counter bump and the log line.
        UNINITIALISED_EPOCH = 1.0  # anything < 1.0s after epoch = never written

        # We'll check all segments in our registry.
        for name, shm in self._segments.items():
            try:
                buf = shm.buf
                import struct as _struct
                # Read version and last_used (offset 24)
                version, last_used = _struct.unpack_from('<I', buf, 0)[0], _struct.unpack_from('<d', buf, 24)[0]

                # Skip segments that have never been written to. They are
                # not actually "stale" - they are simply sitting in the
                # free pool waiting for their first writer.
                if last_used < UNINITIALISED_EPOCH:
                    skipped_uninitialised += 1
                    continue

                # Skip segments still logically in flight. Compare both str
                # and bytes forms because the Redis set may hold either.
                if name in acquired_set or name.encode() in acquired_set:
                    skipped_in_flight += 1
                    continue

                # Reclaim if:
                # 1. Version is EVEN and it's just old.
                # 2. Version is ODD but it's been stuck for a long time (crashed writer).
                is_stale_even = (version % 2 == 0 and (now - last_used > timeout_seconds))
                is_stale_odd = (version % 2 != 0 and (now - last_used > odd_timeout))

                if is_stale_even or is_stale_odd:
                    # Demoted from INFO -> DEBUG: with a 100-segment pool and
                    # no active feeds, every segment would otherwise log here
                    # every prune tick (every 30s by default). The summary
                    # line below preserves visibility when something is
                    # actually being recovered.
                    logger.debug(f"[SHM-LIFECYCLE] PID {os.getpid()} PRUNE {name} (stale, version {version}, age {now - last_used:.2f}s)")
                    # Audit #1B: idempotent re-free. The LIST is the work queue;
                    # _free_set (a Redis SET) mirrors it for O(1) membership.
                    # A legit release() SADDs the name to _free_set before its
                    # put(); prune must not enqueue a name that is already free.
                    # The srem-then-sadd trick: if srem removed it, it was
                    # already free (a racing release already reclaimed it) so we
                    # skip the put_nowait -- no phantom duplicate. We re-SADD so
                    # the SET stays accurate for the next caller. Order matters:
                    # decide membership BEFORE touching the LIST.
                    try:
                        r = get_redis_client()
                        was_free = r.srem(self._free_set_key, name)
                        r.sadd(self._free_set_key, name)
                        if not was_free:
                            self._free_pool.put_nowait(name)
                    except queue.Full:
                        # Pool is full (segment already returned twice) - safe to drop
                        logger.debug(f"SHM free pool full, dropping duplicate release of {name}")
                    stale_count += 1
            except Exception as e:
                logger.debug(f"Could not read header for {name}, might be stale: {e}")

        if stale_count > 0:
            logger.info(f"Recovered {stale_count} stale SHM segments (skipped {skipped_uninitialised} uninitialised, {skipped_in_flight} in-flight).")

    def _read_version(self, name: str) -> Optional[int]:
        """Best-effort read of a segment's current header version.

        Returns None if the segment is unknown or its header can't be read.
        Used by acquire()/release() for the version-snapshot defense-in-depth
        (audit #1B): if the live version differs from the snapshot taken at
        acquire time, the segment was recycled to another producer and the
        caller must not release it.
        """
        shm = self._segments.get(name)
        if shm is None:
            return None
        buf = shm.buf
        if buf is None:
            return None
        try:
            return struct.unpack_from('<I', buf, 0)[0]
        except Exception:
            return None

    def acquire(self, timeout: float = 0.2) -> Optional[str]:
        try:
            name = self._free_pool.get(timeout=timeout)
            # Audit #1B: this name is leaving the free state -- remove it from
            # the free-set mirror so prune/release membership checks stay accurate.
            try:
                get_redis_client().srem(self._free_set_key, name)
            except Exception:
                pass
            get_redis_client().sadd(self._acquired_set_key, name)
            self._acquired_count += 1
            self._last_acquire_time = time.monotonic()
            with self._lock:
                self._in_flight.add(name)
                # Defense-in-depth (audit #1B): snapshot the segment's current
                # header version at acquire time. A producer that recycles this
                # segment will bump the version; if it changed by release time,
                # release() will no-op rather than freeing a segment it no
                # longer legitimately owns (the cross-feed hazard).
                self._in_flight_version[name] = self._read_version(name)
            return name
        except queue.Empty:
            self._drop_count += 1
            logger.warning(f"SHM free pool empty – frame will be dropped (total_drops={self._drop_count})")
            return None

    def write(self, name: str, data: Union[bytes, np.ndarray], feed_id: str = "unknown"):
        """Write data, dimensions, and feed identity into the segment."""
        import struct
        
        if name not in self._segments:
            raise ValueError(f'Segment {name} not found.')
        
        if isinstance(data, np.ndarray):
            h, w, c = data.shape
            raw_bytes = data.tobytes()
        else:
            h, w, c = 0, 0, 0
            raw_bytes = data
        
        size = len(raw_bytes)
        if size > self.max_frame_size - self.HEADER_SIZE:
            raise ValueError(f'Data size {size} exceeds buffer limit.')
        
        # Identity hash to prevent juggling
        feed_hash = zlib.adler32(feed_id.encode())
        
        shm = self._segments[name]
        buf = shm.buf
        
        # 1. Signal start of write by setting version to ODD
        try:
            current_version = struct.unpack_from('<I', buf, 0)[0]
        except Exception:
            current_version = 0
        
        # Ensure version is even before starting
        if current_version % 2 != 0:
            current_version += 1
            
        # Version becomes odd -> Writer is active
        buf[0:4] = struct.pack('<I', current_version + 1)
        
        # 2. Write payload
        buf[self.HEADER_SIZE : self.HEADER_SIZE + size] = raw_bytes
        
        # 3. Finalize header: Write everything including EVEN version
        now = time.monotonic()
        # Layout: [version(i4), size(i4), width(i4), height(i4), channels(i4), feed_hash(i4), last_used(f8)]
        buf[4:24] = struct.pack('<iiiiI', size, w, h, c, feed_hash)
        buf[24:32] = struct.pack('<d', now)
        # Finally, update version to EVEN to signal completion
        buf[0:4] = struct.pack('<I', current_version + 2)

    def read(self, name: Union[str, bytes], expected_feed_id: Optional[str] = None) -> Optional[Tuple[bytes, Tuple[int, int, int]]]:
        """Returns (data_view, (w, h, c)) or None if a stable frame could not be read or feed ID mismatch."""
        if isinstance(name, bytes):
            try:
                name = name.decode('utf-8')
            except UnicodeDecodeError:
                raise ValueError(f'Segment name must be UTF-8 encoded string, got raw bytes: {name!r}')

        # Guard against empty or invalid segment names (e.g. from control signals)
        if not name or name == '/':
            raise ValueError(f'Invalid segment name: {repr(name)}')

        if name not in self._segments:
            with self._lock:
                if name not in self._segments:
                    try:
                        shm = shared_memory.SharedMemory(name=name)
                        self._segments[name] = shm
                    except Exception:
                        raise ValueError(f'Segment {name} not accessible.')

        shm = self._segments[name]
        buf = shm.buf

        import struct
        import time

        # Retry loop to handle race conditions and version mismatches
        # Reduced from 40 to 12 retries to prevent SHM pool exhaustion
        # Total max wait: ~24ms with exponential backoff
        for attempt in range(12):
            try:
                # Header: [version(I4), size(i4), width(i4), height(i4), channels(i4), feed_hash(I4), last_used(f8)]
                version, size, w, h, c, feed_hash = struct.unpack_from('<I iiii I', buf, 0)

                # If version is odd, writer is currently updating the segment.
                if version % 2 != 0:
                    if attempt < 11:
                        # Faster backoff: max 3ms total wait
                        time.sleep(0.0005 * (1 + attempt // 4))
                        continue
                    else:
                        break

                # Feed Identity Verification
                if expected_feed_id is not None:
                    actual_hash = feed_hash
                    expected_hash = zlib.adler32(expected_feed_id.encode())
                    if actual_hash != expected_hash:
                        # Segment has been recycled and now belongs to another feed.
                        # Drop this frame and return None.
                        logger.debug(f"SHM segment {name} feed mismatch! Expected {expected_feed_id} (hash {expected_hash}), found hash {actual_hash}. Frame is stale/recycled.")
                        return None

                # Valid frame found (even version, size > 0)
                if size > 0 and size <= self.max_frame_size:
                    # Read the data
                    data = bytes(buf[self.HEADER_SIZE : self.HEADER_SIZE + size])

                    # Re-verify version to ensure we didn't read while it was being overwritten
                    final_version = struct.unpack_from('<I', buf, 0)[0]
                    if final_version == version:
                        # Successfully read a stable frame. Update heartbeat and return.
                        try:
                            buf[24:32] = struct.pack('<d', time.monotonic())
                        except Exception:
                            pass
                        return data, (w, h, c)
                    else:
                        if attempt < 11:
                            time.sleep(0.0005 * (1 + attempt // 4))
                            continue
                        else:
                            break
                else:
                    if attempt < 11:
                        time.sleep(0.0005 * (1 + attempt // 4))
                        continue
                    else:
                        break
            except (struct.error, Exception):
                if attempt < 11:
                    time.sleep(0.0005 * (1 + attempt // 4))
                    continue
                else:
                    break

        logger.warning(f'Unable to read stable frame from segment {name} after 12 retries.')
        return None
    def release(self, name: str, expected_version: Optional[int] = None):
        """Return a segment to the free pool.

        Args:
            name: segment name.
            expected_version: optional header version captured at acquire time.
                If the live header version differs, the segment was recycled to
                another producer (the cross-feed hazard) and we NO-OP instead of
                freeing a segment this caller no longer legitimately owns
                (audit #1B defense-in-depth). When omitted, the snapshot taken
                in acquire() is consulted; a caller that never acquired (e.g.
                the old inference-worker release sites, now removed) simply
                has no snapshot and the check is skipped.
        """
        if self._free_pool is None:
            return
        # Defense-in-depth (audit #1B): if the segment's header version moved
        # on since we acquired it, a producer already recycled it. Freeing it
        # now would strip the *new* owner's acquired-set claim and push a
        # phantom duplicate into the free pool. Refuse.
        ev = expected_version if expected_version is not None else self._in_flight_version.pop(name, None)
        if ev is not None:
            live = self._read_version(name)
            if live is not None and live != ev:
                logger.debug(
                    f"[SHM-LIFECYCLE] PID {os.getpid()} RELEASE skipped {name}: "
                    f"version moved {ev} -> {live} (recycled to another producer)."
                )
                return
        # Already-free guard (audit #1B): the _free_set mirror is the
        # distributed-safe source of truth for "currently free". A legitimate
        # single release removed `name` from _free_set at acquire() time, so a
        # re-release (double release, or a redundant call after a read-path
        # release) finds it already present and is skipped -- no phantom
        # duplicate in the free pool. This replaces the old process-local
        # _in_flight check, which could not catch cross-process re-releases and
        # (in the previous code) silently fell through to put() anyway.
        try:
            if get_redis_client().sismember(self._free_set_key, name):
                logger.debug(
                    f"[SHM-LIFECYCLE] PID {os.getpid()} RELEASE skipped {name}: "
                    f"already free (double-release guard)."
                )
                return
        except Exception:
            pass
        with self._lock:
            self._in_flight.discard(name)
        try:
            # Return to free pool - don't let tracking failures cause exhaustion
            logger.debug(f"[SHM-LIFECYCLE] PID {os.getpid()} RELEASE {name}")
            # CRITICAL FIX (ordering): clear distributed ownership BEFORE
            # making the segment available in the free pool. The reclaimer
            # (prune_stale_segments) already uses this exact order (srem then
            # put). Previously release() did put() THEN srem(), leaving a window
            # where the segment was acquirable from the free pool but its
            # ownership entry still referenced the *releasing* worker. A
            # concurrent acquire() would sadd() the name, then this srem() would
            # wipe that new owner's claim -- leaving the live segment invisible
            # to the reclaim guard and therefore reclaimable mid-flight (the
            # "~14% read-failure" class of bug: a recycled segment handed to two
            # readers at once). Clearing ownership first keeps the segment in a
            # brief "neither owned nor free" state (not reclaimable, not
            # acquirable) until the put() below makes it available with correct
            # accounting.
            try:
                get_redis_client().srem(self._acquired_set_key, name)
            except Exception:
                pass
            # Audit #1B: mark free in the SET mirror BEFORE enqueueing, so a
            # concurrent prune sees membership and won't double-enqueue.
            try:
                get_redis_client().sadd(self._free_set_key, name)
            except Exception:
                pass
            try:
                self._free_pool.put(name, block=False)
            except queue.Full:
                # Pool is full (segment already returned twice) - safe to drop
                logger.debug(f"SHM free pool full, dropping duplicate release of {name}")
            self._release_count += 1
            self._last_release_time = time.monotonic()
        except Exception as e:
            logger.error(f"Error releasing {name}: {e}")

    def available_count(self) -> int:
        """Public accessor for the number of free SHM segments.

        Replaces callers reaching into the private ``_free_pool`` attribute
        (e.g. the ingestion worker). Lets the internal queue be renamed or
        swapped without breaking consumers. Returns 0 for read-only buffers.
        """
        return self._free_pool.qsize() if self._free_pool else 0

    def get_stats(self) -> dict:
        """Return buffer statistics for diagnostics."""
        with self._lock:
            in_flight = len(self._in_flight)
        # Cross-process counts: every acquire/release calls srem/sadd on the
        # shared Redis SETs, so the SET sizes are the authoritative GLOBAL
        # state across all worker processes. The instance counters
        # (``_acquired_count``, ``_release_count``) above are process-local
        # (the feed_manager process doesn't see worker acquire/release) and
        # stay at 0 in the SHM-STATS log -- misleading. Read the SET sizes
        # instead so a real orphan leak across worker processes is visible.
        # A Redis call is cheap and SHM-STATS logs every 30s, well below the
        # rate at which this dominates.
        global_acquired = 0
        global_free = 0
        try:
            r = get_redis_client()
            global_acquired = int(r.scard(self._acquired_set_key) or 0)
            global_free = int(r.scard(self._free_set_key) or 0)
        except Exception:
            pass
        return {
            'pool_size': self.pool_size,
            'acquired_count': self._acquired_count,
            'release_count': self._release_count,
            'drop_count': self._drop_count,
            'in_flight': in_flight,
            # Acquired minus Released is the orphan count. The free pool's
            # ``qsize`` can lie if segments were leaked by a crash (Redis
            # still holds the name but no /dev/shm backing exists), so this
            # delta is the trustworthy indicator.
            'orphan_count': max(0, self._acquired_count - self._release_count - in_flight),
            'free_pool_size': self._free_pool.qsize() if self._free_pool else 0,
            # Cross-process counters (Redis SET sizes) -- see above. These
            # are what the SHM-STATS log should report: worker acquire/release
            # is invisible to the feed_manager's process-local counters.
            'global_acquired': global_acquired,
            'global_free': global_free,
            'last_acquire_ago': time.monotonic() - self._last_acquire_time if self._last_acquire_time > 0 else None,
            'last_release_ago': time.monotonic() - self._last_release_time if self._last_release_time > 0 else None,
        }

    def cleanup(self):
        """Closes and unlinks all managed segments."""
        with self._lock:
            # If owner, also clear the distributed free pool
            if self._owner and self._free_pool is not None:
                try:
                    self._free_pool.clear()
                    get_redis_client().delete(self._acquired_set_key)
                    get_redis_client().delete(self._free_set_key)
                    logger.debug("Cleared SHM free pool, acquired set, and free set.")
                except Exception as e:
                    logger.error(f"Failed to clear SHM free pool: {e}")

            for name, shm in self._segments.items():
                # 1. Try to close the handle.
                try:
                    shm.close()
                except BufferError:
                    logger.warning(f"BufferError closing SHM segment {name}: active pointers exist. Segment will be closed when views are released.")
                except Exception as e:
                    logger.debug(f"Could not close SHM segment {name}: {e}")
                
                # 2. Always try to unlink. This removes the segment from /dev/shm.
                try:
                    shm.unlink()
                except BufferError:
                    logger.warning(f"BufferError unlinking SHM segment {name}: active pointers exist.")
                except Exception as e:
                    logger.debug(f"Could not unlink SHM segment {name}: {e}")
            
            self._segments.clear()

    @classmethod
    def force_cleanup(cls, prefix: str = 'frame_buffer'):
        """
        Emergency recovery: Unlinks ALL segments matching the pattern {prefix}_*.
        Call this during startup if previous sessions crashed.
        WARNING: This is an aggressive operation and will unlink segments even if they are in use.
        """
        logger.info(f"Performing emergency SHM force cleanup for prefix {prefix}...")
        try:
            shm_dir = '/dev/shm'
            if os.path.exists(shm_dir):
                for filename in os.listdir(shm_dir):
                    if filename.startswith(f'{prefix}_'):
                        try:
                            temp_shm = shared_memory.SharedMemory(name=filename)
                            try:
                                temp_shm.close()
                            except BufferError:
                                pass
                            temp_shm.unlink()
                            logger.debug(f"Force pruned leaked segment: {filename}")
                        except Exception:
                            pass
        except Exception as e:
            logger.error(f"Emergency SHM cleanup failed: {e}")
