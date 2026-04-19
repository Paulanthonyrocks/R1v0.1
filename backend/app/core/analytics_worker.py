
import logging
import queue
import signal
import time
from typing import Any

logger = logging.getLogger(__name__)

def analytics_worker_process(
    config,
    input_queue: Any,
    output_queue: Any,
    stop_event: Any,
    heartbeat: Any = None,
):
    """
    High-level process for handling analytics data.
    """
    logger.info("Analytics worker process started.")

    def signal_handler(signum, frame):
        stop_event.set()
        logger.info("Graceful shutdown signal received in analytics worker.")

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    while not stop_event.is_set():
        # Update heartbeat to signal the process is alive
        if heartbeat is not None:
            heartbeat.value = time.time()

        try:
            # Get data from the input queue with a timeout
            data = input_queue.get(timeout=0.1)
            # Data format: (feed_id, frame_idx, timestamp, vehicles, lane_boundaries, lane_lines, metrics, extra)
            feed_id, frame_idx, timestamp, vehicles, lanes, lines, feed_metrics, extra = data

            # In the future, more complex analytics will go here.
            # For now, we just pass the data through.
            
            try:
                # Output format expected by _read_analytics_results: (feed_id, feed_metrics, vehicles, lanes, lines)
                output_queue.put((feed_id, feed_metrics, vehicles, lanes, lines), block=False)
            except queue.Full:
                logger.warning(f"[{feed_id}] Analytics output queue is full. Dropping data.")
                
        except queue.Empty:
            # This is expected when the queue is empty, so we just continue
            continue
        except (ValueError, TypeError) as e: 
            logger.error(f"Malformed analytics data received: {e}. Data: {data}")
            continue
        except Exception as e:
            logger.error(f"Error in analytics worker: {e}", exc_info=True)

    logger.info("Analytics worker process stopped.")
