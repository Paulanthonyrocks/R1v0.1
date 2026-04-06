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
import torch
import torch.nn.functional as F

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
    """Lightweight process for frame capture."""
    from ..utils.video import FrameReader
    from ..utils.process import start_parent_monitor
    from .worker_utils import SharedFrameManager
    
    from ..config import set_config
    set_config(config)

    import torch
    import torch.nn.functional as F

    start_parent_monitor(stop_event)
    print(f"[Ingestion-{feed_id}] Process started. PID: {os.getpid()}", flush=True)
    import logging.config
    try:
        if "logging" in config: logging.config.dictConfig(config["logging"])
        else: logging.basicConfig(level=logging.INFO)
    except Exception as e: logging.basicConfig(level=logging.INFO)

    def signal_handler(signum, frame):
        stop_event.set()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Metrics tracking
    frames_processed = 0
    frames_dropped = 0
    errors = 0

    video_processing_cfg = config.get("video_processing", {})
    target_fps = video_processing_cfg.get("target_fps", 15)
    perf_cfg = config.get("performance", {})
    gpu_acceleration = perf_cfg.get("video_gpu_acceleration", False)
    device = torch.device("cuda" if gpu_acceleration and torch.cuda.is_available() else "cpu")

    use_shm = perf_cfg.get("use_shm", True) # Default to True for optimization
    video_out_cfg = config.get("video_output", {})
    inference_res = video_processing_cfg.get("inference_resolution", (1280, 720))

    # Initialize Shared Memory Manager if enabled
    shm_manager = None
    if use_shm:
        try:
            # Create a ring buffer of 20 frames to avoid contention
            shm_manager = SharedFrameManager(
                name=f"shm_{feed_id}", 
                frame_shape=(inference_res[1], inference_res[0], 3), 
                num_buffers=20
            )
            logger.info(f"[{feed_id}] Shared memory ring buffer initialized.")
        except Exception as e:
            logger.error(f"[{feed_id}] Failed to init SHM: {e}. Falling back to raw_bytes.")
            use_shm = False

    reader = None
    try:
        source = video_path
        if isinstance(video_path, str) and video_path.startswith("webcam:"):
            try: source = int(video_path.split(":")[1])
            except: source = 0
        reader = FrameReader(source, max_queue_size=15, is_looped=is_looped, target_fps=target_fps, gpu_acceleration=gpu_acceleration)
        if not reader.start(): return

        processed_frames_count = 0
        
        while not stop_event.is_set():
            result = reader.read_raw()
            if result is None:
                if reader.end_of_video: break
                time.sleep(0.01); continue

            frame_index, frame = result
            
            current_skip = 0
            if shared_skip_array is not None:
                try: current_skip = shared_skip_array[worker_idx]
                except Exception: pass
            
            effective_index = frame_index if frame_index >= 0 else processed_frames_count
            if current_skip > 0 and (effective_index % (current_skip + 1) != 0):
                continue

            try:
                # Resize frame
                if device.type == "cuda":
                    frame_tensor = torch.from_numpy(frame).to(device).permute(2, 0, 1).float().unsqueeze(0)
                    resized_tensor = F.interpolate(frame_tensor, size=(inference_res[1], inference_res[0]), mode="bilinear")
                    resized = resized_tensor.squeeze(0).permute(1, 2, 0).byte().cpu().numpy()
                else:
                    resized = cv2.resize(frame, inference_res)

                # SHM Writing Logic
                if use_shm and shm_manager:
                    # Use the frame index to determine the buffer slot in the ring
                    shm_index = effective_index % shm_manager.num_buffers
                    shm_manager.write_frame(shm_index, resized)
                    frame_data = {
                        "shm_name": shm_manager.name,
                        "shm_index": shm_index,
                        "shape": resized.shape,
                        "dtype": str(resized.dtype)
                    }
                else:
                    # Fallback to raw bytes (Slow path)
                    frame_data = {"raw_bytes": resized.tobytes(), "shape": resized.shape, "dtype": str(resized.dtype)}

                fw, fh = resized.shape[:2]
                q_max = config.get("performance", {}).get("queue_max_size", 50)
                if central_input_queue.qsize() >= q_max:
                    frames_dropped += 1
                    continue

                central_input_queue.put((feed_id, frame_index, frame_data, time.time(), fw, fh), timeout=0.1)
                frames_processed += 1
                processed_frames_count += 1
            except queue.Full:
                frames_dropped += 1
            except Exception as e:
                errors += 1
                logger.error(f"Error putting frame to queue: {e}")

            if frames_processed % 100 == 0:
                logger.info(f"[{feed_id}] Processed {frames_processed} frames (Current skip: {current_skip})")

    finally:
        if reader: reader.stop()
        if shm_manager: shm_manager.close()
        try: central_input_queue.put((feed_id, -999, b"", time.time()), timeout=1.0)
        except: pass
        logger.info(f"[{feed_id}] Ingestion terminated.")
