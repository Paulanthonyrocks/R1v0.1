import os
# import sys # F401: Unused import
import time
import json
import subprocess
from datetime import datetime, timedelta
from kafka import KafkaProducer, KafkaConsumer # Removed TopicPartition
from kafka.errors import NoBrokersAvailable # Removed KafkaError
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

KAFKA_BROKER = os.environ.get('KAFKA_BROKER_URL', 'localhost:9092')
KAFKA_TOPIC = os.environ.get('KAFKA_TRAFFIC_TOPIC', 'raw_traffic_data')
MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
MONGO_DB_NAME = os.environ.get('MONGO_DB_NAME', 'traffic_db_improved') # Renamed for clarity
MONGO_COLLECTION_NAME = os.environ.get('MONGO_COLLECTION_NAME', 'processed_traffic_data') # Renamed

REPORT_LINES: list[str] = [] # Changed to list[str] and renamed


def check_kafka():
    try:
        consumer = KafkaConsumer(bootstrap_servers=KAFKA_BROKER, consumer_timeout_ms=5000)
        topics = consumer.topics() # This might block if broker is down
        consumer.close() # Close consumer after use
        if KAFKA_TOPIC in topics:
            REPORT_LINES.append(f"Kafka: Topic '{KAFKA_TOPIC}' exists. [OK]")
            return True
        REPORT_LINES.append(f"Kafka: Topic '{KAFKA_TOPIC}' does not exist. [FAIL]")
        return False
    except NoBrokersAvailable:
        REPORT_LINES.append(f"Kafka: Broker not available at {KAFKA_BROKER}. [FAIL]")
        return False
    except Exception as e:
        REPORT_LINES.append(f"Kafka: Error checking topic: {e} [FAIL]")
        return False

def produce_test_message():
    try:
        producer = KafkaProducer(
            bootstrap_servers=KAFKA_BROKER,
            value_serializer=lambda x: json.dumps(x).encode('utf-8'),
            retries=0, request_timeout_ms=5000 # Fail faster for check
        )
        test_data = {
            'sensor_id': 'CHECK_SENSOR_VALID', # Changed ID for clarity
            'timestamp': datetime.now().isoformat(),
            'location': {'latitude': 0.0, 'longitude': 0.0},
            'vehicle_count': 1, 'average_speed': 10.0, 'congestion_level': 5.0
        }
        future = producer.send(KAFKA_TOPIC, test_data)
        future.get(timeout=5) # Block until sent or timeout
        producer.close()
        REPORT_LINES.append("Kafka: Test message produced. [OK]")
        return test_data
    except Exception as e:
        REPORT_LINES.append(f"Kafka: Failed to produce test message: {e} [FAIL]")
        if 'producer' in locals() and producer: producer.close(timeout=1.0)
        return None

def produce_malformed_message():
    try:
        producer = KafkaProducer(
            bootstrap_servers=KAFKA_BROKER,
            value_serializer=lambda x: json.dumps(x).encode('utf-8'),
            retries=0, request_timeout_ms=5000
        )
        bad_data = {'malformed_field_for_check': 12345}
        future = producer.send(KAFKA_TOPIC, bad_data)
        future.get(timeout=5)
        producer.close()
        REPORT_LINES.append("Kafka: Malformed test message produced. [OK]")
        return True
    except Exception as e:
        REPORT_LINES.append(f"Kafka: Failed to produce malformed message: {e} [FAIL]")
        if 'producer' in locals() and producer: producer.close(timeout=1.0)
        return False

def check_mongo_for_test(sensor_id: str, since_minutes: int = 5):
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        db = client[MONGO_DB_NAME]
        collection = db[MONGO_COLLECTION_NAME]
        since_dt = datetime.utcnow() - timedelta(minutes=since_minutes)
        query = {'sensor_id': sensor_id, 'timestamp': {'$gte': since_dt.isoformat()}}
        found = collection.find_one(query)
        client.close()
        if found:
            REPORT_LINES.append(f"MongoDB: Found test data for sensor '{sensor_id}'. [OK]")
            return True
        REPORT_LINES.append(f"MongoDB: No recent test data for sensor '{sensor_id}'. [FAIL]")
        return False
    except ConnectionFailure:
        REPORT_LINES.append(f"MongoDB: Could not connect to {MONGO_URI}. [FAIL]")
        return False
    except Exception as e:
        REPORT_LINES.append(f"MongoDB: Error querying test data: {e} [FAIL]")
        if 'client' in locals() and client: client.close()
        return False

def check_mongo_for_malformed():
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        db = client[MONGO_DB_NAME]
        collection = db[MONGO_COLLECTION_NAME]
        # Query for the specific malformed data structure
        found = collection.find_one({'malformed_field_for_check': 12345})
        client.close()
        if found:
            REPORT_LINES.append("MongoDB: Malformed data (malformed_field_for_check) WAS stored! [FAIL]")
            # Optionally remove it: collection.delete_one({'malformed_field_for_check': 12345})
            return False
        REPORT_LINES.append("MongoDB: Malformed data (malformed_field_for_check) was NOT stored. [OK]")
        return True
    except Exception as e:
        REPORT_LINES.append(f"MongoDB: Error checking for malformed data: {e} [FAIL]")
        if 'client' in locals() and client: client.close()
        return False

def check_consumer_running():
    try:
        # Check if 'data_consumer.py' is in the list of running python processes
        # This is a basic check and might need adjustment based on how the consumer is run
        result = subprocess.run(['pgrep', '-f', 'data_consumer.py'], capture_output=True, text=True)
        if result.stdout.strip(): # If pgrep finds PIDs, stdout is not empty
            REPORT_LINES.append("Consumer: data_consumer.py process appears to be running. [OK]")
            return True
        REPORT_LINES.append("Consumer: data_consumer.py process is NOT running. [FAIL]")
        return False
    except FileNotFoundError: # pgrep not found
        REPORT_LINES.append("Consumer: 'pgrep' command not found. Cannot check process status. [INFO]")
        return False # Cannot confirm
    except Exception as e:
        REPORT_LINES.append(f"Consumer: Error checking process: {e} [FAIL]")
        return False

def main():
    print("--- Data Ingestion Pipeline Health Check ---")
    if check_kafka():
        test_msg_data = produce_test_message()
        if test_msg_data and test_msg_data.get('sensor_id'):
            print("Waiting for consumer to process test message...")
            time.sleep(10) # Increased wait time
            check_mongo_for_test(test_msg_data['sensor_id'])
    if produce_malformed_message():
        print("Waiting for consumer to process (and hopefully reject) malformed message...")
        time.sleep(10) # Increased wait time
        check_mongo_for_malformed()
    check_consumer_running()

    print("\n--- Summary Report ---")
    for line_item in REPORT_LINES: print(line_item)
    print("--- End of Report ---")


if __name__ == "__main__":
    main()
