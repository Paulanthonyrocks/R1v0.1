
import cv2
import os
from pathlib import Path
import logging
from queue import Queue, Empty
from threading import Thread, Event
import firebase_admin
from firebase_admin import storage

import numpy as np

logger = logging.getLogger(__name__)

class VideoWriter:
    def __init__(self, feed_id: str, output_dir: str, fps: int, frame_queue: Queue, codec: str = 'avc1'):
        self.feed_id = feed_id
        self.output_path = os.path.join(output_dir, f"{feed_id}.mp4")
        self._tmp_output_path = self.output_path + ".tmp"
        self.fps = fps
        self.resolution = None
        self.frame_queue = frame_queue
        
        # Try codecs in order of preference
        codecs_to_try = [codec, 'avc1', 'H264', 'XVID', 'mp4v']
        self.fourcc = None
        for c in codecs_to_try:
            try:
                self.fourcc = cv2.VideoWriter_fourcc(*c)
                logger.info(f"[{self.feed_id}] Using codec: {c}")
                break
            except:
                continue
        
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
                frame = self.frame_queue.get(timeout=1)
                if frame is None:
                    break
                
                # Verify frame is valid
                if not isinstance(frame, np.ndarray):
                    logger.error(f"[{self.feed_id}] Invalid frame type: {type(frame)}")
                    continue

                # Normalize frame: ensure uint8 and 3 channels BGR
                if frame.dtype != np.uint8:
                    frame = frame.astype(np.uint8)
                if len(frame.shape) == 2:
                    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                elif frame.shape[2] == 4:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

                if self.writer is None:
                    self.resolution = (frame.shape[1], frame.shape[0])
                    # Write to temporary file first to avoid exposing partial/corrupt files
                    self.writer = cv2.VideoWriter(self._tmp_output_path, self.fourcc, self.fps, self.resolution)
                    if not self.writer.isOpened():
                        logger.error(f"[{self.feed_id}] Failed to open VideoWriter. Video will not be saved.")
                        self.writer = None
                        break
                    logger.info(f"[{self.feed_id}] VideoWriter successfully opened with resolution {self.resolution}.")
                
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
