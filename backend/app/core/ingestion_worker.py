import base64
import os
import cv2
import logging
import time
import queue
import threading
import signal
import json
from typing import Dict, Any, Optional
from multiprocessing import Queue as MPQueue, Event

logger = logging.getLogger("Ingestion")

def ingestion_worker(
    video_path: str,
    feed_id: str,
    central_input_queue: MPQueue,
    stop_event: Event,
    config: Dict[str, Any],
    is_looped: bool = False,
    shared_skip_array: Optional[Any] = None,
    worker_idx: int = 0
):
    """Lightweight process for frame capture and resizing, using CPU."""
    from ..utils.video import FrameReader
    from ..utils.process import start_parent_monitor
    from .worker_utils import SharedFrameManager
    
    from ..config import set_config
    set_config(config)

    start_parent_monitor(stop_event)
    logger.info(f"[Ingestion-{feed_id}] Process started. PID: {os.getpid()}")
    
    try:
        if "logging" in config:
            logging.config.dictConfig(config["logging"])
        else:
            logging.basicConfig(level=logging.INFO)
    except Exception:
        logging.basicConfig(level=logging.INFO)

    def signal_handler(signum, frame):
        stop_event.set()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    frames_processed = 0
    frames_dropped = 0
    errors = 0

    video_processing_cfg = config.get("video_processing", {})
    target_fps = video_processing_cfg.get("target_fps", 15)
    perf_cfg = config.get("performance", {})
    
    # ISSUE 1 FIX: Force disable GPU for ingestion and resizing.
    # All operations in this worker will be CPU-bound.
    gpu_acceleration = False

    q_max = perf_cfg.get("queue_max_size", 50)
    use_shm = perf_cfg.get("use_shm", True)
    inference_res = video_processing_cfg.get("inference_resolution", (1280, 720))

    shm_manager = None
    if use_shm:
        try:
            num_buffers = max(20, q_max + 10)
            shm_manager = SharedFrameManager(
                name=f"shm_{feed_id}", 
                frame_shape=(inference_res[1], inference_res[0], 3), 
                num_buffers=num_buffers,
                create=True
            )
            logger.info(f"[{feed_id}] Shared memory ring buffer initialized with {num_buffers} buffers.")
        except Exception as e:
            logger.error(f"[{feed_id}] Failed to init SHM: {e}. Falling back to raw_bytes.")
            use_shm = False

    reader = None
    try:
        source = video_path
        if isinstance(video_path, str) and video_path.startswith("webcam:"):
            try: 
                source = int(video_path.split(":")[1])
            except (ValueError, IndexError): 
                source = 0
        
        reader = FrameReader(source, max_queue_size=15, is_looped=is_looped, target_fps=target_fps, gpu_acceleration=gpu_acceleration)
        
        if not reader.start(): 
            logger.error(f"[{feed_id}] FrameReader failed to start.")
            return

        processed_frames_count = 0
        
        while not stop_event.is_set():
            result = reader.read_raw()
            if result is None:
                if reader.end_of_video:
                    logger.info(f"[{feed_id}] End of video stream.")
                    break
                time.sleep(0.01)
                continue

            frame_index, frame = result
            effective_index = frame_index if frame_index >= 0 else processed_frames_count

            if shared_skip_array is not None:
                try: 
                    current_skip = shared_skip_array[worker_idx]
                    if current_skip > 0 and (effective_index % (current_skip + 1) != 0):
                        continue
                except (IndexError, TypeError): pass

            if central_input_queue.qsize() >= q_max:
                frames_dropped += 1
                continue

            try:
                # ISSUE 1 FIX: Always use OpenCV on CPU for resizing.
                resized = cv2.resize(frame, inference_res, interpolation=cv2.INTER_LINEAR)

                frame_data = {}
                if use_shm and shm_manager:
                    if not shm_manager.write_frame(effective_index, resized):
                        frames_dropped += 1
                        continue

                    frame_data = {
                        "shm_name": shm_manager.name, 
                        "shm_index": effective_index,
                        "shape": resized.shape, 
                        "dtype": str(resized.dtype),
                        "num_buffers": shm_manager.num_buffers
                    }
                else:
                    frame_data = {"raw_bytes": resized.tobytes(), "shape": resized.shape, "dtype": str(resized.dtype)}

                fh, fw = resized.shape[:2]
                central_input_queue.put((feed_id, effective_index, frame_data, time.time(), fw, fh), timeout=0.1)
                frames_processed += 1
            except queue.Full:
                frames_dropped += 1
            except Exception as e:
                errors += 1
                logger.error(f"[{feed_id}] Error in ingestion loop: {e}", exc_info=True)
            
            processed_frames_count += 1

    finally:
        if reader: reader.stop()
        if shm_manager: shm_manager.close()
        
        try: 
            central_input_queue.put((feed_id, -999, {}, time.time(), 0, 0), timeout=1.0)
        except queue.Full: 
            logger.warning(f"[{feed_id}] Could not send end-of-stream signal: queue full.")
        
        logger.info(f"[{feed_id}] Ingestion terminated. Processed={frames_processed}, Dropped={frames_dropped}, Errors={errors}")
