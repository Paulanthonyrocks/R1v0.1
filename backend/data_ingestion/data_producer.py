import time
import json
import os # Added os for environment variables
from kafka import KafkaProducer, KafkaAdminClient
from kafka.errors import KafkaTimeoutError, KafkaError, NoBrokersAvailable
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type # Added tenacity
import random
from datetime import datetime, timezone # Added datetime

# Configuration - Default values, can be overridden by environment variables
DEFAULT_KAFKA_BROKER = 'localhost:9092'
DEFAULT_KAFKA_TOPIC = 'raw_traffic_data'

KAFKA_BROKER = os.environ.get('KAFKA_BROKER_URL', DEFAULT_KAFKA_BROKER)
KAFKA_TOPIC = os.environ.get('KAFKA_TRAFFIC_TOPIC', DEFAULT_KAFKA_TOPIC)

def generate_dummy_traffic_data(sensor_id, latitude, longitude):
    """Generates dummy traffic data with ISO timestamp."""
    vehicle_count = random.randint(5, 50) # Reduced for more frequent variation in scores
    average_speed = random.uniform(10, 70)
    # congestion_level is often derived, let's send raw metrics instead
    # congestion_level = random.uniform(1, 5) 

    data = {
        'sensor_id': sensor_id,
        # Produce timestamp as ISO 8601 string in UTC
        'timestamp': datetime.now(timezone.utc).isoformat(), 
        'location': {
            'latitude': round(latitude + random.uniform(-0.001, 0.001), 6), 
            'longitude': round(longitude + random.uniform(-0.001, 0.001), 6)
        },
        'vehicle_count': vehicle_count,
        'average_speed': round(average_speed, 2),
        # 'congestion_level': round(congestion_level, 2) # Removed, consumer calculates score
    }
    return data

@retry(stop=stop_after_attempt(5), 
       wait=wait_exponential(multiplier=1, min=2, max=10),
       retry=retry_if_exception_type((KafkaTimeoutError, KafkaError)))
def send_message_with_retry(producer, topic, value):
    """Sends a message to Kafka with retry logic."""
    print(f"Attempting to send data to topic {topic}: {value}")
    future = producer.send(topic, value=value)
    result = future.get(timeout=10) # Shorter timeout for individual send attempt
    print(f"Successfully sent data: {result}")
    return result

if __name__ == "__main__":
    producer = None
    admin_client = None
    try:
        print(f"Connecting to Kafka broker at {KAFKA_BROKER} for topic {KAFKA_TOPIC}")
        # Initialize Kafka Producer
        producer = KafkaProducer(
            bootstrap_servers=KAFKA_BROKER,
            value_serializer=lambda x: json.dumps(x).encode('utf-8'),
            retries=3, # Producer-level retries for some errors
            acks='all' # Ensure messages are acknowledged by all in-sync replicas
        )
        print(f"Kafka producer initialized.")

        # Optional: Check topic existence or create it (requires admin privileges)
        # try:
        #     admin_client = KafkaAdminClient(bootstrap_servers=KAFKA_BROKER)
        #     topic_metadata = admin_client.describe_topics([KAFKA_TOPIC])
        #     if not topic_metadata or not topic_metadata[0]['partitions']:
        #         print(f"Topic {KAFKA_TOPIC} does not exist or has no partitions. Please create it.")
        #         # from kafka.admin import NewTopic
        #         # new_topic = NewTopic(name=KAFKA_TOPIC, num_partitions=1, replication_factor=1)
        #         # admin_client.create_topics(new_topics=[new_topic], validate_only=False)
        #         # print(f"Topic {KAFKA_TOPIC} created.")
        # except NoBrokersAvailable:
        #     print(f"Could not connect to Kafka admin client at {KAFKA_BROKER}. Topic check skipped.")
        # except Exception as e:
        #     print(f"Error checking/creating topic: {e}")

        sensors = {
            "SENSOR001": {"latitude": 34.0522, "longitude": -118.2437}, # Los Angeles
            "SENSOR002": {"latitude": 40.7128, "longitude": -74.0060},  # New York
            "SENSOR003": {"latitude": 41.8781, "longitude": -87.6298}   # Chicago
        }
        sensor_ids = list(sensors.keys())
        current_sensor_index = 0

        while True:
            # Cycle through sensors to send data more evenly
            sensor_id = sensor_ids[current_sensor_index]
            coords = sensors[sensor_id]
            dummy_data = generate_dummy_traffic_data(sensor_id, coords["latitude"], coords["longitude"])
            current_sensor_index = (current_sensor_index + 1) % len(sensor_ids)

            send_message_with_retry(producer, KAFKA_TOPIC, dummy_data)
            
            time.sleep(random.uniform(0.5, 2.0)) # Randomize sleep slightly

    except NoBrokersAvailable:
        print(f"CRITICAL: Kafka brokers not available at {KAFKA_BROKER}. Producer cannot start.")
    except Exception as e:
        print(f"An unexpected error occurred in producer: {e}", exc_info=True)
    finally:
        if producer:
            print("Closing Kafka producer...")
            producer.flush() # Ensure all outstanding messages are sent
            producer.close()
            print("Kafka producer closed.")
        # if admin_client:
        #     admin_client.close()