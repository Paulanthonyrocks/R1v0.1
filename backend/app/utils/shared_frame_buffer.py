from __future__ import annotations
import logging
import numpy as np
from multiprocessing import shared_memory
from typing import Optional, Union, Tuple
import queue
import os
import time
import threading
from app.utils.distributed_queue import RedisQueue

logger = logging.getLogger('app.utils.shared_frame_buffer')

class SharedFrameBuffer:
    """
    Manages a pool of shared memory segments for high-frequency frame transmission.
    Supports resolution-agnostic reads and orphan/stale segment pruning.
    """
    # Header: [version(i4), size(i4), width(i4), height(i4), channels(i4), last_used(f8)]
    # 5*4 + 8 = 28 bytes
    HEADER_SIZE = 28 
    
    def __init__(self, pool_size: int = 100, max_frame_size: int = 10 * 1024 * 1024, read_only: bool = False, owner: bool = False, odd_timeout: float = 120.0):
        self.pool_size = pool_size
        self.max_frame_size = max_frame_size
        self.read_only = read_only
        self._owner = owner
        self._odd_timeout = odd_timeout
        self._lock = threading.Lock()
        self._segments: dict[str, shared_memory.SharedMemory] = {}
        
        self._free_pool = None
        
        if not read_only:
            # Use RedisQueue for the free pool to ensure distributed access without MP Manager
            self._free_pool = RedisQueue('shm_free_pool', maxsize=pool_size)
            
            if owner:
                # Owner (main process) always creates the pool from scratch.
                # This handles crash restarts where Redis has stale entries
                # but /dev/shm segments are gone.
                self._free_pool.clear()
                self.prune_orphans()
                
                for i in range(pool_size):
                    name = f'frame_buffer_{i}'
                    try:
                        shm = shared_memory.SharedMemory(name=name, create=True, size=max_frame_size)
                        self._segments[name] = shm
                        self._free_pool.put(name)
                    except FileExistsError:
                        shm = shared_memory.SharedMemory(name=name)
                        self._segments[name] = shm
                        self._free_pool.put(name)
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

        now = time.time()
        stale_count = 0
        
        # We'll check all segments in our registry.
        for name, shm in self._segments.items():
            try:
                buf = shm.buf
                import struct as _struct
                # Read version and last_used
                version, last_used = _struct.unpack_from('<i', buf, 0)[0], _struct.unpack_from('<d', buf, 20)[0]
                
                # Reclaim if:
                # 1. Version is EVEN and it's just old.
                # 2. Version is ODD but it's been stuck for a long time (crashed writer).
                is_stale_even = (version % 2 == 0 and (now - last_used > timeout_seconds))
                is_stale_odd = (version % 2 != 0 and (now - last_used > odd_timeout))
                
                if is_stale_even or is_stale_odd:
                    self._free_pool.put_nowait(name)
                    stale_count += 1
            except Exception as e:
                logger.debug(f"Could not read header for {name}, might be stale: {e}")

        if stale_count > 0:
            logger.info(f"Recovered {stale_count} stale SHM segments.")

    def acquire(self, timeout: float = 0.05) -> Optional[str]:
        try:
            return self._free_pool.get(timeout=timeout)
        except queue.Empty:
            return None

    def write(self, name: str, data: Union[bytes, np.ndarray]):
        """Write data and dimensions into the segment."""
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
        
        shm = self._segments[name]
        buf = shm.buf
        
        # 1. Signal start of write by setting version to ODD
        try:
            current_version = struct.unpack_from('<i', buf, 0)[0]
        except Exception:
            current_version = 0
        
        # Ensure version is even before starting
        if current_version % 2 != 0:
            current_version += 1
            
        # Version becomes odd -> Writer is active
        buf[0:4] = struct.pack('<i', current_version + 1)
        
        # 2. Write payload
        buf[self.HEADER_SIZE : self.HEADER_SIZE + size] = raw_bytes
        
        # 3. Finalize header: Write everything including EVEN version
        now = time.time()
        # Layout: [version(i4), size(i4), width(i4), height(i4), channels(i4), last_used(f8)]
        # Write metadata first while version is still ODD
        buf[4:28] = struct.pack('<iiiid', size, w, h, c, now)
        # Finally, update version to EVEN to signal completion
        buf[0:4] = struct.pack('<i', current_version + 2)

    def read(self, name: Union[str, bytes]) -> Optional[Tuple[bytes, Tuple[int, int, int]]]:
        """Returns (data_view, (w, h, c)) or None if a stable frame could not be read."""
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
        for attempt in range(40):
            try:
                # Header: [version(i4), size(i4), width(i4), height(i4), channels(i4), last_used(f8)]
                version, size, w, h, c = struct.unpack_from('<iiiii', buf, 0)

                # If version is odd, writer is currently updating the segment.
                if version % 2 != 0:
                    if attempt < 39:
                        time.sleep(0.001 * (1 + attempt // 10))
                        continue
                    else:
                        break

                # Valid frame found (even version, size > 0)
                if size > 0 and size <= self.max_frame_size:
                    # Read the data
                    data = bytes(buf[self.HEADER_SIZE : self.HEADER_SIZE + size])

                    # Re-verify version to ensure we didn't read while it was being overwritten
                    final_version = struct.unpack_from('<i', buf, 0)[0]
                    if final_version == version:
                        # Successfully read a stable frame. Update heartbeat and return.
                        try:
                            buf[20:28] = struct.pack('<d', time.time())
                        except Exception:
                            pass
                        return data, (w, h, c)
                    else:
                        if attempt < 39:
                            time.sleep(0.001 * (1 + attempt // 10))
                            continue
                        else:
                            break
                else:
                    if attempt < 39:
                        time.sleep(0.001 * (1 + attempt // 10))
                        continue
                    else:
                        break
            except (struct.error, Exception):
                if attempt < 39:
                    time.sleep(0.001 * (1 + attempt // 10))
                    continue
                else:
                    break

        logger.warning(f'Unable to read stable frame from segment {name} after 40 retries.')
        return None
    def release(self, name: str):
        if self._free_pool is None:
            return
        try:
            self._free_pool.put(name, block=False)
        except queue.Full:
            pass

    def cleanup(self):
        """Closes and unlinks all managed segments."""
        with self._lock:
            # If owner, also clear the distributed free pool
            if self._owner and self._free_pool is not None:
                try:
                    self._free_pool.clear()
                    logger.debug("Cleared SHM free pool RedisQueue.")
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

