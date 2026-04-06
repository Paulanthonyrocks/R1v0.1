import numpy as np
from multiprocessing import shared_memory
import logging
from typing import Tuple, Optional, Dict

logger = logging.getLogger(__name__)

class SharedFrameManager:
    """
    Manages a ring buffer of shared memory segments for zero-copy image transfer.
    This prevents the overhead of pickling large NumPy arrays through Redis/Queues.
    """
    def __init__(self, name: str, frame_shape: Tuple[int, int, int], dtype=np.uint8, num_buffers: int = 10):
        self.name = name
        self.frame_shape = frame_shape
        self.dtype = dtype
        self.num_buffers = num_buffers
        self.buffer_size = np.prod(frame_shape) * np.dtype(dtype).itemsize
        
        # Each buffer gets a unique name: name_0, name_1, ...
        self.shm_segments = []
        try:
            for i in range(num_buffers):
                shm = shared_memory.SharedMemory(name=f"{name}_{i}", create=True, size=self.buffer_size)
                self.shm_segments.append(shm)
            logger.info(f"Created {num_buffers} shared memory buffers for {name} (Total: {num_buffers * self.buffer_size / 1024**2:.2f} MB)")
        except FileExistsError:
            # If segments already exist, attach to them
            for i in range(num_buffers):
                shm = shared_memory.SharedMemory(name=f"{name}_{i}")
                self.shm_segments.append(shm)
            logger.info(f"Attached to existing shared memory buffers for {name}")

    def write_frame(self, index: int, frame: np.ndarray):
        """Writes a frame into the specific buffer index."""
        if index >= self.num_buffers:
            index = index % self.num_buffers
            
        shm = self.shm_segments[index]
        # Create a numpy array backed by the shared memory
        shared_array = np.ndarray(self.frame_shape, dtype=self.dtype, buffer=shm.buf)
        # Copy the frame data into shared memory
        np.copyto(shared_array, frame)

    def get_frame(self, index: int) -> np.ndarray:
        """Reads a frame from the specific buffer index."""
        if index >= self.num_buffers:
            index = index % self.num_buffers
            
        shm = self.shm_segments[index]
        return np.ndarray(self.frame_shape, dtype=self.dtype, buffer=shm.buf)

    def close(self):
        """Closes and unlinks all shared memory segments."""
        for shm in self.shm_segments:
            shm.close()
            try:
                shm.unlink()
            except FileNotFoundError:
                pass
        logger.info(f"Cleaned up shared memory for {self.name}")

    @staticmethod
    def access_shm(shm_name: str, shape: Tuple[int, int, int], dtype=np.uint8) -> Tuple[np.ndarray, shared_memory.SharedMemory]:
        """
        Utility for workers to attach to a specific SHM segment without the full manager.
        Returns the numpy array and the shm handle (caller must close handle).
        """
        shm = shared_memory.SharedMemory(name=shm_name)
        array = np.ndarray(shape, dtype=dtype, buffer=shm.buf)
        return array, shm

    @staticmethod
    def cleanup_shm(shm_name: str):
        """Unlinks a specific SHM segment."""
        try:
            shm = shared_memory.SharedMemory(name=shm_name)
            shm.close()
            shm.unlink()
        except Exception:
            pass
