from typing import Dict, Any, List
import asyncio
import logging
import time
from app.models.feeds import FeedOperationalStatusEnum
from app.services.constants import FeedManagerConstants

logger = logging.getLogger("app.services.feed_watchdog")

class FeedWatchdog:
    """
    Monitors the health of video feeds and inference workers.
    Triggers restarts of failed feeds with exponential backoff.
    """
    def __init__(self, 
                 registry: Any, 
                 pool_manager: Any, 
                 restart_callback):
        self.registry = registry
        self.pool_manager = pool_manager
        self.restart_callback = restart_callback
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    async def watchdog_loop(self):
        """Periodically checks if processing workers are alive and responsive."""
        logger.info("Watchdog task started.")
        
        # Track restart attempts and next allowed restart time per feed
        restart_attempts: Dict[str, int] = {}
        next_restart_time: Dict[str, float] = {}

        # Track restart attempts and next allowed restart time per worker
        worker_restart_attempts: Dict[int, int] = {}
        worker_next_restart_time: Dict[int, float] = {}

        while not self._stop_flag:
            try:
                if self._stop_flag:
                    break
                await asyncio.sleep(FeedManagerConstants.WATCHDOG_INTERVAL)

                feeds_to_restart = []
                # Registry provides access to the process_registry
                for feed_id, entry in self.registry.process_registry.items():
                    if entry["status"] not in [
                        FeedOperationalStatusEnum.RUNNING,
                        FeedOperationalStatusEnum.STARTING,
                    ]:
                        continue

                    process = entry.get("process")
                    if process and not process.is_alive():
                        exit_code = process.exitcode if process else "N/A"
                        if exit_code is not None and exit_code != 0:
                            logger.warning(
                                f"Video process {feed_id} exited with error code: {exit_code}"
                            )
                        else:
                            logger.info(f"Video process {feed_id} ended (likely reached EOF).")
                        
                        # Check if we can restart based on exponential backoff
                        now = time.time()
                        if now >= next_restart_time.get(feed_id, 0):
                            feeds_to_restart.append(feed_id)
                        else:
                            logger.debug(f"Watchdog: Feed {feed_id} is in backoff until {next_restart_time[feed_id]}")

                for feed_id in feeds_to_restart:
                    try:
                        logger.info(f"Watchdog: Restarting video feed: {feed_id}")
                        await self.restart_callback(feed_id)
                        logger.info(f"Watchdog: Video feed restarted successfully: {feed_id}")
                    except Exception as e:
                        logger.error(f"Watchdog: Failed to restart video feed {feed_id}: {e}")
                        # Increment backoff
                        attempts = restart_attempts.get(feed_id, 0) + 1
                        restart_attempts[feed_id] = attempts
                        delay = min(5 * (2 ** (attempts - 1)), 3600)
                        next_restart_time[feed_id] = time.time() + delay
                        logger.warning(f"Watchdog: Feed {feed_id} restart failed. Backing off for {delay}s (attempt {attempts})")

                # Reset backoff for feeds that are now healthy
                for feed_id, entry in self.registry.process_registry.items():
                    if entry["status"] == FeedOperationalStatusEnum.RUNNING:
                        restart_attempts.pop(feed_id, None)
                        next_restart_time.pop(feed_id, None)

                # Check inference pool workers
                dead_workers = self.pool_manager.get_dead_workers()
                if dead_workers:
                    now = time.time()
                    workers_to_respawn = []
                    for wid in dead_workers:
                        if now >= worker_next_restart_time.get(wid, 0):
                            workers_to_respawn.append(wid)
                            # Increment attempt count for the next failure detection
                            attempts = worker_restart_attempts.get(wid, 0) + 1
                            worker_restart_attempts[wid] = attempts
                            # Set a provisional backoff in case it dies again immediately
                            delay = min(5 * (2 ** (attempts - 1)), 3600)
                            worker_next_restart_time[wid] = now + delay
                        else:
                            logger.debug(f"Watchdog: Worker {wid} is in backoff until {worker_next_restart_time[wid]}")

                    if workers_to_respawn:
                        try:
                            logger.info(f"Watchdog: Respawning dead inference workers: {workers_to_respawn}")
                            await self.pool_manager.respawn_dead_workers(workers_to_respawn)
                        except Exception as e:
                            logger.error(f"Watchdog: Failed to respawn dead workers {workers_to_respawn}: {e}")

                # Reset backoff for workers that are now healthy
                for wid in list(worker_restart_attempts.keys()):
                    if wid in self.pool_manager._inference_pool:
                        p = self.pool_manager._inference_pool[wid]
                        if p and p.is_alive():
                            worker_restart_attempts.pop(wid, None)
                            worker_next_restart_time.pop(wid, None)

            except asyncio.CancelledError:

                break
            except Exception as e:
                logger.error(f"Error in watchdog loop: {e}", exc_info=True)
                await asyncio.sleep(10.0)
