def _handle_video_ended(self, feed_id: str) -> None:
    """Handles the event when the video ends."""
    self._logger.debug(f"Video ended: {feed_id}")
    
    # Get exit status if process exists
    exit_code = 0
    if feed_id in self._processes:
        process = self._processes[feed_id]["process"]
        exit_code = process.exitcode if process.exitcode is not None else 0

    if exit_code != 0:
        self._logger.warning(
            f"Video process {feed_id} exited with error code: {exit_code}"
        )
    
    # Restart regardless of exit status
    try:
        self._logger.info(f"Restarting video feed: {feed_id}")
        self._stop_video(feed_id)
        self._start_video(feed_id)
        self._logger.info(f"Video feed restarted successfully: {feed_id}")
    except Exception as e:
        self._logger.error(
            f"Failed to restart video feed {feed_id}: {e}", 
            exc_info=True
        )
