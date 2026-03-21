# /content/drive/MyDrive/R1v0.1/backend/app/services/__init__.py

import logging

# Import the functions/classes you want to expose directly from the package level
from .services import (
    initialize_services,
    shutdown_services,
    get_connection_manager,
    get_feed_manager,
    get_route_optimization_service,
    get_traffic_signal_service,
    get_personalized_routing_service,
    get_weather_service,
    get_event_service,
    get_analytics_service,
    get_advanced_analytics_service,
    get_simulation_service,
    analytics_service_instance,
    connection_manager_instance,
    get_incident_manager,
    get_v2x_service,
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
    "get_connection_manager",
    "get_feed_manager",
    "get_route_optimization_service",
    "get_traffic_signal_service",
    "get_personalized_routing_service",
    "get_weather_service",
    "get_event_service",
    "get_analytics_service",
    "get_advanced_analytics_service",
    "get_simulation_service",
    "analytics_service_instance",
    "connection_manager_instance",
    "get_incident_manager",
    # Add class names here if you want them included in '*' import
    # "FeedManager",
]
