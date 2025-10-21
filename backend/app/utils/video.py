import cv2
import threading
import time
from queue import Queue, Full, Empty
import logging
from typing import Optional, Union, Any, Dict
from collections import deque, defaultdict

logger = logging.getLogger(__name__)

class FrameReader:
    def __init__(
        self,
        source: Union[str, int],
        buffer_size: int = 1,
        target_fps: Optional[int] = None,
        max_queue_size: int = 128,
        queue_put_timeout_ms: int = 1000,
        is_looped: bool = False,
    ):
        self.source = source
        self.source_name = str(source)
        self.is_file = isinstance(source, str)
        self.cap = None
        self.cap_lock = threading.Lock()
        self._initialize_and_get_properties()

        self.target_fps = target_fps if target_fps else self.fps # Use actual FPS if target_fps not provided
        self.delay = 1 / self.target_fps if self.target_fps else 0
        self.is_looped = is_looped
        self.end_of_video = False

        self.frames_queue = Queue(maxsize=max_queue_size)
        
        self.thread = threading.Thread(target=self._read_frames_continuously, daemon=True)
        self.stop_event = threading.Event()
        self.started_event = threading.Event()
        self.error_message = None

        self.frame_index = -1
        self.frames_read_count = 0
        self.frames_processed_count = 0

        logger.info(f"FrameReader '{self.source_name}' initialized.")

    def _initialize_and_get_properties(self):
        cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            raise RuntimeError(f"Failed to open video source: {self.source_name}")
        self.fps = cap.get(cv2.CAP_PROP_FPS)
        self.frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) if self.is_file else -1
        cap.release()

    @property
    def isOpened(self) -> bool:
        with self.cap_lock:
            return self.cap is not None and self.cap.isOpened()

    def start(self) -> bool:
        if not self.thread.is_alive():
            self.stop_event.clear()
            self.started_event.clear()
            self.error_message = None
            self.thread.start()
            logger.info(f"FrameReader '{self.source_name}': Thread started.")
            started = self.started_event.wait(timeout=10)
            if not started:
                self.error_message = "Thread failed to start or open capture in time."
                logger.error(f"FrameReader '{self.source_name}': {self.error_message}")
                self.stop()
                return False
            if self.error_message:
                logger.error(f"FrameReader '{self.source_name}': Startup error: {self.error_message}")
                return False
        return True

    def _read_frames_continuously(self) -> None:
        self.cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
        if not self.cap.isOpened():
            self.error_message = "Failed to open video source in thread."
            logger.error(f"FrameReader '{self.source_name}': {self.error_message}")
            self.started_event.set()
            return
        
        self.started_event.set()
        consecutive_fails = 0
        max_read_fails = 10

        while not self.stop_event.is_set():
            if self.end_of_video and not self.is_looped:
                break

            start_time = time.time()
            ret, frame = self.cap.read()

            if ret:
                consecutive_fails = 0
                self.frame_index += 1
                self.frames_read_count += 1
                frame_data = {"frame": frame, "frame_index": self.frame_index, "timestamp": time.time(), "source_name": self.source_name}

                if self.frames_queue.full():
                    try:
                        # Discard the oldest frame
                        self.frames_queue.get_nowait()
                    except Empty:
                        pass  # Should not happen

                try:
                    self.frames_queue.put_nowait(frame_data)
                except Full:
                    # This should not happen if we just made space, but as a fallback
                    logger.warning(f"FrameReader '{self.source_name}': Dropping frame {self.frame_index} due to full queue.")
                    pass
            else:
                consecutive_fails += 1
                if consecutive_fails >= max_read_fails:
                    if self.is_looped:
                        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        self.frame_index = -1
                        consecutive_fails = 0
                    else:
                        self.end_of_video = True
            
            elapsed_time = time.time() - start_time
            sleep_time = self.delay - elapsed_time
            if sleep_time > 0:
                time.sleep(sleep_time)

        if self.cap:
            self.cap.release()
            self.cap = None

    def read(self) -> Optional[tuple[int, Any]]:
        frame_data = self.get_frame()
        return (frame_data["frame_index"], frame_data["frame"]) if frame_data else None

    def get_frame(self) -> Optional[Dict[str, Any]]:
        try:
            frame_data = self.frames_queue.get_nowait()
            self.frames_processed_count += 1
            return frame_data
        except Empty:
            return None

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)
        while not self.frames_queue.empty():
            try:
                self.frames_queue.get_nowait()
            except Empty:
                break

    def get_stats(self) -> Dict[str, Any]:
        return {
            "source": self.source_name,
            "resolution": f"{self.frame_width}x{self.frame_height}",
            "frames_in_queue": self.frames_queue.qsize(),
            "is_running": self.thread.is_alive(),
        }

class FrameTimer:
    def __init__(self, window_size: int = 100):
        self.timings = defaultdict(lambda: deque(maxlen=window_size))

    def log_time(self, name: str, duration: float):
        self.timings[name].append(duration)

    def get_avg(self, name: str) -> float:
        return sum(self.timings[name]) / len(self.timings[name]) if self.timings[name] else 0.0

    def get_fps(self, name: str) -> float:
        avg_time = self.get_avg(name)
        return 1.0 / avg_time if avg_time > 0.0 else 0.0
