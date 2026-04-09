
import numpy as np
from multiprocessing import shared_memory
import logging
import atexit
from typing import Tuple, Optional, Dict

logger = logging.getLogger(__name__)

# CRITICAL #2 FIX: Add synchronization flags
# 0: Free for producer
# 1: Full/Ready for consumer

class SharedFrameManager:
    """
    Manages a ring buffer of shared memory segments for zero-copy image transfer
    with a flag-based system for producer-consumer synchronization.
    """
    def __init__(self, name: str, frame_shape: Tuple[int, int, int], dtype=np.uint8, num_buffers: int = 10, create=True):
        self.name = name
        self.frame_shape = frame_shape
        self.dtype = dtype
        self.num_buffers = num_buffers
        self.buffer_size = np.prod(frame_shape) * np.dtype(dtype).itemsize
        self.shm_segments = []
        self._creator = create

        # Initialize shared memory for synchronization flags
        self.flags_shm_name = f"{name}_flags"
        try:
            if create:
                self.flags_shm = shared_memory.SharedMemory(name=self.flags_shm_name, create=True, size=num_buffers)
                self.flags = self.flags_shm.buf
                # Initialize all buffers to state 0 (Free)
                for i in range(num_buffers):
                    self.flags[i] = 0
            else:
                self.flags_shm = shared_memory.SharedMemory(name=self.flags_shm_name, create=False)
                self.flags = self.flags_shm.buf
        except Exception as e:
            logger.error(f"Failed to create/attach to flags SHM {self.flags_shm_name}: {e}", exc_info=True)
            raise

        # Initialize shared memory for frame buffers
        for i in range(num_buffers):
            shm_name = f"{name}_{i}"
            try:
                shm = shared_memory.SharedMemory(name=shm_name, create=create, size=self.buffer_size)
                self.shm_segments.append(shm)
            except Exception as e:
                logger.error(f"Failed to create/attach to frame SHM {shm_name}: {e}", exc_info=True)
                self.unlink()  # Clean up any resources created so far
                raise

        # WARNING #3 FIX: Register unlink for cleanup on process exit
        if create:
            atexit.register(self.unlink)

        log_action = "Created" if create else "Attached to"
        logger.info(f"{log_action} {num_buffers} synchronized shared memory buffers for {name}")

    def write_frame(self, index: int, frame: np.ndarray) -> bool:
        """Writes a frame into a specific buffer, if it's free."""
        buffer_index = index % self.num_buffers
        
        # If flag is 1, consumer hasn't read the last frame in this slot.
        if self.flags[buffer_index] == 1:
            return False  # Buffer is currently full

        shm = self.shm_segments[buffer_index]
        shared_array = np.ndarray(self.frame_shape, dtype=self.dtype, buffer=shm.buf)
        np.copyto(shared_array, frame)
        
        # Set flag to 1 (Ready for consumer)
        self.flags[buffer_index] = 1
        return True

    def get_frame(self, index: int) -> Optional[np.ndarray]:
        """Reads a frame from a specific buffer, if it's ready."""
        buffer_index = index % self.num_buffers
        
        # If flag is 0, producer hasn't written the frame yet (or it's been consumed).
        if self.flags[buffer_index] == 0:
            logger.warning(f"Attempted to read from unready SHM buffer {buffer_index} for index {index}")
            return None # Frame not ready
        
        shm = self.shm_segments[buffer_index]
        frame = np.ndarray(self.frame_shape, dtype=self.dtype, buffer=shm.buf)
        
        # Set flag to 0 (Free for producer) after we have the buffer reference
        self.flags[buffer_index] = 0
        
        # WARNING #1 FIX: Return a read-only view to prevent mutation and copying
        frame_view = frame.view()
        frame_view.flags.writeable = False
        return frame_view

    def close(self):
        """Closes all shared memory file descriptors without unlinking."""
        for shm in self.shm_segments:
            shm.close()
        if hasattr(self, 'flags_shm') and self.flags_shm:
            self.flags_shm.close()

    def unlink(self):
        """Closes and unlinks all shared memory segments. For producer/creator use only."""
        self.close()
        for shm in self.shm_segments:
            try:
                shm.unlink()
            except FileNotFoundError:
                pass
        if hasattr(self, 'flags_shm') and self.flags_shm:
            try:
                self.flags_shm.unlink()
            except FileNotFoundError:
                pass
        logger.info(f"Unlinked shared memory for {self.name}")

    @staticmethod
    def get_frame_from_shm(shm_info: Dict) -> Optional[np.ndarray]:
        logger.warning("get_frame_from_shm is deprecated and bypasses synchronization.")
        try:
            shm_name_base = shm_info['shm_name']
            buffer_index = shm_info['shm_index'] % shm_info.get('num_buffers', 20)
            shm = shared_memory.SharedMemory(name=f"{shm_name_base}_{buffer_index}")
            frame_array = np.ndarray(shm_info['shape'], dtype=np.dtype(shm_info['dtype']), buffer=shm.buf)
            frame_copy = frame_array.copy()
            shm.close()
            return frame_copy
        except Exception as e:
            logger.warning(f"Could not access shared memory frame via static method: {e}.")
            return None
