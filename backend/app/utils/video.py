import cv2
import threading
import time
from queue import Queue, Full, Empty
import logging
from typing import Optional, Union, Any, Dict
from collections import deque, defaultdict

logger = logging.getLogger(__name__)

class FrameReader:
    """
    A class to read frames from a video source (file or webcam) in a separate thread.
    This helps to prevent blocking the main thread while waiting for a new frame.
    It also includes a configurable buffer to store a certain number of the latest frames.
    """

    def __init__(
        self,
        source: Union[str, int],
        buffer_size: int = 1,
        target_fps: Optional[int] = None,
        max_queue_size: int = 128,
        queue_put_timeout_ms: int = 1000,
        is_looped: bool = False,
    ):
        self.source_name = str(source)
        self.is_file = isinstance(source, str)
        self.cap_lock = threading.Lock()
        self._initialize_capture(source)

        if not self.isOpened:
            logger.error(f"FrameReader '{self.source_name}': Failed to open video source.")
            raise RuntimeError(f"Failed to open video source: {self.source_name}")

        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.frame_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.frame_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)) if self.is_file else -1

        self.target_fps = target_fps
        self.delay = 1 / self.target_fps if self.target_fps else 0

        self.is_looped = is_looped
        self.end_of_video = False

        self.frames_queue = Queue(maxsize=max_queue_size)
        self.queue_put_timeout = queue_put_timeout_ms / 1000.0  # Convert to seconds

        self.thread = threading.Thread(target=self._read_frames_continuously, daemon=True)
        self.stop_event = threading.Event()

        self.frame_index = -1
        self.frames_read_count = 0
        self.frames_processed_count = 0

        logger.info(
            f"FrameReader '{self.source_name}' initialized. "
            f"FPS: {self.fps}, Resolution: {self.frame_width}x{self.frame_height}, "
            f"Target FPS: {self.target_fps}, Looping: {self.is_looped}"
        )

    @property
    def isOpened(self) -> bool:
        """Checks if the video source is currently open."""
        with self.cap_lock:
            return self.cap.isOpened()

    def _initialize_capture(self, source: Union[str, int]) -> None:
        """Helper method to initialize the video capture."""
        with self.cap_lock:
            self.cap = cv2.VideoCapture(source)
            if self.is_file and self.cap.isOpened():
                logger.info(f"FrameReader '{self.source_name}': Video file opened successfully.")
            elif not self.is_file and self.cap.isOpened():
                logger.info(f"FrameReader '{self.source_name}': Camera opened successfully.")

    def start(self) -> "FrameReader":
        """Starts the frame reading thread."""
        if not self.thread.is_alive():
            self.thread.start()
            logger.info(f"FrameReader '{self.source_name}': Frame reading thread started.")
        return self

    def _read_frames_continuously(self) -> None:
        """
        The main loop for the reading thread. Reads frames from the source
        and puts them into the queue.
        """
        consecutive_fails = 0
        max_read_fails = 5  # Max consecutive read failures before deciding to stop/restart

        while not self.stop_event.is_set():
            if self.end_of_video and not self.is_looped:
                logger.info(f"FrameReader '{self.source_name}': End of video and not looping. Stopping thread.")
                break

            start_time = time.time()

            try:
                with self.cap_lock:
                    ret, frame = self.cap.read()
                if ret:
                    consecutive_fails = 0  # Reset on successful read
                    self.frame_index += 1
                    self.frames_read_count += 1
                    
                    frame_data = {
                        "frame": frame,
                        "frame_index": self.frame_index,
                        "timestamp": time.time(),
                        "source_name": self.source_name,
                    }

                    try:
                        self.frames_queue.put(frame_data, timeout=self.queue_put_timeout)
                    except Full:
                        logger.warning(
                            f"FrameReader '{self.source_name}': Frame queue is full. Dropping frame {self.frame_index}."
                        )

                else:  # ret is False
                    consecutive_fails += 1
                    logger.warning(
                        f"FrameReader '{self.source_name}': cv2.read() returned False (Fail {consecutive_fails}/{max_read_fails})."
                    )
                    if consecutive_fails >= max_read_fails:
                        if self.is_looped:
                            logger.info(f"FrameReader '{self.source_name}': End of video reached, but looping is enabled. Attempting to restart video.")
                            with self.cap_lock:
                                self.cap.release()
                                self._initialize_capture(self.source_name)
                            if self.isOpened:
                                self.frame_index = -1
                                consecutive_fails = 0
                                self.end_of_video = False
                                logger.info(f"FrameReader '{self.source_name}': Video restarted for looping.")
                            else:
                                logger.error(f"FrameReader '{self.source_name}': Failed to reopen video for looping. Stopping thread.")
                                self.end_of_video = True
                        else:
                            logger.info(f"FrameReader '{self.source_name}': End of video source. Stopping thread.")
                            self.end_of_video = True
                
                if self.delay > 0:
                    elapsed_time = time.time() - start_time
                    sleep_time = self.delay - elapsed_time
                    if sleep_time > 0:
                        time.sleep(sleep_time)

            except Exception as e:
                logger.error(f"FrameReader '{self.source_name}': An unexpected error occurred in reading loop: {e}", exc_info=True)
                break
        
        logger.info(f"FrameReader '{self.source_name}': Exiting reading loop.")
        with self.cap_lock:
            if self.cap.isOpened():
                self.cap.release()

    def read(self) -> Optional[tuple[int, Any]]:
        """
        Retrieves the oldest frame from the queue and returns it as a tuple.
        This is for compatibility with code that expects a cv2.read()-like tuple response.
        """
        frame_data = self.get_frame()
        if frame_data:
            logger.debug(f"FrameReader.read() returning: ({frame_data['frame_index']}, {type(frame_data['frame'])})")
            return frame_data["frame_index"], frame_data["frame"]
        logger.debug("FrameReader.read() returning: None")
        return None

    def get_frame(self) -> Optional[Dict[str, Any]]:
        """
        Retrieves the oldest frame from the queue.

        Returns:
            Optional[Dict[str, Any]]: A dictionary containing the frame and its metadata,
                                      or None if the queue is empty.
        """
        try:
            frame_data = self.frames_queue.get_nowait()
            self.frames_processed_count += 1
            return frame_data
        except Empty:
            return None

    def stop(self) -> None:
        """Signals the reading thread to stop."""
        logger.info(f"FrameReader '{self.source_name}': Stop signal received.")
        self.stop_event.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            logger.warning(f"FrameReader '{self.source_name}': Thread did not terminate in time.")
        
        with self.cap_lock:
            if self.cap.isOpened():
                self.cap.release()
                logger.info(f"FrameReader '{self.source_name}': Video capture released.")

    def get_stats(self) -> Dict[str, Any]:
        """
        Returns statistics about the frame reader's performance.
        """
        return {
            "source": self.source_name,
            "fps_source": self.fps,
            "target_fps": self.target_fps,
            "resolution": f"{self.frame_width}x{self.frame_height}",
            "frames_in_queue": self.frames_queue.qsize(),
            "frames_read": self.frames_read_count,
            "frames_processed": self.frames_processed_count,
            "is_running": self.thread.is_alive(),
            "is_looped": self.is_looped,
            "end_of_video": self.end_of_video,
        }

class FrameTimer:
    """A utility class to time different parts of a processing loop and calculate FPS."""

    def __init__(self, window_size: int = 100):
        """
        Initializes the FrameTimer.

        Args:
            window_size (int): The number of recent timings to store for averaging.
        """
        self.timings = defaultdict(lambda: deque(maxlen=window_size))
        self.last_log_time = defaultdict(float)

    def log_time(self, name: str, duration: float):
        """
        Logs a time duration for a specific operation.

        Args:
            name (str): The name of the operation being timed (e.g., 'read', 'process').
            duration (float): The time taken for the operation in seconds.
        """
        self.timings[name].append(duration)

    def get_avg(self, name: str) -> float:
        """
        Calculates the average time for a specific operation.

        Args:
            name (str): The name of the operation.

        Returns:
            float: The average time in seconds, or 0.0 if no timings are available.
        """
        if not self.timings[name]:
            return 0.0
        return sum(self.timings[name]) / len(self.timings[name])

    def get_fps(self, name: str) -> float:
        """
        Calculates the frames per second (FPS) for a specific operation.

        Args:
            name (str): The name of the operation.

        Returns:
            float: The calculated FPS, or 0.0 if no timings are available.
        """
        avg_time = self.get_avg(name)
        if avg_time == 0.0:
            return 0.0
        return 1.0 / avg_time