from __future__ import annotations
import logging
import numpy as np
from multiprocessing import shared_memory
from typing import Optional, Union, Tuple
import queue
import os
import time
from app.utils.distributed_queue import RedisQueue

logger = logging.getLogger('app.utils.shared_frame_buffer')

class SharedFrameBuffer:
    """
    Manages a pool of shared memory segments for high-frequency frame transmission.
    Supports resolution-agnostic reads and orphan/stale segment pruning.
    """
    # Header: [size(i4), width(i4), height(i4), channels(i4), last_used(f8)]
    # 4*4 + 8 = 24 bytes
    HEADER_SIZE = 24 
    
    def __init__(self, pool_size: int = 100, max_frame_size: int = 10 * 1024 * 1024, read_only: bool = False, owner: bool = False):
        self.pool_size = pool_size
        self.max_frame_size = max_frame_size
        self.read_only = read_only
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
                    try:
                        shm = shared_memory.SharedMemory(name=name)
                        self._segments[name] = shm
                    except FileNotFoundError:
                        # Segment name is in Redis but /dev/shm was cleared.
                        # Recreate the segment so the pool reference stays valid.
                        try:
                            shm = shared_memory.SharedMemory(name=name, create=True, size=max_frame_size)
                            self._segments[name] = shm
                            logger.debug(f'Recreated missing SHM segment: {name}')
                        except Exception as e:
                            logger.error(f'Failed to recreate SHM segment {name}: {e}')
                    except Exception:
                        pass

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

    def prune_stale_segments(self, timeout_seconds: float):
        """
        Returns abandoned segments (those not in the free pool and not recently accessed) 
        back to the free pool.
        """
        if self.read_only or self._free_pool is None:
            return

        now = time.time()
        stale_count = 0
        
        # We'll check all segments in our registry.
        for name, shm in self._segments.items():
            try:
                # Check if it's in the free pool by checking if we can acquire it? 
                # No, RedisQueue.get() is blocking/removes it.
                # We'll check the last_used timestamp in the header.
                
                buf = shm.buf
                import struct as _struct
                last_used = _struct.unpack_from('<d', buf, 16)[0]
                
                if now - last_used > timeout_seconds:
                    # This segment hasn't been touched for a while.
                    # It might be a leaked segment.
                    # We try to put it back in the free pool.
                    # If it was actually in use, this is a bug, but in a crash scenario, it's how we recover.
                    self._free_pool.put_nowait(name)
                    stale_count += 1
            except Exception as e:
                # If we can't even read the header, it's probably an orphan or corrupted
                logger.debug(f"Could not read header for {name}, might be stale: {e}")
                # We'll skip for now to avoid accidental unlink of active segments

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
        buf = shm.buf  # direct memoryview of SHM — writes go to shared memory
        
        # Write payload FIRST.
        # This ensures that by the time the reader sees size > 0, the data is already there.
        buf[self.HEADER_SIZE : self.HEADER_SIZE + size] = raw_bytes
        
        # Write header LAST.
        # Header layout: [size(i4), width(i4), height(i4), channels(i4), last_used(f8)]
        now = time.time()
        buf[0:24] = struct.pack('<iiiid', size, w, h, c, now)

    def read(self, name: Union[str, bytes]) -> Tuple[bytes, Tuple[int, int, int]]:
        """Returns (data_view, (w, h, c))."""
        if isinstance(name, bytes):
            try:
                name = name.decode('utf-8')
            except UnicodeDecodeError:
                # Not a valid segment name — treat as raw frame bytes fallback
                return name, (0, 0, 0)

        # Guard against empty or invalid segment names (e.g. from control signals)
        if not name or name == '/':
            raise ValueError(f'Invalid segment name: {repr(name)}')

        if name not in self._segments:
            try:
                shm = shared_memory.SharedMemory(name=name)
                self._segments[name] = shm
            except Exception:
                raise ValueError(f'Segment {name} not accessible.')
        
        shm = self._segments[name]
        buf = shm.buf
        
        # Read header using struct.unpack_from — matches write format
        import struct
        import time
        
        # Retry loop to handle race conditions where writer is currently updating the header
        # Increased retries and sleep for better stability
        for attempt in range(10):
            try:
                size, w, h, c = struct.unpack_from('<iiii', buf, 0)
                if size > 0 and size <= self.max_frame_size:
                    break
            except struct.error:
                pass
            
            if attempt < 9:
                time.sleep(0.005) # 5ms delay to let writer finish
        else:
            # All retries failed or size is still invalid
            logger.warning(f'Invalid size {size if "size" in locals() else "unknown"} detected in segment {name} after retries. Returning None.')
            return None, (0, 0, 0)
            
        # Update last_used timestamp to prevent pruning while being read
        # Note: This is a non-atomic write to the header, but it's acceptable for heartbeat
        try:
            buf[16:24] = struct.pack('<d', time.time())
        except Exception as e:
            logger.debug(f"Failed to update heartbeat for {name}: {e}")
            
        return bytes(buf[self.HEADER_SIZE : self.HEADER_SIZE + size]), (w, h, c)

    def release(self, name: str):
        if self._free_pool is None:
            return
        try:
            self._free_pool.put(name, block=False)
        except queue.Full:
            pass

    def cleanup(self):
        """Closes and unlinks all managed segments."""
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
    def force_cleanup(cls):
        """
        Emergency recovery: Unlinks ALL segments matching the frame_buffer_ pattern.
        Call this during startup if previous sessions crashed.
        """
        logger.info("Performing emergency SHM force cleanup...")
        try:
            shm_dir = '/dev/shm'
            if os.path.exists(shm_dir):
                for filename in os.listdir(shm_dir):
                    if filename.startswith('frame_buffer_'):
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

