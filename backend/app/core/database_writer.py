import logging
import queue
import time
from typing import Any, Dict

from app.utils.database import DatabaseManager, DatabaseError

logger = logging.getLogger("app.core.database_writer")

def database_writer_process(
    config: Dict[str, Any],
    db_queue: Any,
    stop_event: Any,
    heartbeat: Any
):
    """Process dedicated to writing data to the database."""
    db_manager = None
    last_prune_time = time.time()

    def signal_handler(signum, frame):
        logger.info("Database writer received termination signal.")
        stop_event.set()

    try:
        db_manager = DatabaseManager(config)
        logger.info("Database writer process started and connected to DB.")

        while not stop_event.is_set():
            try:
                # Batch items from the queue
                items = []
                while len(items) < config.get("database", {}).get("batch_size", 100):
                    try:
                        item = db_queue.get_nowait()
                        items.append(item)
                    except queue.Empty:
                        break

                if not items:
                    time.sleep(0.1) # Wait for more items
                    continue

                # Process batch
                alerts_batch = [item["data"] for item in items if item["type"] == "safety_alert"]
                if alerts_batch:
                    try:
                        db_manager.insert_alerts(alerts_batch)
                    except DatabaseError as e:
                        logger.error(f"Failed to insert batch of alerts: {e}")

                # Update heartbeat
                if heartbeat:
                    heartbeat.value = time.time()

                # Periodic pruning
                if time.time() - last_prune_time > 3600: # Every hour
                    db_manager.prune_old_data(config=config)
                    last_prune_time = time.time()

            except Exception as e:
                logger.error(f"Error in database writer loop: {e}", exc_info=True)
                time.sleep(1)

    except Exception as e:
        logger.error(f"Fatal error in database writer process: {e}", exc_info=True)
    finally:
        if db_manager:
            db_manager.close()
        logger.info("Database writer process terminated.")
