import logging
import os
from typing import Dict, Any, Optional, AsyncGenerator, Tuple
from threading import Lock
from multiprocessing import Queue as MPQueue
import asyncio
import cv2

from app.core.core_module import CoreModule
from app.config import get_current_config
from app.utils.video import FrameReader

logger = logging.getLogger(__name__)


async def process_video_feed(video_path: str, is_looped: bool = True) -> AsyncGenerator[Dict[str, Any], None]:
    video_manager = VideoManager.get_instance()
    processor, frame_reader = video_manager.get_video_pipeline(video_path, is_looped=is_looped)

    if not frame_reader or not frame_reader.isOpened:
        logger.error(f"Failed to get a valid FrameReader for {video_path}")
        return

    try:
        while True:
            frame_data = await asyncio.to_thread(frame_reader.get_frame)
            if frame_data is None:
                if not is_looped:
                    break
                await asyncio.sleep(0.01)
                continue

            frame = frame_data["frame"]
            frame_index = frame_data["frame_index"]

            tracked_vehicles = processor.detect_and_track(frame, frame_index)

            ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if not ret:
                continue

            frame_bytes = buffer.tobytes()

            kpis = {
                "tracked_vehicles": len(tracked_vehicles),
                "vehicles": [processor._serialize_track_data(v) for v in tracked_vehicles.values()]
            }

            yield {"frame": frame_bytes, "kpis": kpis}
            await asyncio.sleep(0.01)
    except Exception as e:
        logger.error(f"Error processing video feed for {video_path}: {e}", exc_info=True)
    finally:
        logger.info(f"Finished processing video feed for {video_path}.")
        # The FrameReader is managed by the VideoManager, so we don't stop it here.


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
            self.processors: Dict[str, Any] = {}
            self.readers: Dict[str, FrameReader] = {}
            self._initialized = True
            logger.info("VideoManager initialized.")

    @classmethod
    def get_instance(cls) -> 'VideoManager':
        return cls()

    def get_video_pipeline(self, video_path: str, is_looped: bool = True) -> Tuple[Any, Optional[FrameReader]]:
        logger.info(f"Getting video pipeline for video_path: {video_path}")
        with self._lock:
            processor = self.processors.get(video_path)
            if processor is None:
                config = get_current_config()
                model_path = config.get("vehicle_detection", {}).get("model_path")
                fps = config.get("fps")
                gemini_api_key = os.environ.get("GEMINI_API_KEY")
                db_queue = MPQueue()
                processor = CoreModule(
                    feed_id=video_path,
                    model_path=model_path,
                    config=config,
                    fps=fps,
                    db_queue=db_queue,
                    gemini_api_key=gemini_api_key,
                )
                self.processors[video_path] = processor
                logger.info(f"Added processor for {video_path}.")

            reader = self.readers.get(video_path)
            if reader is None or not reader.isOpened:
                config = get_current_config()
                target_fps = config.get("video_processing", {}).get("target_fps", 10)
                reader = FrameReader(video_path, is_looped=is_looped, target_fps=target_fps)
                if not reader.start():
                    logger.error(f"Failed to start FrameReader for {video_path}")
                    self.readers[video_path] = None
                    return processor, None
                self.readers[video_path] = reader
                logger.info(f"Started and added FrameReader for {video_path}.")

            return processor, reader

    def remove_processor(self, video_path: str):
        with self._lock:
            if video_path in self.processors:
                del self.processors[video_path]
                logger.info(f"Removed processor for {video_path}.")
            if video_path in self.readers:
                reader = self.readers.pop(video_path)
                if reader:
                    reader.stop()
                logger.info(f"Stopped and removed FrameReader for {video_path}.")
