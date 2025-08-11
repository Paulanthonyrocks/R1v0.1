import logging
import os
from typing import Dict, Any, Optional
from threading import Lock
from multiprocessing import Queue as MPQueue

from app.core.core_module import CoreModule
from app.config import get_current_config

logger = logging.getLogger(__name__)

class VideoManager:
    _instance: Optional['VideoManager'] = None
    _lock: Lock = Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(VideoManager, cls).__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self.processors: Dict[str, Any] = {}  # Stores video processors
            self._initialized = True
            logger.info("VideoManager initialized.")

    @classmethod
    def get_instance(cls) -> 'VideoManager':
        return cls()

    def get_processor(self, video_path: str) -> Any:
        logger.info(f"Getting processor for video_path: {video_path}")
        if video_path not in self.processors:
            config = get_current_config()
            model_path = config.get("vehicle_detection", {}).get("model_path")
            fps = config.get("fps")
            gemini_api_key = os.environ.get("GEMINI_API_KEY")

            # This is a simplification. In a real app, the queue would be managed
            # by a central process and passed down.
            db_queue = MPQueue()

            processor = CoreModule(
                feed_id=video_path,
                model_path=model_path,
                config=config,
                fps=fps,
                db_queue=db_queue,
                gemini_api_key=gemini_api_key,
            )
            self.add_processor(video_path, processor)
        return self.processors[video_path]

    def add_processor(self, video_path: str, processor: Any):
        self.processors[video_path] = processor
        logger.info(f"Added processor for {video_path}.")

    def remove_processor(self, video_path: str):
        if video_path in self.processors:
            del self.processors[video_path]
            logger.info(f"Removed processor for {video_path}.")
