# data_consumer_improved.py
# data_consumer_improved.py
import json
import logging
import signal
import time
from datetime import datetime, timezone
from kafka import KafkaConsumer # Removed TopicPartition
from kafka.errors import NoBrokersAvailable # Removed KafkaError
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure
from pydantic import ValidationError

# Import configurations and models
import config
from models import RawTrafficDataInputModel, ProcessedTrafficDataDBModel, RegionalAggregatedTrafficDBModel

# --- Global Variables ---
shutdown_flag = False
logger = logging.getLogger(__name__)

# In-memory store for windowed data: {region_id: {window_key: {sensor_id: [scores]}}}
# This state is lost on crash if not persisted externally (advanced feature)
windowed_data_store = {}


# --- Logging Setup ---
def setup_logging():
    logging.basicConfig(level=config.LOG_LEVEL, format=config.LOG_FORMAT)
    # Silence some verbose Kafka logs if desired, or set their level higher
    logging.getLogger("kafka").setLevel(logging.WARNING)

# --- Dead Letter Queue (DLQ) Placeholder ---


def send_to_dlq(message_value: dict, error_details: str, kafka_producer=None, dlq_topic=None):
    """
    Placeholder: Simulate sending a failed message to a Dead Letter Queue.
    Real implementation: Publish to another Kafka topic, store in DB, or write to file.
    """
    # dlq_message = { # F841: local variable 'dlq_message' is assigned to but never used
    #     "original_message": message_value,
    #     "error": error_details,
    #     "timestamp": time.time()
    # }
    logger.error(f"DLQ: Error: {error_details}. Message: {message_value}")
    # Example DLQ send logic (if kafka_producer and dlq_topic were passed and configured):
    # if kafka_producer and dlq_topic:
    #     try:
    #         kafka_producer.send(dlq_topic, value=dlq_message)
    #         logger.info(f"Message sent to DLQ topic: {dlq_topic}")
    #     except Exception as e:
    #         logger.error(f"Failed to send to DLQ {dlq_topic}: {e}", exc_info=True)
    # else:
    #     logger.warning("DLQ Kafka producer/topic not configured. Message logged only.")

# --- Signal Handler for Graceful Shutdown ---
def signal_handler(signum, frame):
    global shutdown_flag
    logger.info(
        f"Signal {signal.Signals(signum).name} received. Initiating graceful shutdown...")
    shutdown_flag = True


# --- Kafka and MongoDB Initialization ---
def initialize_kafka_consumer():
    try:
        consumer = KafkaConsumer(
            config.KAFKA_RAW_TOPIC,
            bootstrap_servers=config.KAFKA_BROKERS,
            group_id=config.KAFKA_GROUP_ID,
            auto_offset_reset=config.KAFKA_AUTO_OFFSET_RESET,
            enable_auto_commit=False,  # Manual commits
            value_deserializer=lambda x: json.loads(x.decode('utf-8')) if x else None,
            consumer_timeout_ms=config.KAFKA_POLL_TIMEOUT_MS # Allow loop to check shutdown
        )
        logger.info(f"Kafka consumer connected: {config.KAFKA_BROKERS}, topic: {config.KAFKA_RAW_TOPIC}")
        return consumer
    except NoBrokersAvailable:
        logger.error(f"Kafka brokers at {config.KAFKA_BROKERS} not available. Exiting.", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Unexpected Kafka consumer init error: {e}", exc_info=True)
        raise

def initialize_mongodb_client():
    try:
        client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=5000) # Added timeout
        client.admin.command('ismaster') # Verify connection
        logger.info(f"MongoDB connected: {config.MONGO_URI}")
        db = client[config.MONGO_DB_NAME]
        processed_collection = db[config.PROCESSED_DATA_COLLECTION_NAME]
        aggregated_collection = db[config.REGIONAL_AGGREGATED_COLLECTION_NAME]
        return client, processed_collection, aggregated_collection
    except ConnectionFailure:
        logger.error(f"MongoDB connection failed at {config.MONGO_URI}. Exiting.", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Unexpected MongoDB client init error: {e}", exc_info=True)
        raise

# --- Data Processing Logic ---


def calculate_congestion_score(vehicle_count: int, average_speed: float) -> float:
    if average_speed <= 0:
        congestion_score = min(vehicle_count * 2, 100)
    else:
        congestion_score = min((vehicle_count / average_speed) * 10, 100)
    return round(congestion_score, 2)


def process_raw_data(raw_data_model: RawTrafficDataInputModel) -> ProcessedTrafficDataDBModel:
    congestion_score = calculate_congestion_score(
        raw_data_model.vehicle_count,
        raw_data_model.average_speed
    )
    processed_data = ProcessedTrafficDataDBModel(
        **raw_data_model.model_dump(),
        congestion_score=congestion_score,
        processing_timestamp=datetime.now(timezone.utc)
    )
    return processed_data

# --- Database Operations (with Idempotency) ---


def store_processed_data_to_db(collection, data: ProcessedTrafficDataDBModel):
    document_id = f"{data.sensor_id}_{int(data.timestamp.timestamp())}"
    retries = 3
    for attempt in range(retries):
        try:
            collection.update_one(
                {'_id': document_id},
                {'$set': data.model_dump()},
                upsert=True
            )
            logger.debug(f"Upserted processed data with ID: {document_id}")
            return  # Success
        except OperationFailure as e:
            logger.warning(f"MongoDB op failure (processed data ID: {document_id}), "
                           f"attempt {attempt + 1}/{retries}: {e}")
            if attempt < retries - 1: time.sleep(config.MONGO_RETRY_DELAY_SECONDS)
            else:
                logger.error(f"Failed to store processed data (ID: {document_id}) "
                             f"after {retries} attempts.", exc_info=True)
                raise # Re-raise to be caught by caller
        except Exception as e:
            logger.error(f"Unexpected error storing processed data (ID: {document_id}), "
                           f"attempt {attempt + 1}/{retries}: {e}", exc_info=True)
            if attempt < retries - 1: time.sleep(config.MONGO_RETRY_DELAY_SECONDS)
            else: raise
    # This line should not be reached if retries fail and raise an exception.
    logger.critical(f"Exited retry loop without success/exception for processed ID {document_id}")

def store_aggregated_data_to_db(collection, data: RegionalAggregatedTrafficDBModel):
    document_id = f"{data.region_id}_{int(data.window_start_time.timestamp())}"
    retries = 3
    for attempt in range(retries):
        try:
            collection.update_one({'_id': document_id}, {'$set': data.model_dump()}, upsert=True)
            logger.info(f"Upserted regional aggregated data for region {data.region_id}, "
                        f"window {data.window_start_time.isoformat()}")
            return # Success
        except OperationFailure as e:
            logger.warning(f"MongoDB op failure (aggregated data ID: {document_id}), "
                           f"attempt {attempt + 1}/{retries}: {e}")
            if attempt < retries - 1: time.sleep(config.MONGO_RETRY_DELAY_SECONDS)
            else:
                logger.error(f"Failed to store aggregated data (ID: {document_id}) "
                             f"after {retries} attempts.", exc_info=True)
                raise
        except Exception as e:
            logger.error(f"Unexpected error storing aggregated data (ID: {document_id}), "
                           f"attempt {attempt + 1}/{retries}: {e}", exc_info=True)
            if attempt < retries - 1: time.sleep(config.MONGO_RETRY_DELAY_SECONDS)
            else: raise
    logger.critical(f"Exited retry loop without success/exception for aggregated ID {document_id}")

# --- Windowing Logic ---
def get_window_key(timestamp: float) -> int:
    return int(timestamp // config.WINDOW_SIZE_SECONDS) * config.WINDOW_SIZE_SECONDS


def add_to_windowed_data(processed_data: ProcessedTrafficDataDBModel):
    global windowed_data_store
    sensor_id = processed_data.sensor_id
    congestion_score = processed_data.congestion_score
    event_timestamp = processed_data.timestamp

    region_id = config.SENSOR_TO_REGION_MAPPING.get(sensor_id)
    if not region_id:
        logger.warning(f"Sensor ID {sensor_id} not in SENSOR_TO_REGION_MAPPING. Skipping aggregation.")
        return

    window_key_ts = get_window_key(event_timestamp.timestamp())
    # Ensure path exists in dict before appending
    region_windows = windowed_data_store.setdefault(region_id, {})
    window_sensors = region_windows.setdefault(window_key_ts, {})
    sensor_scores = window_sensors.setdefault(sensor_id, [])
    sensor_scores.append(congestion_score)

    logger.debug(f"Added score {congestion_score} for sensor {sensor_id} to region {region_id}, window {window_key_ts}")

def process_completed_windows(current_processing_time: float, agg_collection):
    """Processes and aggregates data from completed time windows."""
    global windowed_data_store # Ensure modification of global var
    completed_windows_to_process: List[Tuple[str, int]] = []

    # Identify completed windows
    for region_id, region_data in list(windowed_data_store.items()): # list() for safe iteration
        for window_key_ts in list(region_data.keys()): # list() for safe iteration
            # Window is complete if its end time is before or at current_processing_time
            if window_key_ts + config.WINDOW_SIZE_SECONDS <= current_processing_time:
                completed_windows_to_process.append((region_id, window_key_ts))

    for region_id, window_key_ts in completed_windows_to_process:
        logger.info(f"Processing completed window for region {region_id}, window_ts {window_key_ts}")
        region_specific_data = windowed_data_store.get(region_id, {})
        if window_key_ts not in region_specific_data:
            logger.warning(f"Window {window_key_ts} for region {region_id} not found. Already processed?")
            continue

        sensor_data_for_window = region_specific_data.pop(window_key_ts)
        if not region_specific_data: # If no more windows for this region, remove region key
            windowed_data_store.pop(region_id, None)

        all_scores = [score for scores_list in sensor_data_for_window.values() for score in scores_list]
        sensor_count = len(sensor_data_for_window)
        message_count = len(all_scores)
        avg_congestion = sum(all_scores) / message_count if message_count > 0 else 0.0

        agg_model = RegionalAggregatedTrafficDBModel(
            region_id=region_id,
            window_start_time=datetime.fromtimestamp(window_key_ts, tz=timezone.utc),
            average_congestion_score=round(avg_congestion, 2),
            sensor_count_in_window=sensor_count,
            message_count_in_window=message_count
        )
        try:
            store_aggregated_data_to_db(agg_collection, agg_model)
        except Exception as e: # Catch final failure from store_aggregated_data_to_db
            logger.error(f"FINAL FAILURE storing aggregated data for region {region_id}, "
                         f"window {window_key_ts}. Data lost. Error: {e}", exc_info=True)
            # Consider more robust DLQ for sensor_data_for_window here

def process_all_remaining_windows(agg_collection):
    """Processes all windows in the store, typically at shutdown."""
    global windowed_data_store
    logger.info("Processing all remaining windows before shutdown...")
    all_keys_to_process: List[Tuple[str, int]] = []
    for region_id, region_data in list(windowed_data_store.items()):
        for window_key_ts in list(region_data.keys()):
            all_keys_to_process.append((region_id, window_key_ts))

    if not all_keys_to_process:
        logger.info("No remaining windows to process.")
        return

    for region_id, window_key_ts in all_keys_to_process:
        logger.info(f"Processing remaining window for region {region_id}, window_ts {window_key_ts}")
        region_specific_data = windowed_data_store.get(region_id, {})
        if window_key_ts not in region_specific_data:
            logger.warning(f"Remaining window {window_key_ts} for region {region_id} missing.")
            continue

        sensor_data = region_specific_data.pop(window_key_ts)
        if not region_specific_data: windowed_data_store.pop(region_id, None)

        all_scores = [score for scores_list in sensor_data.values() for score in scores_list]
        sensor_count = len(sensor_data)
        message_count = len(all_scores)
        avg_congestion = sum(all_scores) / message_count if message_count > 0 else 0.0

        agg_model = RegionalAggregatedTrafficDBModel(
            region_id=region_id,
            window_start_time=datetime.fromtimestamp(window_key_ts, tz=timezone.utc),
            average_congestion_score=round(avg_congestion, 2),
            sensor_count_in_window=sensor_count,
            message_count_in_window=message_count
        )
        try:
            store_aggregated_data_to_db(agg_collection, agg_model)
        except Exception as e:
            logger.error(f"FINAL FAILURE storing remaining aggregated data for region {region_id}, "
                         f"window {window_key_ts}. Data lost. Error: {e}", exc_info=True)

# --- Main Application Logic ---
def main():
    setup_logging()
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    consumer = None
    mongo_client = None
    # kafka_dlq_producer = None # Initialize if using Kafka for DLQ
    last_window_check_time = time.time()

    # For manual commit tracking across a batch of messages
    current_offsets_to_commit = {}

    try:
        consumer = initialize_kafka_consumer()
        mongo_client, processed_collection, aggregated_collection = initialize_mongodb_client()
        # kafka_dlq_producer = initialize_kafka_producer_for_dlq() # If using

        logger.info("Starting main processing loop...")
        while not shutdown_flag:
            try:
                message_pack = consumer.poll(
                    timeout_ms=config.KAFKA_POLL_TIMEOUT_MS)
            except KafkaError as e:
                logger.error(
                    f"Kafka poll error: {e}. Retrying poll.", exc_info=True)
                time.sleep(5)
                continue
            except Exception as e:  # Catch any other unexpected error during poll
                logger.error(
                    f"Unexpected error during Kafka poll: {e}. Retrying poll.", exc_info=True)
                time.sleep(5)
                continue

            if not message_pack:
                if shutdown_flag:
                    break
                current_time = time.time()
                if current_time - last_window_check_time >= config.FORCE_WINDOW_CHECK_INTERVAL_SECONDS:
                    logger.debug("No messages, checking for completed windows.")
                    process_completed_windows(current_time, aggregated_collection)
                    last_window_check_time = current_time

                if current_offsets_to_commit: # Commit if no new messages but pending offsets
                    try:
                        consumer.commit(current_offsets_to_commit)
                        logger.debug(f"Committed offsets (no new messages): {current_offsets_to_commit}")
                        current_offsets_to_commit = {}
                    except KafkaError as e:
                        logger.error(f"Error committing Kafka offsets (no new messages): {e}", exc_info=True)
                continue # End of no-message-in-poll handling

            # Process messages if message_pack is not empty
            batch_had_errors = False
            max_offsets_in_batch: Dict[TopicPartition, int] = {}

            for tp, messages_in_partition in message_pack.items():
                for msg in messages_in_partition:
                    if shutdown_flag:
                        logger.info(f"Shutdown flag active, breaking from msg processing at offset {msg.offset}")
                        break
                    logger.debug(f"Received: Topic={msg.topic}, Partition={msg.partition}, Offset={msg.offset}")

                    if msg.value is None:
                        logger.warning(f"Skipping empty message at offset {msg.offset}")
                        max_offsets_in_batch[tp] = msg.offset + 1
                        continue
                    try:
                        raw_data = RawTrafficDataInputModel.parse_obj(msg.value)
                        processed_data = process_raw_data(raw_data)
                        store_processed_data_to_db(processed_collection, processed_data)
                        add_to_windowed_data(processed_data)
                        max_offsets_in_batch[tp] = msg.offset + 1 # Mark as successfully processed
                    except ValidationError as e:
                        err_str = f"Data validation failed: {e.errors()}"
                        logger.error(f"{err_str} for msg at offset {msg.offset}. Data: {msg.value}", exc_info=False)
                        send_to_dlq(msg.value, err_str)
                        max_offsets_in_batch[tp] = msg.offset + 1 # Commit offset even for bad data after DLQ
                        batch_had_errors = True
                    except OperationFailure as e: # MongoDB specific
                        err_str = f"MongoDB operation error: {e}"
                        logger.error(f"{err_str} for msg at offset {msg.offset}. Will be re-polled if not committed.", exc_info=True)
                        send_to_dlq(msg.value, err_str) # Send to DLQ after store retries failed
                        max_offsets_in_batch[tp] = msg.offset + 1
                        batch_had_errors = True
                    except Exception as e: # Other unexpected errors
                        err_str = f"Unexpected error: {e}"
                        logger.error(f"{err_str} for msg at offset {msg.offset}. Data: {msg.value}", exc_info=True)
                        send_to_dlq(msg.value, err_str)
                        max_offsets_in_batch[tp] = msg.offset + 1
                        batch_had_errors = True
                if shutdown_flag: break # Break outer loop too

            if max_offsets_in_batch:
                current_offsets_to_commit.update(max_offsets_in_batch)

            if current_offsets_to_commit and (not batch_had_errors or shutdown_flag):
                try:
                    consumer.commit(current_offsets_to_commit)
                    logger.debug(f"Committed offsets: {current_offsets_to_commit}")
                    current_offsets_to_commit = {}
                except KafkaError as e:
                    logger.error(f"Error committing Kafka offsets: {e}. Offsets {current_offsets_to_commit} may be reprocessed.", exc_info=True)
                    # Do not clear current_offsets_to_commit here, so retry happens on next poll

            current_time = time.time() # Re-check time for window processing
            if current_time - last_window_check_time >= config.FORCE_WINDOW_CHECK_INTERVAL_SECONDS:
                process_completed_windows(current_time, aggregated_collection)
                last_window_check_time = current_time

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received. Initiating shutdown...")
        global shutdown_flag # Ensure access to global
        shutdown_flag = True
    except (NoBrokersAvailable, ConnectionFailure) as e: # More specific critical exceptions
        logger.critical(f"Critical infrastructure connection failed: {e}. Exiting.", exc_info=True)
    except Exception as e: # Catch-all for any other unhandled critical errors
        logger.critical(f"Unhandled critical error in main loop: {e}", exc_info=True)
    finally:
        logger.info("Starting final cleanup...")
        if consumer and current_offsets_to_commit: # Final attempt to commit
            try:
                logger.info(f"Attempting final commit of pending offsets: {current_offsets_to_commit}")
                consumer.commit(current_offsets_to_commit)
                logger.info(f"Final offsets committed: {current_offsets_to_commit}")
            except KafkaError as e:
                logger.error(f"Error during final Kafka offset commit: {e}", exc_info=True)

        if 'aggregated_collection' in locals() and aggregated_collection is not None:
            process_all_remaining_windows(aggregated_collection)
        else:
            logger.warning("Aggregated collection not initialized; cannot process remaining windows.")

        if consumer:
            logger.info("Closing Kafka consumer...")
            consumer.close()
        # if kafka_dlq_producer: kafka_dlq_producer.close() # If used
        if mongo_client:
            logger.info("Closing MongoDB connection...")
            mongo_client.close()
        logger.info("Application shutdown complete.")


if __name__ == "__main__":
    main()
