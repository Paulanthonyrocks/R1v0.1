from typing import Dict, Any, List, Optional, Tuple
import asyncio
import logging
import time
import queue
import msgpack
from concurrent.futures import ThreadPoolExecutor
from app.services.constants import FeedManagerConstants
from app.models.feeds import FeedOperationalStatusEnum
from app.models.websocket import WebSocketMessageTypeEnum

logger = logging.getLogger("app.services.result_processor")

# Fields the frontend actually renders from each vehicle on a video frame
# (see hosting/lib/useVideoSocket.ts drawBoundingBoxes). Everything else in
# serialize_tracked_vehicles (embedding, ground_coordinates, vx/vy, lane,
# confidence, class_id, status) is only used server-side (recording, DB) and
# was being re-sent on every frame -- an embedding alone is ~1-2KB/vehicle, so
# 47 vehicles added ~50-90KB of pure waste per frame, which is what forced the
# ~2fps over a tunnel. Trim at the wire boundary; the full record is still
# available to the opt-in subscriber pump via the `vehicles` arg.
_WIRE_VEHICLE_KEYS = (
    "vehicle_id", "global_vehicle_id", "bbox", "speed",
    "license_plate", "class_name", "is_wrong_way", "is_stopped",
)

def _to_native(o: Any) -> Any:
    """Recursively convert numpy scalars / arrays / nested containers to native
    Python types so msgpack.packb can always serialize the payload.

    The earlier converters only recursed into ``dict`` and ``list``; a structure
    like a bbox stored as ``[(np.float32, np.float32), ...]`` (a list of numpy
    scalars / tuples) slipped through and raised ``TypeError: can not serialize
    'numpy.float32' object`` at packb time, dropping the entire frame broadcast.
    This recurses into lists AND tuples, converting every numpy value it finds.
    """
    import numpy as np
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, dict):
        return {k: _to_native(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_to_native(v) for v in o]
    return o


def _convert_metrics(metrics: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Convert numpy types in metrics dict to native Python types for msgpack."""
    if not metrics:
        return {}
    return {k: _to_native(v) for k, v in metrics.items()}


def _wire_vehicles(vehicles: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Project each vehicle down to the subset the frontend renders on a frame."""
    if not vehicles:
        return []
    return [{k: _to_native(v.get(k)) for k in _WIRE_VEHICLE_KEYS} for v in vehicles]


class ResultProcessor:
    """
    Handles the reading and processing of inference results from the central queue.
    Decouples the data pipeline (dedup, KPI updates) from feed orchestration.
    Under Option A the frame bytes are copied out in the inference worker, so this
    processor consumes copied bytes and never touches shared memory.
    """
    def __init__(self, 
                 central_output_queue: Any, 
                 frame_buffer: Any = None, 
                 executor: ThreadPoolExecutor = None, 
                 config: Dict[str, Any] = None,
                 registry: Any = None,
                 broadcaster: Any = None):
        self._central_output_queue = central_output_queue
        self._executor = executor
        self.config = config
        self.registry = registry
        self.broadcaster = broadcaster
        self._stop_flag = False
        # Optional hook for in-process subscribers; assigned via set_subscriber_pump.
        self._subscriber_pump: Optional[Any] = None
        # Optional hook for analytics ingestion (safety/incidents/history);
        # assigned via set_analytics_hook. Invoked once per deduped frame with
        # (feed_id, metrics, vehicles). Without it, SafetyMonitor and per-frame
        # metric history are inert (audit C1).
        self._analytics_hook: Optional[Any] = None
        # Option A: the inference worker copies frame bytes out of shared memory
        # and forwards them directly, so the result processor consumes bytes and
        # never touches shared memory. ``frame_buffer`` is accepted for
        # call-site compatibility but is no longer used here.

    def stop(self):
        self._stop_flag = True

    def set_broadcaster(self, broadcaster: Any):
        """Updates the broadcaster instance used for streaming updates."""
        self.broadcaster = broadcaster
        logger.info("ResultProcessor broadcaster updated.")

    def set_subscriber_pump(self, pump_callable):
        """
        Wire the in-process subscriber pump.

        The callable is FeedManager.deliver_to_subscribers; it is invoked
        once per deduped decoded frame so internal consumers (e.g. the
        recording VideoProcessor) receive the same payloads as WebSocket
        clients. Passing None disables the pump.
        """
        self._subscriber_pump = pump_callable
        logger.info("ResultProcessor subscriber pump updated.")

    def set_analytics_hook(self, hook_callable):
        """
        Wire the analytics ingestion hook.

        The callable is invoked once per deduped frame as
        ``await hook(feed_id, metrics, vehicles)``. It routes tracked-vehicle
        data into AnalyticsService (SafetyMonitor wrong-way/stopped detection,
        incident creation, and per-frame metric history for the predictor).
        Passing None disables analytics ingestion.
        """
        self._analytics_hook = hook_callable
        logger.info("ResultProcessor analytics hook updated.")

    def _process_single_result(self, item):
        """CPU-bound processing of a single result item.

        Option A: the inference worker copies the decoded frame bytes out of
        shared memory and forwards them directly, so ``item[2]`` is already the
        frame bytes -- no SHM read or release happens here. This removes the
        ~14% read-failure race entirely (a segment could previously be recycled
        under this async reader).
        """
        if not item or not isinstance(item, (tuple, list)) or len(item) < 3:
            logger.error(f"Invalid result item format: {item}")
            return None

        frame_bytes = item[2]
        feed_id = None
        frame_idx = None
        metrics = None
        vehicles = None
        extra = None

        try:
            feed_id, frame_idx, _, metrics, vehicles, extra = item
            logger.debug(f"[RESULT_PROC] Processing item: feed={feed_id}, frame_idx={frame_idx}, bytes_size={len(frame_bytes) if frame_bytes else 0}")
            if not frame_bytes:
                logger.warning(f"[RESULT_PROC] Empty frame bytes: feed={feed_id}, frame_idx={frame_idx}")
                return None
            dims = None  # bytes are self-describing; result processor forwards them verbatim
            logger.debug(f"[RESULT_PROC] Frame bytes ready: feed={feed_id}, frame_idx={frame_idx}, bytes_size={len(frame_bytes) if frame_bytes else 0}")
        except Exception as e:
            logger.error(f"Error unpacking result item for {feed_id}: {e}")
            return None

        # Adaptive streaming: produce a smaller background JPEG to ship over the
        # wire when enabled. This runs inside the executor (CPU-bound, off the
        # event loop) so it never stalls frame delivery. The full-res
        # ``frame_bytes`` is preserved downstream for the recording pump; only
        # the WebSocket payload uses the downscaled copy. Detect/lane gating is
        # intentionally NOT applied here -- every frame gets the small bg so
        # tunnel-bound clients see a consistent bitrate instead of alternating
        # full/low res.
        if extra is None:
            extra = {}
        if self.config.get("video_processing", {}).get("adaptive_streaming", False):
            try:
                import cv2
                import numpy as np
                stream_res = tuple(self.config.get("video_output", {}).get("stream_resolution", (320, 240)))
                bg_scale = float(self.config.get("video_processing", {}).get("roi_scale", 0.5))
                arr = np.frombuffer(frame_bytes, dtype=np.uint8)
                dec = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if dec is not None:
                    small = cv2.resize(dec, (0, 0), fx=bg_scale, fy=bg_scale)
                    ok, buf = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
                    if ok:
                        extra["bg"] = buf.tobytes()
            except Exception as e:
                logger.debug(f"[RESULT_PROC] adaptive bg encode skipped for {feed_id}: {e}")

        # Return frame data -- broadcast happens downstream
        return feed_id, frame_idx, frame_bytes, metrics, vehicles, extra

    async def process_results_loop(self, handle_periodic_tasks_callback):
        """
        Main loop for processing results.
        Calls handle_periodic_tasks_callback when the queue is empty.
        """
        logger.info("Result processor loop started.")
        last_heartbeat = time.time()
        _feed_last_processed_ts: Dict[str, float] = {}
        last_shm_stats_log = time.time()

        while not self._stop_flag:
            try:
                now_loop = time.time()
                if now_loop - last_heartbeat > 10.0:
                    logger.debug("Result processor heartbeat: loop is active")
                    last_heartbeat = now_loop
                
                # Log queue depth periodically (SHM pool stats are owned by the
                # frame-buffer owner process; the result processor only consumes
                # copied bytes now, so it no longer tracks SHM read failures).
                if now_loop - last_shm_stats_log > 60.0:
                    logger.info(f"[RESULT_PROC-STATS] processed_items accumulated; queue depth ok")
                    last_shm_stats_log = now_loop

                items_buffer = []
                try:
                    for _ in range(FeedManagerConstants.RESULT_BATCH_SIZE):
                        res = self._central_output_queue.get(block=False)
                        if isinstance(res, tuple) and len(res) == 2:
                            msg_id, item = res
                            items_buffer.append((msg_id, item))
                        # ACK immediately to prevent re-delivery storm.
                        # DESIGN TRADE-OFF: We ACK before SHM read because in a real-time video pipeline,
                        # a failed SHM read means the frame is already stale. It is better to drop 
                        # the frame and move to the next one than to let Redis redeliver it, 
                        # which would introduce latency and potentially cause a backlog.
                        if msg_id:
                            try:
                                self._central_output_queue.ack(msg_id)
                            except Exception as e:
                                logger.error(f"Failed to ack message {msg_id}: {e}")
                        else:
                            items_buffer.append((None, res))
                except queue.Empty:
                    pass

                if not items_buffer:
                    await asyncio.sleep(FeedManagerConstants.RESULT_READER_IDLE_SLEEP)
                    await handle_periodic_tasks_callback()
                    continue

                processed_items = await asyncio.gather(*[
                    asyncio.get_running_loop().run_in_executor(
                        self._executor,
                        self._process_single_result,
                        item,
                    )
                    for msg_id, item in items_buffer
                ])

                # Dedup logic
                latest_per_feed: Dict[str, int] = {}
                for i, result in enumerate(processed_items):
                    if result is None:
                        continue
                    latest_per_feed[result[0]] = i

                latest_indices = set(latest_per_feed.values())

                # Prune dedup
                active_feeds = self.registry.process_registry.keys()
                for fid in list(_feed_last_processed_ts.keys()):
                    if fid not in active_feeds:
                        del _feed_last_processed_ts[fid]

                for i, result in enumerate(processed_items):
                    if result is None or i not in latest_indices:
                        continue

                    feed_id, frame_idx, frame_bytes, metrics, vehicles, extra = result

                    now_dedup = time.time()
                    target_fps_dedup = self.config.get("video_output", {}).get("fps", 10)
                    dedup_window = 1.0 / target_fps_dedup
                    if now_dedup - _feed_last_processed_ts.get(feed_id, 0.0) < dedup_window:
                        continue
                    _feed_last_processed_ts[feed_id] = now_dedup

                    # Registry check
                    entry = self.registry.get_entry(feed_id)
                    if not entry:
                        continue

                    # Handle status transition
                    if entry["status"] == FeedOperationalStatusEnum.STARTING:
                        entry["status"] = FeedOperationalStatusEnum.RUNNING
                        
                        # Construct FeedStatusData to avoid passing None to the broadcaster
                        from app.models.feeds import FeedStatusData, FeedConfigInfo
                        status_data = FeedStatusData(
                            feed_id=feed_id,
                            config=entry.get("config_info") or FeedConfigInfo(
                                name="Unknown", source_type="unknown", source_identifier=entry["source"]
                            ),
                            source=entry["source"],
                            status=entry["status"],
                            current_fps=entry["timer"].get_fps("loop_total") if entry.get("timer") else None,
                            last_error=entry.get("error_message"),
                            latest_metrics=entry.get("latest_metrics"),
                        )
                        if self.broadcaster:
                            await self.broadcaster.broadcast_feed_update(status_data)
                        else:
                            logger.warning(f"Broadcaster is None; skipping feed update for {feed_id}")

                    # Process metrics and vehicles (delegated to FeedManager or specialized service)
                    # To keep it surgical, I'll leave the complex metrics logic in FeedManager 
                    # and call a callback.
                    await self._process_frame_data(feed_id, frame_idx, frame_bytes, metrics, vehicles, extra)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in result processor loop: {e}", exc_info=True)
                await asyncio.sleep(1.0)

    async def _process_frame_data(self, feed_id, frame_idx, frame_bytes, metrics, vehicles, extra):
        """
        Updates metrics and broadcasts the frame to subscribers using Msgpack for binary efficiency.
        """
        try:
            logger.info(f"[RESULT_PROC] Processing frame data: feed={feed_id}, frame_idx={frame_idx}, frame_bytes_size={len(frame_bytes) if frame_bytes else 0}")

            # 0. Resolve feed geo-coordinates. The inference worker does not
            # embed latitude/longitude into the per-frame metrics dict, but the
            # FeedManager registry stores them on the feed's FeedConfigInfo when
            # the feed is registered (e.g. from config.yaml sample_feeds or the
            # add-feed API). AnalyticsService.process_feed_metrics reads
            # metrics["latitude"/"longitude"] to key the TrafficDataCache, so we
            # backfill them here from the registry when absent. Without this,
            # every feed logs "missing latitude or longitude" and never lands in
            # the data cache. Only backfill when truly missing so a feed that
            # legitimately supplies its own coords (e.g. live API with coords)
            # is never overwritten.
            entry = self.registry.get_entry(feed_id)
            if entry and metrics:
                cfg = entry.get("config_info")
                if cfg is not None:
                    lat = getattr(cfg, "latitude", None)
                    lon = getattr(cfg, "longitude", None)
                    if lat is not None and lon is not None:
                        if metrics.get("latitude") is None:
                            metrics["latitude"] = lat
                        if metrics.get("longitude") is None:
                            metrics["longitude"] = lon
            
            # 1. Update registry metrics with Exponential Moving Average (EMA) to smooth spikes
            if entry and metrics:
                alpha = 1.0 / self.config.get("metrics_averaging_window_seconds", 300)
                
                if "ema_metrics" not in entry or entry["ema_metrics"] is None:
                    entry["ema_metrics"] = metrics.copy()
                else:
                    ema = entry["ema_metrics"]
                    for k, v in metrics.items():
                        if isinstance(v, (int, float)):
                            ema[k] = (1 - alpha) * ema.get(k, 0) + alpha * v
                
                entry["latest_metrics"] = entry["ema_metrics"].copy()

            # 2. Serialize as Msgpack to match frontend expectations
            # Compact keys: t=type, f=feed_id, i=frame_index, ts=timestamp, v=vehicles, m=metrics, bg=background
            # When adaptive streaming is on, ``extra`` carries a downscaled ``bg``
            # JPEG. We pack TWO payloads -- full-res (crisp, for low-RTT links)
            # and small (bandwidth-light, for high-RTT tunnels) -- and let the
            # broadcaster pick per client by tracked RTT. Both share the same
            # vehicle/metrics payload; only the background JPEG differs, so the
            # frontend (which reads bg/v/m/t) handles either transparently.
            adaptive = bool(self.config.get("video_processing", {}).get("adaptive_streaming", False))
            small_bg = extra.get("bg") if (extra and isinstance(extra, dict)) else None

            compact_full = {
                "t": WebSocketMessageTypeEnum.VIDEO_FRAME.value,
                "f": feed_id,
                "i": frame_idx,
                "ts": time.time() * 1000,
                "v": _wire_vehicles(vehicles),
                "m": _convert_metrics(metrics),
                "bg": frame_bytes,  # full-res; recording pump also uses this
            }
            full_data = msgpack.packb(compact_full, use_bin_type=True, default=_to_native)

            if adaptive and small_bg:
                compact_small = dict(compact_full)
                compact_small["bg"] = small_bg
                small_data = msgpack.packb(compact_small, use_bin_type=True, default=_to_native)
            else:
                small_data = full_data

            logger.info(
                f"[RESULT_PROC] Msgpack packed: feed={feed_id}, frame_idx={frame_idx}, "
                f"full={len(full_data)}, small={len(small_data)}, vehicles_count={len(vehicles) if vehicles else 0}"
            )

            # 3. Broadcast via broadcaster (latency-aware when adaptive)
            if self.broadcaster:
                if adaptive and small_data is not full_data:
                    threshold = float(self.config.get("video_processing", {}).get("adaptive_latency_threshold_ms", 120))
                    await self.broadcaster.broadcast_to_feed_realtime_bytes_adaptive(
                        feed_id=feed_id,
                        full_data=full_data,
                        small_data=small_data,
                        frame_index=frame_idx,
                        latency_threshold_ms=threshold,
                    )
                else:
                    await self.broadcaster.broadcast_to_feed_realtime_bytes(
                        feed_id=feed_id,
                        data=full_data,
                        frame_index=frame_idx,
                    )
                logger.debug(f"[RESULT_PROC] Broadcast sent: feed={feed_id}, frame_idx={frame_idx}")
            else:
                logger.warning(f"[RESULT_PROC] Broadcaster is None; cannot broadcast frame for {feed_id}")

            # 4. Pump a copy into in-process subscribers (recording, etc.).
            # Opt-in: only runs when something has subscribed. Cheap no-op
            # when the subscriber map is empty (common case).
            if self._subscriber_pump is not None and frame_bytes:
                try:
                    await self._subscriber_pump(
                        feed_id=feed_id,
                        frame_idx=frame_idx,
                        frame_bytes=frame_bytes,
                        metrics=metrics,
                        vehicles=vehicles,
                    )
                except Exception as e:
                    logger.debug(f"Subscriber pump error for {feed_id}: {e}")

            # 5. Route into analytics (SafetyMonitor, incidents, metric history).
            # This is the bridge that was previously missing: without it the
            # entire safety/incident/history path was dead code (audit C1).
            if self._analytics_hook is not None:
                try:
                    await self._analytics_hook(feed_id, metrics, vehicles)
                except Exception as e:
                    logger.debug(f"Analytics hook error for {feed_id}: {e}")

        except Exception as e:
            logger.error(f"Error broadcasting frame data for {feed_id}: {e}", exc_info=True)

