from typing import Dict, Any, List, Optional, Tuple
import asyncio
import logging
import time
import queue
import msgpack
from concurrent.futures import ThreadPoolExecutor
from app.utils.shared_frame_buffer import SharedFrameBuffer
from app.services.constants import FeedManagerConstants
from app.models.feeds import FeedOperationalStatusEnum
from app.models.websocket import WebSocketMessageTypeEnum

logger = logging.getLogger("app.services.result_processor")

class ResultProcessor:
    """
    Handles the reading and processing of inference results from the central queue.
    Decouples the data pipeline (SHM read, dedup, KPI updates) from feed orchestration.
    """
    def __init__(self, 
                 central_output_queue: Any, 
                 frame_buffer: SharedFrameBuffer, 
                 executor: ThreadPoolExecutor, 
                 config: Dict[str, Any],
                 registry: Any,
                 broadcaster: Any):
        self._central_output_queue = central_output_queue
        self.frame_buffer = frame_buffer
        self._executor = executor
        self.config = config
        self.registry = registry
        self.broadcaster = broadcaster
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def set_broadcaster(self, broadcaster: Any):
        """Updates the broadcaster instance used for streaming updates."""
        self.broadcaster = broadcaster
        logger.info("ResultProcessor broadcaster updated.")

    def _process_single_result(self, item):
        """CPU-bound processing of a single result item (SHM read -> bytes)."""
        if not item or not isinstance(item, (tuple, list)) or len(item) < 3:
            logger.error(f"Invalid result item format: {item}")
            return None

        shm_ref = item[2]
        feed_id = None
        frame_idx = None
        metrics = None
        vehicles = None
        extra = None
        frame_bytes = None
        dims = None
        
        try:
            feed_id, frame_idx, _, metrics, vehicles, extra = item
            logger.info(f"[RESULT_PROC] Processing item: feed={feed_id}, frame_idx={frame_idx}, shm_ref={shm_ref}")
            read_result = self.frame_buffer.read(shm_ref, expected_feed_id=feed_id)
            if read_result is not None:
                frame_bytes, dims = read_result
                logger.info(f"[RESULT_PROC] SHM read OK: feed={feed_id}, frame_idx={frame_idx}, bytes_size={len(frame_bytes) if frame_bytes else 0}")
                # CRITICAL FIX: Release SHM immediately after successful read
                # This prevents pool exhaustion when processing is slower than ingestion
                try:
                    self.frame_buffer.release(shm_ref)
                except Exception as e:
                    logger.debug(f"Error releasing SHM ref {shm_ref} after read: {e}")
            else:
                logger.warning(f"[RESULT_PROC] SHM read returned None: feed={feed_id}, frame_idx={frame_idx}, shm_ref={shm_ref}")
                # Release on read failure
                try:
                    self.frame_buffer.release(shm_ref)
                except Exception:
                    pass
                return None
        except Exception as e:
            logger.error(f"Error reading SHM for {feed_id} (ref {shm_ref}): {e}")
            # Release on exception
            try:
                self.frame_buffer.release(shm_ref)
            except Exception:
                pass
            return None

        # Return frame data - SHM already released, we're working with copied bytes
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
                
                # Log SHM buffer stats every 60 seconds
                if now_loop - last_shm_stats_log > 60.0:
                    stats = self.frame_buffer.get_stats()
                    logger.info(f"[SHM-STATS] acquires={stats['acquired_count']}, releases={stats['release_count']}, drops={stats['drop_count']}, free={stats['free_pool_size']}/{stats['pool_size']}")
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
            
            # 1. Update registry metrics with Exponential Moving Average (EMA) to smooth spikes
            entry = self.registry.get_entry(feed_id)
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
            compact_message = {
                "t": WebSocketMessageTypeEnum.VIDEO_FRAME.value,
                "f": feed_id,
                "i": frame_idx,
                "ts": time.time() * 1000,
                "v": vehicles,
                "m": metrics,
                "bg": frame_bytes
            }
            binary_data = msgpack.packb(compact_message, use_bin_type=True)
            logger.info(f"[RESULT_PROC] Msgpack packed: feed={feed_id}, frame_idx={frame_idx}, binary_size={len(binary_data)}, vehicles_count={len(vehicles) if vehicles else 0}")
            
            # 3. Broadcast via broadcaster
            if self.broadcaster:
                await self.broadcaster.broadcast_to_feed_realtime_bytes(
                    feed_id=feed_id,
                    data=binary_data,
                    frame_index=frame_idx
                )
                logger.info(f"[RESULT_PROC] Broadcast sent: feed={feed_id}, frame_idx={frame_idx}")
            else:
                logger.warning(f"[RESULT_PROC] Broadcaster is None; cannot broadcast frame for {feed_id}")

        except Exception as e:
            logger.error(f"Error broadcasting frame data for {feed_id}: {e}", exc_info=True)

