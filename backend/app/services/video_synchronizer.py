def _handle_video_ended(self, feed_id: str) -> None:
    """Handles the event when the video ends, potentially restarting it."""
    self._logger.debug(f"Video ended: {feed_id}")
    process_entry = self._processes.get(feed_id)
    if process_entry and process_entry["process"].is_alive():
        self._logger.info(f"Restarting video: {feed_id}")
        self._stop_video(feed_id) # Assuming this method exists
        self._start_video(feed_id) # Assuming this method exists
    else:
        self._logger.warning(f"Video process for {feed_id} not found or not alive.")
