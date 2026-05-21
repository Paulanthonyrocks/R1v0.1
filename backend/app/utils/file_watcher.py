import logging
import asyncio
from pathlib import Path

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_INSTALLED = True
except ImportError:
    WATCHDOG_INSTALLED = False
    # Define dummy classes to prevent crashes
    class Observer:
        def schedule(self, *args, **kwargs): pass
        def start(self): pass
        def stop(self): pass
        def join(self): pass

    class FileSystemEventHandler:
        def on_created(self, event): pass

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
        # We must be careful with asyncio.get_running_loop() here because
        # the watcher might be initialized before the loop is running.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
            
        self.event_handler = VideoFileEventHandler(loop, self.on_new_video_callback)
        self.logger = logging.getLogger(self.__class__.__name__)

    def start(self):
        if not WATCHDOG_INSTALLED:
            self.logger.warning("Watchdog not installed. FileSystemWatcher is disabled.")
            return

        if not self.path.is_dir():
            self.logger.error(f"Monitoring path does not exist or is not a directory: {self.path}")
            return

        self.observer.schedule(self.event_handler, str(self.path), recursive=False)
        self.observer.start()
        self.logger.info(f"Started monitoring for new video files in: {self.path}")

    def stop(self):
        if not WATCHDOG_INSTALLED:
            return

        self.observer.stop()
        self.observer.join()
        self.logger.info(f"Stopped monitoring for new video files in: {self.path}")
