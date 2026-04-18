     1|import os
     2|import cv2
     3|import logging
     4|import time
     5|import queue
     6|import threading
     7|import signal
     8|import json
     9|from typing import Dict, Any, Optional
    10|from multiprocessing import Queue as MPQueue, Event
    11|
    12|from ..utils.video import FrameReader
    13|from ..utils.process import start_parent_monitor
    14|from .worker_utils import WorkerMetrics
    15|from ..config import set_config_instance
    16|
    17|logger = logging.getLogger("Ingestion")
    18|
    19|def ingestion_worker(
    20|    video_path: str,
    21|    feed_id: str,
    22|    central_input_queue: MPQueue,
    23|    stop_event: Event,
    24|    config: Dict[str, Any],
    25|    is_looped: bool = False
    26|):
    27|    """
    28|    Lightweight process that only captures frames and pushes them to a central queue.
    29|    """
    30|    # Initialize logging for the child process
    31|    import logging.config
    32|    try:
    33|        logging.config.dictConfig(config["logging"])
    34|    except Exception as e:
    35|        # Cannot use logger here as it may not be configured
    36|        pass  # Logging config failed, will use default
    37|
    38|    # Initialize global config instance for this process
    39|    set_config_instance(config)
    40|
    # --- Signal Handling ---
    42|        logger.info(f"[{feed_id}] Received signal {signum}, stopping gracefully")
    43|        stop_event.set()
    44|    
    45|    signal.signal(signal.SIGTERM, signal_handler)
    46|    signal.signal(signal.SIGINT, signal_handler)
    47|
    48|    pid = os.getpid()
    49|    logger.debug(f"Ingestion process {pid} for {feed_id} entering initialization...")
    50|    logger.info(f"Ingestion process {pid} started for {feed_id}")
    51|    
    52|    # Start parent monitor to avoid zombies
    53|    logger.debug(f"[{feed_id}] Starting parent monitor...")
    54|    start_parent_monitor(stop_event, f"Ingestion-{feed_id}")
    55|    
    56|    metrics = WorkerMetrics(feed_id)
    57|
    58|    # Pre-extract config
    59|    video_processing_cfg = config.get("video_processing", {})
    60|    target_fps = video_processing_cfg.get("target_fps", 15)
    61|    
    62|    # Extract performance config
    63|    perf_cfg = config.get("performance", {})
    64|    gpu_acceleration = perf_cfg.get("video_gpu_acceleration", False)
    65|    
    66|    # Stream resolution for the raw frame transmission
    67|    video_out_cfg = config.get("video_output", {})
    68|    stream_res = tuple(video_out_cfg.get("stream_resolution", (640, 480)))
    69|    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), 80]
    70|
    71|    # 1. Config Validation
    72|    if not isinstance(target_fps, (int, float)) or target_fps <= 0 or target_fps > 120:
    73|        logger.warning(f"[{feed_id}] Invalid target_fps {target_fps}, using 15")
    74|        target_fps = 15
    75|
    76|    if not isinstance(stream_res, tuple) or len(stream_res) != 2 or stream_res[0] <= 0 or stream_res[1] <= 0:
    77|        logger.warning(f"[{feed_id}] Invalid stream_resolution {stream_res}, using (640, 480)")
    78|        stream_res = (640, 480)
    79|
    80|    logger.info(f"[{feed_id}] Ingestion Config: FPS={target_fps}, Resolution={stream_res}")
    81|
    82|    # 2. Performance Metrics Initialization
    83|    # frame_count, dropped_frames, start_time are tracked in metrics object now
    84|    last_fps_log = time.time()
    85|    last_metrics_log = time.time()
    86|
    87|    logger.debug(f"[{feed_id}] Initializing FrameReader...")
    88|    
    89|    # Signal feed start
    90|    try:
    91|        central_input_queue.put((feed_id, -888, b'', {"type": "feed_started"}), timeout=2.0)
    92|        logger.info(f"[{feed_id}] Sent feed_started signal")
    93|    except Exception as e:
    94|        logger.error(f"[{feed_id}] Failed to send feed_started signal: {e}")
    95|
    96|    reader = None
    97|    max_retries = 3
    98|    retry_delay = 2.0
    99|
   100|    # 3. Retry Logic for FrameReader Init
   101|    for attempt in range(max_retries):
   102|        try:
   103|            source = video_path
   104|            if isinstance(video_path, str) and video_path.startswith("webcam:"):
   105|                try: 
   106|                    source = int(video_path.split(":")[1])
   107|                except (IndexError, ValueError): 
   108|                    source = 0
   109|            
   110|            reader = FrameReader(
   111|                source, 
   112|                max_queue_size=50,
   113|                is_looped=is_looped,
   114|                target_fps=target_fps,
   115|                gpu_acceleration=gpu_acceleration
   116|            )
   117|            
   118|            if reader.start():
   119|                logger.info(f"[{feed_id}] FrameReader started successfully on attempt {attempt+1}")
   120|                break
   121|            else:
   122|                logger.warning(f"[{feed_id}] FrameReader start failed, attempt {attempt+1}/{max_retries}")
   123|                reader = None
   124|                
   125|        except Exception as e:
   126|            logger.error(f"[{feed_id}] FrameReader init error (attempt {attempt+1}/{max_retries}): {e}")
   127|            reader = None
   128|        
   129|        if attempt < max_retries - 1 and not stop_event.is_set():
   130|            logger.info(f"[{feed_id}] Retrying in {retry_delay}s...")
   131|            time.sleep(retry_delay)
   132|
   133|    if reader is None:
   134|        logger.error(f"[{feed_id}] Failed to initialize FrameReader after {max_retries} attempts")
   135|        return
   136|
   137|    consecutive_errors = 0
   138|    max_consecutive_errors = 300  # ~3 seconds at 100Hz polling (was 30)
   139|
   140|    try:
   141|        while not stop_event.is_set():
   142|            try:
   143|                result = reader.read()
   144|                if result is None:
   145|                    if reader.end_of_video:
   146|                        logger.info(f"[{feed_id}] End of stream.")
   147|                        break
   148|                    
   149|                    consecutive_errors += 1
   150|                    if consecutive_errors > max_consecutive_errors:
   151|                        logger.error(f"[{feed_id}] Too many consecutive read failures, stopping")
   152|                        break
   153|                    
   154|                    time.sleep(0.01)
   155|                    continue
   156|                
   157|                # Reset error counter on successful read
   158|                consecutive_errors = 0
   159|                frame_index, frame = result
   160|
   161|                # --- Backpressure-Aware Capture ---
   162|                # If the AI queue is filling up, drop frames BEFORE expensive resize/encode
   163|                # We target a threshold of 50-70% of QUEUE_MAX_SIZE (500)
   164|                try:
   165|                    q_size = central_input_queue.qsize()
   166|                    if q_size > 300: # Congestion threshold
   167|                        metrics.frames_dropped += 1
   168|                        if metrics.frames_dropped % 100 == 0:
   169|                            logger.warning(f"[{feed_id}] High congestion ({q_size} in queue). Dropping frame {frame_index} EARLY.")
   170|                        continue
   171|                except (AttributeError, NotImplementedError):
   172|                    # qsize() not available on some platforms/types
   173|                    pass
   174|
   175|                try:
   176|                    # total_frames_attempted tracked implicitly via metrics
   177|                    
   178|                    # Resize and encode to bytes for efficient queue transport
   179|                    resized = cv2.resize(frame, stream_res, interpolation=cv2.INTER_LINEAR)
   180|                    success, buffer = cv2.imencode(".jpg", resized, encode_params)
   181|                    
   182|                    if success:
   183|                        try:
   184|                            # Put data in the central queue
   185|                            # Format: (feed_id, frame_index, frame_bytes, metadata)
   186|                            # Reduced timeout to 0.1s to avoid blocking ingestion pulse
   187|                            central_input_queue.put((feed_id, frame_index, buffer.tobytes(), time.time()), timeout=0.1)
   188|                            metrics.frames_processed += 1
   189|                        except queue.Full:
   190|                            metrics.frames_dropped += 1
   191|                            if metrics.frames_dropped % 50 == 0:
   192|                                total = metrics.frames_processed + metrics.frames_dropped
   193|                                drop_rate = (metrics.frames_dropped / total) * 100 if total > 0 else 0
   194|                                logger.warning(f"[{feed_id}] Dropped {metrics.frames_dropped} frames ({drop_rate:.1f}% drop rate)")
   195|                        except Exception as e:
   196|                            metrics.errors += 1
   197|                            logger.error(f"[{feed_id}] Error queueing frame {frame_index}: {e}")
   198|                    else:
   199|                        logger.warning(f"[{feed_id}] Failed to encode frame {frame_index}")
   200|                        metrics.errors += 1
   201|
   202|                    # 4. Periodic Performance Logging
   203|                    current_time = time.time()
   204|                    if current_time - last_fps_log >= 5.0:
   205|                        fps = metrics.frames_processed / (current_time - metrics.start_time)
   206|                        logger.info(f"[{feed_id}] Ingestion FPS: {fps:.2f} | Total Frames: {metrics.frames_processed}")
   207|                        last_fps_log = current_time
   208|
   209|                    if current_time - last_metrics_log > 10.0:
   210|                        logger.info(f"[{feed_id}] METRICS: {json.dumps(metrics.to_dict())}")
   211|                        last_metrics_log = current_time
   212|                        
   213|                except Exception as e:
   214|                    logger.error(f"[{feed_id}] Error processing frame {frame_index}: {e}")
   215|                    metrics.errors += 1
   216|                finally:
   217|                    # Resource Cleanup: Explicitly delete frames to free memory
   218|                    if 'frame' in locals(): del frame
   219|                    if 'resized' in locals(): del resized
   220|                    
   221|            except Exception as e:
   222|                consecutive_errors += 1
   223|                logger.error(f"[{feed_id}] Read error ({consecutive_errors}/{max_consecutive_errors}): {e}")
   224|                if consecutive_errors > max_consecutive_errors:
   225|                    break
   226|                time.sleep(0.1)
   227|
   228|    except Exception as e:
   229|        logger.error(f"[{feed_id}] FATAL Ingestion error: {e}", exc_info=True)
   230|    finally:
   231|        if reader:
   232|            reader.stop()
   233|        
   234|        # 5. Signal end of stream to AI workers (frame_index = -999)
   235|        try:
   236|            central_input_queue.put((feed_id, -999, b'', time.time()), timeout=2.0)
   237|            logger.info(f"[{feed_id}] Sent end-of-stream signal to AI workers")
   238|        except Exception as e:
   239|            logger.error(f"[{feed_id}] Error sending EOS signal: {e}")
   240|
   241|        logger.info(f"[{feed_id}] Ingestion process {pid} terminated.")
   242|