import cv2
import numpy as np
from pathlib import Path
import logging
from typing import Dict, Any, Generator, Optional
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
        """Initialize the video capture"""
        if not self.video_path.exists():
            raise FileNotFoundError(f"Video file not found at {self.video_path}")
        
        self.cap = cv2.VideoCapture(str(self.video_path))
        if not self.cap.isOpened():
            raise RuntimeError(f"Failed to open video file: {self.video_path}")
            
        self.frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = int(self.cap.get(cv2.CAP_PROP_FPS))
        logger.info(f"Initialized video processor for {self.video_path}")
        logger.info(f"Frame count: {self.frame_count}, FPS: {self.fps}")
    
    def process_frame(self, frame: np.ndarray) -> Dict[str, Any]:
        """
        Process a single frame and extract KPIs
        Returns a dictionary of KPIs
        """
        # Convert frame to grayscale for processing
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # Apply background subtraction or other motion detection
        # This is a simple example - you might want to use more sophisticated methods
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        _, thresh = cv2.threshold(blur, 60, 255, cv2.THRESH_BINARY)
        
        # Find contours to detect vehicles
        contours, _ = cv2.findContours(thresh, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        
        # Count vehicles (basic implementation - can be improved)
        vehicle_count = len([c for c in contours if cv2.contourArea(c) > 500])
        
        # Calculate average motion (basic implementation)
        motion_level = np.mean(thresh) / 255.0
        
        return {
            "timestamp": datetime.now().isoformat(),
            "vehicle_count": vehicle_count,
            "motion_level": float(motion_level),
            "frame_number": self.frame_count
        }
    
    def get_frame_generator(self) -> Generator[Dict[str, Any], None, None]:
        """
        Generator that yields processed frames and their KPIs
        """
        if not self.cap or not self.cap.isOpened():
            self.initialize()
            
        try:
            while True:
                ret, frame = self.cap.read()
                if not ret:
                    # If we reach the end, loop back to the beginning
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                    
                # Process the frame and get KPIs
                kpis = self.process_frame(frame)
                
                # Encode frame to JPEG for streaming
                _, buffer = cv2.imencode('.jpg', frame)
                frame_bytes = buffer.tobytes()
                
                yield {
                    "frame": frame_bytes,
                    "kpis": kpis
                }
                
                # Control frame rate
                time.sleep(1/self.fps)
                
        except Exception as e:
            logger.error(f"Error processing video frame: {e}")
            raise
            
    def __del__(self):
        """Cleanup resources"""
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