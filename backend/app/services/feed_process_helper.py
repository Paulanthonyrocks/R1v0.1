# backend/app/services/feed_process_helper.py

import asyncio
import logging
import multiprocessing as mp # Use mp for alias to avoid conflict with queue module
from multiprocessing import Process, Value # Value can be directly imported
from multiprocessing.queues import Queue as MPQueue # Specific import for Queue type hint
from multiprocessing.synchronize import Event as MPEvent # Specific import for Event type hint
import os # For os.getpid() or other os-level operations if needed by process_video indirectly
import queue # For queue.Empty exception

# Placeholder for process_video, assuming it will be in app.core.processing_worker
# This will be resolved when the actual worker is implemented/integrated.
try:
    from app.core.processing_worker import process_video
except ImportError:
    def process_video(*args, **kwargs): # type: ignore
        logger = logging.getLogger(__name__)
        logger.error("process_video function not found. Ensure app.core.processing_worker is available.")
        # Simulate some work or just exit to prevent hanging if this placeholder is ever called
        if 'stop_event' in kwargs and isinstance(kwargs['stop_event'], mp.Event):
            kwargs['stop_event'].wait(timeout=1) # Wait for a short period
        pass


logger = logging.getLogger(__name__)

class FeedProcessHelper:
    """
    Helper class to manage the lifecycle of worker processes for video feeds.
    """
    def __init__(self, config: dict):
        """
        Initializes the FeedProcessHelper.
        Args:
            config: The main application configuration dictionary.
        """
        self.config = config
        logger.info("FeedProcessHelper initialized.")

    def launch_worker_process(
        self,
        feed_id: str,
        source: str,
        result_queue: MPQueue, # type: ignore
        stop_event: MPEvent, # type: ignore
        reduce_fps_event: MPEvent, # type: ignore
        global_fps_value: Value # type: ignore
    ) -> Process:
        """
        Launches a new worker process for a given video feed.

        Args:
            feed_id: Unique identifier for the feed.
            source: The video source (URL, file path, or camera index).
            result_queue: Multiprocessing queue to send results to.
            stop_event: Multiprocessing event to signal the worker to stop.
            reduce_fps_event: Multiprocessing event to signal FPS reduction.
            global_fps_value: Multiprocessing shared value for global FPS.

        Returns:
            The started multiprocessing.Process instance.
        """
        logger.info(f"Attempting to launch worker process for feed_id: {feed_id} with source: {source}")

        # Arguments for the process_video function
        # These should match the signature of your actual process_video function
        worker_args = (
            feed_id,
            source,
            self.config, # Pass the main config
            result_queue,
            stop_event,
            reduce_fps_event,
            global_fps_value
        )

        try:
            process = Process(
                target=process_video,
                args=worker_args,
                name=f"FeedWorker-{feed_id}" # Helpful for debugging
            )
            process.daemon = True # Often desired for worker processes
            process.start()
            logger.info(f"Successfully launched worker process for feed_id: {feed_id} (PID: {process.pid})")
            return process
        except Exception as e:
            logger.error(f"Failed to launch worker process for feed_id {feed_id}: {e}", exc_info=True)
            # Re-raise or handle as appropriate for your application's error strategy
            raise

    def signal_stop_event(self, feed_id: str, stop_event: Optional[MPEvent]): # type: ignore
        """
        Safely sets the stop event for a worker process.
        Args:
            feed_id: Unique identifier for the feed.
            stop_event: The multiprocessing event to signal.
        """
        if stop_event:
            logger.info(f"Signaling stop event for feed_id: {feed_id}")
            try:
                stop_event.set()
            except Exception as e:
                logger.error(f"Error signaling stop event for feed_id {feed_id}: {e}", exc_info=True)
        else:
            logger.warning(f"No stop event provided for feed_id: {feed_id}, cannot signal.")

    async def join_worker_process(self, feed_id: str, process: Optional[Process], timeout: float = 1.5):
        """
        Joins the worker process with a timeout. Terminates if it doesn't exit gracefully.
        This method is async and uses run_in_executor for non-blocking join/terminate.
        Args:
            feed_id: Unique identifier for the feed.
            process: The multiprocessing.Process instance to join.
            timeout: Time in seconds to wait for the process to join.
        """
        if process and process.is_alive():
            logger.info(f"Attempting to join worker process for feed_id: {feed_id} (PID: {process.pid}) with timeout: {timeout}s")
            loop = asyncio.get_running_loop()

            try:
                # Run the blocking join in an executor
                await loop.run_in_executor(None, process.join, timeout)

                if process.is_alive():
                    logger.warning(f"Worker process for feed_id: {feed_id} (PID: {process.pid}) did not exit gracefully after {timeout}s. Terminating.")
                    # Run the blocking terminate in an executor
                    await loop.run_in_executor(None, process.terminate)
                    # Optionally, wait a very short period for termination to complete
                    await asyncio.sleep(0.1)
                    if process.is_alive(): # Check again
                         logger.error(f"Worker process for feed_id: {feed_id} (PID: {process.pid}) could not be terminated.")
                    else:
                         logger.info(f"Worker process for feed_id: {feed_id} (PID: {process.pid}) terminated successfully.")
                else:
                    logger.info(f"Worker process for feed_id: {feed_id} (PID: {process.pid}) joined successfully. Exit code: {process.exitcode}")
            except Exception as e:
                logger.error(f"Error during join/terminate for feed_id {feed_id} (PID: {process.pid}): {e}", exc_info=True)
        elif process:
            logger.info(f"Worker process for feed_id: {feed_id} (PID: {process.pid}) is not alive or already joined. Exit code: {process.exitcode}")
        else:
            logger.warning(f"No process object provided for feed_id: {feed_id} to join.")

    def close_process_handle(self, feed_id: str, process: Optional[Process]):
        """
        Closes the process handle if it exists and is supported (Process.close()).
        Args:
            feed_id: Unique identifier for the feed.
            process: The multiprocessing.Process instance.
        """
        if process:
            logger.info(f"Attempting to close process handle for feed_id: {feed_id} (PID: {process.pid if process.pid else 'N/A'})")
            try:
                if hasattr(process, 'close') and callable(process.close):
                    process.close()
                    logger.info(f"Process handle closed for feed_id: {feed_id}")
                else:
                    logger.info(f"Process object for feed_id: {feed_id} does not have a 'close' method (normal for non-Windows or if already closed).")
            except Exception as e: # Catch potential errors like "handle is invalid" if already closed/terminated
                logger.error(f"Error closing process handle for feed_id {feed_id}: {e}", exc_info=True)
        else:
            logger.debug(f"No process object provided for feed_id: {feed_id} to close handle.")


    def drain_and_close_queue(self, feed_id: str, queue_obj: Optional[MPQueue]): # type: ignore
        """
        Drains all items from a multiprocessing queue and then closes it.
        Args:
            feed_id: Unique identifier for the feed associated with the queue.
            queue_obj: The multiprocessing.Queue to drain and close.
        """
        if queue_obj:
            logger.info(f"Draining and closing queue for feed_id: {feed_id}...")
            drained_count = 0
            while True:
                try:
                    # Non-blocking get to avoid hanging if queue is unexpectedly empty
                    item = queue_obj.get_nowait()
                    drained_count += 1
                    # Process or log the item if necessary, e.g., logger.debug(f"Drained item: {item}")
                except queue.Empty:
                    logger.debug(f"Queue for feed_id: {feed_id} is now empty after draining {drained_count} items.")
                    break # Queue is empty
                except (EOFError, OSError) as e: # Catch errors if queue is already broken/closed
                    logger.warning(f"Error while draining queue for feed_id {feed_id} (possibly already closed/broken): {e}")
                    break
                except Exception as e:
                    logger.error(f"Unexpected error draining queue for feed_id {feed_id}: {e}", exc_info=True)
                    break # Safety break on other errors

            try:
                queue_obj.close()
                # Forcing join of the queue's feeder thread. This is important!
                queue_obj.join_thread()
                logger.info(f"Queue for feed_id: {feed_id} closed and feeder thread joined.")
            except Exception as e:
                logger.error(f"Error closing queue for feed_id {feed_id}: {e}", exc_info=True)
        else:
            logger.debug(f"No queue object provided for feed_id: {feed_id} to drain/close.")
