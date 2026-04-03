import asyncio
import time
import logging
from typing import Optional
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
        self._start_time = time.time()
        self._last_frame: Optional[VideoFrame] = None

    async def recv(self):
        """
        Extracts the next frame from the feed's broadcast queue.
        Calculates presentation timestamps (pts) for smooth playback.
        """
        try:
            # We want to maintain a decent framerate. Wait up to 0.5s for a new frame,
            # otherwise just return the last frame to keep the WebRTC connection alive.
            try:
                frame_data = await asyncio.wait_for(self.frame_queue.get(), timeout=0.1)
                
                # frame_data is expected to be raw rgb/bgr numpy array 
                # OR decoded cv2 frames depending on what we push to the queue.
                # Assuming the FeedManager pushes decoded BGR numpy arrays for WebRTC hooks.
                
                # If frame_data is a numpy array:
                import numpy as np
                import av
                import cv2
                
                if isinstance(frame_data, np.ndarray):
                    # FeedManager now provides pre-converted RGB frames for WebRTC
                    # to avoid redundant conversions for every peer.
                    # We just need to check if we need to convert or if it's already RGB.
                    # In our optimized pipeline, it's already RGB.
                    av_frame = av.VideoFrame.from_ndarray(frame_data, format='rgb24')
                else:
                    # Fallback for legacy byte sources
                    nparr = np.frombuffer(frame_data, np.uint8)
                    bgr_frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    rgb_frame = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
                    av_frame = av.VideoFrame.from_ndarray(rgb_frame, format='rgb24')

                # Calculate relative timestamps for PyAV
                pts, time_base = await self.next_timestamp()
                av_frame.pts = pts
                av_frame.time_base = time_base
                
                self._last_frame = av_frame
                return av_frame
                
            except asyncio.TimeoutError:
                if self._last_frame:
                    # Keep-alive drop-in
                    pts, time_base = await self.next_timestamp()
                    self._last_frame.pts = pts
                    self._last_frame.time_base = time_base
                    return self._last_frame
                else:
                    # Provide a black frame if we have absolutely nothing
                    import numpy as np
                    import av
                    black = np.zeros((480, 640, 3), dtype=np.uint8)
                    av_frame = av.VideoFrame.from_ndarray(black, format='rgb24')
                    pts, time_base = await self.next_timestamp()
                    av_frame.pts = pts
                    av_frame.time_base = time_base
                    return av_frame

        except Exception as e:
            logger.error(f"[FeedStreamTrack {self.feed_id}] Frame error: {e}")
            raise
