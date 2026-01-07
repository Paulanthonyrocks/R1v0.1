# app/services.py
import logging
from app.websocket.connection_manager import ConnectionManager
from app.services.feed_manager import FeedManager as FMClass, initialize_feed_manager

from typing import Optional, Dict, Any
from datetime import datetime

from app.services.traffic_signal_service import TrafficSignalService
from app.services.route_optimization_service import RouteOptimizationService
from app.services.personalized_routing_service import PersonalizedRoutingService
from app.services.weather_service import WeatherService
from app.services.event_service import EventService
from app.database import get_database_manager
from app.services.analytics_service import AnalyticsService
from app.services.analytics_service_pro import AdvancedAnalyticsService
from app.services.retention import RetentionService
from app.services.notification_service import NotificationService
from app.ml.traffic_predictor import TrafficPredictor # Import TrafficPredictor

logger = logging.getLogger(__name__)

connection_manager_instance: Optional[ConnectionManager] = None
feed_manager_instance: Optional[FMClass] = (
    None  # Keep FeedManager instance global if needed outside initialize_services
)


_traffic_signal_service_instance: Optional[TrafficSignalService] = None
_analytics_service_instance: Optional[AnalyticsService] = None
_route_optimization_service_instance: Optional[RouteOptimizationService] = None
_personalized_routing_service_instance: Optional[PersonalizedRoutingService] = None
_weather_service_instance: Optional[WeatherService] = None
_event_service_instance: Optional[EventService] = None
_retention_service_instance: Optional[RetentionService] = None
_notification_service_instance: Optional[NotificationService] = None
_advanced_analytics_service_instance: Optional[AdvancedAnalyticsService] = None


async def initialize_services(
    config: Dict[str, Any], logger: logging.Logger, connection_manager: ConnectionManager
):  # Accept logger as argument
    global \
        feed_manager_instance, \
        _traffic_signal_service_instance, \
        _analytics_service_instance, \
        _route_optimization_service_instance, \
        _personalized_routing_service_instance, \
        _weather_service_instance, \
        _event_service_instance, \
        _retention_service_instance, \
        _notification_service_instance, \
        _advanced_analytics_service_instance, \
        connection_manager_instance

    connection_manager_instance = connection_manager

    # Get the database manager instance
    try:
        db_manager = get_database_manager()
        logger.info("DatabaseManager instance obtained for service initialization.")
    except RuntimeError as e:
        logger.error(f"Failed to get DatabaseManager for service initialization: {e}")
        # Depending on criticality, you might want to raise this error
        db_manager = None  # Ensure db_manager is None if getting it fails

    _traffic_signal_service_instance = TrafficSignalService(
        config=config.get("traffic_signal_service", {}),
        connection_manager=connection_manager,
    )

    # Initialize NotificationService
    _notification_service_instance = NotificationService(
        config=config.get("notifications", {})
    )

    # Pass db_manager to AnalyticsService
    try:
        from app.services.analytics_service import AnalyticsService

        # Load the TrafficPredictor model
        analytics_cfg = config.get("analytics_service", {})
        traffic_predictor_model_path = analytics_cfg.get("model_path")
        prediction_enabled = analytics_cfg.get("traffic_prediction", {}).get("enabled", False)
        
        loaded_traffic_predictor = None
        if traffic_predictor_model_path and prediction_enabled:
            try:
                loaded_traffic_predictor = TrafficPredictor(config=analytics_cfg)
                loaded_traffic_predictor.load_model(traffic_predictor_model_path)
                logger.info(f"TrafficPredictor model loaded from {traffic_predictor_model_path}")
            except Exception as e:
                logger.error(f"Failed to load TrafficPredictor model from {traffic_predictor_model_path}: {e}", exc_info=True)
        elif not prediction_enabled:
            logger.info("Traffic prediction disabled in config. Skipping model load.")
        else:
            logger.warning("No model_path configured for TrafficPredictor. AnalyticsService will use a mock predictor.")

        _analytics_service_instance = AnalyticsService(
            config=config.get("analytics_service", {}),
            connection_manager=connection_manager,
            database_manager=db_manager,
            traffic_predictor=loaded_traffic_predictor, # Pass the loaded predictor
            traffic_signal_service=_traffic_signal_service_instance,
            notification_service=_notification_service_instance,
        )
        logger.info("AnalyticsService initialized successfully in services.py.")
        print(
            f"services.py: _analytics_service_instance created: {_analytics_service_instance}"
        )
        # Initialize FeedManager
        feed_manager_instance = await initialize_feed_manager(config)
        feed_manager_instance.set_connection_manager(connection_manager)
        feed_manager_instance.set_analytics_service(_analytics_service_instance)
        logger.info("FeedManager initialized.")
        print(f"DEBUG: feed_manager_instance in services.py: {feed_manager_instance}")
    except Exception as e:
        logger.error(f"Failed to initialize AnalyticsService: {e}", exc_info=True)
        _analytics_service_instance = None
        raise # Re-raise the exception to propagate the error

    # Initialize weather service
    try:
        _weather_service_instance = WeatherService(
            api_url=config.get("weather_service", {}).get("api_url", ""),
            api_key=config.get("weather_service", {}).get("api_key", "demo-key"),
            cache_ttl_minutes=config.get("weather_service", {}).get(
                "cache_ttl_minutes", 10
            ),
        )
        logger.info("WeatherService initialized successfully")
    except Exception:
        raise  # Re-raise the exception to propagate the error

    if _analytics_service_instance:
        _route_optimization_service_instance = RouteOptimizationService(
            weather_service=_weather_service_instance,
            data_cache=_analytics_service_instance._data_cache,  # Assuming data_cache is also needed by RouteOptimizationService
            traffic_predictor=_analytics_service_instance._traffic_predictor,  # Pass the loaded predictor
        )
        logger.info("RouteOptimizationService initialized successfully.")
        feed_manager_instance.set_analytics_service(_analytics_service_instance)
    else:
        logger.warning(
            "AnalyticsService not initialized, skipping RouteOptimizationService initialization."
        )
    # Initialize personalized routing service
    try:
        _personalized_routing_service_instance = PersonalizedRoutingService(
            database_manager=db_manager,
            traffic_predictor=_analytics_service_instance._traffic_predictor
            if _analytics_service_instance
            else None,
            data_cache=_analytics_service_instance._data_cache
            if _analytics_service_instance
            else None,
        )
        logger.info("PersonalizedRoutingService initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize PersonalizedRoutingService: {e}")
        _personalized_routing_service_instance = None

    # Initialize event service
    try:
        _event_service_instance = EventService(
            api_url=config.get("event_service", {}).get("api_url", ""),
            cache_ttl_minutes=config.get("event_service", {}).get(
                "cache_ttl_minutes", 30
            ),
        )
        logger.info("EventService initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize EventService: {e}")
        _event_service_instance = None

    # Initialize and start RetentionService
    try:
        _retention_service_instance = RetentionService(config=config)
        _retention_service_instance.start()
        logger.info("RetentionService initialized and started successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize RetentionService: {e}")
        _retention_service_instance = None

    # Initialize Advanced Analytics Service (Pro)
    if db_manager:
        _advanced_analytics_service_instance = AdvancedAnalyticsService(db_manager=db_manager)
        logger.info("AdvancedAnalyticsService (Pro) initialized successfully.")

    logger.info("Application services initialized.")




def get_connection_manager() -> ConnectionManager:
    if connection_manager_instance is None:
        raise RuntimeError("ConnectionManager not initialized.")
    return connection_manager_instance

def get_feed_manager() -> FMClass:
    if feed_manager_instance is None:
        logger.error("FeedManager accessed before initialization!")
        raise RuntimeError("FeedManager not initialized.")
    return feed_manager_instance


def get_traffic_signal_service() -> TrafficSignalService:
    if _traffic_signal_service_instance is None:
        # This path should ideally not be taken if initialize_services is called at startup.
        logger.error("TrafficSignalService accessed before initialization!")
        raise RuntimeError("TrafficSignalService not initialized.")
    return _traffic_signal_service_instance


def get_analytics_service() -> AnalyticsService:  # New getter
    if _analytics_service_instance is None:
        logger.error("AnalyticsService accessed before initialization!")
        raise RuntimeError("AnalyticsService not initialized.")
    return _analytics_service_instance


def get_advanced_analytics_service() -> AdvancedAnalyticsService:
    if _advanced_analytics_service_instance is None:
        logger.error("AdvancedAnalyticsService accessed before initialization!")
        raise RuntimeError("AdvancedAnalyticsService not initialized.")
    return _advanced_analytics_service_instance


def get_route_optimization_service() -> RouteOptimizationService:
    """Get the route optimization service instance"""
    if _route_optimization_service_instance is None:
        logger.error("RouteOptimizationService accessed before initialization!")
        raise RuntimeError("RouteOptimizationService not initialized.")
    return _route_optimization_service_instance


def get_personalized_routing_service() -> Optional[PersonalizedRoutingService]:
    """Get the personalized routing service instance."""
    # No logging needed here as it returns Optional
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


async def shutdown_services():  # Make async for feed manager shutdown
    global \
        feed_manager_instance, \
        _traffic_signal_service_instance, \
        _analytics_service_instance, \
        _route_optimization_service_instance, \
        _notification_service_instance
    logger.info("Shutting down application services...")
    

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
        await _traffic_signal_service_instance.close()  # Call its close method
        _traffic_signal_service_instance = None

    if _analytics_service_instance:
        logger.info("Shutting down AnalyticsService background tasks...")
        await _analytics_service_instance.stop_background_tasks()
        _analytics_service_instance = None

    if _notification_service_instance:
        await _notification_service_instance.close()
        _notification_service_instance = None

    # Clear route optimization service
    _route_optimization_service_instance = None

    logger.info("Application services shut down.")


async def health_check() -> Dict[str, Any]:
    """Performs a health check on critical services."""
    # logger = logging.getLogger("app.services") # Ensure logger is defined here too
    # Basic health check, can be expanded
    # For FeedManager, you might check if the result reader task is alive
    # For Database, you might do a simple query
    # For external APIs (like traffic signal controller), you might ping them
    fm_status = "FeedManager not initialized"
    fm_healthy = False
    if feed_manager_instance:
        fm_status = "FeedManager initialized"
        # Add more detailed checks if needed, e.g., _feed_manager._result_reader_task.done() / .exception()
        fm_healthy = (
            feed_manager_instance._result_reader_task is not None
            and not feed_manager_instance._result_reader_task.done()
        )
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
        "status": "healthy"
        if fm_healthy
        else "degraded",  # Overall status based on critical components
        "timestamp": datetime.utcnow().isoformat(),
        "services": {
            "feed_manager": {"status": fm_status, "healthy": fm_healthy},
            
            "traffic_signal_service": {"status": tss_status},
            "analytics_service": {"status": as_status},
            # Add database health here
        },
    }
