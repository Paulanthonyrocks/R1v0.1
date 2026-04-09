
import logging
import queue
import signal
import time
from multiprocessing import Event, Queue as MPQueue

logger = logging.getLogger(__name__)

def analytics_worker_process(
    config,
    input_queue: MPQueue,
    output_queue: MPQueue,
    stop_event: Event,
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
        try:
            # Get data from the input queue with a timeout
            data = input_queue.get(timeout=0.1)
            feed_id, feed_metrics, vehicles, lanes, lines = data

            # In the future, more complex analytics will go here.
            # For now, we just pass the data through.
            
            try:
                output_queue.put((feed_id, feed_metrics, vehicles, lanes, lines), block=False)
            except queue.Full:
                logger.warning(f"[{feed_id}] Analytics output queue is full. Dropping data.")
                
        except queue.Empty:
            # This is expected when the queue is empty, so we just continue
            continue
        except ValueError: 
            logger.debug(f"Malformed analytics data: {data}")
            continue
        except Exception as e:
            logger.error(f"Error in analytics worker: {e}", exc_info=True)

    logger.info("Analytics worker process stopped.")
