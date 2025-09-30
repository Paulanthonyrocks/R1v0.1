
import cv2
import os
from pathlib import Path
import logging
from queue import Queue, Empty
from threading import Thread, Event

logger = logging.getLogger(__name__)

class VideoWriter:
    def __init__(self, feed_id: str, output_dir: str, fps: int, resolution: tuple[int, int], frame_queue: Queue, codec: str = 'mp4v'):
        self.feed_id = feed_id
        self.output_path = os.path.join(output_dir, f"{feed_id}.mp4")
        self.fps = fps
        self.resolution = resolution
        self.frame_queue = frame_queue
        self.fourcc = cv2.VideoWriter_fourcc(*codec)
        self.writer = None
        self.stop_event = Event()
        self.thread = Thread(target=self._write_loop)

        Path(output_dir).mkdir(parents=True, exist_ok=True)

    def start(self):
        self.writer = cv2.VideoWriter(self.output_path, self.fourcc, self.fps, self.resolution)
        if not self.writer.isOpened():
            logger.error(f"[{self.feed_id}] Failed to open VideoWriter for {self.output_path}")
            return
        self.thread.start()
        logger.info(f"[{self.feed_id}] VideoWriter started for {self.output_path}")

    def stop(self):
        self.stop_event.set()
        self.thread.join()
        if self.writer:
            self.writer.release()
            self.writer = None
        logger.info(f"[{self.feed_id}] VideoWriter stopped and video saved to {self.output_path}")

    def _write_loop(self):
        while not self.stop_event.is_set():
            try:
                frame = self.frame_queue.get(timeout=1)
                if frame is None:  # Sentinel value to stop
                    break
                if self.writer:
                    self.writer.write(frame)
            except Empty:
                continue
            except Exception as e:
                logger.error(f"[{self.feed_id}] Error in VideoWriter loop: {e}", exc_info=True)
                break
