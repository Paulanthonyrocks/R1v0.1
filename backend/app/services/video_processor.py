import logging
import asyncio
import cv2
import numpy as np
import os
import time
from typing import Dict, AsyncGenerator, Optional
from datetime import datetime
import base64
import json
from concurrent.futures import ThreadPoolExecutor

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
        self._tmp_output_path: Optional[str] = None
        self._recording_start_time: Optional[float] = None
        self._frame_rate: float = 10.0
        self._frame_size: Optional[tuple] = None
        
        # Executor for CPU-bound OpenCV tasks
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"VP-{stream_id}")
        
        logger.info(f"VideoProcessor initialized for stream_id: {self.stream_id}")

    async def start_recording(self, output_filename: str, frame_rate: float):
        if self._is_recording:
            logger.warning(f"Recording already in progress for stream {self.stream_id}")
            return False

        self._output_path = os.path.join(self.output_directory, output_filename)
        base, ext = os.path.splitext(self._output_path)
        self._tmp_output_path = f"{base}.tmp{ext}"
        os.makedirs(os.path.dirname(self._output_path), exist_ok=True)
        
        try:
            if os.path.exists(self._tmp_output_path):
                os.remove(self._tmp_output_path)
        except Exception as e:
            logger.warning(f"[{self.stream_id}] Unable to remove stale tmp file: {e}")

        self._frame_rate = frame_rate
        self._is_recording = True
        self._recording_start_time = time.time()
        logger.info(f"Started recording for {self.stream_id} to {self._output_path}")
        return True

    async def stop_recording(self):
        if not self._is_recording:
            return False

        # Release writer in executor to ensure buffers are flushed without blocking loop
        if self._video_writer:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._video_writer.release)
            self._video_writer = None
            logger.info(f"Stopped recording for {self.stream_id}.")

        try:
            if self._tmp_output_path and os.path.exists(self._tmp_output_path):
                os.replace(self._tmp_output_path, self._output_path)
        except Exception as e:
            logger.error(f"[{self.stream_id}] Failed to finalize video file: {e}", exc_info=True)

        if self._output_path and self._recording_start_time:
            try:
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
            except Exception as e:
                logger.error(f"Failed to save metadata: {e}")

        self._is_recording = False
        self._output_path = None
        self._recording_start_time = None
        return True

    def _process_frame_sync(self, raw_frame_bytes: bytes, kpis: Dict) -> Optional[bytes]:
        """
        Synchronous function to decode, draw, write to file, and re-encode.
        Run this in a separate thread.
        """
        try:
            np_arr = np.frombuffer(raw_frame_bytes, np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

            if frame is None:
                return raw_frame_bytes # Fallback

            # Ensure consistent format
            if len(frame.shape) == 2:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            elif frame.shape[2] == 4:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

            # Draw overlays
            # self._draw_overlays(frame, kpis)

            # Handle Recording
            if self._is_recording:
                self._handle_recording(frame)

            # Re-encode for streaming
            # Quality 85 gives good balance of speed/quality
            ret, jpeg_frame = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            if ret:
                return jpeg_frame.tobytes()
            return raw_frame_bytes
        except Exception as e:
            logger.error(f"Error in sync frame processing: {e}")
            return raw_frame_bytes

    def _handle_recording(self, frame: np.ndarray):
        """Helper to initialize writer and write frame."""
        if self._video_writer is None:
            self._frame_size = (frame.shape[1], frame.shape[0])
            # Use mp4v as it's widely supported and container-friendly
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            self._video_writer = cv2.VideoWriter(
                self._tmp_output_path, fourcc, self._frame_rate, self._frame_size
            )
        
        if self._video_writer.isOpened():
            if (frame.shape[1], frame.shape[0]) != self._frame_size:
                frame = cv2.resize(frame, self._frame_size)
            self._video_writer.write(frame)

    async def get_frame_generator(self) -> AsyncGenerator[Dict, None]:
        frame_queue = await self.feed_manager.subscribe_to_frames(self.stream_id)
        loop = asyncio.get_running_loop()
        
        try:
            while True:
                data = await frame_queue.get()
                raw_frame_bytes = data["frame"]
                kpis = data.get("metrics", {})
                vehicles_data = data.get("vehicles", [])

                # Run heavy processing in thread pool
                processed_jpeg_bytes = await loop.run_in_executor(
                    self._executor, 
                    self._process_frame_sync, 
                    raw_frame_bytes, 
                    kpis
                )

                if processed_jpeg_bytes:
                    # Use already serialized vehicles from FeedManager
                    vehicles = vehicles_data

                    # Broadcast via WebSocket
                    jpeg_base64 = base64.b64encode(processed_jpeg_bytes).decode('utf-8')
                    payload = {
                        "feed_id": self.stream_id,
                        "frame": jpeg_base64,
                        "kpis": kpis,
                        "vehicles": vehicles
                    }
                    
                    # Fire and forget broadcast to avoid slowing down generator
                    asyncio.create_task(video_ws_manager.broadcast(
                        self.stream_id, {
                            "type": "video_frame",
                            "data": payload
                        }
                    ))
                    
                    yield {"frame": processed_jpeg_bytes, "kpis": kpis}

        except asyncio.CancelledError:
            logger.debug(f"Frame generator cancelled for {self.stream_id}")
        except Exception as e:
            logger.error(f"Error in generator: {e}", exc_info=True)
        finally:
            await self.feed_manager.unsubscribe_from_frames(self.stream_id, frame_queue)
            # We don't shutdown the executor here because the processor might be reused
            # Shutdown happens in VideoManager.cleanup or explicit removal

    def _draw_overlays(self, frame: np.ndarray, kpis: Dict):
        detections = kpis.get("detections")
        if not detections:
            return

        for det in detections:
            # Check for numpy array from some detection pipelines
            bbox = det.get("bbox")
            if bbox is None: continue
            
            # Safe unpacking
            try:
                x1, y1, x2, y2 = map(int, bbox)
            except (ValueError, TypeError):
                continue

            label = det.get("label", "Object")
            conf = det.get("confidence", 0.0)
            behavior = det.get("behavior", "unknown")
            color = self.COLOR_MAP.get(behavior, (128, 128, 128))

            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            
            # Simple label background for readability
            label_text = f"{label} {conf:.2f}"
            (w, h), _ = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(frame, (x1, y1 - 20), (x1 + w, y1), color, -1)
            cv2.putText(frame, label_text, (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,0), 1)

class VideoManager:
    _instance = None

    def __init__(self):
        self.video_processors: Dict[str, VideoProcessor] = {}
        self.output_directory: Optional[str] = None

    @classmethod
    def get_instance(cls, output_directory: Optional[str] = None) -> "VideoManager":
        if cls._instance is None:
            cls._instance = VideoManager()
            if output_directory:
                cls._instance.output_directory = output_directory
        elif output_directory:
             # Allow updating directory if not set
             if not cls._instance.output_directory:
                 cls._instance.output_directory = output_directory
        
        if not cls._instance.output_directory:
             raise ValueError("output_directory must be provided")
             
        return cls._instance

    def get_processor(self, stream_id: str, feed_manager: FeedManager) -> VideoProcessor:
        if stream_id not in self.video_processors:
            self.video_processors[stream_id] = VideoProcessor(
                stream_id, feed_manager, self.output_directory
            )
        return self.video_processors[stream_id]
    
    def remove_processor(self, stream_id: str):
        """Cleanly remove a processor to free memory/threads."""
        if stream_id in self.video_processors:
            proc = self.video_processors.pop(stream_id)
            proc._executor.shutdown(wait=False)

    async def cleanup(self):
        for pid, processor in list(self.video_processors.items()):
            if processor._is_recording:
                await processor.stop_recording()
            processor._executor.shutdown(wait=True)
        self.video_processors.clear()
        logger.info("VideoManager cleanup complete.")