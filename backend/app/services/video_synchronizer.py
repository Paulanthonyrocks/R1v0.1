def _handle_video_ended(self, feed_id: str) -> None:
    """Handles the event when the video ends."""
    self._logger.debug(f"Video ended: {feed_id}")
    if feed_id in self._processes and self._processes[feed_id]["process"].is_alive():
        self._logger.info(f"Restarting video: {feed_id}")
        self._stop_video(feed_id)
        self._start_video(feed_id)
    else:
        self._logger.warning(f"Video process not found or not alive: {feed_id}")