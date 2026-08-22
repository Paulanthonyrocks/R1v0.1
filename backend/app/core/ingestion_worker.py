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
    video_writer_queue_name: Optional[str] = None,
    feed_stop_key: Optional[str] = None,
):
    """
    Lightweight process that captures frames and pushes them to a central queue.

    When ``video_writer_queue_name`` is provided (i.e., the feed has
    ``video_output.enabled=true``), an opt-in secondary producer fans JPEG bytes
    out to the named RedisQueue so the feed-side ``VideoWriter`` can persist them.
    Closes to ``None`` by default so feeds that don't need local persistence pay no
    cost.
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

    # Mirror the inference worker's stop semantics: a local flag set by the
    # signal handler, the (possibly None) stop_event, and the global Redis
    # "signal:pipeline_stop" key that FeedManager publishes on shutdown.
    # NOTE: stop_event is always None for ingestion workers (FeedManager passes
    # None and relies on the Redis key / signals), so a bare `stop_event.is_set()`
    # check would never break the loop — this helper closes that hole.
    #
    # feed_stop_key (NEW): the per-feed RedisEvent key name FeedManager passes
    # in at start time (e.g. "event:feed_stop_Feed_1_sample_traffic.mp4").
    # When FeedManager.stop_feed → _terminate_resources calls
    # stop_event.set() on the parent's RedisEvent, the underlying Redis SET
    # fires immediately. Before this plumbing the key was created and set
    # but never read by the worker, so every per-feed termination fell through
    # to SIGTERM after a full 1.0s join wait — observable in production as
    # "Process N for Feed_X hung. Terminating." for every feed at shutdown
    # even though the worker responded to SIGTERM in 3-140ms.
    signal_stop = {"flag": False}

    def should_stop() -> bool:
        if signal_stop["flag"]:
            return True
        if stop_event and getattr(stop_event, "is_set", lambda: False)():
            return True
        try:
            if redis_client:
                if redis_client.exists("signal:pipeline_stop"):
                    return True
                # Per-feed key check — set by the parent's stop_event.set()
                # in _terminate_resources. Cheap EXISTS (~0.1ms local Redis)
                # but we throttle below so we don't hit Redis 8-15 times/sec
                # per feed (3 feeds × 24 slots = 72 redundant EXISTS/sec at
                # 8fps before this throttle).
                if feed_stop_key and redis_client.exists(feed_stop_key):
                    return True
        except Exception:
            pass
        return False

    # Per-feed SIGTERM/SIGINT sets the local stop flag — never publishes a
    # global Redis signal, so a single feed crash doesn't tear down the whole
    # pipeline.
    def signal_handler(signum, frame):
        logger.info(f"[{feed_id}] Received signal {signum}, setting local stop event")
        signal_stop["flag"] = True
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

    # Throttle the Redis-backed parts of should_stop() the same way. The
    # local signal_stop["flag"] check stays free (zero-cost per frame);
    # only the two Redis EXISTS calls (signal:pipeline_stop + feed_stop_key)
    # are throttled. At 8fps with interval=5, worst-case stop detection
    # latency is ~625ms — well below the 200ms grace window the parent
    # now waits in _terminate_resources (was 1.0s). signal_stop still
    # short-circuits on SIGTERM regardless of the throttle, so a hard
    # SIGTERM-style kill lands within one frame.
    stop_check_counter = 0
    stop_check_interval = 5

    video_processing_cfg = config.get("video_processing", {})
    target_fps = video_processing_cfg.get("target_fps", 15)

    perf_cfg = config.get("performance", {})
    gpu_acceleration = perf_cfg.get("video_gpu_acceleration", False)
    logger.info(f"[{feed_id}] Video GPU Acceleration enabled: {gpu_acceleration}")

    # Decode-side throttle floor. When the SHM free pool drops below this
    # fraction we skip reading/decoding the frame entirely instead of burning
    # CPU on a frame that would be dropped at acquire(). This lets the pool
    # drain toward the inference pipeline's ~3fps rate rather than the
    # decoder's ~45fps, which is the root cause of the bulk of
    # "SHM free pool empty" drops. 0.0 disables the gate (legacy behaviour).
    shm_min_free_fraction = float(perf_cfg.get("shm_min_free_fraction", 0.20))
    # Hysteresis resume band: shedding latches ON below shm_min_free_fraction
    # and only releases once the free fraction climbs back to this level. Must
    # be > shm_min_free_fraction; a single-threshold gate pins the pool AT the
    # floor forever (Aug-22 run: 12min at exactly 20.0%, ~98k shed frames/feed).
    _shm_resume_fraction = float(
        perf_cfg.get("shm_resume_fraction", max(shm_min_free_fraction + 0.15, 0.35))
    )
    if _shm_resume_fraction <= shm_min_free_fraction:
        _shm_resume_fraction = min(shm_min_free_fraction + 0.10, 0.95)

    video_out_cfg = config.get("video_output", {})
    stream_res = tuple(video_out_cfg.get("stream_resolution", (640, 480)))
    # JPEG quality for the dashboard wire frame. 70 at 640x480 keeps the
    # loca.lt tunnel payload near the previous 320x240@80 size; 80 was visibly
    # mushy on busy scenes at 640x480. See ingestion_worker.py for the inline
    # ``encode_params`` it pairs with.
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), 70]

    # Opt-in secondary producer for the per-feed VideoWriter (local persistence).
    # When ``video_output.enabled=true``, FeedManager passes the RedisQueue name
    # so we can re-attach to the existing list from this process without forcing
    # a RedisQueue handle through ``multiprocessing.Process``.
    video_writer_queue = None
    if video_writer_queue_name:
        try:
            from app.utils.distributed_queue import RedisQueue
            video_writer_queue = RedisQueue(
                video_writer_queue_name,
                maxsize=config.get("video_input", {}).get("max_queue_size", 500),
            )
            logger.info(f"[{feed_id}] VideoWriter producer wired to queue '{video_writer_queue_name}'")
        except Exception as e:
            logger.warning(f"[{feed_id}] Failed to wire VideoWriter producer: {e}")
            video_writer_queue = None

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

    # Cap critical-signal retries by attempt count rather than wall-clock, so a
    # permanently blocked central queue can't hang ingestion for 10s on every
    # start/stop signal. 50 * 0.1s = ~5s max, then fail fast with a fatal log.
    CRITICAL_MAX_ATTEMPTS = 50
    CRITICAL_RETRY_SLEEP = 0.1

    def safe_put(item: Any, timeout: float = 0.1, critical: bool = False) -> bool:
        """Wrapper for queue.put to ensure compatibility across backends and guaranteed delivery for critical signals."""
        attempts = 0
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

                # Critical signals (start/stop) must be delivered, but bound the
                # retry by attempt count to avoid an indefinite hang when the
                # downstream pipeline is dead.
                attempts += 1
                if attempts >= CRITICAL_MAX_ATTEMPTS:
                    logger.critical(
                        f"[{feed_id}] FAILED to deliver critical signal after "
                        f"{attempts} attempts (~{attempts * CRITICAL_RETRY_SLEEP:.1f}s): {item}"
                    )
                    return False

                logger.warning(f"[{feed_id}] Queue full, retrying critical signal delivery ({attempts}/{CRITICAL_MAX_ATTEMPTS})...")
                time.sleep(CRITICAL_RETRY_SLEEP)

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

        if attempt < max_retries - 1 and not should_stop():
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

        def save_snapshot_async(bytes_data: bytes, fmt: Optional[str], snap_dir: Path):
            try:
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
        
        # Offload disk I/O to a background thread, passing the captured frame data and destination
        from app.config import get_current_config
        cfg = get_current_config()
        snap_dir = Path(cfg.snapshots_dir)
        threading.Thread(target=save_snapshot_async, args=(snapshot_bytes, snapshot_format, snap_dir), daemon=True).start()

    try:
        while True:
            # Throttled stop check. signal_stop["flag"] (set by the SIGTERM/
            # SIGINT handler) is consulted every frame for instant hard-stop
            # response; the two Redis EXISTS calls are skipped except every
            # stop_check_interval frames.
            nonlocal_stop_check = stop_check_counter % stop_check_interval == 0
            if signal_stop["flag"]:
                logger.info(f"[{feed_id}] Received stop signal. Terminating...")
                break
            if nonlocal_stop_check:
                if stop_event and getattr(stop_event, "is_set", lambda: False)():
                    logger.info(f"[{feed_id}] Received stop signal. Terminating...")
                    break
                try:
                    if redis_client:
                        if redis_client.exists("signal:pipeline_stop"):
                            logger.info(f"[{feed_id}] Received pipeline stop signal. Terminating...")
                            break
                        if feed_stop_key and redis_client.exists(feed_stop_key):
                            logger.info(f"[{feed_id}] Received per-feed stop signal. Terminating...")
                            break
                except Exception:
                    pass
            stop_check_counter += 1

            # Command queue: feed_manager puts snapshot commands on the
            # Redis-backed RedisQueue('feed_cmd_' + feed_id) but passes None
            # for command_queue in worker_args (comment: "handled via Redis") --
            # so this drain was DEAD CODE and the queue filled to maxsize 50,
            # making every request_snapshot raise queue.Full (83 empty ERROR
            # lines in one 2-min run). Attach a same-name handle here so the
            # worker actually drains it. A RedisQueue is just a key wrapper;
            # constructing it again connects to the SAME Redis list.
            if command_queue is None and feed_id:
                try:
                    from app.utils.distributed_queue import RedisQueue

                    command_queue = RedisQueue("feed_cmd_" + feed_id, maxsize=50)
                except Exception as e:
                    logger.warning(f"[{feed_id}] Could not attach to feed command queue: {e}")
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
                # Resolve pipeline pressure BEFORE reading/decoding. Prefer the
                # shared dict; fall back to a throttled Redis check so we don't
                # round-trip on every frame. Moving this above reader.read()
                # avoids burning CPU/GPU decoding frames we will drop anyway
                # under backpressure.
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
                # Always advance the throttle counter, regardless of which
                # pressure source was used (previously only incremented in the
                # else branch, which stalled the Redis check cadence when a
                # shared pressure object was supplied).
                frame_check_counter += 1

                if cached_pressure > 0.7:
                    metrics.frames_dropped += 1
                    if metrics.frames_dropped % 100 == 0:
                        logger.warning(
                            f"[{feed_id}] Pipeline pressure high ({cached_pressure:.2f}). "
                            f"Skipping read for frame."
                        )
                    time.sleep(0.001)
                    continue

                # Decode-side throttle (F1/F3): if the SHM free pool is below
                # the configured floor, skip reading/decoding this frame. We
                # deliberately do NOT call reader.read() — that would advance
                # the decoder and waste a frame when there's nowhere to put it.
                # Shedding here lets free segments rebuild so the pool drains at
                # the inference rate. This replaces the cosmetic post-acquire
                # backpressure that only fired after a frame was already decoded.
                #
                # Hysteresis (Aug-22 21:33-21:46 run): with a single threshold,
                # once ingestion sheds down to the inference drain rate the pool
                # sits pinned AT the floor forever (observed: free=1198/6000 =
                # 19.97% for 12 straight minutes, ~2,950 gate warnings, ~98k
                # shed frames/feed). Shedding below `floor` but resuming normal
                # decode only above `resume` (a higher hysteresis band) lets
                # freed segments accumulate between waves so ingestion bursts
                # instead of trickling at the equilibrium point.
                if shm_min_free_fraction > 0.0 and frame_buffer is not None:
                    _free = frame_buffer.available_count()
                    _pool = frame_buffer.pool_size
                    _free_frac = (_free / _pool) if _pool > 0 else 1.0
                    # Latch: stay shedding until we clear the resume band, so
                    # flapping around the floor doesn't re-trigger per frame.
                    if not _shedding:
                        _shedding = _free_frac < shm_min_free_fraction
                    elif _free_frac >= _shm_resume_fraction:
                        _shedding = False
                        logger.info(
                            f"[{feed_id}] SHM pool recovered "
                            f"({_free_frac:.1%} >= {_shm_resume_fraction:.1%}); resuming decode."
                        )
                    if _shedding:
                        metrics.frames_dropped += 1
                        if metrics.frames_dropped % 100 == 0:
                            logger.warning(
                                f"[{feed_id}] SHM free pool low "
                                f"({_free_frac:.1%} < {shm_min_free_fraction:.1%}); "
                                f"skipping decode to shed load."
                            )
                        time.sleep(0.005)
                        continue

                result = reader.read()
                if result is None:
                    if reader.end_of_video and not is_looped:
                        # Non-looped file reached its end (FrameReader sets
                        # end_of_video and stops its thread, so isOpened is
                        # already False). No reconnect path exists here — exit
                        # cleanly rather than busy-looping on a dead source.
                        logger.info(f"[{feed_id}] End of stream.")
                        break
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

                    # Secondary producer: fan JPEG bytes to the per-feed
                    # VideoWriter if recording is enabled. Non-blocking drop
                    # on queue overflow so we never stall the main pipeline.
                    if video_writer_queue is not None:
                        try:
                            video_writer_queue.put_nowait(last_frame_bytes)
                        except queue.Full:
                            # Drop oldest by skipping this frame silently.
                            pass
                        except Exception as e:
                            logger.debug(f"[{feed_id}] VideoWriter put failed: {e}")

                    if not frame_buffer:
                        logger.error(
                            f"[{feed_id}] SharedFrameBuffer not initialized. Cannot queue frame."
                        )
                        metrics.errors += 1
                        continue

                    shm_ref = frame_buffer.acquire()
                    if not shm_ref:
                        metrics.frames_dropped += 1
                        # SHM pool exhausted - apply aggressive backpressure
                        # Graduated backpressure based on recent drop rate
                        drop_rate = metrics.frames_dropped / max(1, metrics.frames_processed + metrics.frames_dropped)
                        
                        # Higher drop rate = longer backoff (10ms to 100ms)
                        sleep_time = min(0.01 + (drop_rate * 0.09), 0.1)
                        time.sleep(sleep_time)
                        
                        if metrics.frames_dropped % 20 == 0:
                            logger.warning(f"[{feed_id}] SHM pool exhausted, drop_rate={drop_rate:.1%}, applying backpressure ({sleep_time*1000:.0f}ms)")
                        continue
                    
                    # Get free pool size to apply graduated backpressure
                    free_pool_size = frame_buffer.available_count()
                    pool_size = frame_buffer.pool_size
                    
                    # Graduated backpressure based on free pool percentage
                    free_percent = (free_pool_size / pool_size) * 100 if pool_size > 0 else 0
                    
                    if free_percent < 10:
                        # Critical: < 10% free - aggressive slowdown
                        time.sleep(0.03)
                        logger.debug(f"[{feed_id}] SHM pool critical: {free_pool_size}/{pool_size} free")
                    elif free_percent < 25:
                        # Warning: < 25% free - moderate slowdown
                        time.sleep(0.01)
                        logger.debug(f"[{feed_id}] SHM pool low: {free_pool_size}/{pool_size} free")
                    elif free_percent < 40:
                        # Caution: < 40% free - slight slowdown
                        time.sleep(0.005)

                    frame_buffer.write(shm_ref, last_frame_bytes, feed_id=feed_id)

                    try:
                        if not safe_put((feed_id, frame_index, shm_ref, time.time()), timeout=0.1):
                            raise queue.Full
                        metrics.mark_frame()  # increments frames_processed AND records rolling fps
                        shm_ref = None  # Ownership transferred; guard against double-release
                    except queue.Full:
                        # Queue is full — release the SHM slot we just acquired
                        frame_buffer.release(shm_ref)
                        shm_ref = None
                        metrics.frames_dropped += 1
                        # NOTE: do NOT increment consecutive_errors here. That
                        # counter is for *read/stream* failures (it breaks the
                        # loop after max_consecutive_errors). A full output
                        # queue is backpressure, not a broken source — counting
                        # it here would kill a healthy feed after sustained load.
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
        # Drain and signal the secondary VideoWriter queue so its consumer
        # (if recording is enabled) can flush and terminate instead of
        # blocking forever on a full queue. A b"" sentinel marks EOS; the
        # subscriber pump treats an empty payload as end-of-stream.
        if video_writer_queue is not None:
            try:
                while True:
                    try:
                        video_writer_queue.get_nowait()
                    except queue.Empty:
                        break
                video_writer_queue.put_nowait(b"")  # EOS sentinel
            except Exception:
                pass

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