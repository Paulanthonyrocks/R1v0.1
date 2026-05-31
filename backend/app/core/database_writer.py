import logging
import queue
import signal
import threading
import time
import os
from collections import Counter
from typing import Any, Dict, List

from app.utils.database import DatabaseManager, DatabaseError

logger = logging.getLogger("app.core.database_writer")

# Maximum consecutive DB failures before circuit-breaker trips
_MAX_CONSECUTIVE_FAILURES = 50
# Maximum items to re-enqueue per failure (prevents queue oscillation)
_MAX_REENQUEUE = 500
# Drain timeout in seconds during graceful shutdown
_DRAIN_TIMEOUT = 5.0
# Minimum batch accumulation window (seconds) for write amplification reduction
_BATCH_ACCUMULATE_WINDOW = 0.05


def database_writer_process(
    config: Dict[str, Any],
    db_queue: Any,
    stop_event: Any,
    heartbeat: Any,
):
    """Process dedicated to writing data to the database.

    Handles vehicle_data (primary), safety_alert, and identified_vehicle
    message types. Validates all items, re-enqueues on transient failures,
    drains the queue on shutdown, and includes a circuit-breaker for
    persistent DB outages.
    """

    batch_size = config.get("database", {}).get("batch_size", 100)
    retention_days = config.get("database", {}).get("retention_days", 7)
    max_attempts = config.get("database", {}).get("connection_retries", 6)

    def signal_handler(signum, frame):
        logger.info("Database writer received termination signal.")
        stop_event.set()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    db_manager = None
    consecutive_failures = 0
    circuit_trip_count = 0

    for attempt in range(1, max_attempts + 1):
        try:
            db_manager = DatabaseManager(config)
            logger.info("Database writer process started and connected to DB.")
            break
        except Exception as e:
            wait_time = min(2 ** attempt, 32)
            logger.warning(f"DB connection attempt {attempt}/{max_attempts} failed: {e}. Retrying in {wait_time}s...")
            time.sleep(wait_time)
    else:
        logger.error(f"Database writer: all {max_attempts} DB connection attempts exhausted. Exiting.")
        return

    last_metrics_log = time.time()

    def prune_worker():
        """Background thread to prune old data without blocking the main loop."""
        last_prune = 0.0
        while not stop_event.is_set():
            try:
                # Check every 10 seconds to remain responsive to stop_event
                time.sleep(10)
                now = time.time()
                if now - last_prune >= 3600:
                    if stop_event.is_set():
                        break
                    # Create separate connection to avoid thread-safety issues
                    with DatabaseManager(config) as prune_db:
                        pruned = prune_db.prune_old_data(retention_days=retention_days)
                        if pruned:
                            logger.info(f"Background prune: removed {pruned} old records.")
                    last_prune = now
            except Exception as e:
                logger.error(f"Background prune failed: {e}")

    prune_thread = threading.Thread(target=prune_worker, daemon=True)
    prune_thread.start()

    try:
        while not stop_event.is_set():
            try:
                if heartbeat:
                    heartbeat.value = (time.time(), os.getpid())

                items: List[Dict] = []
                try:
                    first_item = db_queue.get(timeout=0.1)
                    items.append(first_item)
                except queue.Empty:
                    continue

                batch_deadline = time.time() + _BATCH_ACCUMULATE_WINDOW
                while len(items) < batch_size:
                    remaining = batch_deadline - time.time()
                    if remaining <= 0:
                        break
                    try:
                        item = db_queue.get(timeout=remaining)
                        items.append(item)
                    except queue.Empty:
                        break

                valid_items = []
                malformed_count = 0
                for item in items:
                    if isinstance(item, dict) and "type" in item:
                        valid_items.append(item)
                    else:
                        malformed_count += 1
                        logger.warning(f"Discarding malformed db_queue item (type={type(item).__name__}): {item!r}")

                if malformed_count:
                    logger.warning(f"Discarded {malformed_count} malformed items in this batch.")

                vehicle_batch = [it for it in valid_items if it["type"] == "vehicle_data"]
                alert_items = [it for it in valid_items if it["type"] == "safety_alert"]
                identified_batch = [it for it in valid_items if it["type"] == "identified_vehicle"]
                
                failed_items = []

                if vehicle_batch:
                    try:
                        rows = db_manager.save_vehicle_data_batch(vehicle_batch)
                        logger.debug(f"Wrote {rows} vehicle tracks to DB.")
                    except (DatabaseError, Exception) as e:
                        logger.error(f"Failed to write {len(vehicle_batch)} vehicle records: {e}")
                        failed_items.extend(vehicle_batch)

                if alert_items:
                    alert_data = []
                    malformed_alerts = 0
                    for it in alert_items:
                        if "data" in it:
                            alert_data.append(it["data"])
                        else:
                            malformed_alerts += 1
                            logger.warning(f"Malformed safety alert missing 'data' key: {it!r}")
                            failed_items.append(it)
                    if alert_data:
                        try:
                            db_manager.insert_alerts_batch(alert_data)
                        except (DatabaseError, Exception) as e:
                            logger.error(f"Failed to write {len(alert_data)} alerts: {e}")
                            failed_items.extend(it for it in alert_items if "data" in it)
                    if malformed_alerts:
                        logger.warning(f"Discarded {malformed_alerts} malformed alerts in this batch.")

                if identified_batch:
                    try:
                        rows = db_manager.upsert_identified_vehicles_batch(identified_batch)
                        logger.debug(f"Upserted {rows} identified vehicles to DB.")
                    except (DatabaseError, Exception) as e:
                        logger.error(f"Failed to upsert {len(identified_batch)} identified vehicles: {e}")
                        failed_items.extend(identified_batch)

                if failed_items:
                    dropped_count = len(failed_items) - _MAX_REENQUEUE
                    if dropped_count > 0:
                        logger.warning(f"Failure batch size ({len(failed_items)}) exceeds _MAX_REENQUEUE. Permanently dropping {dropped_count} items.")
                    re_enqueued = 0
                    for item in failed_items[:_MAX_REENQUEUE]:
                        try:
                            db_queue.put_nowait(item)
                            re_enqueued += 1
                        except queue.Full:
                            logger.warning(f"Queue full during re-enqueue. Dropped {len(failed_items) - re_enqueued} items.")
                            break
                    if re_enqueued:
                        logger.info(f"Re-enqueued {re_enqueued} failed items.")

                if failed_items:
                    consecutive_failures += 1
                    if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                        circuit_trip_count += 1
                        pause_duration = min(30 * (2 ** (circuit_trip_count - 1)), 300)
                        logger.critical(f"Circuit-breaker tripped (trip #{circuit_trip_count}) after {consecutive_failures} consecutive failures. Pausing {pause_duration}s before retry.")
                        time.sleep(pause_duration)
                        consecutive_failures = 0
                else:
                    consecutive_failures = 0
                    # circuit_trip_count is NOT reset on success to preserve progressive backoff

                now = time.time()
                total = len(valid_items)
                if total > 0 and (now - last_metrics_log > 30.0):
                    try:
                        q_depth = db_queue.qsize()
                    except (NotImplementedError, Exception):
                        q_depth = "unknown"
                    logger.info(f"Batch summary (30s): {total} items processed (vehicles={len(vehicle_batch)}, alerts={len(alert_items)}, identified={len(identified_batch)}, failures={len(failed_items)}), queue_depth={q_depth}")
                    last_metrics_log = now

            except Exception as e:
                logger.error(f"Error in database writer loop: {e}", exc_info=True)
                time.sleep(1)

    except Exception as e:
        logger.error(f"Fatal error in database writer process: {e}", exc_info=True)
    finally:
        drain_start = time.time()
        drained = 0
        while (time.time() - drain_start) < _DRAIN_TIMEOUT:
            try:
                item = db_queue.get_nowait()
                if isinstance(item, dict):
                    try:
                        msg_type = item.get("type")
                        if msg_type == "vehicle_data":
                            db_manager.save_vehicle_data_batch([item])
                        elif msg_type == "safety_alert":
                            if "data" in item:
                                db_manager.insert_alerts_batch([item["data"]])
                            else:
                                logger.warning(f"Malformed alert during drain: {item!r}")
                        elif msg_type == "identified_vehicle":
                            db_manager.upsert_identified_vehicles_batch([item])
                        drained += 1
                    except Exception as e:
                        logger.critical(f"Critical data loss during drain for {msg_type}: {e}. Item: {item!r}")
                else:
                    logger.warning(f"Skipping malformed drain item: {item!r}")
            except queue.Empty:
                break

        if drained:
            logger.info(f"Drained {drained} items during shutdown.")

        if prune_thread:
            prune_thread.join(timeout=1.0)

        if db_manager:
            try:
                pruned = db_manager.prune_old_data(retention_days=retention_days)
                if pruned:
                    logger.info(f"Final prune: removed {pruned} old records.")
            except Exception as e:
                logger.error(f"Final prune failed: {e}")
            db_manager.close()

        logger.info("Database writer process terminated.")
