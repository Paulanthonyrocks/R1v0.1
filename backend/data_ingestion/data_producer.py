import time
import json
from kafka import KafkaProducer

import random

# Configuration
KAFKA_BROKER = 'localhost:9092'  # Placeholder for Kafka broker address
KAFKA_TOPIC = 'raw_traffic_data'

def generate_dummy_traffic_data(sensor_id, latitude, longitude):
    """Generates dummy traffic data."""
    # Generate slightly varied data
    vehicle_count = random.randint(50, 200)
    average_speed = random.uniform(20, 60)
    congestion_level = random.uniform(1, 5) # 1: Free Flow, 5: Severe Congestion

    data = {
        'sensor_id': sensor_id,
        'timestamp': int(time.time()),
        'location': {
            'latitude': latitude + random.uniform(-0.01, 0.01), # Add small variation
            'longitude': longitude + random.uniform(-0.01, 0.01) # Add small variation
        },
        'vehicle_count': vehicle_count,
        'average_speed': average_speed,
        'congestion_level': congestion_level
    }
    return data

if __name__ == "__main__":
    try:
        # Initialize Kafka Producer
        producer = KafkaProducer(
            bootstrap_servers=KAFKA_BROKER,
            value_serializer=lambda x: json.dumps(x).encode('utf-8')
        )
        print(f"Kafka producer configured to connect to {KAFKA_BROKER}")

        location_counter = 0
        while True:
            # Simulate data from a few different sensors
            sensors = {
                "SENSOR001": {"latitude": 34.0522, "longitude": -118.2437}, # Los Angeles
                "SENSOR002": {"latitude": 40.7128, "longitude": -74.0060},  # New York
                "SENSOR003": {"latitude": 41.8781, "longitude": -87.6298}   # Chicago
            }
            for sensor_id, coords in sensors.items():
                dummy_data = generate_dummy_traffic_data(sensor_id, coords["latitude"], coords["longitude"])

            # Send data to Kafka topic
            future = producer.send(KAFKA_TOPIC, value=dummy_data)
            result = future.get(timeout=60) # Wait for the send to complete

            print(f"Sent data to topic {KAFKA_TOPIC}: {dummy_data}")

            # Add a delay
            time.sleep(1)

    except Exception as e:
        print(f"An error occurred: {e}")
        if producer:
            producer.close()