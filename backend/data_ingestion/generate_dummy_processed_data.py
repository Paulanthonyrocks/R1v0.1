import random
from datetime import datetime, timezone, timedelta
# pymongo imports removed to avoid dependency
# from pymongo import MongoClient
# from pymongo.errors import ConnectionFailure
from models import ProcessedTrafficDataDBModel, LocationModel
import config
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=config.LOG_LEVEL, format=config.LOG_FORMAT)

def generate_dummy_processed_data(num_entries: int = 1000):
    logger.warning("generate_dummy_processed_data is disabled because pymongo is not installed.")
    return

