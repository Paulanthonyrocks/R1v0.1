import logging
import asyncio
import cv2
import numpy as np
import os
import time
from typing import Dict, Generator, Optional
from datetime import datetime

from app.services.video_ws_manager import video_ws_manager
from app.services.feed_manager import FeedManager
from app.database import get_database_manager
from app.models.processed_video import ProcessedVideo

logger = logging.getLogger(__name__)


class VideoProcessor:
    def __init__(self, stream_id: str, feed_manager: FeedManager):
        self.stream_id = stream_id
        self.feed_manager = feed_manager
        self._is_recording: bool = False
        self._video_writer: Optional[cv2.VideoWriter] = None
        self._output_path: Optional[str] = None
        self._recording_start_time: Optional[float] = None
        self._frame_rate: float = 10.0  # Default frame rate for output video
        self._frame_size: tuple = (1280, 720)  # Default frame size (width, height)
        logger.info(f"VideoProcessor: Initializing for stream_id: {self.stream_id}")

    async def start_recording(
        self, output_filename: str, frame_rate: float, frame_size: tuple
    ):
        if self._is_recording:
            logger.warning(f"Recording already in progress for stream {self.stream_id}")
            return False

        self._output_path = os.path.join(
            "backend", "data", "processed_videos", output_filename
        )
        os.makedirs(os.path.dirname(self._output_path), exist_ok=True)

        self._frame_rate = frame_rate
        self._frame_size = frame_size

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")  # Codec for .mp4 files
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
            f"Started recording processed video for stream {self.stream_id} to {self._output_path}"
        )
        return True

    async def stop_recording(self):
        if not self._is_recording:
            logger.warning(f"No recording in progress for stream {self.stream_id}")
            return False

        if self._video_writer:
            self._video_writer.release()
            self._video_writer = None
            logger.info(
                f"Stopped recording for stream {self.stream_id}. Video saved to {self._output_path}"
            )

            # Save metadata to database
            if self._output_path and self._recording_start_time:
                db_manager = get_database_manager()
                processed_video_entry = ProcessedVideo(
                    stream_id=self.stream_id,
                    file_path=self._output_path,
                    start_time=datetime.fromtimestamp(self._recording_start_time),
                    end_time=datetime.now(),
                    duration=datetime.now().timestamp() - self._recording_start_time,
                )
                await db_manager.save_processed_video_metadata(processed_video_entry)
                logger.info(f"Saved processed video metadata for {self.stream_id}")

        self._is_recording = False
        self._output_path = None
        self._recording_start_time = None
        return True

    async def get_frame_generator(self) -> Generator[bytes, None, None]:
        """
        Generator that yields raw frames and broadcasts KPIs over WebSocket.
        If recording is active, it also processes frames with overlays and saves them.
        """
        try:
            while True:
                feed_entry = await self.feed_manager.get_feed_entry(self.stream_id)
                if (
                    feed_entry
                    and feed_entry.get("latest_frame_bytes")
                    and feed_entry.get("latest_metrics")
                ):
                    raw_frame_bytes = feed_entry["latest_frame_bytes"]
                    kpis = feed_entry["latest_metrics"]

                    # Broadcast KPIs over WebSocket (for frontend display)
                    await video_ws_manager.broadcast_kpis(self.stream_id, kpis)

                    if self._is_recording and self._video_writer:
                        # Decode raw frame bytes to numpy array for OpenCV processing
                        np_arr = np.frombuffer(raw_frame_bytes, np.uint8)
                        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

                        if frame is not None:
                            # Resize frame to match writer's expected size if necessary
                            if (
                                frame.shape[1] != self._frame_size[0]
                                or frame.shape[0] != self._frame_size[1]
                            ):
                                frame = cv2.resize(frame, self._frame_size)

                            # Draw overlays (example: bounding boxes, vehicle IDs, speed)
                            # This part needs actual implementation based on KPI structure
                            # For now, a placeholder:
                            if "detections" in kpis:
                                for det in kpis["detections"]:
                                    bbox = det["bbox"]  # Assuming [x1, y1, x2, y2]
                                    label = det.get("label", "Object")
                                    confidence = det.get("confidence", 0.0)
                                    speed = det.get("speed", None)
                                    behavior = det.get("behavior", "unknown")

                                    # Define colors based on behavior
                                    color_map = {
                                        "moving": (0, 255, 0),  # Green
                                        "stopped": (0, 0, 255),  # Red
                                        "speeding": (255, 0, 0),  # Blue
                                        "accelerating": (255, 255, 0),  # Yellow
                                        "decelerating": (0, 255, 255),  # Cyan
                                        "lane_changing": (255, 0, 255),  # Magenta
                                        "unknown": (128, 128, 128),  # Gray
                                    }
                                    color = color_map.get(behavior, (128, 128, 128)) # Default to gray

                                    x1, y1, x2, y2 = map(int, bbox)
                                    cv2.rectangle(
                                        frame, (x1, y1), (x2, y2), color, 2
                                    )
                                    text = f"{label}: {confidence:.2f}"
                                    if speed is not None:
                                        text += f" Speed: {speed:.1f}km/h"
                                    cv2.putText(
                                        frame,
                                        text,
                                        (x1, y1 - 10),
                                        cv2.FONT_HERSHEY_SIMPLEX,
                                        0.5,
                                        color,
                                        2,
                                    )

                            # Write processed frame to video file
                            self._video_writer.write(frame)
                        else:
                            logger.warning(
                                f"Failed to decode raw frame for stream {self.stream_id} for recording."
                            )

                    yield raw_frame_bytes  # Yield raw frame bytes for frontend
                else:
                    logger.debug(
                        f"No new frame or metrics available for stream_id: {self.stream_id}. Waiting..."
                    )

                await asyncio.sleep(0.01)

        except Exception as e:
            logger.error(
                f"Error in video frame generator for stream_id {self.stream_id}: {e}",
                exc_info=True,
            )
            raise


class VideoManager:
    _instance = None

    def __init__(self):
        self.video_processors: Dict[str, VideoProcessor] = {}

    @classmethod
    def get_instance(cls) -> "VideoManager":
        if cls._instance is None:
            cls._instance = VideoManager()
        return cls._instance

    def get_processor(
        self, stream_id: str, feed_manager: FeedManager
    ) -> VideoProcessor:
        """Get or create a video processor for the given stream_id."""
        if stream_id not in self.video_processors:
            self.video_processors[stream_id] = VideoProcessor(stream_id, feed_manager)
        return self.video_processors[stream_id]

    async def cleanup(self):
        """Cleanup all video processors and stop any active recordings."""
        for processor in self.video_processors.values():
            if processor._is_recording:
                await processor.stop_recording()
        self.video_processors.clear()
