import os
import cv2
import numpy as np
import logging
import time
import queue
import signal
import json
import threading
from typing import Dict, Any, Optional
from pathlib import Path

from app.utils.video import FrameReader
from app.utils.process import start_parent_monitor
from .worker_utils import WorkerMetrics
from app.config import set_config_instance

logger = logging.getLogger("Ingestion")


def ingestion_worker(
    video_path: str,
    feed_id: str,
    central_input_queue: Any,
    stop_event: Any,
    config: Dict[str, Any],
    is_looped: bool = False,
    command_queue: Any = None,
    frame_buffer: Any = None,
    pipeline_pressure: Any = None,
):
    """
    Lightweight process that captures frames and pushes them to a central queue.
    """
    import logging.config as logging_config

    try:
        logging_config.dictConfig(config["logging"])
    except Exception:
        pass  # Fall back to default logging config

    # Initialize global config instance for this process
    set_config_instance(config)

    from app.utils.redis_client import get_redis_client

    redis_client = get_redis_client()

    # Per-feed SIGTERM only sets the local stop event — never publishes a global
    # Redis signal, so a single feed crash doesn't tear down the whole pipeline.
    def signal_handler(signum, frame):
        logger.info(f"[{feed_id}] Received signal {signum}, setting local stop event")
        if stop_event:
            stop_event.set()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    pid = os.getpid()
    logger.debug(f"Ingestion process {pid} for {feed_id} entering initialization...")
    logger.info(f"Ingestion process {pid} started for {feed_id}")

    logger.debug(f"[{feed_id}] Starting parent monitor...")
    start_parent_monitor(stop_event, f"Ingestion-{feed_id}")

    from app.utils.shared_frame_buffer import SharedFrameBuffer

    if frame_buffer is None:
        frame_buffer = SharedFrameBuffer(
            pool_size=config.get("performance", {}).get("shm_pool_size", 100),
            read_only=False,
        )

    metrics = WorkerMetrics(feed_id)

    # Throttle Redis pressure checks to avoid per-frame round-trips
    frame_check_counter = 0
    pressure_check_interval = 30

    video_processing_cfg = config.get("video_processing", {})
    target_fps = video_processing_cfg.get("target_fps", 15)

    perf_cfg = config.get("performance", {})
    gpu_acceleration = perf_cfg.get("video_gpu_acceleration", False)
    logger.info(f"[{feed_id}] Video GPU Acceleration enabled: {gpu_acceleration}")

    video_out_cfg = config.get("video_output", {})
    stream_res = tuple(video_out_cfg.get("stream_resolution", (640, 480)))
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), 80]

    # Config validation
    if not isinstance(target_fps, (int, float)) or target_fps <= 0 or target_fps > 120:
        logger.warning(f"[{feed_id}] Invalid target_fps {target_fps}, using 15")
        target_fps = 15

    if (
        not isinstance(stream_res, tuple)
        or len(stream_res) != 2
        or stream_res[0] <= 0
        or stream_res[1] <= 0
    ):
        logger.warning(f"[{feed_id}] Invalid stream_resolution {stream_res}, using (640, 480)")
        stream_res = (640, 480)

    logger.info(f"[{feed_id}] Ingestion Config: FPS={target_fps}, Resolution={stream_res}")

    last_fps_log = time.time()
    last_metrics_log = time.time()

    logger.debug(f"[{feed_id}] Initializing FrameReader...")

    def safe_put(item: Any, timeout: float = 0.1, critical: bool = False) -> bool:
        """Wrapper for queue.put to ensure compatibility across backends and guaranteed delivery for critical signals."""
        start_time = time.time()
        while True:
            try:
                # Try to put the item. 
                # RedisStreamQueue.put might not support timeout, so we catch AttributeError.
                try:
                    central_input_queue.put(item, timeout=timeout)
                except AttributeError:
                    # Fallback for queues without timeout support: use put_nowait
                    central_input_queue.put_nowait(item)
                return True
            except queue.Full:
                if not critical:
                    # Non-critical frames are just dropped if the queue is full
                    return False
                
                # Critical signals (start/stop) must be delivered. Retry until timeout (max 10s).
                if (time.time() - start_time) > 10.0:
                    logger.critical(f"[{feed_id}] FAILED to deliver critical signal after 10s: {item}")
                    return False
                
                logger.warning(f"[{feed_id}] Queue full, retrying critical signal delivery...")
                time.sleep(0.1)

    try:
        # Sent feed_started signal with guaranteed delivery
        if not safe_put((feed_id, -888, b"", {"type": "feed_started", "timestamp": time.time()}), critical=True):
            logger.error(f"[{feed_id}] Critical failure: could not send feed_started signal")
        else:
            logger.info(f"[{feed_id}] Sent feed_started signal")
    except Exception as e:
        logger.error(f"[{feed_id}] Unexpected error sending feed_started signal: {e}")

    reader = None
    max_retries = 3
    retry_delay = 2.0

    for attempt in range(max_retries):
        try:
            source = video_path
            if isinstance(video_path, str):
                if video_path.startswith("webcam:"):
                    try:
                        source = int(video_path.split(":")[1])
                    except (IndexError, ValueError):
                        logger.warning(
                            f"[{feed_id}] Invalid webcam index '{video_path}', defaulting to camera 0"
                        )
                        source = 0
                elif not video_path.startswith(("rtsp:", "http:", "https:", "tcp:")):
                    # Resolve relative to project root to avoid CWD issues in multiprocess
                    root = config.get("project_root_dir")
                    if root:
                        source = str(Path(root) / video_path)
                    else:
                        source = str(Path(video_path).resolve())

            reader = FrameReader(
                source,
                max_queue_size=50,
                is_looped=is_looped,
                target_fps=target_fps,
                gpu_acceleration=gpu_acceleration,
            )

            if reader.start():
                logger.info(
                    f"[{feed_id}] FrameReader started successfully on attempt {attempt + 1}"
                )
                last_fps_log = time.time()
                last_metrics_log = time.time()
                metrics.start_time = time.monotonic()
                break
            else:
                logger.warning(
                    f"[{feed_id}] FrameReader start failed, attempt {attempt + 1}/{max_retries}"
                )
                reader = None

        except Exception as e:
            logger.error(
                f"[{feed_id}] FrameReader init error (attempt {attempt + 1}/{max_retries}): {e}"
            )
            reader = None

        if attempt < max_retries - 1 and (stop_event is None or not stop_event.is_set()):
            logger.info(f"[{feed_id}] Retrying in {retry_delay}s...")
            time.sleep(retry_delay)

    if reader is None:
        logger.error(f"[{feed_id}] Failed to initialize FrameReader after {max_retries} attempts")
        return

    consecutive_errors = 0
    max_consecutive_errors = 300
    last_frame_bytes = None
    last_frame_format: Optional[str] = None
    shm_ref = None

    def handle_command(cmd: Dict[str, Any]) -> None:
        nonlocal last_frame_bytes, last_frame_format

        cmd_type = cmd.get("type")
        if cmd_type != "save_snapshot":
            return

        incident_id = cmd.get("incident_id")
        if not incident_id:
            logger.error(f"[{feed_id}] save_snapshot command missing incident_id")
            return

        if not last_frame_bytes:
            logger.warning(f"[{feed_id}] save_snapshot requested but no frame available")
            return

        # Capture current frame and format to avoid race conditions with the main loop
        snapshot_bytes = last_frame_bytes
        snapshot_format = last_frame_format

        def save_snapshot_async(bytes_data: bytes, fmt: Optional[str]):
            try:
                from app.config import get_current_config

                cfg = get_current_config()
                snap_dir = Path(cfg.snapshots_dir)
                snap_dir.mkdir(parents=True, exist_ok=True)

                ext = ".png" if fmt == "png" else ".jpg"
                snap_path = snap_dir / f"{feed_id}_{incident_id}{ext}"

                with open(snap_path, "wb") as f:
                    f.write(bytes_data)

                logger.info(f"[{feed_id}] Snapshot saved: {snap_path} for incident {incident_id}")

                safe_put(
                    (
                        feed_id,
                        -999,
                        b"",
                        {
                            "type": "snapshot_saved",
                            "incident_id": incident_id,
                            "snapshot_path": str(snap_path),
                        },
                    ),
                    critical=True,
                )
            except Exception as e:
                logger.error(f"[{feed_id}] Async snapshot save failed: {e}")
        
        # Offload disk I/O to a background thread, passing the captured frame data
        threading.Thread(target=save_snapshot_async, args=(snapshot_bytes, snapshot_format), daemon=True).start()

    try:
        while True:
            if stop_event and stop_event.is_set():
                logger.info(f"[{feed_id}] Received stop signal via event. Terminating...")
                break

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
                    if reader.end_of_video and not is_looped:
                        # Live stream disconnect — attempt reconnect once
                        logger.warning(f"[{feed_id}] Stream disconnected, attempting reconnect...")
                        try:
                            if reader.reconnect():
                                logger.info(f"[{feed_id}] Stream reconnected successfully")
                                continue
                        except Exception:
                            pass
                        
                        # Verify if reader is actually back online before continuing
                        if not reader.isOpened:
                            logger.info(f"[{feed_id}] End of stream (or reconnect failed).")
                            break
                        continue
                    elif reader.end_of_video:
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

                # Determine pipeline pressure — prefer the shared dict, fall back to Redis
                # (throttled to avoid a round-trip on every frame)
                cached_pressure = 0.0
                if pipeline_pressure is not None:
                    cached_pressure = (
                        getattr(pipeline_pressure, 'value', 0.0)
                        if not isinstance(pipeline_pressure, dict)
                        else pipeline_pressure.get("value", 0.0)
                    )
                else:
                    if frame_check_counter % pressure_check_interval == 0:
                        pressure_val = redis_client.get("pipeline:pressure")
                        cached_pressure = float(pressure_val) if pressure_val else 0.0
                    frame_check_counter += 1

                if cached_pressure > 0.7:
                    metrics.frames_dropped += 1
                    if metrics.frames_dropped % 100 == 0:
                        logger.warning(
                            f"[{feed_id}] Pipeline pressure high ({cached_pressure:.2f}). "
                            f"Dropping frame {frame_index}."
                        )
                    continue

                try:
                    # Normalise frame to uint8 BGR before resize/encode
                    if frame.dtype != np.uint8:
                        if frame.dtype in (np.float32, np.float64):
                            if 0.0 <= frame.min() and frame.max() <= 1.0:
                                frame = (np.clip(frame, 0.0, 1.0) * 255).astype(np.uint8)
                            else:
                                frame = frame.astype(np.uint8)
                        else:
                            frame = frame.astype(np.uint8)

                    resized = cv2.resize(frame, stream_res, interpolation=cv2.INTER_LINEAR)

                    success, snap_buf = cv2.imencode(".jpg", resized, encode_params)
                    if success:
                        last_frame_format = "jpg"
                    else:
                        logger.warning(f"[{feed_id}] JPEG encode failed for frame {frame_index}, dropping frame")
                        metrics.errors += 1
                        continue

                    last_frame_bytes = snap_buf.tobytes()

                    if not last_frame_bytes:
                        logger.warning(
                            f"[{feed_id}] Empty frame bytes, skipping frame {frame_index}"
                        )
                        metrics.frames_dropped += 1
                        continue

                    if not frame_buffer:
                        logger.error(
                            f"[{feed_id}] SharedFrameBuffer not initialized. Cannot queue frame."
                        )
                        metrics.errors += 1
                        continue

                    shm_ref = frame_buffer.acquire()
                    if not shm_ref:
                        metrics.frames_dropped += 1
                        continue

                    frame_buffer.write(shm_ref, last_frame_bytes)

                    try:
                        if not safe_put((feed_id, frame_index, shm_ref, time.time()), timeout=0.1):
                            raise queue.Full
                        metrics.frames_processed += 1
                        metrics.mark_frame()
                        shm_ref = None  # Ownership transferred; guard against double-release
                    except queue.Full:
                        # Queue is full — release the SHM slot we just acquired
                        frame_buffer.release(shm_ref)
                        shm_ref = None
                        metrics.frames_dropped += 1
                        consecutive_errors += 1
                        if metrics.frames_dropped % 50 == 0:
                            total = metrics.frames_processed + metrics.frames_dropped
                            drop_rate = (metrics.frames_dropped / total * 100) if total > 0 else 0
                            logger.warning(
                                f"[{feed_id}] Dropped {metrics.frames_dropped} frames "
                                f"({drop_rate:.1f}% drop rate)"
                            )

                    current_time = time.time()
                    if current_time - last_fps_log >= 5.0:
                        elapsed = current_time - last_fps_log
                        fps = metrics.frames_processed / elapsed if elapsed > 0 else 0
                        logger.debug(
                            f"[{feed_id}] Ingestion FPS: {fps:.2f} | "
                            f"Total Frames: {metrics.frames_processed}"
                        )
                        last_fps_log = current_time

                    if current_time - last_metrics_log > 10.0:
                        logger.info("[%s] METRICS: %s", feed_id, metrics.to_dict())
                        last_metrics_log = current_time

                except Exception as e:
                    if shm_ref:
                        frame_buffer.release(shm_ref)
                        shm_ref = None
                    logger.error(f"[{feed_id}] Error processing frame {frame_index}: {e}")
                    metrics.errors += 1

            except Exception as e:
                consecutive_errors += 1
                logger.error(
                    f"[{feed_id}] Read error ({consecutive_errors}/{max_consecutive_errors}): {e}"
                )
                if consecutive_errors > max_consecutive_errors:
                    break
                time.sleep(0.1)

    except Exception as e:
        logger.error(f"[{feed_id}] FATAL Ingestion error: {e}", exc_info=True)
    finally:
        if reader:
            reader.stop()

    try:
        if not safe_put(
            (feed_id, -999, b"", {"type": "feed_ended", "timestamp": time.time()}),
            critical=True,
        ):
            logger.error(f"[{feed_id}] Critical failure: could not send feed_ended signal")
        else:
            logger.info(f"[{feed_id}] Sent end-of-stream signal")
    except Exception as e:
        logger.error(f"[{feed_id}] Error sending EOS signal: {e}")

    logger.info(f"[{feed_id}] Ingestion process {pid} terminated.")