import cv2
import numpy as np # np is used by FrameTimer.get_avg
import logging
import queue # queue is used by FrameReader
import threading # threading is used by FrameTimer and FrameReader
import time # time is used by FrameReader
from pathlib import Path # Path is used by FrameReader
from typing import Dict, List, Optional, Any, Tuple # Typing hints used by both classes
from collections import deque # deque is used by FrameTimer

logger = logging.getLogger(__name__)

# --- Timers ---
class FrameTimer:
    """Simple class to track timings of different stages in a processing loop."""
    def __init__(self, maxlen: int = 100):
        self.timings: Dict[str, deque] = {
            'read': deque(maxlen=maxlen), 'detect_track': deque(maxlen=maxlen),
            'ocr': deque(maxlen=maxlen), 'monitor': deque(maxlen=maxlen),
            'visualize': deque(maxlen=maxlen), 'db_save': deque(maxlen=maxlen),
            'queue_put': deque(maxlen=maxlen), 'loop_total': deque(maxlen=maxlen)
        }
        self._lock = threading.Lock()

    def log_time(self, stage: str, duration: float):
        with self._lock:
            if stage in self.timings: self.timings[stage].append(duration)
            else: logger.warning(f"FrameTimer: Unknown stage '{stage}'")

    def get_avg(self, stage: str) -> float:
        with self._lock:
            # Ensure numpy is available if np.mean is used.
            # If numpy is not a desired dependency here, use statistics.mean or manual calculation.
            return np.mean(self.timings[stage]) if stage in self.timings and self.timings[stage] else 0.0

    def get_fps(self, stage: str = 'loop_total') -> float:
        avg_time = self.get_avg(stage)
        return 1.0 / avg_time if avg_time > 0 else 0.0

    def update_from_dict(self, timings_dict: Dict[str, List[float]]):
        with self._lock:
            for stage, times in timings_dict.items():
                if stage in self.timings and isinstance(times, (list, deque)): # check type to be list or deque
                    self.timings[stage].extend(times)

# --- FrameReader ---
class FrameReader:
    def __init__(self, source: Any, buffer_size: int = 2, target_fps: Optional[int] = None):
        self.source_name = str(source)
        self.target_fps = target_fps
        self.is_webcam = False
        capture_source: Any = source # Explicitly type hint

        try:
            capture_source = int(source) # Try converting source to int (for webcam index)
            self.is_webcam = True
        except ValueError:
            # If not an int, check if it's "webcam" string or a file/URL path
            if str(source).lower() == "webcam":
                 capture_source = 0 # Default webcam index
                 self.is_webcam = True
            elif "://" not in str(source) and not Path(source).exists(): # Check if it's not a URL and path doesn't exist
                 raise FileNotFoundError(f"Video file not found: {source}")
            # If it's a URL or an existing file path, capture_source remains as is

        self.cap = cv2.VideoCapture(capture_source)
        if not self.cap.isOpened():
            logger.error(f"FrameReader: Failed to open video source: {capture_source} (from original source: {self.source_name})")
            raise RuntimeError(f"Cannot open video source: {capture_source}")
        logger.info(f"FrameReader: Successfully opened video source: {capture_source} (from original source: {self.source_name})")

        self.source_fps: float = self.cap.get(cv2.CAP_PROP_FPS)
        self.source_width: int = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.source_height: int = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        logger.info(f"Source properties: {self.source_width}x{self.source_height} @ {self.source_fps:.2f} FPS")

        if self.is_webcam:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, buffer_size)
            logger.info(f"Webcam buffer size set to: {buffer_size}")
            if self.target_fps: # If a target FPS is set for the webcam
                self.cap.set(cv2.CAP_PROP_FPS, float(self.target_fps)) # Ensure it's float for OpenCV
                logger.info(f"Attempting to set webcam FPS to: {self.target_fps}")
        elif self.target_fps and self.target_fps != self.source_fps:
            logger.warning(f"Target FPS {self.target_fps} differs from source FPS {self.source_fps}. Frame dropping/duplication may occur if not handled by reader logic.")

        self.frame_queue: queue.Queue[Tuple[int, np.ndarray]] = queue.Queue(maxsize=30) # Type hint for clarity
        self.stop_event = threading.Event()
        self._end_of_video_flag = False # Internal flag
        self.state_lock = threading.Lock() # For thread-safe access to _end_of_video_flag
        self.thread = threading.Thread(target=self._update_loop, daemon=True, name=f"FrameReader-{self.source_name}")
        self.frame_index: int = 0 # To track frame numbers
        self.thread.start()

    @property
    def end_of_video(self) -> bool:
        with self.state_lock:
            return self._end_of_video_flag

    @end_of_video.setter
    def end_of_video(self, value: bool):
        with self.state_lock:
            self._end_of_video_flag = value

    def _update_loop(self):
        max_read_fails = 10
        consecutive_fails = 0
        last_read_time = time.monotonic()

        while not self.stop_event.is_set():
            try:
                if self.target_fps: # Simple sleep to approximate target FPS
                    wait_time = (1.0 / self.target_fps) - (time.monotonic() - last_read_time)
                    if wait_time > 0:
                        time.sleep(wait_time)

                ret, frame = self.cap.read()
                last_read_time = time.monotonic()

                if ret:
                    consecutive_fails = 0 # Reset fail counter on successful read
                    if self.frame_queue.full():
                        try:
                            self.frame_queue.get_nowait() # Discard oldest frame if queue is full
                            logger.warning(f"FrameReader queue for '{self.source_name}' was full. Discarded oldest frame.")
                        except queue.Empty:
                            pass # Should not happen if full() is true, but good practice

                    # Put a copy of the frame into the queue
                    try:
                        self.frame_queue.put((self.frame_index, frame.copy()), timeout=0.1) # Short timeout
                        self.frame_index += 1
                    except queue.Full:
                        logger.warning(f"FrameReader queue for '{self.source_name}' still full after trying to make space. Frame {self.frame_index} lost.")

                else: # ret is False
                    consecutive_fails += 1
                    logger.warning(f"FrameReader '{self.source_name}': cv2.read() returned False (Fail {consecutive_fails}/{max_read_fails}).")
                    if consecutive_fails >= max_read_fails:
                        logger.error(f"FrameReader '{self.source_name}': Max read fails reached. Assuming end of video or hardware issue.")
                        self.end_of_video = True
                        break
                    time.sleep(0.05) # Wait a bit before retrying
                    # Check if it's a video file and we've reached the end
                    if not self.is_webcam and self.cap.get(cv2.CAP_PROP_POS_FRAMES) >= self.cap.get(cv2.CAP_PROP_FRAME_COUNT):
                        logger.info(f"FrameReader '{self.source_name}': Reached end of video file.")
                        self.end_of_video = True
                        break
            except Exception as e:
                logger.error(f"FrameReader thread error in '{self.source_name}': {e}", exc_info=True)
                self.end_of_video = True # Signal error/end
                break

        logger.info(f"FrameReader thread stopping for '{self.source_name}'.")
        self.end_of_video = True # Ensure flag is set on exit
        if self.cap and self.cap.isOpened():
            self.cap.release()
            logger.info(f"Video capture released for '{self.source_name}'.")

        # Clear the queue
        while not self.frame_queue.empty():
             try: self.frame_queue.get_nowait()
             except queue.Empty: break


    def read(self) -> Optional[Tuple[int, np.ndarray]]:
        try:
            return self.frame_queue.get(timeout=0.5) # Wait up to 0.5s for a frame
        except queue.Empty:
            # Check if the thread is still alive and it's not the end of video
            if self.end_of_video and (not self.thread.is_alive() or self.frame_queue.empty()):
                 logger.debug(f"FrameReader '{self.source_name}': Read call, queue empty and EOV / thread stopped.")
                 return None # End of video or reader stopped
            logger.debug(f"FrameReader '{self.source_name}': Read call, queue temporarily empty.")
            return None # Queue is empty but reader might still be running

    def stop(self):
        logger.info(f"FrameReader '{self.source_name}': Stop requested.")
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2.0) # Wait for thread to finish
            if self.thread.is_alive():
                logger.warning(f"FrameReader thread '{self.source_name}' did not exit cleanly after 2s.")

        # Ensure capture is released if not already by the thread
        if self.cap and self.cap.isOpened():
            self.cap.release()
            logger.info(f"Video capture explicitly released by stop() for '{self.source_name}'.")
        logger.info(f"FrameReader '{self.source_name}': Stopped.")
