# data_consumer_improved.py
import json
import logging
import signal
import time
from datetime import datetime, timezone
from kafka import KafkaConsumer, TopicPartition
from kafka.errors import KafkaError, NoBrokersAvailable
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
    Placeholder function to simulate sending a failed message to a Dead Letter Queue.
    In a real system, this would involve publishing to a different Kafka topic,
    storing in a database, or writing to a file.
    """
    dlq_message = {
        "original_message": message_value,
        "error": error_details,
        "timestamp": time.time()
    }
    logger.error(f"DLQ: Sending message to DLQ. Error: {error_details}. Message: {message_value}")
    # if kafka_producer and dlq_topic:
    # try:
    # kafka_producer.send(dlq_topic, value=dlq_message)
    #         logger.info(f"Message successfully sent to DLQ topic: {dlq_topic}")
    #     except Exception as e:
    #         logger.error(f"Failed to send message to DLQ topic {dlq_topic}: {e}", exc_info=True)
    # else:
    #     logger.warning("DLQ Kafka producer or topic not configured. DLQ message logged only.")


# --- Signal Handler for Graceful Shutdown ---
def signal_handler(signum, frame):
    global shutdown_flag
    logger.info(f"Signal {signal.Signals(signum).name} received. Initiating graceful shutdown...")
    shutdown_flag = True


# --- Kafka and MongoDB Initialization ---
def initialize_kafka_consumer():
    try:
        consumer = KafkaConsumer(
            config.KAFKA_RAW_TOPIC,
            bootstrap_servers=config.KAFKA_BROKERS,
            group_id=config.KAFKA_GROUP_ID,
            auto_offset_reset=config.KAFKA_AUTO_OFFSET_RESET,
            enable_auto_commit=False, # Manual commits for better control
            value_deserializer=lambda x: json.loads(x.decode('utf-8')) if x else None,
            consumer_timeout_ms=config.KAFKA_POLL_TIMEOUT_MS # To allow loop to check shutdown_flag
        )
        logger.info(f"Successfully connected to Kafka brokers: {config.KAFKA_BROKERS}, subscribed to topic: {config.KAFKA_RAW_TOPIC}")
        return consumer
    except NoBrokersAvailable:
        logger.error(f"Could not connect to Kafka brokers at {config.KAFKA_BROKERS}. Exiting.", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"An unexpected error occurred during Kafka consumer initialization: {e}", exc_info=True)
        raise

def initialize_mongodb_client():
    try:
        client = MongoClient(config.MONGO_URI)
        client.admin.command('ismaster')
        logger.info(f"Successfully connected to MongoDB at {config.MONGO_URI}")
        db = client[config.MONGO_DB_NAME]
        processed_collection = db[config.PROCESSED_DATA_COLLECTION_NAME]
        aggregated_collection = db[config.REGIONAL_AGGREGATED_COLLECTION_NAME]
        return client, processed_collection, aggregated_collection
    except ConnectionFailure:
        logger.error(f"Could not connect to MongoDB at {config.MONGO_URI}. Exiting.", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"An unexpected error occurred during MongoDB client initialization: {e}", exc_info=True)
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
            return # Success
        except OperationFailure as e:
            logger.warning(f"MongoDB operation failure storing processed data (ID: {document_id}) attempt {attempt + 1}/{retries}: {e}")
            if attempt < retries - 1:
                time.sleep(config.MONGO_RETRY_DELAY_SECONDS)
            else:
                logger.error(f"Failed to store processed data (ID: {document_id}) after {retries} attempts.", exc_info=True)
                raise # Re-raise to be caught by the caller
        except Exception as e: # Catch other unexpected errors during DB operation
            logger.error(f"Unexpected error storing processed data (ID: {document_id}) attempt {attempt + 1}/{retries}: {e}", exc_info=True)
            if attempt < retries - 1:
                time.sleep(config.MONGO_RETRY_DELAY_SECONDS)
            else:
                raise # Re-raise to be caught by the caller
    # This part should not be reached if raise occurs in the loop for final failure
    logger.critical(f"Logic error: store_processed_data_to_db exited retry loop without success or exception for ID {document_id}")


def store_aggregated_data_to_db(collection, data: RegionalAggregatedTrafficDBModel):
    document_id = f"{data.region_id}_{int(data.window_start_time.timestamp())}"
    retries = 3
    for attempt in range(retries):
        try:
            collection.update_one(
                {'_id': document_id},
                {'$set': data.model_dump()},
                upsert=True
            )
            logger.info(f"Upserted regional aggregated data for region {data.region_id}, window {data.window_start_time.isoformat()}")
            return # Success
        except OperationFailure as e:
            logger.warning(f"MongoDB operation failure storing aggregated data (ID: {document_id}) attempt {attempt + 1}/{retries}: {e}")
            if attempt < retries - 1:
                time.sleep(config.MONGO_RETRY_DELAY_SECONDS)
            else:
                logger.error(f"Failed to store aggregated data (ID: {document_id}) after {retries} attempts.", exc_info=True)
                raise
        except Exception as e:
            logger.error(f"Unexpected error storing aggregated data (ID: {document_id}) attempt {attempt + 1}/{retries}: {e}", exc_info=True)
            if attempt < retries - 1:
                time.sleep(config.MONGO_RETRY_DELAY_SECONDS)
            else:
                raise
    logger.critical(f"Logic error: store_aggregated_data_to_db exited retry loop without success or exception for ID {document_id}")


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
        logger.warning(f"Sensor ID {sensor_id} not found in SENSOR_TO_REGION_MAPPING. Skipping aggregation for this message.")
        return # Corrected: return should be on its own line

    window_key_ts = get_window_key(event_timestamp.timestamp())
    windowed_data_store.setdefault(region_id, {}).setdefault(window_key_ts, {}).setdefault(sensor_id, []).append(congestion_score)
    logger.debug(f"Added score {congestion_score} for sensor {sensor_id} to region {region_id}, window_ts {window_key_ts}")


def process_completed_windows(current_processing_time: float, agg_collection):
    global windowed_data_store
    completed_windows_to_process = []

    for region_id, region_data in list(windowed_data_store.items()):
        for window_key_ts in list(region_data.keys()):
            if window_key_ts + config.WINDOW_SIZE_SECONDS <= current_processing_time:
                completed_windows_to_process.append((region_id, window_key_ts))

    for region_id, window_key_ts in completed_windows_to_process:
        logger.info(f"Processing completed window for region {region_id}, window_ts {window_key_ts}")
        
        region_specific_data = windowed_data_store.get(region_id, {})
        if window_key_ts in region_specific_data:
            sensor_data_for_window = region_specific_data.pop(window_key_ts)
            if not region_specific_data:
                windowed_data_store.pop(region_id, None)
        else:
            logger.warning(f"Window_ts {window_key_ts} not found for region {region_id} during processing. Already processed?")
            continue # Corrected: continue should be indented

        all_scores_in_window = []
        sensor_count = 0
        message_count = 0
        for sensor_id_in_window, scores in sensor_data_for_window.items():
            all_scores_in_window.extend(scores)
            sensor_count +=1
            message_count += len(scores)
        
        avg_congestion = 0
        if all_scores_in_window:
            avg_congestion = sum(all_scores_in_window) / len(all_scores_in_window)

        aggregated_data_model = RegionalAggregatedTrafficDBModel(
            region_id=region_id,
            window_start_time=datetime.fromtimestamp(window_key_ts, tz=timezone.utc),
            average_congestion_score=round(avg_congestion, 2),
            sensor_count_in_window=sensor_count,
            message_count_in_window=message_count
        )
        try:
            store_aggregated_data_to_db(agg_collection, aggregated_data_model)
        except Exception as e:
            # The store_aggregated_data_to_db function now handles its own retries and raises on final failure.
            # This exception block will catch that final failure.
            logger.error(f"FINAL FAILURE: Could not store aggregated data for region {region_id}, window {window_key_ts}. Data for this window is lost. Error: {e}", exc_info=True)
            # Optionally, attempt to put the sensor_data_for_window back or send to a more persistent DLQ.
            # For simplicity, we log the loss here.


def process_all_remaining_windows(agg_collection):
    global windowed_data_store
    logger.info("Processing all remaining windows before shutdown...")
    all_keys_to_process = []
    for region_id, region_data in list(windowed_data_store.items()):
        for window_key_ts in list(region_data.keys()):
            all_keys_to_process.append((region_id, window_key_ts))
    
    if not all_keys_to_process:
        logger.info("No remaining windows to process.")
        return

    for region_id, window_key_ts in all_keys_to_process:
        logger.info(f"Processing remaining window for region {region_id}, window_ts {window_key_ts}")
        
        region_specific_data = windowed_data_store.get(region_id, {})
        if window_key_ts in region_specific_data:
            sensor_data_for_window = region_specific_data.pop(window_key_ts)
            if not region_specific_data:
                windowed_data_store.pop(region_id, None)
        else:
            logger.warning(f"Remaining window_ts {window_key_ts} not found for region {region_id}. Already processed?")
            continue

        all_scores_in_window = []
        sensor_count = 0
        message_count = 0
        for sensor_id_in_window, scores in sensor_data_for_window.items():
            all_scores_in_window.extend(scores)
            sensor_count+=1
            message_count+=len(scores)
        
        avg_congestion = 0
        if all_scores_in_window:
            avg_congestion = sum(all_scores_in_window) / len(all_scores_in_window)

        aggregated_data_model = RegionalAggregatedTrafficDBModel(
            region_id=region_id,
            window_start_time=datetime.fromtimestamp(window_key_ts, tz=timezone.utc),
            average_congestion_score=round(avg_congestion, 2),
            sensor_count_in_window=sensor_count,
            message_count_in_window=message_count
        )
        try:
            store_aggregated_data_to_db(agg_collection, aggregated_data_model)
        except Exception as e:
            logger.error(f"FINAL FAILURE: Could not store remaining aggregated data for region {region_id}, window {window_key_ts}. Data lost. Error: {e}", exc_info=True)


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
                message_pack = consumer.poll(timeout_ms=config.KAFKA_POLL_TIMEOUT_MS)
            except KafkaError as e:
                logger.error(f"Kafka poll error: {e}. Retrying poll.", exc_info=True)
                time.sleep(5)
                continue
            except Exception as e: # Catch any other unexpected error during poll
                logger.error(f"Unexpected error during Kafka poll: {e}. Retrying poll.", exc_info=True)
                time.sleep(5)
                continue


            if not message_pack:
                if shutdown_flag: break
                current_time = time.time()
                if current_time - last_window_check_time >= config.FORCE_WINDOW_CHECK_INTERVAL_SECONDS:
                    logger.debug("No messages, checking for completed windows.")
                    process_completed_windows(current_time, aggregated_collection)
                    last_window_check_time = current_time
                
                # If there were messages processed in a previous iteration and now none, commit.
                if current_offsets_to_commit:
                    try:
                        consumer.commit(current_offsets_to_commit)
                        logger.debug(f"Committed offsets (no new messages): {current_offsets_to_commit}")
                        current_offsets_to_commit = {}
                    except KafkaError as e:
                        logger.error(f"Error committing Kafka offsets (no new messages): {e}", exc_info=True)
                continue

            batch_had_errors = False
            max_offsets_in_batch = {}

            for tp, messages in message_pack.items(): # tp is TopicPartition
                for msg in messages:
                    if shutdown_flag:
                        logger.info(f"Shutdown flag active, breaking from message processing at offset {msg.offset}")
                        break
                    
                    logger.debug(f"Received message: Topic={msg.topic}, Partition={msg.partition}, Offset={msg.offset}, Key={msg.key}")

                    if msg.value is None:
                        logger.warning(f"Skipping empty message at offset {msg.offset}")
                        # Still need to advance offset for this empty message
                        max_offsets_in_batch[tp] = msg.offset + 1
                        continue
                    
                    try:
                        raw_data_model = RawTrafficDataInputModel.parse_obj(msg.value)
                        processed_data_model = process_raw_data(raw_data_model)
                        store_processed_data_to_db(processed_collection, processed_data_model)
                        add_to_windowed_data(processed_data_model)
                        
                        # Successfully processed, update max offset for this partition in the batch
                        max_offsets_in_batch[tp] = msg.offset + 1

                    except ValidationError as e:
                        error_str = f"Data validation failed: {e.errors()}"
                        logger.error(f"{error_str} for message at offset {msg.offset}. Data: {msg.value}", exc_info=False)
                        send_to_dlq(msg.value, error_str)
                        max_offsets_in_batch[tp] = msg.offset + 1 # Skip bad message, commit its offset
                        batch_had_errors = True # Mark batch as having errors, but continue processing others
                    except OperationFailure as e: # MongoDB specific operational errors from store_processed_data
                        error_str = f"MongoDB operation error: {e}"
                        logger.error(f"{error_str} processing message at offset {msg.offset}. Message will be re-polled if not committed.", exc_info=True)
                        send_to_dlq(msg.value, error_str) # Send to DLQ after retries failed in store_
                        max_offsets_in_batch[tp] = msg.offset + 1 # Assume we skip after DLQing
                        batch_had_errors = True
                    except Exception as e: # Catch-all for other unexpected errors during single message processing
                        error_str = f"Unexpected error: {e}"
                        logger.error(f"{error_str} processing message at offset {msg.offset}. Data: {msg.value}", exc_info=True)
                        send_to_dlq(msg.value, error_str)
                        max_offsets_in_batch[tp] = msg.offset + 1 # Skip bad message
                        batch_had_errors = True
                
                if shutdown_flag: break

            # After processing all messages in message_pack or if shutdown
            if max_offsets_in_batch:
                offsets_to_commit_this_round = {
                    tp: offset for tp, offset in max_offsets_in_batch.items()
                }
                current_offsets_to_commit.update(offsets_to_commit_this_round)


            # Commit successfully processed messages in the batch
            if current_offsets_to_commit and (not batch_had_errors or shutdown_flag): # Commit if no errors or shutting down
                try:
                    consumer.commit(current_offsets_to_commit)
                    logger.debug(f"Committed offsets: {current_offsets_to_commit}")
                    current_offsets_to_commit = {} # Reset for next batch
                except KafkaError as e:
                    logger.error(f"Error committing Kafka offsets: {e}. Offsets {current_offsets_to_commit} may be reprocessed.", exc_info=True)
                    # If commit fails, these offsets will be re-polled.
                    # The current_offsets_to_commit should NOT be cleared here, so next poll tries again.


            current_time = time.time()
            if current_time - last_window_check_time >= config.FORCE_WINDOW_CHECK_INTERVAL_SECONDS:
                process_completed_windows(current_time, aggregated_collection)
                last_window_check_time = current_time
            

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received. Initiating shutdown...")
        global shutdown_flag
        shutdown_flag = True
    except (NoBrokersAvailable, ConnectionFailure) as e:
        logger.critical(f"Critical infrastructure connection failed: {e}. Exiting.", exc_info=True)
    except Exception as e:
        logger.critical(f"An unhandled critical error occurred in the main loop: {e}", exc_info=True)
    finally:
        logger.info("Starting final cleanup...")

        # Final attempt to commit any pending offsets
        if consumer and current_offsets_to_commit:
            try:
                logger.info(f"Attempting final commit of pending offsets: {current_offsets_to_commit}")
                consumer.commit(current_offsets_to_commit)
                logger.info(f"Final offsets committed: {current_offsets_to_commit}")
            except KafkaError as e:
                logger.error(f"Error during final commit of Kafka offsets: {e}", exc_info=True)


        if 'aggregated_collection' in locals() and aggregated_collection is not None:
             process_all_remaining_windows(aggregated_collection)
        else:
            logger.warning("Aggregated collection not initialized, cannot process remaining windows.")

        if consumer:
            logger.info("Closing Kafka consumer...")
            consumer.close()
            logger.info("Kafka consumer closed.")
        # if kafka_dlq_producer: kafka_dlq_producer.close()
        if mongo_client:
            logger.info("Closing MongoDB connection...")
            mongo_client.close()
            logger.info("MongoDB connection closed.")
        logger.info("Application shutdown complete.")


if __name__ == "__main__":
    main()