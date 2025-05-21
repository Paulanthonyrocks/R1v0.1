# /content/drive/MyDrive/R1v0.1/backend/app/services/__init__.py

import logging

# Import the functions/classes you want to expose directly from the package level
from .services import ( # Assuming your file is named services.py
    initialize_services,
    shutdown_services,
    get_feed_manager,
    get_connection_manager,
    get_route_optimization_service,
    feed_manager_instance, # Expose instance if needed directly (less common)
    connection_manager_instance # Expose instance if needed directly (less common)
)
# Optional: Import specific classes if they are needed elsewhere directly
# from .feed_manager import FeedManager # If FeedManager class is in its own file
# from .some_other_service import SomeOtherService

logger = logging.getLogger(__name__)
logger.debug("app.services package initialized.")

# Optional: Define what '*' imports if someone does 'from app.services import *'
__all__ = [
    "initialize_services",
    "shutdown_services",
    "get_feed_manager",
    "get_connection_manager",
    "get_route_optimization_service",
    # Add class names here if you want them included in '*' import
    # "FeedManager",
]