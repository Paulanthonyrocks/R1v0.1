import logging
import threading
import copy
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
from app.models.user import User, UserRole
from app.utils.rate_limiter import RateLimiterManager

logger = logging.getLogger(__name__)

class DependencyContainer:
    """A lightweight dependency injection container."""

    def __init__(self):
        self._config: Dict[str, Any] = {}
        self._rate_limiter_manager: Optional[RateLimiterManager] = None
        self._lock = threading.Lock()

    def set_config(self, config: Any):
        """Set the configuration for the container. This can only be called once."""
        with self._lock:
            if self._config:
                logger.warning("DependencyContainer.set_config called again. Ignoring subsequent configuration.")
                return

            if hasattr(config, "dict"):
                self._config = config.dict()
            else:
                self._config = config

            ws_cfg = self._config.get("websocket", {})
            rl_cfg = ws_cfg.get("rate_limit", {})
            self._rate_limiter_manager = RateLimiterManager(
                rate=rl_cfg.get("rate", 5.0),
                capacity=rl_cfg.get("capacity", 10.0)
            )

    async def get_connection_manager(self) -> ConnectionManager:
        return ConnectionManager.get_instance()

    async def get_feed_manager(self) -> FeedManager:
        from app.services import get_feed_manager
        return get_feed_manager()

    async def get_rate_limiter_manager(self) -> RateLimiterManager:
        with self._lock:
            if self._rate_limiter_manager is None:
                if not self._config:
                    raise RuntimeError("Configuration not set. Cannot initialize RateLimiterManager.")
                ws_cfg = self._config.get("websocket", {})
                rl_cfg = ws_cfg.get("rate_limit", {})
                self._rate_limiter_manager = RateLimiterManager(
                    rate=rl_cfg.get("rate", 5.0),
                    capacity=rl_cfg.get("capacity", 10.0)
                )
            return self._rate_limiter_manager

    def get_feature_flags(self) -> FeatureFlags:
        return FeatureFlags(self._config)

    async def get_analytics_service(self) -> AnalyticsService:
        from app.services import get_analytics_service
        return get_analytics_service()

    async def get_advanced_analytics_service(self) -> AdvancedAnalyticsService:
        from app.services import get_advanced_analytics_service
        return get_advanced_analytics_service()

container = DependencyContainer()

def get_container() -> DependencyContainer:
    return container

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

async def get_db():
    try:
        db_manager = get_database_manager()
    except RuntimeError as e:
        logger.error(f"Database access failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, 
            detail="Database service is temporarily unavailable"
        )
        
    async with db_manager.get_session() as session:
        yield session

def get_db_manager() -> DatabaseManager:
    try:
        return get_database_manager()
    except RuntimeError as e:
        logger.error(f"Database manager access failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, 
            detail="Database service is temporarily unavailable"
        )

async def get_redis():
    try:
        from app.utils.redis_client import get_async_redis_client
        return await get_async_redis_client()
    except Exception as e:
        logger.error(f"Redis access failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, 
            detail="Redis service is temporarily unavailable"
        )

async def get_mongodb():
    try:
        db_manager = get_database_manager()
        return db_manager.mongo_db
    except RuntimeError as e:
        logger.error(f"MongoDB access failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, 
            detail="Database service is temporarily unavailable"
        )

async def get_connection_manager() -> ConnectionManager:
    return await container.get_connection_manager()

async def get_feed_manager() -> FeedManager:
    return await container.get_feed_manager()

async def get_rate_limiter_manager() -> RateLimiterManager:
    return await container.get_rate_limiter_manager()

async def get_analytics_service() -> AnalyticsService:
    return await container.get_analytics_service()

async def get_advanced_analytics_service() -> AdvancedAnalyticsService:
    return await container.get_advanced_analytics_service()

def get_config() -> Dict[str, Any]:
    return copy.deepcopy(container._config)

def is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN

async def get_traffic_signal_service():
    from app.services import get_traffic_signal_service as get_tss_global
    return get_tss_global()

async def get_event_service_api():
    from app.services import get_event_service as get_es_global
    return get_es_global()

async def get_personalized_routing_service():
    from app.services import get_personalized_routing_service as get_prs_global
    return get_prs_global()

async def get_weather_service_api():
    from app.services import get_weather_service as get_ws_global
    return get_ws_global()

async def get_route_optimization_service():
    from app.services import get_route_optimization_service as get_ros_global
    return get_ros_global()

async def get_current_active_user(token: str = Depends(oauth2_scheme)) -> User:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        decoded_token = await verify_firebase_token(token)
        username = decoded_token.get("uid") or decoded_token.get("sub")
        return User(
            username=username,
            email=decoded_token.get("email", ""),
            full_name=decoded_token.get("name", username),
            role=decoded_token.get("role", UserRole.USER),
        )
    except Exception as e:
        logger.warning(f"Authentication failed for token: {token[:10]}... Error: {e}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

async def get_current_viewer(current_user: User = Depends(get_current_active_user)) -> User:
    if not current_user or current_user.role not in [UserRole.ADMIN, UserRole.VIEWER]:
        logger.warning(f"Authorization failed: User {current_user.username if current_user else 'Unknown'} attempted to access viewer resource without required role.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Viewer or Admin role required")
    return current_user

async def get_current_active_user_optional(token: str = Depends(oauth2_scheme)) -> Optional[User]:
    if not token: return None
    try:
        return await get_current_active_user(token)
    except HTTPException:
        return None

async def get_current_admin(current_user: User = Depends(get_current_active_user)) -> User:
    if not current_user or current_user.role != UserRole.ADMIN:
        logger.warning(f"Authorization failed: User {current_user.username if current_user else 'Unknown'} attempted to access admin resource without required role.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    return current_user