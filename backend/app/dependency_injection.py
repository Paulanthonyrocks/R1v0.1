import logging
from typing import Dict, Any, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from app.websocket.connection_manager import ConnectionManager
from app.services.feed_manager import FeedManager
from app.database import get_database_manager
from app.utils.database import DatabaseManager
from app.services.analytics_service import AnalyticsService
from app.services.analytics_service_pro import AdvancedAnalyticsService
from app.core.feature_flags import FeatureFlags
from app.utils.auth_utils import verify_firebase_token
from app.models.user import User

logger = logging.getLogger(__name__)

class DependencyContainer:
    """A lightweight dependency injection container."""
    
    def __init__(self):
        self._config: Dict[str, Any] = {}
        self._connection_manager: Optional[ConnectionManager] = None

    def set_config(self, config: Any):
        """Set the configuration for the container."""
        if hasattr(config, "dict"):
            self._config = config.dict()
        else:
            self._config = config

    # Factory methods for core services
    
    async def get_connection_manager(self) -> ConnectionManager:
        if self._connection_manager is None:
            self._connection_manager = ConnectionManager()
        return self._connection_manager

    async def get_feed_manager(self) -> FeedManager:
        from app.services import get_feed_manager
        return get_feed_manager()

    def get_feature_flags(self) -> FeatureFlags:
        from app.core.feature_flags import FeatureFlags
        return FeatureFlags(self._config)

    async def get_analytics_service(self) -> AnalyticsService:
        from app.services import get_analytics_service
        return get_analytics_service()

    async def get_advanced_analytics_service(self) -> AdvancedAnalyticsService:
        from app.services import get_advanced_analytics_service
        return get_advanced_analytics_service()

# Global container instance
container = DependencyContainer()

def get_container() -> DependencyContainer:
    """Get the global dependency container."""
    return container

# --- FastAPI Dependencies ---

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

async def get_db():
    """Dependency to get a database session."""
    db_manager = get_database_manager()
    async with db_manager.get_session() as session:
        yield session

def get_db_manager() -> DatabaseManager:
    """Dependency to get the database manager."""
    return get_database_manager()

async def get_redis():
    """Dependency to get an async Redis client."""
    from app.utils.redis_client import get_async_redis_client
    return await get_async_redis_client()

async def get_mongodb():
    """Dependency to get MongoDB database instance."""
    from app.database import get_database_manager
    db_manager = get_database_manager()
    return db_manager.mongo_db

async def get_connection_manager() -> ConnectionManager:
    """Dependency to get the connection manager from the container."""
    # Assuming the container has been initialized/config set during startup
    return await container.get_connection_manager()

async def get_feed_manager() -> FeedManager:
    """Dependency to get the feed manager from the container."""
    return await container.get_feed_manager()

async def get_analytics_service() -> AnalyticsService:
    """Dependency to get the analytics service from the container."""
    return await container.get_analytics_service()

async def get_advanced_analytics_service() -> AdvancedAnalyticsService:
    """Dependency to get the advanced analytics service from the container."""
    return await container.get_advanced_analytics_service()

# Aliases used in analysis router
get_as = get_analytics_service
get_aas = get_advanced_analytics_service

def get_config() -> Dict[str, Any]:
    """Dependency to get the application configuration."""
    return container._config

def is_admin(user: User) -> bool:
    """Check if a user has admin role."""
    return user.role == "admin"

async def get_traffic_signal_service():
    """Dependency to get the traffic signal service."""
    from app.services import get_traffic_signal_service as get_tss_global
    return get_tss_global()

get_tss = get_traffic_signal_service

async def get_event_service_api():
    """Dependency to get the event service."""
    from app.services import get_event_service as get_es_global
    return get_es_global()

async def get_personalized_routing_service():
    """Dependency to get the personalized routing service."""
    from app.services import get_personalized_routing_service as get_prs_global
    return get_prs_global()

get_prs = get_personalized_routing_service

async def get_weather_service_api():
    """Dependency to get the weather service."""
    from app.services import get_weather_service as get_ws_global
    return get_ws_global()

async def get_route_optimization_service():
    """Dependency to get the route optimization service."""
    from app.services import get_route_optimization_service as get_ros_global
    return get_ros_global()

async def get_current_active_user(token: str = Depends(oauth2_scheme)) -> Optional[User]:
    """Dependency to get the current active user from Firebase token."""
    if not token:
        # Allow unauthenticated access if needed, or raise 401
        # For now, returning None allows endpoints to decide or use Depends(get_current_active_user) which might fail if typed strictly
        return None
        
    try:
        decoded_token = await verify_firebase_token(token)
        username = decoded_token.get("uid") or decoded_token.get("sub")
        return User(
            username=username,
            email=decoded_token.get("email", ""),
            full_name=decoded_token.get("name", username),
            role=decoded_token.get("role", "user"),
        )
    except Exception as e:
        logger.warning(f"Auth failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

async def get_current_active_user_optional(token: str = Depends(oauth2_scheme)) -> Optional[User]:
    """Dependency to get the current active user, but don't fail if token is missing or invalid."""
    if not token:
        return None
    try:
        return await get_current_active_user(token)
    except HTTPException:
        return None

async def get_current_admin(current_user: User = Depends(get_current_active_user)) -> User:
    """Dependency to enforce admin privileges."""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized",
        )
    return current_user