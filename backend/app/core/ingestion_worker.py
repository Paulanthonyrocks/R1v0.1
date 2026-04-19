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
from pathlib import Path

from app.utils.video import FrameReader
from app.utils.process import start_parent_monitor
from .worker_utils import WorkerMetrics
from app.config import set_config_instance

logger = logging.getLogger("Ingestion")

def ingestion_worker(
    video_path: str,
    feed_id: str,
    central_input_queue: MPQueue,
    stop_event: Event,
    config: Dict[str, Any],
    is_looped: bool = False,
    command_queue: Optional[MPQueue] = None,
    frame_buffer: Any = None,
    pipeline_pressure: Any = None
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

    # Initialize global config instance for this process
    set_config_instance(config)

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
    max_consecutive_errors = 300  # ~3 seconds at 100Hz polling
    last_frame_bytes = None

    def handle_command(cmd: Dict[str, Any]):
        nonlocal last_frame_bytes
        cmd_type = cmd.get("type")
        if cmd_type == "save_snapshot":
            incident_id = cmd.get("incident_id")
            if not incident_id:
                logger.error(f"[{feed_id}] save_snapshot command missing incident_id")
                return

            if not last_frame_bytes:
                logger.warning(f"[{feed_id}] save_snapshot requested but no frame available")
                return

            try:
                from app.config import get_current_config
                cfg = get_current_config()
                snap_dir = Path(cfg.snapshots_dir)
                snap_dir.mkdir(parents=True, exist_ok=True)

                snap_path = snap_dir / f"{feed_id}_{incident_id}.jpg"

                with open(snap_path, "wb") as f:
                    f.write(last_frame_bytes)

                logger.info(f"[{feed_id}] Snapshot saved: {snap_path} for incident {incident_id}")

                central_input_queue.put((feed_id, -999, b'', {
                    "type": "snapshot_saved",
                    "incident_id": incident_id,
                    "snapshot_path": str(snap_path)
                }), timeout=1.0)
            except Exception as e:
                logger.error(f"[{feed_id}] Error saving snapshot: {e}")

    try:
        while not stop_event.is_set():
            if command_queue:
                try:
                    while True:
                        cmd = command_queue.get_nowait()
                        handle_command(cmd)
                except queue.Empty:
                    pass
                except Exception as e:
                    logger.error(f"[{feed_id}] Command execution error: {e}")

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

                consecutive_errors = 0
                frame_index, frame = result

                try:
                    pressure = pipeline_pressure.value if pipeline_pressure else 0.0
                    if pressure > 0.7:
                        metrics.frames_dropped += 1
                        if metrics.frames_dropped % 100 == 0:
                            logger.warning(f"[{feed_id}] Global Pressure High ({pressure:.2f}). Dropping frame {frame_index} EARLY.")
                        continue
                    
                    q_size = central_input_queue.qsize()
                    if q_size > 300:
                        metrics.frames_dropped += 1
                        continue
                except (AttributeError, NotImplementedError):
                    pass

                try:
                    resized = cv2.resize(frame, stream_res, interpolation=cv2.INTER_LINEAR)
                    
                    success, snap_buf = cv2.imencode(".jpg", resized, encode_params)
                    if success:
                        last_frame_bytes = snap_buf.tobytes()

                    try:
                        is_distributed = 'RedisStreamQueue' in str(type(central_input_queue))
                        shm_ref = None
                        if not is_distributed and frame_buffer:
                            shm_ref = frame_buffer.acquire()
                            if shm_ref:
                                frame_buffer.write(shm_ref, resized)
                        
                        if shm_ref:
                            central_input_queue.put((feed_id, frame_index, shm_ref, time.time()), timeout=0.1)
                            metrics.frames_processed += 1
                        else:
                            central_input_queue.put((feed_id, frame_index, resized.tobytes(), time.time()), timeout=0.1)
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

        try:
            central_input_queue.put((feed_id, -999, b'', time.time()), timeout=2.0)
            logger.info(f"[{feed_id}] Sent end-of-stream signal to AI workers")
        except Exception as e:
            logger.error(f"[{feed_id}] Error sending EOS signal: {e}")

        logger.info(f"[{feed_id}] Ingestion process {pid} terminated.")
