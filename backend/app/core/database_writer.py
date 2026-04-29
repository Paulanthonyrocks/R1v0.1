import logging
import queue
import signal
import time
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

    # --- Issue #7: Extract config values once before the loop ---
    batch_size = config.get("database", {}).get("batch_size", 100)
    retention_days = config.get("database", {}).get("retention_days", 7)

    # --- Issue #13: import signal is now at the top of the file ---

    def signal_handler(signum, frame):
        logger.info("Database writer received termination signal.")
        stop_event.set()

    # --- Issue #1: Register the signal handler ---
    signal.signal(signal.SIGTERM, signal_handler)

    db_manager = None
    consecutive_failures = 0
    last_prune_time = 0.0  # Set after DB connection (Issue #14)

    # --- Issue #6: Retry DB connection on startup with backoff ---
    for attempt in range(1, 7):  # Up to 6 attempts (~63s total backoff)
        try:
            db_manager = DatabaseManager(config)
            logger.info("Database writer process started and connected to DB.")
            break
        except Exception as e:
            wait_time = min(2 ** attempt, 32)
            logger.warning(
                f"DB connection attempt {attempt}/6 failed: {e}. "
                f"Retrying in {wait_time}s..."
            )
            time.sleep(wait_time)
    else:
        logger.error(
            "Database writer: all DB connection attempts exhausted. Exiting."
        )
        return

    # --- Issue #14: Start prune timer AFTER connection is confirmed ---
    last_prune_time = time.time()

    try:
        while not stop_event.is_set():
            try:
                # --- Issue #5: Always update heartbeat, even on empty queue ---
                if heartbeat:
                    heartbeat.value = time.time()

                # --- Issue #10: Use blocking get with timeout instead of sleep ---
                items: List[Dict] = []
                try:
                    first_item = db_queue.get(timeout=0.1)
                    items.append(first_item)
                except queue.Empty:
                    # Queue is empty — heartbeat was already updated above
                    continue

                # --- Issue #11: Batch accumulation with a minimum time window ---
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

                # --- Issue #4: Validate item schema before processing ---
                valid_items = []
                malformed_count = 0
                for item in items:
                    if isinstance(item, dict) and "type" in item:
                        valid_items.append(item)
                    else:
                        malformed_count += 1
                        logger.warning(
                            f"Discarding malformed db_queue item "
                            f"(type={type(item).__name__}): {item!r}"
                        )

                if malformed_count:
                    logger.warning(
                        f"Discarded {malformed_count} malformed items in this batch."
                    )

                # --- Issue #2: Process ALL message types, not just safety_alert ---
                vehicle_batch = [
                    it for it in valid_items if it["type"] == "vehicle_data"
                ]
                alert_items = [
                    it for it in valid_items if it["type"] == "safety_alert"
                ]
                identified_batch = [
                    it for it in valid_items if it["type"] == "identified_vehicle"
                ]
                other_count = len(valid_items) - (
                    len(vehicle_batch) + len(alert_items) + len(identified_batch)
                )
                if other_count:
                    type_counts = Counter(it["type"] for it in valid_items)
                    logger.debug(f"Unhandled item types in batch: {dict(type_counts)}")

                # --- Issue #3: Write succeeds before items are considered consumed ---
                # All write operations happen below. If any raises, we re-enqueue
                # the unsaved items instead of silently dropping them.
                failed_items = []

                # Vehicle tracking data — primary write path
                if vehicle_batch:
                    try:
                        rows = db_manager.save_vehicle_data_batch(vehicle_batch)
                        logger.debug(f"Wrote {rows} vehicle tracks to DB.")
                    except (DatabaseError, Exception) as e:
                        logger.error(
                            f"Failed to write {len(vehicle_batch)} vehicle records: {e}"
                        )
                        failed_items.extend(vehicle_batch)

                # Safety alerts
                if alert_items:
                    alert_data = [it["data"] for it in alert_items if "data" in it]
                    if alert_data:
                        try:
                            db_manager.insert_alerts_batch(alert_data)
                        except (DatabaseError, Exception) as e:
                            logger.error(
                                f"Failed to write {len(alert_data)} alerts: {e}"
                            )
                            failed_items.extend(
                                it for it in alert_items if "data" in it
                            )

                # Identified vehicles
                if identified_batch:
                    for iv in identified_batch:
                        try:
                            db_manager.upsert_identified_vehicle(iv)
                        except (DatabaseError, Exception) as e:
                            logger.error(
                                f"Failed to upsert identified vehicle: {e}"
                            )
                            failed_items.append(iv)

                # --- Re-enqueue failed items (Issue #3 fix) ---
                if failed_items:
                    re_enqueued = 0
                    for item in failed_items[:_MAX_REENQUEUE]:
                        try:
                            db_queue.put_nowait(item)
                            re_enqueued += 1
                        except queue.Full:
                            logger.warning(
                                "Queue full during re-enqueue. "
                                f"Dropped {len(failed_items) - re_enqueued} items."
                            )
                            break
                    if re_enqueued:
                        logger.info(f"Re-enqueued {re_enqueued} failed items.")

                # --- Issue #12: Circuit-breaker on repeated failures ---
                if failed_items:
                    consecutive_failures += 1
                    if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                        logger.critical(
                            f"Circuit-breaker tripped after "
                            f"{consecutive_failures} consecutive failures. "
                            f"Pausing 30s before retry."
                        )
                        time.sleep(30)
                        consecutive_failures = 0  # Reset after pause
                else:
                    consecutive_failures = 0

                # --- Periodic pruning (Issue #8) ---
                if time.time() - last_prune_time > 3600:
                    try:
                        pruned = db_manager.prune_old_data(
                            retention_days=retention_days
                        )
                        logger.info(f"Pruned {pruned} old records from DB.")
                    except Exception as e:
                        logger.error(f"Prune failed: {e}")
                    last_prune_time = time.time()

                # --- Issue #15: Operational metrics ---
                total = len(valid_items)
                if total > 0:
                    logger.info(
                        f"Batch complete: {total} items processed "
                        f"(vehicles={len(vehicle_batch)}, "
                        f"alerts={len(alert_items)}, "
                        f"identified={len(identified_batch)}, "
                        f"failures={len(failed_items)}), "
                        f"queue_depth={db_queue.qsize()}"
                    )

            except Exception as e:
                logger.error(
                    f"Error in database writer loop: {e}", exc_info=True
                )
                time.sleep(1)

    except Exception as e:
        logger.error(
            f"Fatal error in database writer process: {e}", exc_info=True
        )
    finally:
        # --- Issue #9: Drain the queue on shutdown ---
        drain_start = time.time()
        drained = 0
        while (time.time() - drain_start) < _DRAIN_TIMEOUT:
            try:
                item = db_queue.get_nowait()
                # Attempt to write each drained item
                if isinstance(item, dict):
                    try:
                        msg_type = item.get("type")
                        if msg_type == "vehicle_data":
                            db_manager.save_vehicle_data_batch([item])
                        elif msg_type == "safety_alert" and "data" in item:
                            db_manager.insert_alerts_batch([item["data"]])
                        elif msg_type == "identified_vehicle":
                            db_manager.upsert_identified_vehicle(item)
                        drained += 1
                    except Exception as e:
                        logger.error(f"Failed to drain item: {e}")
                else:
                    logger.warning(f"Skipping malformed drain item: {item!r}")
            except queue.Empty:
                break

        if drained:
            logger.info(f"Drained {drained} items during shutdown.")

        # --- Issue #8: Final prune on shutdown ---
        if db_manager:
            try:
                pruned = db_manager.prune_old_data(
                    retention_days=retention_days
                )
                if pruned:
                    logger.info(f"Final prune: removed {pruned} old records.")
            except Exception as e:
                logger.error(f"Final prune failed: {e}")

            db_manager.close()

        logger.info("Database writer process terminated.")
