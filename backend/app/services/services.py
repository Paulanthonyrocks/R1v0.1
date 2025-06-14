# app/services.py
import logging
from app.services.feed_manager import FeedManager as FMClass
from app.websocket.connection_manager import ConnectionManager
from app.services.traffic_signal_service import TrafficSignalService
from app.services.analytics_service import AnalyticsService
from app.services.route_optimization_service import RouteOptimizationService
from app.services.personalized_routing_service import PersonalizedRoutingService
from app.services.weather_service import WeatherService
from app.services.event_service import EventService
from app.database import get_database_manager # Import the getter
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

feed_manager_instance: Optional[FMClass] = None
connection_manager_instance: Optional[ConnectionManager] = None
_traffic_signal_service_instance: Optional[TrafficSignalService] = None
_analytics_service_instance: Optional[AnalyticsService] = None
_route_optimization_service_instance: Optional[RouteOptimizationService] = None
_personalized_routing_service_instance: Optional[PersonalizedRoutingService] = None
_weather_service_instance: Optional[WeatherService] = None
_event_service_instance: Optional[EventService] = None

def initialize_services(config: Dict[str, Any]):
    global feed_manager_instance, connection_manager_instance, _traffic_signal_service_instance, \
           _analytics_service_instance, _route_optimization_service_instance, \
           _personalized_routing_service_instance, _weather_service_instance, _event_service_instance
    
    logger.info("Initializing application services...")
    if connection_manager_instance is None:
         try:
              connection_manager_instance = ConnectionManager()
              logger.info("WebSocket ConnectionManager initialized via app.services.")
         except Exception as e:
               logger.error(f"Failed to initialize ConnectionManager in app.services: {e}")
               connection_manager_instance = None # Ensure it's None on failure
               logger.info(f"WebSocket ConnectionManager initialization: Failed. Reason: {e}")
         else:
              logger.info("WebSocket ConnectionManager initialization: Successful.")


    if feed_manager_instance is None:
        try:
            feed_manager_instance = FMClass(config=config)
            # Inject connection manager if both initialized
            if connection_manager_instance:
                 feed_manager_instance.set_connection_manager(connection_manager_instance)
            logger.info("FeedManager initialized via app.services.")
        except Exception as e:
            logger.error(f"Failed to initialize FeedManager in app.services: {e}", exc_info=True)
            feed_manager_instance = None  
            logger.info(f"FeedManager initialization: Failed. Reason: {e}")
        else:
             logger.info("FeedManager initialization: Successful.")
   
    # Get the database manager instance
    try:
        db_manager = get_database_manager()
        logger.info("DatabaseManager instance obtained for service initialization.")
    except RuntimeError as e:
        logger.error(f"Failed to get DatabaseManager for service initialization: {e}")
        # Depending on criticality, you might want to raise this error
        db_manager = None # Ensure db_manager is None if getting it fails

    _traffic_signal_service_instance = TrafficSignalService(
        config=config.get("traffic_signal_service", {}),
        connection_manager=connection_manager_instance
    )
    # Pass db_manager to AnalyticsService
    _analytics_service_instance = AnalyticsService(
        config=config.get("analytics_service", {}),
        connection_manager=connection_manager_instance,
        database_manager=db_manager # Add this line
    )
    _route_optimization_service_instance = RouteOptimizationService(
        traffic_predictor=_analytics_service_instance._traffic_predictor,
        data_cache=_analytics_service_instance._data_cache
    )
    # Initialize personalized routing service
    try:
        _personalized_routing_service_instance = PersonalizedRoutingService(
            db_url=config.get("database", {}).get("url", "sqlite:///data/vehicle_data.db"),
            traffic_predictor=_analytics_service_instance._traffic_predictor if _analytics_service_instance else None,
            data_cache=_analytics_service_instance._data_cache if _analytics_service_instance else None
        )
        logger.info("PersonalizedRoutingService initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize PersonalizedRoutingService: {e}")
        _personalized_routing_service_instance = None

    # Initialize weather service
    try:
        _weather_service_instance = WeatherService(
            api_key=config.get("weather_service", {}).get("api_key", "demo-key"),
            cache_ttl_minutes=config.get("weather_service", {}).get("cache_ttl_minutes", 10)
        )
        logger.info("WeatherService initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize WeatherService: {e}")
        _weather_service_instance = None

    # Initialize event service
    try:
        _event_service_instance = EventService(
            api_url=config.get("event_service", {}).get("api_url", ""),
            cache_ttl_minutes=config.get("event_service", {}).get("cache_ttl_minutes", 30)
        )
        logger.info("EventService initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize EventService: {e}")
        _event_service_instance = None

    logger.info("Application services initialized.")

def get_feed_manager() -> FMClass:
    if feed_manager_instance is None: raise RuntimeError("FeedManager not initialized.")
    return feed_manager_instance

def get_connection_manager() -> ConnectionManager:
     if connection_manager_instance is None:
          raise RuntimeError("WebSocket ConnectionManager not initialized.")
     return connection_manager_instance

def get_traffic_signal_service() -> TrafficSignalService:
    if _traffic_signal_service_instance is None:
        # This path should ideally not be taken if initialize_services is called at startup.
        logger.error("TrafficSignalService accessed before initialization!")
        raise RuntimeError("TrafficSignalService not initialized.")
    return _traffic_signal_service_instance

def get_analytics_service() -> AnalyticsService: # New getter
    if _analytics_service_instance is None:
        logger.error("AnalyticsService accessed before initialization!")
        raise RuntimeError("AnalyticsService not initialized.")
    return _analytics_service_instance

def get_route_optimization_service() -> RouteOptimizationService:
    """Get the route optimization service instance"""
    if _route_optimization_service_instance is None:
        logger.error("RouteOptimizationService accessed before initialization!")
        raise RuntimeError("RouteOptimizationService not initialized.")
    return _route_optimization_service_instance

def get_personalized_routing_service() -> Optional[PersonalizedRoutingService]:
    """Get the personalized routing service instance."""
    return _personalized_routing_service_instance

def get_weather_service() -> WeatherService:
    """Get the weather service instance"""
    if _weather_service_instance is None:
        logger.error("WeatherService accessed before initialization!")
        raise RuntimeError("WeatherService not initialized")
    return _weather_service_instance

def get_event_service() -> EventService:
    """Get the event service instance"""
    if _event_service_instance is None:
        logger.error("EventService accessed before initialization!")
        raise RuntimeError("EventService not initialized")
    return _event_service_instance

async def shutdown_services(): # Make async for feed manager shutdown
    global feed_manager_instance, connection_manager_instance, _traffic_signal_service_instance, _analytics_service_instance, _route_optimization_service_instance
    logger.info("Shutting down application services...")
    if connection_manager_instance:
        try:
            logger.info("Disconnecting all WebSocket connections...")
            await connection_manager_instance.disconnect_all()
            logger.info("Successfully disconnected all WebSocket connections.")
        except Exception as e:
            logger.error(f"Failed to disconnect all websockets: {e}")
    else:
        logger.info("WebSocket ConnectionManager not initialized, skipping disconnect.")

    if feed_manager_instance:
        try:
            logger.info("Requesting FeedManager shutdown from app.services...")
            await feed_manager_instance.shutdown()
            logger.info("FeedManager shutdown completed successfully.")
        except Exception as e:
            logger.error(f"Error during FeedManager shutdown: {e}")
    else:
        logger.info("FeedManager not initialized, skipping shutdown.")

    if _traffic_signal_service_instance:
        await _traffic_signal_service_instance.close() # Call its close method
        _traffic_signal_service_instance = None
    
    # No specific shutdown for AnalyticsService for now, unless it holds resources like DB connections directly
    if _analytics_service_instance:
        logger.info("AnalyticsService does not require explicit shutdown currently.")
        _analytics_service_instance = None

    # Clear route optimization service
    _route_optimization_service_instance = None
        
    logger.info("Application services shut down.")

async def health_check() -> Dict[str, Any]:
    """Performs a health check on critical services."""
    # Basic health check, can be expanded
    # For FeedManager, you might check if the result reader task is alive
    # For Database, you might do a simple query
    # For external APIs (like traffic signal controller), you might ping them
    fm_status = "FeedManager not initialized" 
    fm_healthy = False
    if feed_manager_instance:
        fm_status = "FeedManager initialized"
        # Add more detailed checks if needed, e.g., _feed_manager._result_reader_task.done() / .exception()
        fm_healthy = feed_manager_instance._result_reader_task is not None and not feed_manager_instance._result_reader_task.done()
        fm_status += f", ResultReader: {'Alive' if fm_healthy else 'Not Alive'}"
        
    # Add checks for other services like TSS, Analytics if they have health indicators
    tss_status = "TrafficSignalService not initialized or no health check implemented."
    as_status = "AnalyticsService not initialized or no health check implemented."
    
    if _traffic_signal_service_instance:
        # Placeholder: a real TSS health check might try a benign API call
        tss_status = "TrafficSignalService initialized."

    if _analytics_service_instance:
        as_status = "AnalyticsService initialized."

    return {
        "status": "healthy" if fm_healthy else "degraded", # Overall status based on critical components
        "timestamp": datetime.utcnow().isoformat(),
        "services": {
            "feed_manager": {"status": fm_status, "healthy": fm_healthy},
            "connection_manager": {"status": "Initialized" if connection_manager_instance else "Not Initialized", 
                                   "active_connections": len(connection_manager_instance.active_connections) if connection_manager_instance else 0},
            "traffic_signal_service": {"status": tss_status},
            "analytics_service": {"status": as_status}
            # Add database health here
        }
    }