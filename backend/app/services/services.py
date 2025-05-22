# app/services.py (Example)
import logging
from app.services.feed_manager import FeedManager as FMClass
from app.websocket.connection_manager import ConnectionManager
from app.services.traffic_signal_service import TrafficSignalService
from app.services.analytics_service import AnalyticsService
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Module-level instances, initialized by initialize_services
feed_manager_instance: Optional[FMClass] = None
connection_manager_instance: Optional[ConnectionManager] = None
_traffic_signal_service_instance: Optional[TrafficSignalService] = None
_analytics_service_instance: Optional[AnalyticsService] = None


def initialize_services(config: Dict[str, Any]):
    # Use global keyword to modify module-level variables
    global feed_manager_instance, connection_manager_instance
    global _traffic_signal_service_instance, _analytics_service_instance
    logger.info("Initializing application services...")

    if connection_manager_instance is None:
        try:
            connection_manager_instance = ConnectionManager()
            logger.info("WebSocket ConnectionManager initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize ConnectionManager: {e}", exc_info=True)
            # connection_manager_instance remains None

    if feed_manager_instance is None:
        try:
            feed_manager_instance = FMClass(config=config)
            if connection_manager_instance: # Inject if available
                feed_manager_instance.set_connection_manager(connection_manager_instance)
            logger.info("FeedManager initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize FeedManager: {e}", exc_info=True)
            # feed_manager_instance remains None

    if _traffic_signal_service_instance is None:
        try:
            _traffic_signal_service_instance = TrafficSignalService(
                config=config.get("traffic_signal_service", {}),
                connection_manager=connection_manager_instance # Pass manager
            )
            logger.info("TrafficSignalService initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize TrafficSignalService: {e}", exc_info=True)

    if _analytics_service_instance is None:
        try:
            _analytics_service_instance = AnalyticsService(
                config=config.get("analytics_service", {}),
                connection_manager=connection_manager_instance # Pass manager
            )
            logger.info("AnalyticsService initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize AnalyticsService: {e}", exc_info=True)

    logger.info("Application services initialization attempt complete.")


def get_feed_manager() -> FMClass:
    if feed_manager_instance is None:
        raise RuntimeError("FeedManager not initialized.")
    return feed_manager_instance


def get_connection_manager() -> ConnectionManager:
    if connection_manager_instance is None:
        # This should ideally not happen if initialize_services is called at startup
        raise RuntimeError("WebSocket ConnectionManager not initialized.")
    return connection_manager_instance


def get_traffic_signal_service() -> TrafficSignalService:
    if _traffic_signal_service_instance is None:
        logger.error("TrafficSignalService accessed before initialization!")
        raise RuntimeError("TrafficSignalService not initialized.")
    return _traffic_signal_service_instance


def get_analytics_service() -> AnalyticsService:
    if _analytics_service_instance is None:
        logger.error("AnalyticsService accessed before initialization!")
        raise RuntimeError("AnalyticsService not initialized.")
    return _analytics_service_instance


async def shutdown_services():
    # Use global keyword to modify module-level variables
    global feed_manager_instance, connection_manager_instance
    global _traffic_signal_service_instance, _analytics_service_instance
    logger.info("Shutting down application services...")

    if connection_manager_instance:
        try:
            logger.info("Disconnecting all WebSocket connections...")
            await connection_manager_instance.disconnect_all()
            logger.info("WebSocket connections disconnected.")
        except Exception as e:
            logger.error(f"Error disconnecting WebSockets: {e}", exc_info=True)
    # connection_manager_instance = None # Clear instance after shutdown

    if feed_manager_instance:
        try:
            logger.info("Shutting down FeedManager...")
            await feed_manager_instance.shutdown()
            logger.info("FeedManager shut down.")
        except Exception as e:
            logger.error(f"Error during FeedManager shutdown: {e}", exc_info=True)
    # feed_manager_instance = None

    if _traffic_signal_service_instance:
        try:
            logger.info("Closing TrafficSignalService...")
            await _traffic_signal_service_instance.close()
            logger.info("TrafficSignalService closed.")
        except Exception as e:
            logger.error(f"Error closing TrafficSignalService: {e}", exc_info=True)
    # _traffic_signal_service_instance = None

    if _analytics_service_instance:
        logger.info("AnalyticsService does not require explicit async shutdown.")
        # _analytics_service_instance = None # Clear instance

    logger.info("Application services shutdown process complete.")


async def health_check() -> Dict[str, Any]:
    """Performs a health check on critical services."""
    fm_status, fm_healthy = "FeedManager not initialized", False
    if feed_manager_instance:
        fm_healthy = (feed_manager_instance._result_reader_task is not None and
                      not feed_manager_instance._result_reader_task.done())
        fm_status = f"FeedManager initialized, ResultReader: {'Alive' if fm_healthy else 'Not Alive'}"

    cm_status = "Not Initialized"
    active_ws_connections = 0
    if connection_manager_instance:
        cm_status = "Initialized"
        active_ws_connections = len(connection_manager_instance.active_connections)

    tss_status = "Not Initialized"
    if _traffic_signal_service_instance:
        tss_status = "Initialized" # Add more specific health check if available

    as_status = "Not Initialized"
    if _analytics_service_instance:
        as_status = "Initialized" # Add more specific health check if available

    overall_healthy = fm_healthy # Critical service
    return {
        "status": "healthy" if overall_healthy else "degraded",
        "timestamp": datetime.utcnow().isoformat(),
        "services": {
            "feed_manager": {"status": fm_status, "healthy": fm_healthy},
            "connection_manager": {"status": cm_status, "active_connections": active_ws_connections},
            "traffic_signal_service": {"status": tss_status},
            "analytics_service": {"status": as_status}
        }
    }
