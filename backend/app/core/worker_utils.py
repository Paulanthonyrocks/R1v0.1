import numpy as np
from multiprocessing import shared_memory
import logging
from typing import Tuple, Optional, Dict

logger = logging.getLogger(__name__)

class SharedFrameManager:
    """
    Manages a ring buffer of shared memory segments for zero-copy image transfer.
    """
    def __init__(self, name: str, frame_shape: Tuple[int, int, int], dtype=np.uint8, num_buffers: int = 10, create=True):
        self.name = name
        self.frame_shape = frame_shape
        self.dtype = dtype
        self.num_buffers = num_buffers
        self.buffer_size = np.prod(frame_shape) * np.dtype(dtype).itemsize
        self.shm_segments = []

        for i in range(num_buffers):
            try:
                shm_name = f"{name}_{i}"
                if create:
                    shm = shared_memory.SharedMemory(name=shm_name, create=True, size=self.buffer_size)
                else:
                    shm = shared_memory.SharedMemory(name=shm_name, create=False)
                self.shm_segments.append(shm)
            except FileNotFoundError:
                if not create:
                    logger.error(f"SHM consumer tried to attach to non-existent segment: {shm_name}")
                    raise
                else: # Should not happen if create=True
                    logger.error(f"Unexpected FileNotFoundError for {shm_name}")
                    raise
            except FileExistsError:
                if create:
                    # Producer trying to recreate, attach instead
                    shm = shared_memory.SharedMemory(name=shm_name, create=False)
                    self.shm_segments.append(shm)
                else:
                    # Consumer attaching, this is expected
                    pass # Already handled by the create=False path

        log_action = "Created" if create else "Attached to"
        logger.info(f"{log_action} {num_buffers} shared memory buffers for {name}")

    def write_frame(self, index: int, frame: np.ndarray):
        """Writes a frame into a specific buffer index."""
        shm = self.shm_segments[index % self.num_buffers]
        shared_array = np.ndarray(self.frame_shape, dtype=self.dtype, buffer=shm.buf)
        np.copyto(shared_array, frame)

    def get_frame(self, index: int) -> np.ndarray:
        """Reads a frame from a specific buffer index."""
        shm = self.shm_segments[index % self.num_buffers]
        # Return a copy to avoid downstream mutation issues if the buffer is overwritten
        return np.ndarray(self.frame_shape, dtype=self.dtype, buffer=shm.buf).copy()

    def close(self):
        """Closes all shared memory file descriptors. Does NOT unlink."""
        for shm in self.shm_segments:
            shm.close()

    def unlink(self):
        """Closes and unlinks all shared memory segments. FOR PRODUCER USE ONLY."""
        for shm in self.shm_segments:
            try:
                shm.close()
                shm.unlink() # This destroys the memory block
            except FileNotFoundError:
                pass # Already unlinked
            except Exception as e:
                logger.error(f"Error unlinking SHM segment: {e}")
        logger.info(f"Unlinked shared memory for {self.name}")

    @staticmethod
    def get_frame_from_shm(shm_info: Dict) -> Optional[np.ndarray]:
        """Static method for consumers to access a single frame from shared memory."""
        try:
            # Use a short-lived SHM object to access the data
            shm = shared_memory.SharedMemory(name=f"{shm_info['shm_name']}_{shm_info['shm_index']}")
            frame_array = np.ndarray(shm_info['shape'], dtype=np.dtype(shm_info['dtype']), buffer=shm.buf)
            frame_copy = frame_array.copy() # Essential to copy out the data
            shm.close() # Immediately close the handle
            return frame_copy
        except (FileNotFoundError, KeyError) as e:
            logger.warning(f"Could not access shared memory frame: {e}. It might have been cleaned up.")
            return None
