import time
import json
import os  # For environment variables
from kafka import KafkaProducer # Removed KafkaAdminClient as it's not used
from kafka.errors import KafkaTimeoutError, KafkaError, NoBrokersAvailable
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import random
from datetime import datetime, timezone

# Configuration - Default values, can be overridden by environment variables
DEFAULT_KAFKA_BROKER = 'localhost:9092'
DEFAULT_KAFKA_TOPIC = 'raw_traffic_data'

KAFKA_BROKER = os.environ.get('KAFKA_BROKER_URL', DEFAULT_KAFKA_BROKER)
KAFKA_TOPIC = os.environ.get('KAFKA_TRAFFIC_TOPIC', DEFAULT_KAFKA_TOPIC)


def generate_dummy_traffic_data(sensor_id: str, latitude: float, longitude: float) -> dict:
    """Generates dummy traffic data with ISO timestamp."""
    # Reduced vehicle_count for more frequent variation in derived scores
    vehicle_count = random.randint(5, 50)
    average_speed = random.uniform(10, 70)
    # Congestion level is often derived by consumer; send raw metrics instead.

    data = {
        'sensor_id': sensor_id,
        'timestamp': datetime.now(timezone.utc).isoformat(), # UTC ISO 8601
        'location': {
            'latitude': round(latitude + random.uniform(-0.001, 0.001), 6),
            'longitude': round(longitude + random.uniform(-0.001, 0.001), 6)
        },
        'vehicle_count': vehicle_count,
        'average_speed': round(average_speed, 2),
    }
    return data


@retry(stop=stop_after_attempt(5),
       wait=wait_exponential(multiplier=1, min=2, max=10),
       retry=retry_if_exception_type((KafkaTimeoutError, KafkaError)))
def send_message_with_retry(producer: KafkaProducer, topic: str, value: dict):
    """Sends a message to Kafka with retry logic."""
    # print(f"Attempting to send data to topic {topic}: {value}") # Too verbose for frequent calls
    future = producer.send(topic, value=value)
    result = future.get(timeout=10) # Shorter timeout for individual send attempt
    # print(f"Successfully sent data: {result}") # Too verbose
    return result


if __name__ == "__main__":
    producer: Optional[KafkaProducer] = None # Initialize with Optional for finally block
    # admin_client: Optional[KafkaAdminClient] = None # Not used

    try:
        print(f"Connecting to Kafka broker: {KAFKA_BROKER}, topic: {KAFKA_TOPIC}")
        producer = KafkaProducer(
            bootstrap_servers=KAFKA_BROKER,
            value_serializer=lambda x: json.dumps(x).encode('utf-8'),
            retries=3, # Producer-level retries
            acks='all' # Ensure messages are acknowledged by all in-sync replicas
        )
        print("Kafka producer initialized successfully.")

        # Topic existence check/creation can be done by an external script or deployment process.
        # If needed here, KafkaAdminClient would be used, but it adds complexity and permissions.
        # For this script, assume the topic exists.

        sensors = {
            "SENSOR001": {"latitude": 34.0522, "longitude": -118.2437}, # Los Angeles
            "SENSOR002": {"latitude": 40.7128, "longitude": -74.0060}, # New York
            "SENSOR003": {"latitude": 41.8781, "longitude": -87.6298}  # Chicago
        }
        sensor_ids = list(sensors.keys())
        current_sensor_index = 0

        while True:
            sensor_id_to_use = sensor_ids[current_sensor_index]
            coords = sensors[sensor_id_to_use]
            dummy_data_payload = generate_dummy_traffic_data(
                sensor_id_to_use, coords["latitude"], coords["longitude"]
            )
            current_sensor_index = (current_sensor_index + 1) % len(sensor_ids)

            send_message_with_retry(producer, KAFKA_TOPIC, dummy_data_payload)
            # print(f"Sent: {dummy_data_payload}") # Verbose, uncomment if needed for debugging

            time.sleep(random.uniform(0.5, 2.0)) # Randomize sleep

    except NoBrokersAvailable:
        print(f"CRITICAL: Kafka brokers not available at {KAFKA_BROKER}. Producer cannot start.")
    except Exception as e:
        print(f"An unexpected error occurred in producer: {e}", exc_info=True)
    finally:
        if producer:
            print("Closing Kafka producer...")
            producer.flush(timeout=5.0) # Ensure all outstanding messages are sent with timeout
            producer.close(timeout=5.0)
            print("Kafka producer closed.")
        # if admin_client: admin_client.close()
