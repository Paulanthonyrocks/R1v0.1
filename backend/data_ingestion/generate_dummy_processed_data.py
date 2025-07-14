import random
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
from models import ProcessedTrafficDataDBModel, LocationModel
import config
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=config.LOG_LEVEL, format=config.LOG_FORMAT)

def generate_dummy_processed_data(num_entries: int = 1000):
    try:
        client = MongoClient(config.MONGO_URI)
        db = client[config.MONGO_DB_NAME]
        processed_collection = db[config.PROCESSED_DATA_COLLECTION_NAME]
        logger.info(f"Connected to MongoDB at {config.MONGO_URI}, using database {config.MONGO_DB_NAME}")

        sensors = {
            "SENSOR001": {"latitude": 34.0522, "longitude": -118.2437}, # Los Angeles
            "SENSOR002": {"latitude": 40.7128, "longitude": -74.0060},  # New York
            "SENSOR003": {"latitude": 41.8781, "longitude": -87.6298}   # Chicago
        }
        sensor_ids = list(sensors.keys())

        for i in range(num_entries):
            sensor_id = random.choice(sensor_ids)
            base_coords = sensors[sensor_id]
            
            timestamp = datetime.now(timezone.utc) - timedelta(minutes=random.randint(0, 60 * 24 * 7)) # Last 7 days
            
            vehicle_count = random.randint(5, 100)
            average_speed = random.uniform(10, 80)
            congestion_score = min((vehicle_count / average_speed) * 10, 100) if average_speed > 0 else random.uniform(50, 100)
            
            # Simulate incident occurrence for training
            incident_occurred = 0
            if congestion_score > 70 and random.random() < 0.3: # 30% chance of incident if high congestion
                incident_occurred = 1
            elif average_speed < 20 and random.random() < 0.2: # 20% chance if very slow
                incident_occurred = 1

            data = ProcessedTrafficDataDBModel(
                sensor_id=sensor_id,
                timestamp=timestamp,
                location=LocationModel(
                    latitude=round(base_coords["latitude"] + random.uniform(-0.01, 0.01), 6),
                    longitude=round(base_coords["longitude"] + random.uniform(-0.01, 0.01), 6)
                ),
                vehicle_count=vehicle_count,
                average_speed=round(average_speed, 2),
                congestion_level=round(random.uniform(1, 5), 2), # Keep original for consistency
                congestion_score=round(congestion_score, 2),
                processing_timestamp=datetime.now(timezone.utc),
                status='processed',
                hour_of_day=timestamp.hour,
                day_of_week=timestamp.weekday(),
                is_weekend=timestamp.weekday() >= 5,
                road_type=random.choice(["major_artery", "highway", "local_road"]),
                weather_conditions={"temperature": random.uniform(10, 35), "precipitation": random.uniform(0, 5)},
                truck_percentage=round(random.uniform(0.05, 0.3), 2),
                is_outlier=False, # For now, no outliers
                incident_occurred=incident_occurred # New field for training
            )
            
            # Upsert to avoid duplicates if run multiple times
            document_id = f"{data.sensor_id}_{int(data.timestamp.timestamp())}"
            processed_collection.update_one(
                {'_id': document_id},
                {'$set': data.model_dump(by_alias=True)}, # Use by_alias for pydantic v2
                upsert=True
            )
            if i % 100 == 0:
                logger.info(f"Generated and stored {i+1}/{num_entries} dummy entries.")

        logger.info(f"Finished generating and storing {num_entries} dummy processed data entries.")
        client.close()

    except ConnectionFailure as e:
        logger.error(f"Could not connect to MongoDB: {e}. Please ensure MongoDB is running.")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}", exc_info=True)

if __name__ == "__main__":
    generate_dummy_processed_data(num_entries=5000) # Generate 5000 entries for training
