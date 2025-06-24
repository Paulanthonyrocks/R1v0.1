# backend/app/utils/service_getters.py
"""
Service getter utilities for accessing globally initialized service instances.

This module provides a clean interface for accessing service instances that are
initialized at application startup. It uses lazy imports to avoid circular
dependency issues.
"""

from typing import Optional, TYPE_CHECKING
import logging

# Use TYPE_CHECKING to avoid runtime circular imports
if TYPE_CHECKING:
    from backend.app.services.feed_manager import FeedManager
    from backend.app.services.analytics_service import AnalyticsService
    from backend.app.websocket.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)

# Cache for service instances to avoid repeated imports
_service_cache: dict = {}


def get_feed_manager() -> "FeedManager":
    """
    Returns the initialized FeedManager instance.
    
    Returns:
        FeedManager: The initialized feed manager instance.
        
    Raises:
        RuntimeError: If the FeedManager has not been initialized.
        ImportError: If there are issues importing the services module.
    """
    if "feed_manager" in _service_cache:
        return _service_cache["feed_manager"]
    
    try:
        # Lazy import to avoid circular dependencies
        from ..services import services
        
        if not hasattr(services, 'feed_manager_instance') or services.feed_manager_instance is None:
            logger.error("FeedManager not initialized. Call initialize_services() first.")
            raise RuntimeError(
                "FeedManager not initialized. Ensure initialize_services() "
                "is called before accessing services."
            )
        
        # Cache the instance for future calls
        _service_cache["feed_manager"] = services.feed_manager_instance
        return services.feed_manager_instance
        
    except ImportError as e:
        logger.critical(f"Failed to import services module: {e}")
        raise ImportError(f"Cannot access services module: {e}") from e


def get_analytics_service() -> "AnalyticsService":
    """
    Returns the initialized AnalyticsService instance.
    
    Returns:
        AnalyticsService: The initialized analytics service instance.
        
    Raises:
        RuntimeError: If the AnalyticsService has not been initialized.
        ImportError: If there are issues importing the services module.
    """
    if "analytics_service" in _service_cache:
        return _service_cache["analytics_service"]
    
    try:
        from ..services import services
        
        # Check for both possible attribute names (with and without underscore)
        analytics_instance = getattr(services, 'analytics_service_instance', None) or \
                           getattr(services, '_analytics_service_instance', None)
        
        if analytics_instance is None:
            logger.error("AnalyticsService not initialized. Call initialize_services() first.")
            raise RuntimeError(
                "AnalyticsService not initialized. Ensure initialize_services() "
                "is called before accessing services."
            )
        
        _service_cache["analytics_service"] = analytics_instance
        return analytics_instance
        
    except ImportError as e:
        logger.critical(f"Failed to import services module: {e}")
        raise ImportError(f"Cannot access services module: {e}") from e


def get_connection_manager() -> "ConnectionManager":
    """
    Returns the initialized ConnectionManager instance.
    
    Returns:
        ConnectionManager: The initialized connection manager instance.
        
    Raises:
        RuntimeError: If the ConnectionManager has not been initialized.
        ImportError: If there are issues importing the services module.
    """
    if "connection_manager" in _service_cache:
        return _service_cache["connection_manager"]
    
    try:
        from ..services import services
        
        if not hasattr(services, 'connection_manager_instance') or services.connection_manager_instance is None:
            logger.error("ConnectionManager not initialized. Call initialize_services() first.")
            raise RuntimeError(
                "ConnectionManager not initialized. Ensure initialize_services() "
                "is called before accessing services."
            )
        
        _service_cache["connection_manager"] = services.connection_manager_instance
        return services.connection_manager_instance
        
    except ImportError as e:
        logger.critical(f"Failed to import services module: {e}")
        raise ImportError(f"Cannot access services module: {e}") from e


def is_service_initialized(service_name: str) -> bool:
    """
    Check if a service has been initialized without raising exceptions.
    
    Args:
        service_name: Name of the service ('feed_manager', 'analytics_service', 'connection_manager')
        
    Returns:
        bool: True if the service is initialized, False otherwise.
    """
    try:
        from ..services import services
        
        service_map = {
            'feed_manager': 'feed_manager_instance',
            'analytics_service': ['analytics_service_instance', '_analytics_service_instance'],
            'connection_manager': 'connection_manager_instance'
        }
        
        if service_name not in service_map:
            return False
        
        attr_names = service_map[service_name]
        if isinstance(attr_names, str):
            attr_names = [attr_names]
        
        for attr_name in attr_names:
            if hasattr(services, attr_name):
                instance = getattr(services, attr_name)
                if instance is not None:
                    return True
        
        return False
        
    except ImportError:
        logger.warning(f"Could not check initialization status for {service_name}")
        return False


def clear_service_cache() -> None:
    """
    Clear the internal service cache.
    
    This can be useful for testing or when services are reinitialized.
    """
    global _service_cache
    _service_cache.clear()
    logger.debug("Service cache cleared")


# Convenience function for getting any service by name
def get_service(service_name: str):
    """
    Generic service getter by name.
    
    Args:
        service_name: Name of the service to get
        
    Returns:
        The requested service instance
        
    Raises:
        ValueError: If the service name is not recognized
        RuntimeError: If the service is not initialized
    """
    service_getters = {
        'feed_manager': get_feed_manager,
        'analytics_service': get_analytics_service,
        'connection_manager': get_connection_manager,
    }
    
    if service_name not in service_getters:
        raise ValueError(f"Unknown service: {service_name}. Available: {list(service_getters.keys())}")
    
    return service_getters[service_name]()