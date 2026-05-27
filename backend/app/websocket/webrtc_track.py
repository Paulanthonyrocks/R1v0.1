import asyncio
import time
import logging
from typing import Optional
import numpy as np
import av
import cv2
from av import VideoFrame
from aiortc import VideoStreamTrack

logger = logging.getLogger("webrtc")

class FeedStreamTrack(VideoStreamTrack):
    """
    A video stream track that reads frames from an asyncio.Queue,
    converts them from JPEG/raw bytes to PyAV frames, and pushes 
    them to the WebRTC peer.
    """

    def __init__(self, feed_id: str, frame_queue: asyncio.Queue):
        super().__init__()
        self.feed_id = feed_id
        self.frame_queue = frame_queue
        self._timestamp = 0
        self._last_frame: Optional[VideoFrame] = None

    async def recv(self):
        """
        Extracts the next frame from the feed's broadcast queue.
        Calculates presentation timestamps (pts) for smooth playback.
        """
        try:
            # If we haven't received any frames yet, block indefinitely to establish 
            # the correct resolution for the WebRTC encoder.
            if self._last_frame is None:
                frame_data = await self.frame_queue.get()
            else:
                # Once the stream is established, wait briefly for a new frame,
                # otherwise return the last one to maintain framerate.
                try:
                    frame_data = await asyncio.wait_for(self.frame_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    pts, time_base = self.next_timestamp()
                    self._last_frame.pts = pts
                    self._last_frame.time_base = time_base
                    return self._last_frame
                
            # Process frame data
            if isinstance(frame_data, np.ndarray):
                # Optimized pipeline provides RGB frames.
                av_frame = av.VideoFrame.from_ndarray(frame_data, format='rgb24')
            else:
                # Fallback for legacy byte sources.
                nparr = np.frombuffer(frame_data, np.uint8)
                bgr_frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if bgr_frame is None:
                    logger.error(f"[FeedStreamTrack {self.feed_id}] Failed to decode frame bytes")
                    # If decoding fails, we must still return something or we'll hang.
                    # Return last frame if available, otherwise we can't proceed.
                    if self._last_frame:
                        pts, time_base = self.next_timestamp()
                        self._last_frame.pts = pts
                        self._last_frame.time_base = time_base
                        return self._last_frame
                    raise RuntimeError("Initial frame decoding failed; no fallback available")

                rgb_frame = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
                av_frame = av.VideoFrame.from_ndarray(rgb_frame, format='rgb24')

            # Calculate relative timestamps for PyAV
            pts, time_base = self.next_timestamp()
            av_frame.pts = pts
            av_frame.time_base = time_base
            
            self._last_frame = av_frame
            return av_frame

        except Exception as e:
            logger.error(f"[FeedStreamTrack {self.feed_id}] Frame error: {e}")
            raise
