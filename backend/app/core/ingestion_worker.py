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

from ..utils.video import FrameReader
from ..utils.process import start_parent_monitor
from .worker_utils import WorkerMetrics

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
    # Initialize logging for the child process
    import logging.config
    try:
        logging.config.dictConfig(config["logging"])
    except Exception as e:
        # Cannot use logger here as it may not be configured
        pass  # Logging config failed, will use default

    # --- Signal Handling ---
    def signal_handler(signum, frame):
        logger.info(f"[{feed_id}] Received signal {signum}, stopping gracefully")
        stop_event.set()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    pid = os.getpid()
    logger.debug(f"Ingestion process {pid} for {feed_id} entering initialization...")
    logger.info(f"Ingestion process {pid} started for {feed_id}")
    
    # Start parent monitor to avoid zombies
    logger.debug(f"[{feed_id}] Starting parent monitor...")
    start_parent_monitor(stop_event, f"Ingestion-{feed_id}")
    
    metrics = WorkerMetrics(feed_id)

    # Pre-extract config
    video_processing_cfg = config.get("video_processing", {})
    target_fps = video_processing_cfg.get("target_fps", 15)
    
    # Extract performance config
    perf_cfg = config.get("performance", {})
    gpu_acceleration = perf_cfg.get("video_gpu_acceleration", False)
    
    # Stream resolution for the raw frame transmission
    video_out_cfg = config.get("video_output", {})
    stream_res = tuple(video_out_cfg.get("stream_resolution", (640, 480)))
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), 80]

    # 1. Config Validation
    if not isinstance(target_fps, (int, float)) or target_fps <= 0 or target_fps > 120:
        logger.warning(f"[{feed_id}] Invalid target_fps {target_fps}, using 15")
        target_fps = 15

    if not isinstance(stream_res, tuple) or len(stream_res) != 2 or stream_res[0] <= 0 or stream_res[1] <= 0:
        logger.warning(f"[{feed_id}] Invalid stream_resolution {stream_res}, using (640, 480)")
        stream_res = (640, 480)

    logger.info(f"[{feed_id}] Ingestion Config: FPS={target_fps}, Resolution={stream_res}")

    # 2. Performance Metrics Initialization
    # frame_count, dropped_frames, start_time are tracked in metrics object now
    last_fps_log = time.time()
    last_metrics_log = time.time()

    logger.debug(f"[{feed_id}] Initializing FrameReader...")
    
    # Signal feed start
    try:
        central_input_queue.put((feed_id, -888, b'', {"type": "feed_started"}), timeout=2.0)
        logger.info(f"[{feed_id}] Sent feed_started signal")
    except Exception as e:
        logger.error(f"[{feed_id}] Failed to send feed_started signal: {e}")

    reader = None
    max_retries = 3
    retry_delay = 2.0

    # 3. Retry Logic for FrameReader Init
    for attempt in range(max_retries):
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
                target_fps=target_fps,
                gpu_acceleration=gpu_acceleration
            )
            
            if reader.start():
                logger.info(f"[{feed_id}] FrameReader started successfully on attempt {attempt+1}")
                break
            else:
                logger.warning(f"[{feed_id}] FrameReader start failed, attempt {attempt+1}/{max_retries}")
                reader = None
                
        except Exception as e:
            logger.error(f"[{feed_id}] FrameReader init error (attempt {attempt+1}/{max_retries}): {e}")
            reader = None
        
        if attempt < max_retries - 1 and not stop_event.is_set():
            logger.info(f"[{feed_id}] Retrying in {retry_delay}s...")
            time.sleep(retry_delay)

    if reader is None:
        logger.error(f"[{feed_id}] Failed to initialize FrameReader after {max_retries} attempts")
        return

    consecutive_errors = 0
    max_consecutive_errors = 300  # ~3 seconds at 100Hz polling (was 30)

    try:
        while not stop_event.is_set():
            try:
                result = reader.read()
                if result is None:
                    if reader.end_of_video:
                        logger.info(f"[{feed_id}] End of stream.")
                        break
                    
                    consecutive_errors += 1
                    if consecutive_errors > max_consecutive_errors:
                        logger.error(f"[{feed_id}] Too many consecutive read failures, stopping")
                        break
                    
                    time.sleep(0.01)
                    continue
                
                # Reset error counter on successful read
                consecutive_errors = 0
                frame_index, frame = result

                # --- Backpressure-Aware Capture ---
                # If the AI queue is filling up, drop frames BEFORE expensive resize/encode
                # We target a threshold of 50-70% of QUEUE_MAX_SIZE (500)
                try:
                    q_size = central_input_queue.qsize()
                    if q_size > 300: # Congestion threshold
                        metrics.frames_dropped += 1
                        if metrics.frames_dropped % 100 == 0:
                            logger.warning(f"[{feed_id}] High congestion ({q_size} in queue). Dropping frame {frame_index} EARLY.")
                        continue
                except (AttributeError, NotImplementedError):
                    # qsize() not available on some platforms/types
                    pass

                try:
                    # total_frames_attempted tracked implicitly via metrics
                    
                    # Resize and encode to bytes for efficient queue transport
                    resized = cv2.resize(frame, stream_res, interpolation=cv2.INTER_LINEAR)
                    success, buffer = cv2.imencode(".jpg", resized, encode_params)
                    
                    if success:
                        try:
                            # Put data in the central queue
                            # Format: (feed_id, frame_index, frame_bytes, metadata)
                            # Reduced timeout to 0.1s to avoid blocking ingestion pulse
                            central_input_queue.put((feed_id, frame_index, buffer.tobytes(), time.time()), timeout=0.1)
                            metrics.frames_processed += 1
                        except queue.Full:
                            metrics.frames_dropped += 1
                            if metrics.frames_dropped % 50 == 0:
                                total = metrics.frames_processed + metrics.frames_dropped
                                drop_rate = (metrics.frames_dropped / total) * 100 if total > 0 else 0
                                logger.warning(f"[{feed_id}] Dropped {metrics.frames_dropped} frames ({drop_rate:.1f}% drop rate)")
                        except Exception as e:
                            metrics.errors += 1
                            logger.error(f"[{feed_id}] Error queueing frame {frame_index}: {e}")
                    else:
                        logger.warning(f"[{feed_id}] Failed to encode frame {frame_index}")
                        metrics.errors += 1

                    # 4. Periodic Performance Logging
                    current_time = time.time()
                    if current_time - last_fps_log >= 5.0:
                        fps = metrics.frames_processed / (current_time - metrics.start_time)
                        logger.info(f"[{feed_id}] Ingestion FPS: {fps:.2f} | Total Frames: {metrics.frames_processed}")
                        last_fps_log = current_time

                    if current_time - last_metrics_log > 10.0:
                        logger.info(f"[{feed_id}] METRICS: {json.dumps(metrics.to_dict())}")
                        last_metrics_log = current_time
                        
                except Exception as e:
                    logger.error(f"[{feed_id}] Error processing frame {frame_index}: {e}")
                    metrics.errors += 1
                finally:
                    # Resource Cleanup: Explicitly delete frames to free memory
                    if 'frame' in locals(): del frame
                    if 'resized' in locals(): del resized
                    
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"[{feed_id}] Read error ({consecutive_errors}/{max_consecutive_errors}): {e}")
                if consecutive_errors > max_consecutive_errors:
                    break
                time.sleep(0.1)

    except Exception as e:
        logger.error(f"[{feed_id}] FATAL Ingestion error: {e}", exc_info=True)
    finally:
        if reader:
            reader.stop()
        
        # 5. Signal end of stream to AI workers (frame_index = -999)
        try:
            central_input_queue.put((feed_id, -999, b'', time.time()), timeout=2.0)
            logger.info(f"[{feed_id}] Sent end-of-stream signal to AI workers")
        except Exception as e:
            logger.error(f"[{feed_id}] Error sending EOS signal: {e}")

        logger.info(f"[{feed_id}] Ingestion process {pid} terminated.")
