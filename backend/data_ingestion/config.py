# config.py
import os

# config.py
import os

# Kafka Configuration
KAFKA_BROKERS = os.getenv('KAFKA_BROKERS', 'localhost:9092').split(',')
KAFKA_RAW_TOPIC = os.getenv('KAFKA_RAW_TOPIC', 'raw_traffic_data')
# Changed KAFKA_GROUP_ID for clarity
KAFKA_GROUP_ID = os.getenv('KAFKA_GROUP_ID', 'traffic_processor_group')
KAFKA_AUTO_OFFSET_RESET = os.getenv('KAFKA_AUTO_OFFSET_RESET', 'earliest')
# KAFKA_ENABLE_AUTO_COMMIT default is now false in consumer
# For non-blocking polls in consumer
KAFKA_POLL_TIMEOUT_MS = int(os.getenv('KAFKA_POLL_TIMEOUT_MS', 1000))

# MongoDB Configuration
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/')
# Changed MONGO_DB_NAME for clarity
MONGO_DB_NAME = os.getenv('MONGO_DB_NAME', 'traffic_db_improved')
PROCESSED_DATA_COLLECTION_NAME = 'processed_traffic_data'
REGIONAL_AGGREGATED_COLLECTION_NAME = 'regional_aggregated_traffic_data'
# Added for DB retry logic
MONGO_RETRY_DELAY_SECONDS = int(os.getenv('MONGO_RETRY_DELAY_SECONDS', 2))
# RAW_DATA_COLLECTION_NAME = 'raw_data' # Optional: if storing raw data

# Processing Configuration
WINDOW_SIZE_SECONDS = int(os.getenv('WINDOW_SIZE_SECONDS', 60))
SENSOR_TO_REGION_MAPPING = {
    'sensor_1': 'north_region', 'sensor_2': 'north_region',
    'sensor_3': 'south_region', 'sensor_4': 'south_region',
    'sensor_5': 'east_region', # Add more mappings as needed
}

# Logging Configuration
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
LOG_FORMAT = ('%(asctime)s - %(name)s - %(levelname)s - '
              '%(module)s:%(lineno)d - %(message)s')

# Set for frequent window processing in testing
FORCE_WINDOW_CHECK_INTERVAL_SECONDS = int(os.getenv(
    'FORCE_WINDOW_CHECK_INTERVAL_SECONDS', 10
))
