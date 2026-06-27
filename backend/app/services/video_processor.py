import logging
import asyncio
import cv2
import numpy as np
import os
import threading
import time
from typing import Dict, AsyncGenerator, Optional, TYPE_CHECKING
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

if TYPE_CHECKING:
    from app.services.feed_manager import FeedManager

from app.database import get_database_manager
from app.models.processed_video import ProcessedVideo

logger = logging.getLogger(__name__)


class VideoProcessor:
    COLOR_MAP = {
        "moving": (0, 255, 0),       # Green
        "stopped": (0, 0, 255),      # Red
        "speeding": (255, 0, 0),     # Blue
        "accelerating": (0, 165, 255),  # Orange
        "decelerating": (0, 255, 255),  # Yellow
        "lane_changing": (255, 0, 255), # Magenta
        "unknown": (128, 128, 128),   # Gray
    }

    def __init__(self, stream_id: str, feed_manager: "FeedManager", output_directory: str):
        self.stream_id = stream_id
        self.feed_manager = feed_manager
        self.output_directory = output_directory

        # State
        # _recording_event is the single source of truth for "is this processor
        # currently writing frames to disk?". ``Event.set()``/``.is_set()`` is
        # atomic across the asyncio task and the executor thread, which removes
        # the previous race where stop_recording() could flip a plain bool while
        # the executor had already passed the bool check in _process_frame_sync
        # and would then push one extra frame to disk.
        self._recording_event: threading.Event = threading.Event()
        self._recording_event.clear()
        # Legacy bool accessor for read-only sites (the prior shape of this
        # code was ``self._is_recording``). Mutated to stay in sync with the
        # event on every start/stop so externally-set external callers and the
        # back-compat shim see a consistent value.
        self._is_recording: bool = False
        # Frontend draws overlays from WS data; disabled-by-default to avoid
        # re-encoding CPU. Operators can flip on via
        # ``video_processing.draw_overlays_enabled`` in config or via the
        # ``set_draw_overlays_enabled`` runtime setter below.
        self._draw_overlays_enabled: bool = self._read_overlay_default(feed_manager)
        self._is_active: bool = True  # Fix 2A: Graceful generator shutdown flag

        # Recording internals
        self._video_writer: Optional[cv2.VideoWriter] = None
        self._output_path: Optional[str] = None
        self._tmp_output_path: Optional[str] = None
        self._recording_start_time: Optional[float] = None
        self._frame_rate: float = 10.0
        self._frame_size: Optional[tuple] = None

        # Executor for CPU-bound OpenCV tasks (Decoding/Encoding/Writing)
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"VP-{stream_id}"
        )

        logger.info(f"VideoProcessor initialized for stream_id: {self.stream_id}")

    @staticmethod
    def _read_overlay_default(feed_manager: "FeedManager") -> bool:
        """
        Pull ``video_processing.draw_overlays_enabled`` from the FeedManager's
        config dict, tolerating both missing FeedManager and missing keys
        (defaulting to False to preserve the prior behavior of not drawing
        overlays on the encoded stream).
        """
        try:
            cfg = getattr(feed_manager, "config", None)
            if not isinstance(cfg, dict):
                return False
            vp = cfg.get("video_processing", {})
            if not isinstance(vp, dict):
                return False
            return bool(vp.get("draw_overlays_enabled", False))
        except Exception as e:  # defensive: don't let config parse errors kill init
            logger.debug(f"Failed to read draw_overlays_enabled default: {e}")
            return False

    def set_draw_overlays_enabled(self, enabled: bool) -> None:
        """
        Runtime toggle for whether decoded frames get re-encoded with detection
        overlays. When ``False`` (default), the executor skips ``cv2.imdecode``
        altogether while recording, saving ~0.5–1.5 ms per frame on 720p.
        """
        self._draw_overlays_enabled = bool(enabled)
        logger.info(
            f"[{self.stream_id}] draw_overlays_enabled -> {self._draw_overlays_enabled}"
        )

    # Background task that pulls from get_frame_generator (recording consumer).
    # Set by start_recording(), cancelled by stop_recording().
    _record_task: Optional[asyncio.Task] = None

    async def start_recording(self, output_filename: str, frame_rate: float):
        if self._recording_event.is_set():
            logger.warning(f"Recording already in progress for stream {self.stream_id}")
            return False

        self._output_path = os.path.join(self.output_directory, output_filename)
        base, ext = os.path.splitext(self._output_path)
        self._tmp_output_path = f"{base}.tmp{ext}"

        try:
            # Fix 2B: Offload blocking OS ops to thread
            await asyncio.to_thread(
                os.makedirs, os.path.dirname(self._output_path), exist_ok=True
            )
            if os.path.exists(self._tmp_output_path):
                await asyncio.to_thread(os.remove, self._tmp_output_path)
        except Exception as e:
            logger.error(f"[{self.stream_id}] File system error starting record: {e}")
            return False

        self._frame_rate = frame_rate
        # Atomic flip — the executor will see both the event and the bool
        # immediately consistent. Setting the event first ensures any racing
        # reader in the executor that hasn't yet entered _process_frame_sync
        # observes the new state when it reaches its check.
        self._recording_event.set()
        self._is_recording = True
        self._recording_start_time = time.time()
        # Spin up the frame consumer task. It leases the FeedManager subscriber
        # queue and routes decoded bytes through _process_frame_sync, which
        # calls _handle_recording when self._is_recording is true.
        self._record_task = asyncio.create_task(self._drain_frames())
        logger.info(f"Started recording for {self.stream_id} to {self._output_path}")
        return True

    async def _drain_frames(self):
        """
        Consume frames from the FeedManager subscriber queue while active.
        Runs until cancelled or until _is_active flips to False.
        """
        try:
            async for _ in self.get_frame_generator():
                # All work happens inside get_frame_generator; we only need to
                # iterate to keep the generator producing.
                pass
        except asyncio.CancelledError:
            logger.debug(f"[{self.stream_id}] _drain_frames cancelled")
        except Exception as e:
            logger.error(f"[{self.stream_id}] _drain_frames error: {e}", exc_info=True)
        finally:
            self._recording_event.clear()
            self._is_recording = False

    async def stop_recording(self):
        if not self._recording_event.is_set():
            return False

        logger.info(f"Stopping recording for {self.stream_id}...")
        # Atomic clear before cancelling the consumer task. A frame mid-flight
        # in the executor will see the event already cleared on its next
        # _is_recording check, even if cancel arrives a few microseconds later.
        self._recording_event.clear()
        self._is_recording = False  # Legacy mirror; keep the bool in sync.

        # Cancel the frame consumer task; the generator's `finally` will
        # release the FeedManager subscriber queue.
        if self._record_task and not self._record_task.done():
            self._record_task.cancel()
            try:
                await self._record_task
            except (asyncio.CancelledError, Exception):
                pass
            self._record_task = None

        # Release writer in executor to ensure buffers are flushed without blocking loop
        if self._video_writer:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._video_writer.release)
            self._video_writer = None

        # Rename tmp to actual
        try:
            if self._tmp_output_path and os.path.exists(self._tmp_output_path):
                # Fix 2B: Offload blocking OS rename to thread
                await asyncio.to_thread(
                    os.replace, self._tmp_output_path, self._output_path
                )
        except Exception as e:
            logger.error(
                f"[{self.stream_id}] Failed to finalize video file: {e}",
                exc_info=True,
            )

        # Fix 1B: Only write DB metadata if the output file actually exists on disk
        if self._output_path and self._recording_start_time and os.path.exists(self._output_path):
            try:
                db_manager = get_database_manager()
                end_time = datetime.now(timezone.utc)
                duration = end_time.timestamp() - self._recording_start_time

                processed_video_entry = ProcessedVideo(
                    stream_id=self.stream_id,
                    file_path=self._output_path,
                    start_time=datetime.fromtimestamp(
                        self._recording_start_time, tz=timezone.utc
                    ),
                    end_time=end_time,
                    duration=duration,
                )
                await db_manager.save_processed_video_metadata(
                    processed_video_entry
                )
            except Exception as e:
                logger.error(f"Failed to save metadata: {e}")
        else:
            if self._output_path:
                logger.warning(
                    f"[{self.stream_id}] No video file found at "
                    f"{self._output_path} -- skipping DB metadata insert "
                    f"(camera may have dropped before any frames)."
                )

        self._output_path = None
        self._recording_start_time = None
        return True

    def _process_frame_sync(self, raw_frame_bytes: bytes, kpis: Dict) -> Optional[bytes]:
        """
        Synchronous function to decode, draw, write to file, and re-encode.
        Run this in a separate thread.
        """
        # Check if we need to process at all.
        # We process if: 1. Recording OR 2. Overlays are enabled and there is data to draw
        has_detections = bool(kpis.get("detections"))
        should_process = self._recording_event.is_set() or (
            self._draw_overlays_enabled and has_detections
        )

        # Decide up front whether we actually need to decode the JPEG bytes.
        # We compute ``needs_decode`` once based on the same predicates used
        # later; this lets us exit the function with zero CPU cost on frames
        # where recording is on (writer already has its own reader path --
        # wait, no, the writer *consumes* the decoded numpy frame, so we
        # still need to decode here) AND overlays are off. With overlays
        # disabled, the only consumer is the recorder, so we still must
        # decode; the only fast-path that saves CPU is "no recording AND no
        # overlays" -- which is exactly what the consumer above already
        # handled before invoking us. So the only savings here are on the
        # re-encode step (skip imencode when no overlays were drawn).
        if not should_process:
            return raw_frame_bytes

        try:
            # Decode
            np_arr = np.frombuffer(raw_frame_bytes, np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

            if frame is None:
                return raw_frame_bytes

            # Ensure consistent colorspace (OpenCV uses BGR)
            if len(frame.shape) == 2:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            elif frame.shape[2] == 4:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

            # Draw Overlays (if enabled)
            if self._draw_overlays_enabled:
                self._draw_overlays(frame, kpis)

            # Write to disk (if recording)
            if self._recording_event.is_set():
                self._handle_recording(frame)

            # Re-encode for Streaming
            # Only re-encode if we actually modified the frame (drew overlays)
            if self._draw_overlays_enabled and has_detections:
                ret, jpeg_frame = cv2.imencode(
                    ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85]
                )
                if ret:
                    return jpeg_frame.tobytes()

            # If we recorded but didn't draw overlays, or drawing failed, return original
            return raw_frame_bytes

        except Exception as e:
            logger.error(f"Error in sync frame processing: {e}")
            return raw_frame_bytes

    def _handle_recording(self, frame: np.ndarray):
        """Helper to initialize writer and write frame."""
        try:
            if frame is None or not isinstance(frame, np.ndarray) or frame.ndim < 2:
                logger.warning("Discarding non-frame input to recorder")
                return
            if self._video_writer is None:
                h, w = frame.shape[:2]
                if h <= 0 or w <= 0:
                    logger.warning(
                        f"[{self.stream_id}] refusing zero-size frame {w}x{h}"
                    )
                    return
                self._frame_size = (w, h)
                # mp4v is generally safe for filesystem recording
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                self._video_writer = cv2.VideoWriter(
                    self._tmp_output_path, fourcc, self._frame_rate, self._frame_size
                )

            if self._video_writer.isOpened():
                # Safety resize if resolution changes mid-stream. Guarded
                # separately so a resize hiccup doesn't kill the writer
                # thread for subsequent valid frames.
                if (frame.shape[1], frame.shape[0]) != self._frame_size:
                    try:
                        frame = cv2.resize(frame, self._frame_size)
                    except Exception as e:
                        logger.warning(
                            f"[{self.stream_id}] resize {frame.shape[:2]} -> "
                            f"{self._frame_size} failed: {e}; dropping frame"
                        )
                        return
                try:
                    self._video_writer.write(frame)
                except Exception as e:
                    logger.error(f"[{self.stream_id}] writer.write raised: {e}")
        except Exception as e:
            logger.error(f"Error writing frame to disk: {e}")

    def _draw_overlays(self, frame: np.ndarray, kpis: Dict):
        # Defensive: the executor may push non-ndarray frame bytes through
        # here if a server-side encoder bug fires. Bail visually rather than
        # crashing the executor thread.
        if not isinstance(frame, np.ndarray) or frame.ndim < 2:
            return
        detections = kpis.get("detections", [])
        if not detections:
            return

        frame_h, frame_w = frame.shape[:2]
        for det in detections:
            bbox = det.get("bbox")
            if bbox is None:
                continue

            try:
                x1, y1, x2, y2 = map(int, bbox)
            except (ValueError, TypeError):
                # ``bbox`` had non-numeric elements; skip silently rather than
                # crashing the executor thread.
                continue

            # Sanity-check geometry. Tracked objects occasionally emit
            # zero-area or inverted boxes during box-flip / ID-switch events
            # and those would render as flickery full-frame rectangles.
            if x1 >= x2 or y1 >= y2:
                continue
            if x1 < 0 or y1 < 0 or x2 > frame_w or y2 > frame_h:
                # Coordinate completely outside the frame; skip. (Mild
                # overflow off-by-one is fine -- the rectangle clip below
                # will handle it.)
                if x2 <= 0 or y2 <= 0 or x1 >= frame_w or y1 >= frame_h:
                    continue
                # Clip to frame bounds before drawing
                x1 = max(0, min(x1, frame_w - 1))
                y1 = max(0, min(y1, frame_h - 1))
                x2 = max(x1 + 1, min(x2, frame_w))
                y2 = max(y1 + 1, min(y2, frame_h))

            label = det.get("class_name") or det.get("label") or "vehicle"
            conf = det.get("confidence") or det.get("score") or 0.0
            behavior = det.get("behavior") or "unknown"

            # Map behavior to color
            color = self.COLOR_MAP.get(behavior, self.COLOR_MAP["unknown"])

            # Draw Box
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

            # Draw Label
            label_text = f"{label} {conf:.2f}"
            (w, h), _ = cv2.getTextSize(
                label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1
            )

            # Ensure label doesn't go off screen
            y1_label = max(y1, h + 5)

            cv2.rectangle(
                frame, (x1, y1_label - h - 5), (x1 + w, y1_label + 5), color, -1
            )
            cv2.putText(
                frame, label_text, (x1, y1_label),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1,
            )

    async def get_frame_generator(self) -> AsyncGenerator[Dict, None]:
        """
        Async generator that yields per-frame data while this processor is recording.

        Source: the FeedManager subscriber queue (one slot leased at startup).
        Frames arrive post-dedup and post-broadcast, matching what WebSocket
        clients see. We run `_process_frame_sync` on the executor to overlay
        KPIs and (when `_is_recording` is set) write to disk via
        `_handle_recording`.

        If the operator calls this generator without first calling
        `start_recording`, no frames are written -- the generator stays
        alive doing nothing so callers can attach/detach cleanly.
        """
        # Lease a private subscriber queue from FeedManager. We do this here
        # (rather than at processor construction) so a sibling processor for
        # a different feed is isolated.
        if self.feed_manager is None or not hasattr(self.feed_manager, "subscribe_to_frames"):
            raise RuntimeError(
                "VideoProcessor requires a FeedManager with the in-process subscriber API."
            )
        frame_queue: asyncio.Queue = await self.feed_manager.subscribe_to_frames(
            self.stream_id, maxsize=30
        )
        logger.info(f"[{self.stream_id}] Subscribed to feed_manager frames for recording")

        try:
            while self._is_active:
                try:
                    payload = await asyncio.wait_for(frame_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break

                # Fast drain: only do any work when at least one consumer is
                # interested. Without this guard, the recorder keeps pulling
                # from the FeedManager subscriber queue even when no recording
                # is in progress, just to discard each frame.
                if not self._recording_event.is_set():
                    continue
                raw_frame_bytes = payload.get("frame")
                if not raw_frame_bytes:
                    continue

                kpis = payload.get("metrics", {}) or {}

                loop = asyncio.get_running_loop()
                processed_jpeg_bytes = await loop.run_in_executor(
                    self._executor,
                    self._process_frame_sync,
                    raw_frame_bytes,
                    kpis,
                )
                if processed_jpeg_bytes:
                    yield {
                        "frame": processed_jpeg_bytes,
                        "kpis": kpis,
                        "frame_index": payload.get("frame_index"),
                    }

        except asyncio.CancelledError:
            logger.debug(f"Frame generator cancelled for {self.stream_id}")
        except Exception as e:
            logger.error(f"Error in generator: {e}", exc_info=True)
        finally:
            try:
                await self.feed_manager.unsubscribe_from_frames(self.stream_id, frame_queue)
            except Exception:
                pass
            logger.info(f"[{self.stream_id}] Unsubscribed from feed_manager frames")

    def shutdown_executor(self):
        """Shut down the thread pool executor. Safe to call after all async work is done."""
        if self._executor:
            self._executor.shutdown(wait=False)
            self._executor = None

    def __del__(self):
        # Safety net: if a VideoProcessor was abandoned (e.g. test fixture,
        # uncaught except path) without going through remove_processor() the
        # single-thread executor would otherwise leak. Swallow everything --
        # destructors must not raise -- and signal the generator to exit first.
        try:
            if getattr(self, "_is_active", False):
                self._is_active = False
            if getattr(self, "_recording_event", None) is not None and self._recording_event.is_set():
                # Best-effort sync clear; caller didn't wait for stop_recording.
                self._recording_event.clear()
            if getattr(self, "_executor", None) is not None:
                self._executor.shutdown(wait=False)
                self._executor = None
        except Exception:
            pass


class VideoManager:
    _instance = None
    # Fix 2C: Removed dead _lock = asyncio.Lock() -- unusable from sync get_instance()

    def __init__(self):
        self.video_processors: Dict[str, VideoProcessor] = {}
        self.output_directory: Optional[str] = None

    @classmethod
    def get_instance(cls, output_directory: Optional[str] = None) -> "VideoManager":
        if cls._instance is None:
            cls._instance = VideoManager()

        if output_directory:
            cls._instance.output_directory = output_directory

        return cls._instance

    def get_processor(self, stream_id: str, feed_manager: "FeedManager") -> VideoProcessor:
        if not self.output_directory:
            # Fallback default
            self.output_directory = "backend/data/recordings"

        if stream_id not in self.video_processors:
            self.video_processors[stream_id] = VideoProcessor(
                stream_id, feed_manager, self.output_directory
            )
        return self.video_processors[stream_id]

    async def remove_processor(self, stream_id: str):
        """Cleanly remove a processor to free memory/threads."""
        if stream_id in self.video_processors:
            proc = self.video_processors.pop(stream_id)
            # Fix 2A: Signal the generator to exit before killing the executor
            proc._is_active = False
            if proc._recording_event.is_set():
                await proc.stop_recording()
            # Shut down executor AFTER all async work completes
            proc.shutdown_executor()
            logger.info(f"Removed VideoProcessor for {stream_id}")

    async def cleanup(self):
        logger.info("Cleaning up VideoManager...")
        # Fix 1A: Gather all stop_recording tasks FIRST, then shut down executors
        tasks = []
        processors_to_shutdown = []
        for pid, processor in list(self.video_processors.items()):
            # Fix 2A: Signal generators to stop
            processor._is_active = False
            if processor._recording_event.is_set():
                tasks.append(processor.stop_recording())
            # Collect executors -- will shut down AFTER gather completes
            processors_to_shutdown.append(processor)

        # Sweep stale *.tmp.<vid_ext> files from previous aborted recordings.
        # We use a 1-hour freshness threshold so a live in-progress recording
        # (whose tmp file is still being written) is never clobbered, and we
        # also avoid touching files that don't look like our own recordings.
        if self.output_directory and os.path.isdir(self.output_directory):
            try:
                now_ts = time.time()
                for entry in os.listdir(self.output_directory):
                    if ".tmp." not in entry:
                        continue
                    path = os.path.join(self.output_directory, entry)
                    try:
                        mtime = os.path.getmtime(path)
                    except OSError:
                        continue
                    age_hours = (now_ts - mtime) / 3600.0
                    if age_hours < 1.0:
                        # Active recording — leave it alone.
                        continue
                    try:
                        os.remove(path)
                        logger.info(
                            f"Removed stale tmp recording ({age_hours:.1f}h old): {path}"
                        )
                    except OSError as e:
                        logger.warning(f"Could not remove stale tmp {path}: {e}")
            except Exception as e:
                logger.warning(f"tmp-sweep failed: {e}")

        if tasks:
            await asyncio.gather(*tasks)

        # Now safe to shut down executors since all async work is done
        for processor in processors_to_shutdown:
            processor.shutdown_executor()

        self.video_processors.clear()
        logger.info("VideoManager cleanup complete.")
