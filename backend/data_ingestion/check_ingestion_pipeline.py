import os
import sys
import time
import json
import subprocess
from datetime import datetime, timedelta
from kafka import KafkaProducer, KafkaConsumer, TopicPartition
from kafka.errors import KafkaError, NoBrokersAvailable
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

KAFKA_BROKER = os.environ.get('KAFKA_BROKER_URL', 'localhost:9092')
KAFKA_TOPIC = os.environ.get('KAFKA_TRAFFIC_TOPIC', 'raw_traffic_data')
MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
MONGO_DB = os.environ.get('MONGO_DB_NAME', 'traffic_db_improved')
MONGO_COLLECTION = os.environ.get('MONGO_COLLECTION', 'processed_traffic_data')

REPORT = []

def check_kafka():
    try:
        consumer = KafkaConsumer(bootstrap_servers=KAFKA_BROKER)
        topics = consumer.topics()
        if KAFKA_TOPIC in topics:
            REPORT.append(f"Kafka: Topic '{KAFKA_TOPIC}' exists. [OK]")
            return True
        else:
            REPORT.append(f"Kafka: Topic '{KAFKA_TOPIC}' does not exist. [FAIL]")
            return False
    except NoBrokersAvailable:
        REPORT.append(f"Kafka: Broker not available at {KAFKA_BROKER}. [FAIL]")
        return False
    except Exception as e:
        REPORT.append(f"Kafka: Error checking topic: {e} [FAIL]")
        return False

def produce_test_message():
    try:
        producer = KafkaProducer(bootstrap_servers=KAFKA_BROKER, value_serializer=lambda x: json.dumps(x).encode('utf-8'))
        test_data = {
            'sensor_id': 'CHECK_SENSOR',
            'timestamp': datetime.now().isoformat(),
            'location': {'latitude': 0.0, 'longitude': 0.0},
            'vehicle_count': 1,
            'average_speed': 10.0,
            'congestion_level': 5.0
        }
        producer.send(KAFKA_TOPIC, test_data)
        producer.flush()
        REPORT.append("Kafka: Test message produced. [OK]")
        return test_data
    except Exception as e:
        REPORT.append(f"Kafka: Failed to produce test message: {e} [FAIL]")
        return None

def produce_malformed_message():
    try:
        producer = KafkaProducer(bootstrap_servers=KAFKA_BROKER, value_serializer=lambda x: json.dumps(x).encode('utf-8'))
        bad_data = {'bad_field': 123}
        producer.send(KAFKA_TOPIC, bad_data)
        producer.flush()
        REPORT.append("Kafka: Malformed test message produced. [OK]")
        return True
    except Exception as e:
        REPORT.append(f"Kafka: Failed to produce malformed message: {e} [FAIL]")
        return False

def check_mongo_for_test(sensor_id, since_minutes=5):
    try:
        client = MongoClient(MONGO_URI)
        db = client[MONGO_DB]
        collection = db[MONGO_COLLECTION]
        since = datetime.utcnow() - timedelta(minutes=since_minutes)
        found = collection.find_one({
            'sensor_id': sensor_id,
            'timestamp': {'$gte': since.isoformat()}
        })
        if found:
            REPORT.append(f"MongoDB: Found recent test data for sensor_id '{sensor_id}'. [OK]")
            return True
        else:
            REPORT.append(f"MongoDB: No recent test data for sensor_id '{sensor_id}'. [FAIL]")
            return False
    except ConnectionFailure:
        REPORT.append(f"MongoDB: Could not connect to {MONGO_URI}. [FAIL]")
        return False
    except Exception as e:
        REPORT.append(f"MongoDB: Error querying for test data: {e} [FAIL]")
        return False

def check_mongo_for_malformed():
    try:
        client = MongoClient(MONGO_URI)
        db = client[MONGO_DB]
        collection = db[MONGO_COLLECTION]
        found = collection.find_one({'bad_field': 123})
        if found:
            REPORT.append("MongoDB: Malformed data was stored! [FAIL]")
            return False
        else:
            REPORT.append("MongoDB: Malformed data was NOT stored. [OK]")
            return True
    except Exception as e:
        REPORT.append(f"MongoDB: Error checking for malformed data: {e} [FAIL]")
        return False

def check_consumer_running():
    # This is a best-effort check; you may want to improve it for your environment
    try:
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        if 'data_consumer.py' in result.stdout:
            REPORT.append("Consumer: data_consumer.py process is running. [OK]")
            return True
        else:
            REPORT.append("Consumer: data_consumer.py process is NOT running. [FAIL]")
            return False
    except Exception as e:
        REPORT.append(f"Consumer: Error checking process: {e} [FAIL]")
        return False

def main():
    print("--- Data Ingestion Pipeline Health Check ---")
    kafka_ok = check_kafka()
    if kafka_ok:
        test_data = produce_test_message()
        if test_data:
            time.sleep(5)  # Give consumer time to process
            check_mongo_for_test(test_data['sensor_id'])
    produce_malformed_message()
    time.sleep(5)
    check_mongo_for_malformed()
    check_consumer_running()
    print("\n--- Summary Report ---")
    for line in REPORT:
        print(line)
    print("--- End of Report ---")

if __name__ == "__main__":
    main() 