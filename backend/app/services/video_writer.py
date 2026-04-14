import cv2
import os
from datetime import datetime, timezone
from pathlib import Path
import logging
from queue import Queue, Empty
from threading import Thread, Event
from firebase_admin import storage

import numpy as np

logger = logging.getLogger(__name__)

class VideoWriter:
    def __init__(self, feed_id: str, output_dir: str, fps: int, frame_queue: Queue, codec: str = 'avc1'):
        self.feed_id = feed_id
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        
        # Use .mp4 for H.264/avc1 and mp4v, fallback to .avi for others if needed
        ext = ".mp4" if codec in ['avc1', 'mp4v', 'H264'] else ".avi"
        self.output_path = os.path.join(output_dir, f"{feed_id}_{timestamp}{ext}")
        self._tmp_output_path = f"{os.path.splitext(self.output_path)[0]}.tmp{ext}"
        
        self.fps = fps
        self.resolution = None
        self.frame_queue = frame_queue

        # Check for GPU config
        from app.config import get_current_config
        self.perf_cfg = get_current_config().performance
        
        # Prepare codec preference list
        self._codec_candidates = []
        if self.perf_cfg.video_gpu_acceleration:
            # NVIDIA hardware acceleration candidates
            self._codec_candidates.extend(['h264_nvenc', 'hevc_nvenc'])

        seen = set(self._codec_candidates)
        for c in [codec, 'avc1', 'H264', 'mp4v', 'XVID', 'MJPG']:
            if c and c not in seen:
                self._codec_candidates.append(c)
                seen.add(c)
        self.fourcc = None
        
        self.writer = None
        self.stop_event = Event()
        self.thread = Thread(target=self._write_loop)

        Path(output_dir).mkdir(parents=True, exist_ok=True)
        # Clean up any stale tmp file from a previous crash
        try:
            if os.path.exists(self._tmp_output_path):
                os.remove(self._tmp_output_path)
        except Exception as e:
            logger.warning(f"[{self.feed_id}] Unable to remove stale tmp file {self._tmp_output_path}: {e}")
        self._first_frame_saved = False # Flag to save only the first frame

    def start(self):
        self.thread.start()
        return True

    def stop(self):
        self.stop_event.set()
        self.thread.join()
        if self.writer:
            logger.debug(f"[{self.feed_id}] Releasing VideoWriter for {self.output_path}.")
            self.writer.release()
            self.writer = None
            logger.debug(f"[{self.feed_id}] VideoWriter released.")
        else:
            logger.warning(f"[{self.feed_id}] VideoWriter was not initialized or already released for {self.output_path}.")
        # After writer is released and background thread has exited, atomically move tmp to final
        try:
            if os.path.exists(self._tmp_output_path):
                os.replace(self._tmp_output_path, self.output_path)
                logger.info(f"[{self.feed_id}] Finalized video atomically: {self.output_path}")
        except Exception as e:
            logger.error(f"[{self.feed_id}] Failed to finalize video file {self._tmp_output_path} -> {self.output_path}: {e}", exc_info=True)
        logger.info(f"[{self.feed_id}] VideoWriter stopped and video saved to {self.output_path}")
        # self._upload_to_firebase_and_cleanup()

    def _write_loop(self):
        frames_written = 0
        while not self.stop_event.is_set():
            try:
                frame_data = self.frame_queue.get(timeout=1)
                if frame_data is None:
                    break
                
                frame = None
                if isinstance(frame_data, np.ndarray):
                    frame = frame_data
                elif isinstance(frame_data, bytes):
                    np_arr = np.frombuffer(frame_data, np.uint8)
                    frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                
                if frame is None:
                    logger.error(f"[{self.feed_id}] Invalid frame data received. Type: {type(frame_data)}")
                    continue

                # Normalize frame: ensure uint8 and 3 channels BGR
                if frame.dtype != np.uint8:
                    frame = frame.astype(np.uint8)
                if len(frame.shape) == 2:
                    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                elif frame.shape[2] == 4:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

                # Save the first frame as an image for debugging
                if not self._first_frame_saved:
                    try:
                        first_frame_path = os.path.join(os.path.dirname(self.output_path), f"{self.feed_id}_first_frame.jpg")
                        cv2.imwrite(first_frame_path, frame)
                        logger.info(f"[{self.feed_id}] Saved first frame to {first_frame_path}")
                        self._first_frame_saved = True
                    except Exception as e_save:
                        logger.error(f"[{self.feed_id}] Error saving first frame: {e_save}", exc_info=True)

                if self.writer is None:
                    self.resolution = (frame.shape[1], frame.shape[0])
                    # Attempt codecs in order until a writer opens successfully
                    opened = False
                    for c in self._codec_candidates:
                        fourcc = cv2.VideoWriter_fourcc(*c)
                        # Explicitly try .avi extension for this diagnostic run
                        writer = cv2.VideoWriter(self._tmp_output_path, fourcc, self.fps, self.resolution)
                        if writer.isOpened():
                            self.writer = writer
                            self.fourcc = fourcc
                            logger.info(f"[{self.feed_id}] Opened VideoWriter with codec '{c}', fps={self.fps}, res={self.resolution} -> {self._tmp_output_path}")
                            opened = True
                            break
                        else:
                            try:
                                writer.release()
                            except Exception:
                                pass
                            logger.warning(f"[{self.feed_id}] Failed to open VideoWriter with codec '{c}'. Trying next.")
                    if not opened:
                        logger.error(f"[{self.feed_id}] Could not open any VideoWriter codec from {self._codec_candidates}. Aborting write loop.")
                        break
                
                if self.writer and self.writer.isOpened():
                    # Ensure subsequent frames match initial resolution
                    if (frame.shape[1], frame.shape[0]) != self.resolution:
                        try:
                            frame = cv2.resize(frame, self.resolution)
                        except Exception as e:
                            logger.error(f"[{self.feed_id}] Failed to resize frame to {self.resolution}: {e}")
                            continue
                    self.writer.write(frame)
                    frames_written += 1
            except Empty:
                continue
            except Exception as e:
                logger.error(f"[{self.feed_id}] Error in VideoWriter loop: {e}", exc_info=True)
                break
        logger.info(f"[{self.feed_id}] Write loop finished. Total frames written: {frames_written} to {self.output_path}")

    def _upload_to_firebase_and_cleanup(self):
        try:
            bucket = storage.bucket()
            blob = bucket.blob(f"videos/{self.feed_id}.mp4")
            blob.upload_from_filename(self.output_path)
            logger.info(f"[{self.feed_id}] Successfully uploaded video to Firebase Storage at: {blob.public_url}")
            
            # Clean up the local file after successful upload
            # try:
            #     os.remove(self.output_path)
            #     logger.info(f"[{self.feed_id}] Successfully removed local video file: {self.output_path}")
            # except OSError as e:
            #     logger.error(f"[{self.feed_id}] Error removing local video file {self.output_path}: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"[{self.feed_id}] Failed to upload video to Firebase Storage: {e}", exc_info=True)
