from typing import Dict, Any, List, Optional
import json
import logging
from pathlib import Path
from collections import deque
from app.models.feeds import FeedConfigInfo, FeedOperationalStatusEnum
from app.utils.monitoring import FrameTimer
from app.services.constants import FeedManagerConstants

logger = logging.getLogger("app.services.feed_registry")

class FeedRegistry:
    """
    Manages the registry of feeds and their configurations.
    Handles persistence, feed ID generation, and registry state.
    """
    def __init__(self, persistence_path: Path, initial_feed_id_counter: int = 1):
        self.persistence_path = persistence_path
        self._feed_id_counter = initial_feed_id_counter
        self.process_registry: Dict[str, Dict[str, Any]] = {}

    def generate_feed_id(self, source: str, name_hint: Optional[str] = None) -> str:
        """Generates a unique feed ID based on source and optional name hint."""
        import re
        if name_hint:
            base_name = re.sub(r"[^\w\-.]+", "_", name_hint)
        elif str(source).startswith("webcam:"):
            base_name = f"Webcam_{str(source).split(':')[1]}"
        else:
            base_name = re.sub(r"[^\w\-.]+", "_", Path(source).stem)

        feed_id = f"Feed_{self._feed_id_counter}_{base_name}"
        while feed_id in self.process_registry:
            self._feed_id_counter += 1
            feed_id = f"Feed_{self._feed_id_counter}_{base_name}"
        self._feed_id_counter += 1
        return feed_id

    def save_persistence(self):
        """Saves current feeds configuration to disk."""
        try:
            feeds_data = {}
            for feed_id, entry in self.process_registry.items():
                config_info = entry.get("config_info")
                if config_info:
                    data = config_info.model_dump()
                    data["_is_looped_feed"] = entry.get("is_looped_feed", True)
                    data["_is_sample_feed"] = entry.get("is_sample_feed", False)
                    feeds_data[feed_id] = data

            self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.persistence_path, 'w') as f:
                json.dump(feeds_data, f, indent=2)
            logger.info(f"Saved {len(feeds_data)} feeds to {self.persistence_path}")
        except Exception as e:
            logger.error(f"Failed to save feeds persistence: {e}")

    def load_persistence(self) -> List[str]:
        """Loads feeds configuration from disk. Returns a list of loaded feed IDs."""
        if not self.persistence_path.exists():
            return []

        loaded_ids = []
        try:
            with open(self.persistence_path, 'r') as f:
                feeds_data = json.load(f)

            for feed_id, feed_data in feeds_data.items():
                try:
                    is_looped = feed_data.pop("_is_looped_feed", True)
                    is_sample = feed_data.pop("_is_sample_feed", False)
                    config_info = FeedConfigInfo(**feed_data)

                    self.process_registry[feed_id] = {
                        "process": None,
                        "command_queue": None,
                        "stop_event": None,
                        "reduce_fps_event": None,
                        "status": FeedOperationalStatusEnum.STOPPED,
                        "source": config_info.source_identifier,
                        "start_time": None,
                        "error_message": None,
                        "latest_metrics": None,
                        "metrics_history": deque(maxlen=FeedManagerConstants.MAX_METRICS_HISTORY_LENGTH),
                        "timer": FrameTimer(),
                        "is_sample_feed": is_sample,
                        "is_looped_feed": is_looped,
                        "config_info": config_info,
                        "last_broadcast_time": 0.0,
                    }
                    loaded_ids.append(feed_id)

                    parts = feed_id.split('_')
                    if len(parts) >= 2 and parts[1].isdigit():
                        num = int(parts[1])
                        if num >= self._feed_id_counter:
                            self._feed_id_counter = num + 1
                except Exception as e:
                    logger.error(f"Failed to load feed {feed_id}: {e}")

            logger.info(f"Loaded {len(loaded_ids)} feeds from {self.persistence_path}")
        except Exception as e:
            logger.error(f"Failed to load feeds persistence: {e}")
        return loaded_ids

    def get_entry(self, feed_id: str) -> Optional[Dict[str, Any]]:
        return self.process_registry.get(feed_id)

    def remove_entry(self, feed_id: str):
        if feed_id in self.process_registry:
            del self.process_registry[feed_id]
