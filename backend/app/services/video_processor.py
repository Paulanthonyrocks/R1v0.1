import logging
import asyncio
import cv2
import numpy as np
import os
import time
from typing import Dict, AsyncGenerator, Optional, TYPE_CHECKING
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

if TYPE_CHECKING:
    from app.services.feed_manager import FeedManager

from app.database import get_database_manager 
from app.models.processed_video import ProcessedVideo

logger = logging.getLogger(__name__)

class VideoProcessor:
    COLOR_MAP = {
        "moving": (0, 255, 0),       # Green
        "stopped": (0, 0, 255),      # Red
        "speeding": (255, 0, 0),     # Blue
        "accelerating": (0, 165, 255), # Orange
        "decelerating": (0, 255, 255), # Yellow
        "lane_changing": (255, 0, 255), # Magenta
        "unknown": (128, 128, 128),  # Gray
    }

    def __init__(self, stream_id: str, feed_manager: "FeedManager", output_directory: str):
        self.stream_id = stream_id
        self.use_shm = False # Final Force Disable for Colab
        self.feed_manager = feed_manager
        self.output_directory = output_directory
        
        # State
        self._is_recording: bool = False
        self._draw_overlays_enabled: bool = True # Configuration default
        
        # Recording internals
        self._video_writer: Optional[cv2.VideoWriter] = None
        self._output_path: Optional[str] = None
        self._tmp_output_path: Optional[str] = None
        self._recording_start_time: Optional[float] = None
        self._frame_rate: float = 10.0
        self._frame_size: Optional[tuple] = None
        
        # Executor for CPU-bound OpenCV tasks (Decoding/Encoding/Writing)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"VP-{stream_id}")
        
        logger.info(f"VideoProcessor initialized for stream_id: {self.stream_id}")

    async def start_recording(self, output_filename: str, frame_rate: float):
        if self._is_recording:
            logger.warning(f"Recording already in progress for stream {self.stream_id}")
            return False

        self._output_path = os.path.join(self.output_directory, output_filename)
        base, ext = os.path.splitext(self._output_path)
        self._tmp_output_path = f"{base}.tmp{ext}"
        
        try:
            os.makedirs(os.path.dirname(self._output_path), exist_ok=True)
            if os.path.exists(self._tmp_output_path):
                os.remove(self._tmp_output_path)
        except Exception as e:
            logger.error(f"[{self.stream_id}] File system error starting record: {e}")
            return False

        self._frame_rate = frame_rate
        self._is_recording = True
        self._recording_start_time = time.time()
        logger.info(f"Started recording for {self.stream_id} to {self._output_path}")
        return True

    async def stop_recording(self):
        if not self._is_recording:
            return False

        logger.info(f"Stopping recording for {self.stream_id}...")
        self._is_recording = False # Flag stop immediately to prevent new frames entering writer

        # Release writer in executor to ensure buffers are flushed without blocking loop
        if self._video_writer:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._video_writer.release)
            self._video_writer = None

        # Rename tmp to actual
        try:
            if self._tmp_output_path and os.path.exists(self._tmp_output_path):
                os.replace(self._tmp_output_path, self._output_path)
        except Exception as e:
            logger.error(f"[{self.stream_id}] Failed to finalize video file: {e}", exc_info=True)

        # Save Metadata to DB
        if self._output_path and self._recording_start_time:
            try:
                db_manager = get_database_manager()
                end_time = datetime.now(timezone.utc)
                duration = end_time.timestamp() - self._recording_start_time
                
                processed_video_entry = ProcessedVideo(
                    stream_id=self.stream_id,
                    file_path=self._output_path,
                    start_time=datetime.fromtimestamp(self._recording_start_time, tz=timezone.utc),
                    end_time=end_time,
                    duration=duration,
                )
                await db_manager.save_processed_video_metadata(processed_video_entry)
            except Exception as e:
                logger.error(f"Failed to save metadata: {e}")

        self._output_path = None
        self._recording_start_time = None
        return True

    def _process_frame_sync(self, raw_frame_bytes: bytes, kpis: Dict) -> Optional[bytes]:
        """
        Synchronous function to decode, draw, write to file, and re-encode.
        Run this in a separate thread.
        """
        # Check if we need to process at all.
        # We process if: 1. Recording OR 2. Overlays are enabled and there is data to draw
        has_detections = bool(kpis.get("detections"))
        should_process = self._is_recording or (self._draw_overlays_enabled and has_detections)

        if not should_process:
            return raw_frame_bytes

        try:
            # Decode
            np_arr = np.frombuffer(raw_frame_bytes, np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

            if frame is None:
                return raw_frame_bytes

            # Ensure consistent colorspace (OpenCV uses BGR)
            if len(frame.shape) == 2:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            elif frame.shape[2] == 4:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

            # Draw Overlays (if enabled)
            if self._draw_overlays_enabled:
                self._draw_overlays(frame, kpis)

            # Write to disk (if recording)
            if self._is_recording:
                self._handle_recording(frame)

            # Re-encode for Streaming
            # Only re-encode if we actually modified the frame (drew overlays)
            if self._draw_overlays_enabled and has_detections:
                ret, jpeg_frame = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                if ret:
                    return jpeg_frame.tobytes()
            
            # If we recorded but didn't draw overlays, or drawing failed, return original
            return raw_frame_bytes

        except Exception as e:
            logger.error(f"Error in sync frame processing: {e}")
            return raw_frame_bytes

    def _handle_recording(self, frame: np.ndarray):
        """Helper to initialize writer and write frame."""
        try:
            if self._video_writer is None:
                h, w = frame.shape[:2]
                self._frame_size = (w, h)
                # mp4v is generally safe for filesystem recording
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                self._video_writer = cv2.VideoWriter(
                    self._tmp_output_path, fourcc, self._frame_rate, self._frame_size
                )
            
            if self._video_writer.isOpened():
                # Safety resize if resolution changes mid-stream
                if (frame.shape[1], frame.shape[0]) != self._frame_size:
                    frame = cv2.resize(frame, self._frame_size)
                self._video_writer.write(frame)
        except Exception as e:
            logger.error(f"Error writing frame to disk: {e}")

    def _draw_overlays(self, frame: np.ndarray, kpis: Dict):
        detections = kpis.get("detections", [])
        if not detections:
            return

        for det in detections:
            bbox = det.get("bbox")
            if bbox is None: continue
            
            try:
                x1, y1, x2, y2 = map(int, bbox)
            except (ValueError, TypeError): continue

            label = det.get("class_name") or det.get("label") or "vehicle"
            conf = det.get("confidence") or det.get("score") or 0.0
            behavior = det.get("behavior") or "unknown"
            
            # Map behavior to color
            color = self.COLOR_MAP.get(behavior, self.COLOR_MAP["unknown"])

            # Draw Box
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            
            # Draw Label
            label_text = f"{label} {conf:.2f}"
            (w, h), _ = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            
            # Ensure label doesn't go off screen
            y1_label = max(y1, h + 5)
            
            cv2.rectangle(frame, (x1, y1_label - h - 5), (x1 + w, y1_label + 5), color, -1)
            cv2.putText(frame, label_text, (x1, y1_label), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,0), 1)

    async def get_frame_generator(self) -> AsyncGenerator[Dict, None]:
        """
        Yields frames to the API for streaming. 
        Uses a dedicated thread for image processing to avoid blocking the Event Loop.
        """
        frame_queue = await self.feed_manager.subscribe_to_frames(self.stream_id)
        loop = asyncio.get_running_loop()
        
        try:
            while True:
                data = await frame_queue.get()
                raw_frame_bytes = data.get("frame")
                if not raw_frame_bytes: continue
                
                kpis = data.get("metrics", {})

                # Offload CPU-heavy decoding/drawing to thread
                processed_jpeg_bytes = await loop.run_in_executor(
                    self._executor, 
                    self._process_frame_sync, 
                    raw_frame_bytes, 
                    kpis
                )

                if processed_jpeg_bytes:
                    yield {"frame": processed_jpeg_bytes, "kpis": kpis}

        except asyncio.CancelledError:
            logger.debug(f"Frame generator cancelled for {self.stream_id}")
        except Exception as e:
            logger.error(f"Error in generator: {e}", exc_info=True)
        finally:
            await self.feed_manager.unsubscribe_from_frames(self.stream_id, frame_queue)


class VideoManager:
    _instance = None
    _lock = asyncio.Lock()

    def __init__(self):
        self.video_processors: Dict[str, VideoProcessor] = {}
        self.output_directory: Optional[str] = None

    @classmethod
    def get_instance(cls, output_directory: Optional[str] = None) -> "VideoManager":
        if cls._instance is None:
            cls._instance = VideoManager()
        
        if output_directory:
            cls._instance.output_directory = output_directory
            
        return cls._instance

    def get_processor(self, stream_id: str, feed_manager: "FeedManager") -> VideoProcessor:
        if not self.output_directory:
             # Fallback default
             self.output_directory = "backend/data/recordings"
             
        if stream_id not in self.video_processors:
            self.video_processors[stream_id] = VideoProcessor(
                stream_id, feed_manager, self.output_directory
            )
        return self.video_processors[stream_id]
    
    async def remove_processor(self, stream_id: str):
        """Cleanly remove a processor to free memory/threads."""
        if stream_id in self.video_processors:
            proc = self.video_processors.pop(stream_id)
            if proc._is_recording:
                await proc.stop_recording()
            proc._executor.shutdown(wait=False)
            logger.info(f"Removed VideoProcessor for {stream_id}")

    async def cleanup(self):
        logger.info("Cleaning up VideoManager...")
        tasks = []
        for pid, processor in list(self.video_processors.items()):
            if processor._is_recording:
                tasks.append(processor.stop_recording())
            processor._executor.shutdown(wait=False)
        
        if tasks:
            await asyncio.gather(*tasks)
        self.video_processors.clear()
        logger.info("VideoManager cleanup complete.")
