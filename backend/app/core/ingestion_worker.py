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

# from ..utils.video import FrameReader
# from ..utils.process import start_parent_monitor
# from .worker_utils import WorkerMetrics, SharedFrameManager

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
    from .worker_utils import WorkerMetrics, SharedFrameManager
    
    # Initialize global config for this process
    from ..config import set_config
    set_config(config)

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

    metrics = WorkerMetrics(feed_id)
    video_processing_cfg = config.get("video_processing", {})
    target_fps = video_processing_cfg.get("target_fps", 15)
    perf_cfg = config.get("performance", {})
    gpu_acceleration = perf_cfg.get("video_gpu_acceleration", False)
    device = torch.device("cuda" if gpu_acceleration and torch.cuda.is_available() else "cpu")

    use_shm = False # Forced Off for Stability
    video_out_cfg = config.get("video_output", {})
    stream_res = tuple(video_out_cfg.get("stream_resolution", (640, 480)))
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), 80]

    reader = None
    try:
        source = video_path
        if isinstance(video_path, str) and video_path.startswith("webcam:"):
            try: source = int(video_path.split(":")[1])
            except: source = 0
        reader = FrameReader(source, max_queue_size=50, is_looped=is_looped, target_fps=target_fps, gpu_acceleration=gpu_acceleration)
        if not reader.start(): return

        while not stop_event.is_set():
            result = reader.read_raw()
            if result is None:
                if reader.end_of_video: break
                time.sleep(0.01); continue

            frame_index, frame = result
            try:
                if device.type == "cuda":
                    frame_tensor = torch.from_numpy(frame).to(device).permute(2, 0, 1).float().unsqueeze(0)
                    resized_tensor = F.interpolate(frame_tensor, size=(stream_res[1], stream_res[0]), mode="bilinear")
                    resized = resized_tensor.squeeze(0).permute(1, 2, 0).byte().cpu().numpy()
                else:
                    resized = cv2.resize(frame, stream_res)

                success, buffer = cv2.imencode(".jpg", resized, encode_params)
                if success:
                    central_input_queue.put((feed_id, frame_index, buffer.tobytes(), time.time()), timeout=0.1)
                    metrics.frames_processed += 1
            except queue.Full: metrics.frames_dropped += 1
            except Exception: metrics.errors += 1

            if metrics.frames_processed % 100 == 0:
                logger.info(f"[{feed_id}] Processed {metrics.frames_processed} frames")

    finally:
        if reader: reader.stop()
        try: central_input_queue.put((feed_id, -999, b"", time.time()), timeout=1.0)
        except: pass
        logger.info(f"[{feed_id}] Ingestion terminated.")