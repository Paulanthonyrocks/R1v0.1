import cv2
import os
import threading
import time
from queue import Queue, Empty
import logging
from typing import Optional, Union, Any, Dict, Tuple
from collections import deque, defaultdict

logger = logging.getLogger(__name__)

class FrameReader:
    def __init__(
        self,
        source: Union[str, int],
        target_fps: Optional[int] = None,
        max_queue_size: int = 128,
        is_looped: bool = False,
        reconnect_delay: int = 5,
        gpu_acceleration: bool = False
    ):
        self.source = source
        self.source_name = str(source)
        self.is_file = isinstance(source, str) and not str(source).startswith(('rtsp:', 'http:', 'https:', 'tcp:'))
        self.gpu_acceleration = gpu_acceleration
        
        # Reader State
        self.stop_event = threading.Event()
        self.started_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.reconnect_delay = reconnect_delay
        
        # Video Properties (Cached to avoid thread contention on cap)
        self.fps = 0.0
        self.frame_width = 0
        self.frame_height = 0
        self.frame_count = 0
        self._inspect_source()

        # Config
        self.target_fps = target_fps if target_fps else (self.fps if self.fps > 0 else 30)
        self.delay = 1.0 / self.target_fps
        self.is_looped = is_looped

        # Data
        self.frames_queue = Queue(maxsize=max_queue_size)
        self.frames_processed_count = 0
        self.end_of_video = False
        self.start_time: Optional[float] = None
        self.frame_index = -1
        self.frames_read_count = 0
        
        # Start
        self._start_thread()
        logger.info(f"FrameReader '{self.source_name}' initialized. Target FPS: {self.target_fps}")

    def _inspect_source(self):
        """Opens source briefly to get metadata, then closes it."""
        try:
            cap = cv2.VideoCapture(self.source)
            if cap.isOpened():
                self.fps = cap.get(cv2.CAP_PROP_FPS)
                self.frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                self.frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                self.frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.release()
        except Exception as e:
            logger.warning(f"Could not inspect source '{self.source_name}': {e}")

    def _start_thread(self):
        self.thread = threading.Thread(target=self._read_frames_continuously, daemon=True)
        self.thread.start()
        # Wait for the thread to actually initialize the capture
        if not self.started_event.wait(timeout=10):
            logger.error(f"FrameReader '{self.source_name}' timed out starting up.")

    def start(self) -> bool:
        """Compatibility method if called externally, though init starts it automatically."""
        if not self.thread.is_alive():
            self.stop_event.clear()
            self.started_event.clear()
            self._start_thread()
        return True

    def _read_frames_continuously(self) -> None:
        # Select backend and options based on GPU config
        backend = cv2.CAP_ANY
        if self.gpu_acceleration:
            # Optimized FFMPEG HWAccel strings for different vendors
            # Priority: NVIDIA (cuvid) -> Intel (qsv) -> Generic (vaapi)
            hw_options = [
                "hwaccel;cuvid|video_codec;h264_cuvid",
                "hwaccel;qsv|video_codec;h264_qsv",
                "hwaccel;vaapi"
            ]
            
            # Combine options with basic stream optimizations for low latency
            # 'rtsp_transport;tcp' is critical for avoiding packet loss on high-res streams
            base_options = "rtsp_transport;tcp|reorder_queue_size;0|buffer_size;1024000"
            
            # Try the primary (NVIDIA) first
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"{hw_options[0]}|{base_options}"
            backend = cv2.CAP_FFMPEG
            logger.info(f"FrameReader '{self.source_name}' attempting GPU acceleration via FFMPEG (NVDEC).")

        cap = cv2.VideoCapture(self.source, backend)
        if not cap.isOpened():
            logger.error(f"FrameReader '{self.source_name}': Failed to open source.")
            self.started_event.set() # Unblock init
            return
        
        self.started_event.set()
        consecutive_fails = 0
        max_read_fails = 100 if not self.is_file else 0 # Be more patient with streams

        self.start_time = time.time() # Set start time when the reading actually begins
        next_frame_time = self.start_time

        while not self.stop_event.is_set():
            # 1. Read Frame
            ret, frame = cap.read()

            # 2. Handle Read Failure
            if not ret:
                consecutive_fails += 1
                
                # Case A: Video File finished
                if self.is_file:
                    if self.is_looped:
                        logger.info(f"Looping video '{self.source_name}'")
                        # Robust looping: Reopen the file to ensure a clean state
                        cap.release()
                        time.sleep(0.05) # Brief pause to release handles
                        cap = cv2.VideoCapture(self.source)
                        
                        if not cap.isOpened():
                             logger.error(f"Failed to reopen video '{self.source_name}' for looping.")
                             self.end_of_video = True
                             break
                        
                        # Reset reader state
                        self.frame_index = -1
                        # Reset timing to prevent fast-forwarding after the pause
                        next_frame_time = time.time() 
                        continue
                    else:
                        logger.info(f"End of video '{self.source_name}'")
                        self.end_of_video = True
                        break # Exit loop
                
                # Case B: Stream Disconnected
                else:
                    if consecutive_fails % 20 == 0:
                        logger.warning(f"Stream '{self.source_name}' unstable. Fail {consecutive_fails}.")
                    
                    if consecutive_fails > max_read_fails:
                        logger.error(f"Stream '{self.source_name}' lost. Attempting reconnect...")
                        cap.release()
                        time.sleep(self.reconnect_delay)
                        cap = cv2.VideoCapture(self.source)
                        consecutive_fails = 0
                    
                    time.sleep(0.1) # Prevent busy loop on fail
                    continue

            # 3. Handle Success
            consecutive_fails = 0
            self.frame_index += 1
            self.frames_read_count += 1
            
            frame_data = {
                "frame": frame, 
                "frame_index": self.frame_index, 
                "timestamp": time.time(), 
                "source_name": self.source_name
            }

            # 4. Queue Management (Leaky Bucket)
            if self.frames_queue.full():
                try:
                    self.frames_queue.get_nowait() # Drop oldest
                except Empty:
                    pass 
            
            self.frames_queue.put(frame_data)

            # 5. FPS Control (Drift-corrected)
            next_frame_time += self.delay
            sleep_time = next_frame_time - time.time()
            
            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                # If we are significantly behind (e.g. > 10 frames), reset to avoid burst
                if sleep_time < -0.5:
                     next_frame_time = time.time()

        # Cleanup within thread
        if cap.isOpened():
            cap.release()
        logger.info(f"FrameReader '{self.source_name}' thread exiting.")

    def read(self) -> Optional[Tuple[int, Any]]:
        """Returns (frame_index, frame) or None."""
        frame_data = self.get_frame()
        return (frame_data["frame_index"], frame_data["frame"]) if frame_data else None

    def read_raw(self) -> Optional[Tuple[int, Any]]:
        """Returns (frame_index, frame) directly without dict wrapper for lower overhead."""
        try:
            data = self.frames_queue.get_nowait()
            self.frames_processed_count += 1
            return (data["frame_index"], data["frame"])
        except Empty:
            return None

    def get_frame(self) -> Optional[Dict[str, Any]]:
        """Returns full dictionary including metadata or None."""
        try:
            frame_data = self.frames_queue.get_nowait()
            self.frames_processed_count += 1
            return frame_data
        except Empty:
            return None

    def stop(self) -> None:
        """Stops the thread safely."""
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            # Wait for thread to finish to ensure cap is released safely
            self.thread.join(timeout=2.0)
        
        # Clear queue
        with self.frames_queue.mutex:
            self.frames_queue.queue.clear()
            
    def __del__(self):
        self.stop()

    def get_stats(self) -> Dict[str, Any]:
        return {
            "source": self.source_name,
            "resolution": f"{self.frame_width}x{self.frame_height}",
            "target_fps": self.target_fps,
            "frames_read": self.frames_read_count,
            "frames_processed_count": self.frames_processed_count,
            "frames_queued": self.frames_queue.qsize(),
            "alive": self.thread.is_alive() if self.thread else False,
        }

    @property
    def isOpened(self) -> bool:
        """
        Check if the frame reader is actively reading frames.
        
        Provides API consistency with cv2.VideoCapture interface.
        
        Returns:
            True if the reader thread is alive and video hasn't ended.
        """
        return (
            self.thread is not None 
            and self.thread.is_alive() 
            and not self.end_of_video
        )