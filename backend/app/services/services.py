# app/services.py
import logging
import asyncio
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path

from app.websocket.connection_manager import ConnectionManager
from app.services.feed_manager import FeedManager as FMClass
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
from app.services.incident_manager import IncidentManager
from app.services.node_manager import NodeManager
from app.ml.traffic_predictor import TrafficPredictor
from app.services.video_processor import VideoProcessor, VideoManager

logger = logging.getLogger(__name__)


class ServiceRegistry:
    """Centralized service registry for managing application services."""
    
    def __init__(self):
        self._connection_manager: Optional[ConnectionManager] = None
        self._feed_manager: Optional[FMClass] = None
        self._traffic_signal_service: Optional[TrafficSignalService] = None
        self._analytics_service: Optional[AnalyticsService] = None
        self._route_optimization_service: Optional[RouteOptimizationService] = None
        self._personalized_routing_service: Optional[PersonalizedRoutingService] = None
        self._weather_service: Optional[WeatherService] = None
        self._event_service: Optional[EventService] = None
        self._retention_service: Optional[RetentionService] = None
        self._notification_service: Optional[NotificationService] = None
        self._advanced_analytics_service: Optional[AdvancedAnalyticsService] = None
        self._incident_manager: Optional[IncidentManager] = None
        self._node_manager: Optional[NodeManager] = None
        self._video_manager: Optional[VideoManager] = None
        self._init_lock = asyncio.Lock()
        self._initialized = False

    @property
    def connection_manager(self) -> ConnectionManager:
        if self._connection_manager is None:
            raise RuntimeError("ConnectionManager not initialized.")
        return self._connection_manager

    @property
    def feed_manager(self) -> FMClass:
        if self._feed_manager is None:
            raise RuntimeError("FeedManager not initialized.")
        return self._feed_manager

    @property
    def traffic_signal_service(self) -> TrafficSignalService:
        if self._traffic_signal_service is None:
            raise RuntimeError("TrafficSignalService not initialized.")
        return self._traffic_signal_service

    @property
    def analytics_service(self) -> AnalyticsService:
        if self._analytics_service is None:
            raise RuntimeError("AnalyticsService not initialized.")
        return self._analytics_service

    @property
    def advanced_analytics_service(self) -> AdvancedAnalyticsService:
        if self._advanced_analytics_service is None:
            raise RuntimeError("AdvancedAnalyticsService not initialized.")
        return self._advanced_analytics_service

    @property
    def route_optimization_service(self) -> RouteOptimizationService:
        if self._route_optimization_service is None:
            raise RuntimeError("RouteOptimizationService not initialized.")
        return self._route_optimization_service

    @property
    def personalized_routing_service(self) -> PersonalizedRoutingService:
        if self._personalized_routing_service is None:
            raise RuntimeError("PersonalizedRoutingService not initialized.")
        return self._personalized_routing_service

    @property
    def weather_service(self) -> WeatherService:
        if self._weather_service is None:
            raise RuntimeError("WeatherService not initialized.")
        return self._weather_service

    @property
    def event_service(self) -> EventService:
        if self._event_service is None:
            raise RuntimeError("EventService not initialized.")
        return self._event_service

    @property
    def notification_service(self) -> NotificationService:
        if self._notification_service is None:
            raise RuntimeError("NotificationService not initialized.")
        return self._notification_service

    @property
    def retention_service(self) -> RetentionService:
        if self._retention_service is None:
            raise RuntimeError("RetentionService not initialized.")
        return self._retention_service

    @property
    def incident_manager(self) -> IncidentManager:
        if self._incident_manager is None:
            raise RuntimeError("IncidentManager not initialized.")
        return self._incident_manager

    @property
    def node_manager(self) -> NodeManager:
        if self._node_manager is None:
            raise RuntimeError("NodeManager not initialized.")
        return self._node_manager

    @property
    def video_manager(self) -> VideoManager:
        if self._video_manager is None:
            raise RuntimeError("VideoManager not initialized.")
        return self._video_manager

    async def initialize(
        self, 
        config: Dict[str, Any], 
        connection_manager: ConnectionManager
    ) -> None:
        """Initialize all application services."""
        async with self._init_lock:
            if self._initialized:
                logger.warning("Services already initialized. Skipping re-initialization.")
                return

            self._connection_manager = connection_manager

            try:
                # Get database manager once and pass it down
                db_manager = get_database_manager()
                logger.info("DatabaseManager instance obtained for service initialization.")
            except RuntimeError as e:
                logger.error(f"Failed to get DatabaseManager: {e}")
                raise

            try:
                # Initialize core services
                await self._initialize_core_services(config, connection_manager, db_manager)
                
                # Initialize dependent services
                await self._initialize_dependent_services(config, db_manager)
                
                # Initialize optional services
                await self._initialize_optional_services(config, db_manager)
                
                self._initialized = True
                logger.info("All application services initialized successfully.")
            except Exception as e:
                logger.error(f"Initialization failed midway: {e}. Tearing down partial states.")
                await self.shutdown()
                raise

    async def _initialize_core_services(
        self, 
        config: Dict[str, Any], 
        connection_manager: ConnectionManager,
        db_manager: Any
    ) -> None:
        """Initialize critical services required for basic operation."""
        try:
            # Traffic Signal Service
            self._traffic_signal_service = TrafficSignalService(
                config=config,
                connection_manager=connection_manager,
            )
            logger.info("TrafficSignalService initialized.")

            # Notification Service
            self._notification_service = NotificationService(
                config=config.get("notifications", {})
            )
            logger.info("NotificationService initialized.")

            # Weather Service
            self._weather_service = WeatherService(
                api_url=config.get("weather_service", {}).get("api_url", ""),
                api_key=config.get("weather_service", {}).get("api_key", "demo-key"),
                cache_ttl_minutes=config.get("weather_service", {}).get("cache_ttl_minutes", 10),
            )
            logger.info("WeatherService initialized.")

            # Incident Manager
            self._incident_manager = IncidentManager(
                config=config,
                db_manager=db_manager,
                connection_manager=connection_manager,
                notification_service=self._notification_service
            )
            logger.info("IncidentManager initialized.")

            # Analytics Service with Traffic Predictor
            traffic_predictor = await self._load_traffic_predictor(config)
            
            self._analytics_service = AnalyticsService(
                config=config,
                connection_manager=connection_manager,
                database_manager=db_manager,
                traffic_predictor=traffic_predictor,
                traffic_signal_service=self._traffic_signal_service,
                notification_service=self._notification_service,
                incident_manager=self._incident_manager,
            )
            logger.info("AnalyticsService initialized.")

            # Node Manager
            self._node_manager = NodeManager(config=config)
            await self._node_manager.start()
            logger.info("NodeManager initialized and started.")

            # Feed Manager
            self._feed_manager = FMClass(config)
            self._feed_manager.set_connection_manager(connection_manager)
            self._feed_manager.set_analytics_service(self._analytics_service)
            
            # Initialize async background tasks (result reader, watchdog, etc.)
            # Guard in initialize() prevents double-creation if start_processing() also calls it.
            await self._feed_manager.initialize()
            
            # Link Feed Manager to Incident Manager
            self._incident_manager.set_feed_manager(self._feed_manager)
            logger.info("FeedManager initialized and linked to IncidentManager.")

            # Video Manager (manages per-stream VideoProcessors for recording)
            # Uses the same output_directory as video_output config.
            video_out_cfg = config.get("video_output", {})
            self._video_manager = VideoManager.get_instance(
                output_directory=video_out_cfg.get("output_directory", "backend/data/recordings")
            )
            logger.info("VideoManager initialized.")
        except Exception as e:
            logger.error(f"Error during core service initialization: {e}")
            raise

    async def _load_traffic_predictor(self, config: Dict[str, Any]) -> Optional[TrafficPredictor]:
        """Load the traffic predictor model if configured."""
        analytics_cfg = config.get("analytics_service", {})
        model_path = analytics_cfg.get("model_path")
        prediction_enabled = analytics_cfg.get("traffic_prediction", {}).get("enabled", False)
        
        if not prediction_enabled:
            logger.info("Traffic prediction disabled in config.")
            return None
            
        if not model_path:
            logger.warning("No model_path configured for TrafficPredictor.")
            return None

        model_file = Path(model_path)
        if not model_file.exists():
            logger.error(f"TrafficPredictor model file not found: {model_path}")
            return None
        
        if not model_file.is_file():
            logger.error(f"TrafficPredictor model path is not a file: {model_path}")
            return None

        try:
            predictor = TrafficPredictor(config=config)
            # Load the heavy TensorFlow model in a separate thread to avoid blocking the event loop
            await asyncio.to_thread(predictor.load_model, str(model_file))
            logger.info(f"TrafficPredictor model loaded from {model_path}")
            return predictor
        except Exception as e:
            logger.error(f"Failed to load TrafficPredictor model: {e}", exc_info=True)
            return None

    async def _initialize_dependent_services(
        self, 
        config: Dict[str, Any], 
        db_manager
    ) -> None:
        """Initialize services that depend on core services."""
        
        # Get public interfaces instead of private attributes
        data_cache = self._analytics_service.get_data_cache()
        traffic_predictor = self._analytics_service.get_traffic_predictor()

        # Route Optimization Service
        self._route_optimization_service = RouteOptimizationService(
            traffic_predictor=traffic_predictor,
            data_cache=data_cache,
            weather_service=self._weather_service
        )
        logger.info("RouteOptimizationService initialized.")

        # Personalized Routing Service
        self._personalized_routing_service = PersonalizedRoutingService(
            database_manager=db_manager,
            traffic_predictor=traffic_predictor,
            data_cache=data_cache,
        )
        logger.info("PersonalizedRoutingService initialized.")

        # Advanced Analytics Service
        self._advanced_analytics_service = AdvancedAnalyticsService(
            db_manager=db_manager
        )
        logger.info("AdvancedAnalyticsService initialized.")

    async def _initialize_optional_services(
        self, 
        config: Dict[str, Any], 
        db_manager
    ) -> None:
        """Initialize optional services that can fail without breaking the app."""
        
        # Event Service
        try:
            self._event_service = EventService(
                api_url=config.get("event_service", {}).get("api_url", ""),
                cache_ttl_minutes=config.get("event_service", {}).get("cache_ttl_minutes", 30),
            )
            logger.info("EventService initialized.")
        except Exception as e:
            logger.warning(f"EventService initialization failed (non-critical): {e}")
            self._event_service = None

        # Retention Service
        try:
            self._retention_service = RetentionService(config=config)
            self._retention_service.start()
            logger.info("RetentionService initialized and started.")
        except Exception as e:
            logger.warning(f"RetentionService initialization failed (non-critical): {e}")
            self._retention_service = None

    async def shutdown(self) -> None:
        """Gracefully shutdown all services."""
        logger.info("Shutting down application services...")

        # Shutdown in reverse order of dependency
        shutdown_tasks = [
            ("FeedManager", self._shutdown_feed_manager),
            ("RetentionService", self._shutdown_retention_service),
            ("IncidentManager", self._shutdown_incident_manager),
            ("AnalyticsService", self._shutdown_analytics_service),
            ("NotificationService", self._shutdown_notification_service),
            ("TrafficSignalService", self._shutdown_traffic_signal_service),
            ("WeatherService", self._shutdown_weather_service),
            ("NodeManager", self._shutdown_node_manager),
            ("EventService", self._shutdown_event_service),
            ("VideoManager", self._shutdown_video_manager),
        ]

        for service_name, shutdown_func in shutdown_tasks:
            try:
                await shutdown_func()
            except Exception as e:
                logger.error(f"Error shutting down {service_name}: {e}", exc_info=True)

        # Clear all references
        self._reset_services()
        self._initialized = False
        logger.info("All services shut down successfully.")

    async def _shutdown_feed_manager(self) -> None:
        if self._feed_manager:
            await self._feed_manager.shutdown()
            logger.info("FeedManager shutdown completed.")

    async def _shutdown_retention_service(self) -> None:
        if self._retention_service:
            await self._retention_service.stop()
            logger.info("RetentionService stopped.")

    async def _shutdown_incident_manager(self) -> None:
        if self._incident_manager:
            # Add any specific cleanup if needed
            logger.info("IncidentManager shutdown completed.")

    async def _shutdown_analytics_service(self) -> None:
        if self._analytics_service:
            await self._analytics_service.stop_background_tasks()
            logger.info("AnalyticsService background tasks stopped.")

    async def _shutdown_notification_service(self) -> None:
        if self._notification_service and hasattr(self._notification_service, 'close'):
            await self._notification_service.close()
            logger.info("NotificationService closed.")

    async def _shutdown_traffic_signal_service(self) -> None:
        if self._traffic_signal_service:
            await self._traffic_signal_service.close()
            logger.info("TrafficSignalService closed.")

    async def _shutdown_weather_service(self) -> None:
        if self._weather_service and hasattr(self._weather_service, 'close'):
            await self._weather_service.close()
            logger.info("WeatherService closed.")

    async def _shutdown_event_service(self) -> None:
        if self._event_service and hasattr(self._event_service, 'close'):
            await self._event_service.close()
            logger.info("EventService closed.")

    async def _shutdown_node_manager(self) -> None:
        if self._node_manager:
            await self._node_manager.stop()
            logger.info("NodeManager shutdown completed.")

    async def _shutdown_video_manager(self) -> None:
        if self._video_manager:
            await self._video_manager.cleanup()
            logger.info("VideoManager shutdown completed.")

    def _reset_services(self) -> None:
        """Reset all service references to None."""
        self._feed_manager = None
        self._traffic_signal_service = None
        self._analytics_service = None
        self._route_optimization_service = None
        self._personalized_routing_service = None
        self._weather_service = None
        self._event_service = None
        self._retention_service = None
        self._notification_service = None
        self._advanced_analytics_service = None
        self._incident_manager = None
        self._node_manager = None
        self._video_manager = None
        self._connection_manager = None

    async def health_check(self) -> Dict[str, Any]:
        """Perform health check on all services."""
        services_health = {}
        overall_healthy = True

        # Check Feed Manager
        if self._feed_manager:
            fm_healthy = self._feed_manager.is_healthy()
            services_health["feed_manager"] = {
                "status": "healthy" if fm_healthy else "unhealthy",
                "healthy": fm_healthy
            }
            overall_healthy = overall_healthy and fm_healthy
        else:
            services_health["feed_manager"] = {"status": "not initialized", "healthy": False}
            overall_healthy = False

        # Check other critical services
        critical_services = [
            ("traffic_signal_service", self._traffic_signal_service),
            ("analytics_service", self._analytics_service),
            ("weather_service", self._weather_service),
        ]

        for service_name, service in critical_services:
            if service:
                # Check if service has a health check method
                if hasattr(service, 'health_check'):
                    try:
                        health_status = await service.health_check()
                        services_health[service_name] = health_status
                        overall_healthy = overall_healthy and health_status.get("healthy", True)
                    except Exception as e:
                        logger.error(f"Health check failed for {service_name}: {e}")
                        services_health[service_name] = {"status": "error", "healthy": False}
                        overall_healthy = False
                else:
                    services_health[service_name] = {"status": "initialized", "healthy": True}
            else:
                services_health[service_name] = {"status": "not initialized", "healthy": False}
                overall_healthy = False

        # Check optional services (don't affect overall health)
        optional_services = [
            ("event_service", self._event_service),
            ("retention_service", self._retention_service),
        ]

        for service_name, service in optional_services:
            if service:
                services_health[service_name] = {"status": "initialized", "healthy": True}
            else:
                services_health[service_name] = {"status": "not initialized", "healthy": None}

        return {
            "status": "healthy" if overall_healthy else "degraded",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "services": services_health,
        }


# Global registry instance
_service_registry: Optional[ServiceRegistry] = None

# Globals for backward compatibility
connection_manager_instance: Optional[ConnectionManager] = None
analytics_service_instance: Optional[AnalyticsService] = None


def get_service_registry() -> ServiceRegistry:
    """Get the global service registry instance."""
    global _service_registry
    if _service_registry is None:
        raise RuntimeError("ServiceRegistry not initialized. Call initialize_services first.")
    return _service_registry


async def initialize_services(
    config: Dict[str, Any], 
    logger_instance: logging.Logger, 
    connection_manager: ConnectionManager
) -> None:
    """Initialize all application services."""
    global _service_registry, connection_manager_instance, analytics_service_instance
    
    if _service_registry is not None:
        logger_instance.warning("Services already initialized.")
        return
    
    # Initialize locally first to avoid "zombie state" on failure
    registry = ServiceRegistry()
    await registry.initialize(config, connection_manager)
    _service_registry = registry
    
    # Update globals for backward compatibility
    try:
        connection_manager_instance = _service_registry.connection_manager
        analytics_service_instance = _service_registry.analytics_service
    except RuntimeError as e:
        logger_instance.error(f"Failed to set backward compatibility globals: {e}")


async def shutdown_services() -> None:
    """Shutdown all application services."""
    global _service_registry, connection_manager_instance, analytics_service_instance
    
    if _service_registry is None:
        logger.warning("Services not initialized, nothing to shutdown.")
        return
    
    await _service_registry.shutdown()
    _service_registry = None
    
    # Reset globals
    connection_manager_instance = None
    analytics_service_instance = None


async def health_check() -> Dict[str, Any]:
    """Perform health check on all services."""
    return await get_service_registry().health_check()


# Convenience getter functions for backward compatibility
def get_connection_manager() -> ConnectionManager:
    return get_service_registry().connection_manager


def get_feed_manager() -> FMClass:
    return get_service_registry().feed_manager


def get_traffic_signal_service() -> TrafficSignalService:
    return get_service_registry().traffic_signal_service


def get_analytics_service() -> AnalyticsService:
    return get_service_registry().analytics_service


def get_advanced_analytics_service() -> AdvancedAnalyticsService:
    return get_service_registry().advanced_analytics_service


def get_route_optimization_service() -> RouteOptimizationService:
    return get_service_registry().route_optimization_service


def get_personalized_routing_service() -> PersonalizedRoutingService:
    return get_service_registry().personalized_routing_service


def get_weather_service() -> WeatherService:
    return get_service_registry().weather_service


def get_event_service() -> EventService:
    return get_service_registry().event_service


def get_notification_service() -> NotificationService:
    return get_service_registry().notification_service


def get_retention_service() -> RetentionService:
    return get_service_registry().retention_service


def get_incident_manager() -> IncidentManager:
    return get_service_registry().incident_manager


def get_video_manager() -> VideoManager:
    """Get the VideoManager singleton. Raises if not yet initialized."""
    return get_service_registry().video_manager
