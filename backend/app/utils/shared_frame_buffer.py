from __future__ import annotations
import logging
import numpy as np
from multiprocessing import shared_memory, Manager
from typing import Optional, Union, Tuple
import queue
import os

logger = logging.getLogger('app.utils.shared_frame_buffer')

class SharedFrameBuffer:
    """
    Manages a pool of shared memory segments for high-frequency frame transmission.
    Supports resolution-agnostic reads and orphan pruning.
    """
    HEADER_SIZE = 16 # size (4), width (4), height (4), channels (4)

    def __init__(self, pool_size: int = 100, max_frame_size: int = 10 * 1024 * 1024):
        self.pool_size = pool_size
        self.max_frame_size = max_frame_size
        self.manager = Manager()
        
        self._free_pool = self.manager.Queue(maxsize=pool_size)
        self._segments: dict[str, shared_memory.SharedMemory] = {}
        
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
        
        # Write Header: [size, width, height, channels]
        header = np.array([size, w, h, c], dtype=np.int32).tobytes()
        buf[:self.HEADER_SIZE] = header
        # Write Data
        buf[self.HEADER_SIZE : self.HEADER_SIZE + size] = raw_bytes

    def read(self, name: str) -> Tuple[memoryview, Tuple[int, int, int]]:
        """Returns (data_view, (w, h, c))."""
        if name not in self._segments:
            try:
                shm = shared_memory.SharedMemory(name=name)
                self._segments[name] = shm
            except Exception:
                raise ValueError(f'Segment {name} not accessible.')
        
        shm = self._segments[name]
        buf = shm.buf
        
        # Read Header
        header = np.frombuffer(buf[:self.HEADER_SIZE], dtype=np.int32)
        size, w, h, c = header
        
        if size <= 0 or size > self.max_frame_size:
            raise ValueError(f'Invalid size {size} in segment {name}')
            
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
