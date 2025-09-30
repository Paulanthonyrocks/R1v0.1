import logging
import asyncio
import cv2
import numpy as np
import os
import time
from typing import Dict, AsyncGenerator, Optional
from datetime import datetime

from app.services.video_ws_manager import video_ws_manager
from app.services.feed_manager import FeedManager
from app.database import get_database_manager
from app.models.processed_video import ProcessedVideo

logger = logging.getLogger(__name__)


class VideoProcessor:
    COLOR_MAP = {
        "moving": (0, 255, 0),  # Green
        "stopped": (0, 0, 255),  # Red
        "speeding": (255, 0, 0),  # Blue
        "accelerating": (255, 255, 0),  # Yellow
        "decelerating": (0, 255, 255),  # Cyan
        "lane_changing": (255, 0, 255),  # Magenta
        "unknown": (128, 128, 128),  # Gray
    }

    def __init__(self, stream_id: str, feed_manager: FeedManager, output_directory: str):
        self.stream_id = stream_id
        self.feed_manager = feed_manager
        self.output_directory = output_directory
        self._is_recording: bool = False
        self._video_writer: Optional[cv2.VideoWriter] = None
        self._output_path: Optional[str] = None
        self._recording_start_time: Optional[float] = None
        self._frame_rate: float = 10.0
        self._frame_size: tuple = (1280, 720)
        logger.info(f"VideoProcessor initialized for stream_id: {self.stream_id}")

    async def start_recording(
        self, output_filename: str, frame_rate: float, frame_size: tuple
    ):
        if self._is_recording:
            logger.warning(f"Recording already in progress for stream {self.stream_id}")
            return False

        self._output_path = os.path.join(self.output_directory, output_filename)
        os.makedirs(os.path.dirname(self._output_path), exist_ok=True)

        self._frame_rate = frame_rate
        self._frame_size = frame_size

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self._video_writer = cv2.VideoWriter(
            self._output_path, fourcc, self._frame_rate, self._frame_size
        )

        if not self._video_writer.isOpened():
            logger.error(f"Could not open video writer for {self._output_path}")
            self._video_writer = None
            return False

        self._is_recording = True
        self._recording_start_time = time.time()
        logger.info(
            f"Started recording for stream {self.stream_id} to {self._output_path}"
        )
        return True

    async def stop_recording(self):
        if not self._is_recording or not self._video_writer:
            logger.warning(f"No active recording to stop for stream {self.stream_id}")
            return False

        self._video_writer.release()
        self._video_writer = None
        logger.info(f"Stopped recording for stream {self.stream_id}. Video saved to {self._output_path}")

        if self._output_path and self._recording_start_time:
            db_manager = get_database_manager()
            end_time = datetime.now()
            duration = end_time.timestamp() - self._recording_start_time
            processed_video_entry = ProcessedVideo(
                stream_id=self.stream_id,
                file_path=self._output_path,
                start_time=datetime.fromtimestamp(self._recording_start_time),
                end_time=end_time,
                duration=duration,
            )
            await db_manager.save_processed_video_metadata(processed_video_entry)
            logger.info(f"Saved processed video metadata for {self.stream_id}")

        self._is_recording = False
        self._output_path = None
        self._recording_start_time = None
        return True

    async def get_frame_generator(self) -> AsyncGenerator[Dict, None]:
        """
        Generator that yields dictionaries of raw frames and KPIs.
        If recording is active, it also processes frames with overlays and saves them.
        """
        while True:
            try:
                feed_entry = await self.feed_manager.get_feed_entry(self.stream_id)
                if not feed_entry or "latest_frame_bytes" not in feed_entry or "latest_metrics" not in feed_entry:
                    logger.debug(f"No new frame or metrics for {self.stream_id}, waiting.")
                    await asyncio.sleep(0.01) # Prevent busy-waiting
                    continue

                raw_frame_bytes = feed_entry["latest_frame_bytes"]
                kpis = feed_entry["latest_metrics"]

                if self._is_recording and self._video_writer:
                    np_arr = np.frombuffer(raw_frame_bytes, np.uint8)
                    frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

                    if frame is not None:
                        if frame.shape[1] != self._frame_size[0] or frame.shape[0] != self._frame_size[1]:
                            frame = cv2.resize(frame, self._frame_size)
                        
                        self._draw_overlays(frame, kpis)
                        self._video_writer.write(frame)
                    else:
                        logger.warning(f"Failed to decode frame for recording on stream {self.stream_id}.")

                yield {"frame": raw_frame_bytes, "kpis": kpis}
                await asyncio.sleep(0.01) # Yield control to the event loop

            except asyncio.CancelledError:
                logger.info(f"Frame generator for stream {self.stream_id} cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in frame generator for {self.stream_id}: {e}", exc_info=True)
                await asyncio.sleep(1) # Wait a bit before retrying on error
    
    def _draw_overlays(self, frame: np.ndarray, kpis: Dict):
        """Helper to draw overlays on a frame."""
        if "detections" not in kpis:
            return

        for det in kpis["detections"]:
            bbox = det.get("bbox")
            if not bbox:
                continue

            label = det.get("label", "Object")
            confidence = det.get("confidence", 0.0)
            speed = det.get("speed")
            behavior = det.get("behavior", "unknown")
            color = self.COLOR_MAP.get(behavior, (128, 128, 128))

            x1, y1, x2, y2 = map(int, bbox)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            
            text = f"{label} ({confidence:.2f})"
            if speed is not None:
                text += f" {speed:.1f}km/h"
            
            cv2.putText(frame, text, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)


class VideoManager:
    _instance = None

    def __init__(self):
        self.video_processors: Dict[str, VideoProcessor] = {}

    @classmethod
    def get_instance(cls, output_directory: Optional[str] = None) -> "VideoManager":
        if cls._instance is None:
            if not output_directory:
                raise ValueError("output_directory must be provided for the first call to get_instance")
            cls._instance = VideoManager()
            cls._instance.output_directory = output_directory
        return cls._instance

    def get_processor(
        self, stream_id: str, feed_manager: FeedManager
    ) -> VideoProcessor:
        if stream_id not in self.video_processors:
            self.video_processors[stream_id] = VideoProcessor(
                stream_id, feed_manager, self.output_directory
            )
        return self.video_processors[stream_id]

    async def cleanup(self):
        """Stops all active recordings and cleans up processors."""
        for processor in self.video_processors.values():
            if processor._is_recording:
                await processor.stop_recording()
        self.video_processors.clear()
        logger.info("All video processors cleaned up.")
