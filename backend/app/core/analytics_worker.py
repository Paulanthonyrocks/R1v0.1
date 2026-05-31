
import logging
import queue
import signal
import time
from typing import Any, Optional
from multiprocessing import Queue, Event
from multiprocessing.sharedctypes import Synchronized

logger = logging.getLogger(__name__)

def analytics_worker_process(
    config: Any,
    input_queue: Queue,
    output_queue: Queue,
    stop_event: Event,
    heartbeat: Optional[Synchronized] = None,
) -> None:
    """
    High-level process for handling analytics data.

    This worker forwards analytics data from the input queue to the output queue,
    performing necessary transformations.

    Expected Input Queue Format:
        Tuple: (feed_id, frame_idx, timestamp, vehicles, lanes, lines, feed_metrics, extra)
        - feed_id (str): Unique identifier for the feed.
        - frame_idx (int): Index of the frame in the stream.
        - timestamp (float): Epoch timestamp of the frame.
        - vehicles (list): List of detected vehicle data.
        - lanes (list): Lane boundary information.
        - lines (list): Lane line information.
        - feed_metrics (dict): Aggregated metrics for the feed.
        - extra (dict): Additional metadata.

    Expected Output Queue Format:
        Tuple: (feed_id, feed_metrics, vehicles, lanes, lines)
    """
    logger.info("Analytics worker process started.")

    def signal_handler(signum, frame):
        stop_event.set()
        logger.info("Graceful shutdown signal received in analytics worker.")

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    while not stop_event.is_set():
        data = None
        try:
            # Get data from the input queue with a timeout
            data = input_queue.get(timeout=0.1)
            
            # Unpack data based on the expected contract
            # Format: (feed_id, frame_idx, timestamp, vehicles, lanes, lines, feed_metrics, extra)
            feed_id, frame_idx, timestamp, vehicles, lanes, lines, feed_metrics, extra = data

            # In the future, more complex analytics will go here.
            # For now, we just pass the data through.
            
            try:
                # Output format expected by _read_analytics_results: (feed_id, feed_metrics, vehicles, lanes, lines)
                # Use a small timeout to provide back-pressure instead of immediate drop
                output_queue.put((feed_id, feed_metrics, vehicles, lanes, lines), block=True, timeout=1.0)
            except queue.Full:
                logger.warning(f"[{feed_id}] Analytics output queue is full. Dropping data for frame {frame_idx}.")
                
        except queue.Empty:
            # This is expected when the queue is empty, so we just continue
            pass
        except (ValueError, TypeError) as e: 
            logger.error(f"Malformed analytics data received: {e}. Data: {data}")
        except Exception:
            logger.exception("Unexpected error in analytics worker loop")

        # Update heartbeat at the end of the loop to signal the process is still responsive
        if heartbeat is not None:
            heartbeat.value = time.time()

    logger.info("Analytics worker process stopped.")
