import logging
import asyncio
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logger = logging.getLogger(__name__)

class VideoFileEventHandler(FileSystemEventHandler):
    def __init__(self, loop: asyncio.AbstractEventLoop, on_new_video_callback):
        super().__init__()
        self.loop = loop
        self.on_new_video_callback = on_new_video_callback
        self.logger = logging.getLogger(self.__class__.__name__)

    def on_created(self, event):
        if not event.is_directory and self._is_video_file(Path(event.src_path)):
            self.logger.info(f"Detected new video file: {event.src_path}")
            # Schedule the callback to be run in the asyncio event loop
            self.loop.call_soon_threadsafe(self.on_new_video_callback, event.src_path)

    def _is_video_file(self, file_path: Path) -> bool:
        # Simple check for common video extensions
        return file_path.suffix.lower() in ['.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv']

class FileSystemWatcher:
    def __init__(self, path: str, on_new_video_callback):
        self.path = Path(path)
        self.on_new_video_callback = on_new_video_callback
        self.observer = Observer()
        self.event_handler = VideoFileEventHandler(asyncio.get_running_loop(), self.on_new_video_callback)
        self.logger = logging.getLogger(self.__class__.__name__)

    def start(self):
        if not self.path.is_dir():
            self.logger.error(f"Monitoring path does not exist or is not a directory: {self.path}")
            return

        self.observer.schedule(self.event_handler, str(self.path), recursive=False)
        self.observer.start()
        self.logger.info(f"Started monitoring for new video files in: {self.path}")

    def stop(self):
        self.observer.stop()
        self.observer.join()
        self.logger.info(f"Stopped monitoring for new video files in: {self.path}")
