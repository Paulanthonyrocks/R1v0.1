# backend/app/utils/service_getters.py

from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Access the global instance from services.py (assuming services.py is imported elsewhere)
# This requires that services.py's initialize_services has been called.
# A more robust approach might involve dependency injection or passing the instance down.
# For now, we'll assume services.py has initialized and holds the instance.
# We cannot directly import the instance from services.py due to circular dependency issues.
# This setup implies that the caller MUST ensure initialize_services is run BEFORE
# calling get_feed_manager.

# We need a way to reference the instance that was created in services.py
# A common, though not ideal, pattern is to set a module-level variable
# in services.py that this getter can access.
# Example in services.py:
# feed_manager_instance: Optional[FMClass] = None
# def initialize_services(...):
#    global feed_manager_instance
#    feed_manager_instance = FMClass(...)
# This file would then need to import services *as a module* and access its variable.

# Let's refine based on the structure found in services.py where instances are module globals.
# We need to import the services module to access its global variables.

# This import creates a potential for the circular dependency to reappear if services
# itself tries to import something that eventually leads back here.
# A cleaner pattern might be to have the main application (`main.py`)
# pass the initialized service instances where needed, rather than using global getters.
# However, to match the apparent existing pattern, we'll try importing the services module.

# This import *could* still cause issues if services.py initialization
# triggers imports that lead back here before feed_manager_instance is set.
try:
 from app.services import services # Import the services module to access its global instance variables
except ImportError as e:
 logger.critical(f"Failed to import services module in service_getters: {e}")
    # Re-raise or handle appropriately - indicates a core structure issue

def get_feed_manager() -> FMClass:
    """
    Returns the initialized FeedManager instance.

    Raises:
        RuntimeError: If the FeedManager has not been initialized.
    """
    # Access the global instance variable from the imported services module
    if services.feed_manager_instance is None:
        logger.error("Attempted to get FeedManager before initialization.")
        raise RuntimeError("FeedManager not initialized.")
    return services.feed_manager_instance

# You would add similar getters for other services if needed elsewhere
# def get_connection_manager() -> ConnectionManager:
#     from app.websocket.connection_manager import ConnectionManager # Local import if only used here? Or put getter here.
#     if services_module.connection_manager_instance is None:
#          raise RuntimeError("WebSocket ConnectionManager not initialized.")
#      return services_module.connection_manager_instance

# def get_analytics_service() -> AnalyticsService:
#     from app.services.analytics_service import AnalyticsService # Local import
#     if services_module._analytics_service_instance is None: # Note the underscore
#          raise RuntimeError("AnalyticsService not initialized.")
#      return services_module._analytics_service_instance