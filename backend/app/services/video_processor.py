import cv2
import numpy as np
from pathlib import Path
import logging
from typing import Dict, Any, Generator # Removed Optional
import time
from datetime import datetime

logger = logging.getLogger(__name__)


class VideoProcessor:
    def __init__(self, video_path: str):
        self.video_path = Path(video_path)
        self.cap = None
        self.frame_count = 0
        self.fps = 0
        self.initialize()

    def initialize(self):
        """Initialize the video capture."""
        if not self.video_path.exists():
            raise FileNotFoundError(f"Video file not found: {self.video_path}")

        self.cap = cv2.VideoCapture(str(self.video_path))
        if not self.cap.isOpened():
            raise RuntimeError(f"Failed to open video: {self.video_path}")

        self.frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = int(self.cap.get(cv2.CAP_PROP_FPS))
        if self.fps == 0: # Handle cases where FPS might not be read correctly
            logger.warning(f"Video {self.video_path} FPS reported as 0, defaulting to 25.")
            self.fps = 25 # Default FPS
        logger.info(f"Initialized video: {self.video_path} ({self.frame_count} frames, {self.fps} FPS)")

    def process_frame(self, frame: np.ndarray) -> Dict[str, Any]:
        """Process a single frame and extract KPIs. Returns a dictionary of KPIs."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # Example: Simple motion detection using thresholding
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        _, thresh = cv2.threshold(blur, 60, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(thresh, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        vehicle_count = len([c for c in contours if cv2.contourArea(c) > 500]) # Basic count
        motion_level = np.mean(thresh) / 255.0 # Basic motion metric

        return {
            "timestamp": datetime.now().isoformat(),
            "vehicle_count": vehicle_count,
            "motion_level": float(motion_level), # Ensure float for JSON
            "frame_number": int(self.cap.get(cv2.CAP_PROP_POS_FRAMES)) # Current frame
        }

    def get_frame_generator(self) -> Generator[Dict[str, Any], None, None]:
        """Generator that yields processed frames (JPEG bytes) and their KPIs."""
        if not self.cap or not self.cap.isOpened():
            self.initialize() # Ensure cap is ready

        frame_interval = 1.0 / self.fps if self.fps > 0 else 0.04 # ~25 FPS default

        try:
            while True:
                ret, frame = self.cap.read()
                if not ret:
                    logger.info(f"End of video {self.video_path}, looping.")
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0) # Loop video
                    ret, frame = self.cap.read() # Read the first frame again
                    if not ret: # Still no frame after trying to loop
                        logger.error(f"Failed to loop video {self.video_path}. Stopping generator.")
                        break

                kpis = self.process_frame(frame)
                _, buffer = cv2.imencode('.jpg', frame)
                frame_bytes = buffer.tobytes()

                yield {"frame": frame_bytes, "kpis": kpis}
                time.sleep(frame_interval) # Control frame rate
        except Exception as e:
            logger.error(f"Error processing video frame for {self.video_path}: {e}", exc_info=True)
            # No re-raise, allow generator to stop
        finally:
            if self.cap:
                self.cap.release()
            logger.info(f"Released video capture for {self.video_path}")


    def __del__(self):
        """Cleanup resources when the object is deleted."""
        if self.cap:
            self.cap.release()


class VideoManager:
    _instance = None

    def __init__(self):
        self.video_processors: Dict[str, VideoProcessor] = {}

    @classmethod
    def get_instance(cls) -> 'VideoManager':
        if cls._instance is None:
            cls._instance = VideoManager()
        return cls._instance

    def get_processor(self, video_path: str) -> VideoProcessor:
        """Get or create a video processor for the given path"""
        if video_path not in self.video_processors:
            self.video_processors[video_path] = VideoProcessor(video_path)
        return self.video_processors[video_path]

    def cleanup(self):
        """Cleanup all video processors"""
        for processor in self.video_processors.values():
            del processor
        self.video_processors.clear()
