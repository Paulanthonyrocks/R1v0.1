import os
import cv2
import logging
import time
import queue
import threading
from typing import Dict, Any, Optional
from multiprocessing import Queue as MPQueue, Event

from ..utils.video import FrameReader

logger = logging.getLogger("Ingestion")

def ingestion_worker(
    video_path: str,
    feed_id: str,
    central_input_queue: MPQueue,
    stop_event: Event,
    config: Dict[str, Any],
    is_looped: bool = False
):
    """
    Lightweight process that only captures frames and pushes them to a central queue.
    """
    pid = os.getpid()
    logger.info(f"Ingestion process {pid} started for {feed_id}")
    
    # Pre-extract config
    video_processing_cfg = config.get("video_processing", {})
    target_fps = video_processing_cfg.get("target_fps", 15)
    
    # Stream resolution for the raw frame transmission
    video_out_cfg = config.get("video_output", {})
    stream_res = tuple(video_out_cfg.get("stream_resolution", (640, 480)))
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), 80]

    reader = None
    try:
        source = video_path
        if isinstance(video_path, str) and video_path.startswith("webcam:"):
            try: 
                source = int(video_path.split(":")[1])
            except (IndexError, ValueError): 
                source = 0
        
        reader = FrameReader(
            source, 
            max_queue_size=50,
            is_looped=is_looped,
            target_fps=target_fps
        )
        
        if not reader.start():
            logger.error(f"[{feed_id}] FrameReader failed to start.")
            return

        while not stop_event.is_set():
            result = reader.read()
            if result is None:
                if reader.end_of_video:
                    logger.info(f"[{feed_id}] End of stream.")
                    break
                time.sleep(0.01)
                continue
            
            frame_index, frame = result
            
            try:
                # Resize and encode to bytes for efficient queue transport
                resized = cv2.resize(frame, stream_res, interpolation=cv2.INTER_LINEAR)
                success, buffer = cv2.imencode(".jpg", resized, encode_params)
                
                if success:
                    # Put data in the central queue
                    # Format: (feed_id, frame_index, frame_bytes, metadata)
                    central_input_queue.put((feed_id, frame_index, buffer.tobytes(), time.time()), timeout=1.0)
                
            except queue.Full:
                # If the AI is slow, we drop frames at ingestion to maintain real-time
                pass
            except Exception as e:
                logger.error(f"[{feed_id}] Ingestion error: {e}")

    except Exception as e:
        logger.error(f"[{feed_id}] FATAL Ingestion error: {e}")
    finally:
        if reader:
            reader.stop()
        logger.info(f"[{feed_id}] Ingestion process {pid} terminated.")
