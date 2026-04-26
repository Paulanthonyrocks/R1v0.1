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
    
    def __init__(self, pool_size: int = 100, max_frame_size: int = 10 * 1024 * 1024, read_only: bool = False):
        self.pool_size = pool_size
        self.max_frame_size = max_frame_size
        self.read_only = read_only
        self._segments: dict[str, shared_memory.SharedMemory] = {}
        
        self._free_pool = None
        
        if not read_only:
            # Use RedisQueue for the free pool to ensure distributed access without MP Manager
            self._free_pool = RedisQueue('shm_free_pool', maxsize=pool_size)
            
            # Check if pool already exists (e.g., from main process or prior worker)
            existing_count = self._free_pool.qsize()
            
            if existing_count > 0:
                # Pool already populated — just attach. Don't clear or re-populate.
                # This prevents the race condition where multiple ingestion workers
                # each clear and re-populate the same Redis queue.
                logger.info(f'SharedFrameBuffer: Attaching to existing free pool ({existing_count} segments).')
                # Still need to register SHM segment handles for read/write
                for i in range(pool_size):
                    name = f'frame_buffer_{i}'
                    try:
                        shm = shared_memory.SharedMemory(name=name)
                        self._segments[name] = shm
                    except Exception:
                        # Segment doesn't exist yet — skip, it'll be created by the owner
                        pass
            else:
                # First initializer — create the pool from scratch
                self._free_pool.clear()
                # 1. Prune orphans from previous crashes
                self.prune_orphans()
                # 2. Pre-allocate the pool
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
                # Read metadata
                # Header: [size(i4), width(i4), height(i4), channels(i4), last_used(f8)]
                header_data = np.frombuffer(buf[:self.HEADER_SIZE], dtype=[
                    ('size', 'i4'), 
                    ('width', 'i4'), 
                    ('height', 'i4'), 
                    ('channels', 'i4'),
                    ('last_used', 'f8')
                ])
                last_used = header_data[0]['last_used']
                
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

    def acquire(self, timeout: float = 1.0) -> Optional[str]:
        try:
            return self._free_pool.get(timeout=timeout)
        except queue.Empty:
            return None

    def write(self, name: str, data: Union[bytes, np.ndarray]):
        """Write data and dimensions into the segment."""
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
        
        # Write Header: [size(i4), width(i4), height(i4), channels(i4), last_used(f8)]
        now = time.time()
        header = np.array([size, w, h, c, now], dtype=[
            ('size', 'i4'), 
            ('width', 'i4'), 
            ('height', 'i4'), 
            ('channels', 'i4'),
            ('last_used', 'f8')
        ])
        
        # Cast buffer to unsigned bytes to avoid memoryview structure mismatch
        buf_bytes = buf.cast('B')
        # Must cast source to memoryview('B') to match lvalue format
        # Using memoryview() on bytes/bytearray ensures format compatibility
        header_mv = memoryview(header.tobytes())
        buf_bytes[:self.HEADER_SIZE] = header_mv
        
        data_mv = memoryview(raw_bytes) if isinstance(raw_bytes, bytes) else raw_bytes
        if data_mv.format != 'B':
            data_mv = data_mv.cast('B')
        buf_bytes[self.HEADER_SIZE : self.HEADER_SIZE + size] = data_mv[:size]

    def read(self, name: Union[str, bytes]) -> Tuple[memoryview, Tuple[int, int, int]]:
        """Returns (data_view, (w, h, c))."""
        if isinstance(name, bytes):
            try:
                name = name.decode('utf-8')
            except UnicodeDecodeError:
                return name, (0, 0, 0)

        if name not in self._segments:
            try:
                shm = shared_memory.SharedMemory(name=name)
                self._segments[name] = shm
            except Exception:
                raise ValueError(f'Segment {name} not accessible.')
        
        shm = self._segments[name]
        buf = shm.buf
        
        # Read Header
        header_data = np.frombuffer(buf[:self.HEADER_SIZE], dtype=[
            ('size', 'i4'), 
            ('width', 'i4'), 
            ('height', 'i4'), 
            ('channels', 'i4'),
            ('last_used', 'f8')
        ])
        
        size = header_data[0]['size']
        w = header_data[0]['width']
        h = header_data[0]['height']
        c = header_data[0]['channels']
        
        if size <= 0 or size > self.max_frame_size:
            raise ValueError(f'Invalid size {size} in segment {name}')
            
        # Update last_used timestamp to prevent pruning while being read
        # Note: This is a non-atomic write to the header, but it's acceptable for heartbeat
        try:
            # Re-use the buffer to write just the timestamp (offset 16)
            timestamp_buf = np.array([time.time()], dtype='f8').tobytes()
            buf_bytes = buf.cast('B')
            buf_bytes[16:24] = memoryview(timestamp_buf)
        except Exception as e:
            logger.debug(f"Failed to update heartbeat for {name}: {e}")
            
        return buf[self.HEADER_SIZE : self.HEADER_SIZE + size], (w, h, c)

    def release(self, name: str):
        try:
            self._free_pool.put(name, block=False)
        except queue.Full:
            pass

    def cleanup(self):
        for name, shm in self._segments.items():
            try:
                shm.close()
                shm.unlink()
            except Exception:
                pass
        self._segments.clear()
