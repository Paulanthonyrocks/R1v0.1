import cv2
import numpy as np  # np is used by FrameTimer.get_avg
import logging
import queue  # queue is used by FrameReader
import threading  # threading is used by FrameTimer and FrameReader
import time  # time is used by FrameReader
from typing import Dict, List, Optional, Tuple  # Typing hints used by both classes
from collections import deque  # deque is used by FrameTimer

logger = logging.getLogger(__name__)


# --- Timers ---
class FrameTimer:
    """Simple class to track timings of different stages in a processing loop."""

    def __init__(self, maxlen: int = 100):
        self.timings: Dict[str, deque] = {
            "read": deque(maxlen=maxlen),
            "detect_track": deque(maxlen=maxlen),
            "ocr": deque(maxlen=maxlen),
            "monitor": deque(maxlen=maxlen),
            "visualize": deque(maxlen=maxlen),
            "db_save": deque(maxlen=maxlen),
            "queue_put": deque(maxlen=maxlen),
            "loop_total": deque(maxlen=maxlen),
        }
        self._lock = threading.Lock()

    def log_time(self, stage: str, duration: float):
        with self._lock:
            if stage in self.timings:
                self.timings[stage].append(duration)
            else:
                logger.warning(f"FrameTimer: Unknown stage '{stage}'")

    def get_avg(self, stage: str) -> float:
        with self._lock:
            # Ensure numpy is available if np.mean is used.
            # If numpy is not a desired dependency here, use statistics.mean or manual calculation.
            return (
                np.mean(self.timings[stage])
                if stage in self.timings and self.timings[stage]
                else 0.0
            )

    def get_fps(self, stage: str = "loop_total") -> float:
        avg_time = self.get_avg(stage)
        return 1.0 / avg_time if avg_time > 0 else 0.0

    def update_from_dict(self, timings_dict: Dict[str, List[float]]):
        with self._lock:
            for stage, times in timings_dict.items():
                if stage in self.timings and isinstance(
                    times, (list, deque)
                ):  # check type to be list or deque
                    self.timings[stage].extend(times)


# --- FrameReader ---
class FrameReader:
    def __init__(
        self,
        source,
        buffer_size: int = 1,
        target_fps: Optional[int] = None,
        max_queue_size: int = 100,
    ):
        self.source_name = str(source)
        self.cap = None
        self.buffer_size = buffer_size
        self.target_fps = target_fps
        self.frame_queue: queue.Queue[Tuple[int, np.ndarray]] = queue.Queue(
            maxsize=max_queue_size
        )
        self.state_lock = threading.Lock()  # Lock for accessing shared state
        self._end_of_video_flag = False  # Internal state, controlled by property
        self.thread = None
        self.stop_event = threading.Event()
        self.frame_index = (
            -1
        )  # Current frame index being processed by the reader thread
        self.last_read_time = time.time()
        self.read_interval = 0  # Calculated based on target_fps

        if self.target_fps:
            self.read_interval = 1.0 / self.target_fps

        self._initialize_capture(source)

        if self.cap and self.cap.isOpened():
            self.thread = threading.Thread(
                target=self._update_loop,
                daemon=True,
                name=f"FrameReader-{self.source_name}",
            )
            self.thread.start()
        else:
            logger.error(
                f"FrameReader: Failed to open video source: {source} (from original source: {self.source_name})"
            )
            raise RuntimeError(f"FrameReader: Failed to open video source: {source}")

    def _initialize_capture(self, source):
        """Initializes the OpenCV video capture object."""
        if isinstance(source, (int, str)):
            self.cap = cv2.VideoCapture(source)
        else:
            raise ValueError(f"Unsupported source type for FrameReader: {type(source)}")

        if not self.cap.isOpened():
            logger.error(
                f"FrameReader: Failed to open video source: {source} (from original source: {self.source_name})"
            )
            raise RuntimeError(f"FrameReader: Failed to open video source: {source}")
        logger.info(
            f"FrameReader: Successfully opened video source: {source} (from original source: {self.source_name})"
        )

    @property
    def isOpened(self) -> bool:
        """Returns True if the video capture is opened, False otherwise."""
        return self.cap.isOpened()

    @property
    def end_of_video(self) -> bool:
        with self.state_lock:
            return self._end_of_video_flag

    @end_of_video.setter
    def end_of_video(self, value: bool):
        with self.state_lock:
            self._end_of_video_flag = value

    def _update_loop(self):
        max_read_fails = 100
        consecutive_fails = 0
        last_read_time = time.monotonic()

        while not self.stop_event.is_set():
            try:
                if self.target_fps:  # Simple sleep to approximate target FPS
                    wait_time = (1.0 / self.target_fps) - (
                        time.monotonic() - last_read_time
                    )
                    if wait_time > 0:
                        time.sleep(wait_time)

                ret, frame = self.cap.read()
                last_read_time = time.monotonic()

                if ret:
                    consecutive_fails = 0  # Reset fail counter on successful read
                    if self.frame_queue.full():
                        # If the queue is full, wait a bit for the consumer to catch up
                        # This prevents discarding frames too aggressively and allows for backpressure
                        logger.warning(
                            f"FrameReader queue for '{self.source_name}' is full. Waiting for space..."
                        )
                        try:
                            # Wait with a timeout to avoid blocking indefinitely
                            self.frame_queue.put(
                                (self.frame_index, frame.copy()), timeout=0.1
                            )
                            self.frame_index += 1
                        except queue.Full:
                            logger.warning(
                                f"FrameReader queue for '{self.source_name}' still full after waiting. Discarding frame {self.frame_index}."
                            )
                            # Discard the current frame if still full after waiting
                            pass
                    else:
                        # Put a copy of the frame into the queue
                        self.frame_queue.put((self.frame_index, frame.copy()))
                        self.frame_index += 1

                else:  # ret is False
                    consecutive_fails += 1
                    logger.warning(
                        f"FrameReader '{self.source_name}': cv2.read() returned False (Fail {consecutive_fails}/{max_read_fails})."
                    )
                    if consecutive_fails >= max_read_fails:
                        logger.error(
                            f"FrameReader '{self.source_name}': Max read fails reached. Assuming end of video or hardware issue."
                        )
                        self.end_of_video = True
                        break
                    time.sleep(0.1)  # Wait a bit before retrying
            except Exception as e:
                logger.error(
                    f"FrameReader thread error in '{self.source_name}': {e}",
                    exc_info=True,
                )
                self.end_of_video = True  # Signal error/end
                break

        logger.info(f"FrameReader thread stopping for '{self.source_name}'.")
        self.end_of_video = True  # Ensure flag is set on exit
        if self.cap and self.cap.isOpened():
            self.cap.release()
            logger.info(f"Video capture released for '{self.source_name}'.")

        # Clear the queue
        while not self.frame_queue.empty():
            try:
                self.frame_queue.get_nowait()
            except queue.Empty:
                break

    def read(self) -> Optional[Tuple[int, np.ndarray]]:
        try:
            return self.frame_queue.get(timeout=0.5)  # Wait up to 0.5s for a frame
        except queue.Empty:
            # Check if the thread is still alive and it's not the end of video
            if self.end_of_video and (
                not self.thread.is_alive() or self.frame_queue.empty()
            ):
                logger.debug(
                    f"FrameReader '{self.source_name}': Read call, queue empty and EOV / thread stopped."
                )
                return None  # End of video or reader stopped
            logger.debug(
                f"FrameReader '{self.source_name}': Read call, queue temporarily empty."
            )
            return None  # Queue is empty but reader might still be running

    def stop(self):
        logger.info(f"FrameReader '{self.source_name}': Stop requested.")
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2.0)  # Wait for thread to finish
            if self.thread.is_alive():
                logger.warning(
                    f"FrameReader thread '{self.source_name}' did not exit cleanly after 2s."
                )

        # Ensure capture is released if not already by the thread
        if self.cap and self.cap.isOpened():
            self.cap.release()
            logger.info(
                f"Video capture explicitly released by stop() for '{self.source_name}'."
            )
        logger.info(f"FrameReader '{self.source_name}': Stopped.")
