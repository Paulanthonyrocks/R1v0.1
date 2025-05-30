# /content/drive/MyDrive/R1v0.1/backend/app/dependencies.py

from typing import Dict, Any, Optional
from fastapi import HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import firebase_admin
from firebase_admin import auth, credentials

from .database import get_database_manager
from .services.services import (
    get_feed_manager, 
    get_traffic_signal_service, 
    get_analytics_service,
    get_personalized_routing_service,
    get_route_optimization_service,
    get_weather_service,
    get_event_service
)
from .config import get_current_config
from .services.traffic_signal_service import TrafficSignalService
from .services.analytics_service import AnalyticsService
from .services.route_optimization_service import RouteOptimizationService
from .services.personalized_routing_service import PersonalizedRoutingService
from .services.weather_service import WeatherService
from .services.event_service import EventService

async def get_db():
    """Dependency to get the database manager instance."""
    # Note: If DatabaseManager methods become async, this might need changes
    db = get_database_manager()
    return db

async def get_fm():
    """Dependency to get the feed manager instance."""
    fm = get_feed_manager()
    return fm

async def get_config() -> Dict[str, Any]:
    """Dependency to get the currently loaded configuration dictionary."""
    config = get_current_config()
    return config

async def get_tss() -> TrafficSignalService:
    """Dependency to get the TrafficSignalService instance."""
    tss = get_traffic_signal_service()
    return tss

async def get_as() -> AnalyticsService:
    """Dependency to get the AnalyticsService instance."""
    analytics_svc = get_analytics_service()
    return analytics_svc

async def get_ros() -> RouteOptimizationService:
    """Dependency to get the RouteOptimizationService instance."""
    route_service = get_route_optimization_service()
    return route_service

async def get_personalized_routing_service() -> PersonalizedRoutingService:
    """Dependency to get the PersonalizedRoutingService instance."""
    routing_service = get_personalized_routing_service()
    if routing_service is None:
        raise RuntimeError("PersonalizedRoutingService not initialized")
    return routing_service

async def get_weather_service_api() -> WeatherService:
    """Dependency to get the WeatherService instance."""
    weather_service = get_weather_service()
    if weather_service is None:
        raise RuntimeError("WeatherService not initialized")
    return weather_service

async def get_event_service_api() -> EventService:
    """Dependency to get the EventService instance."""
    event_service = get_event_service()
    if event_service is None:
        raise RuntimeError("EventService not initialized")
    return event_service

# Initialize the authentication scheme
auth_scheme = HTTPBearer(auto_error=False)

# Role-based access control constants
ADMIN_ROLE = "admin"
USER_ROLE = "user"
SUPER_ADMIN_ROLE = "super_admin"

def is_admin(user_data: dict) -> bool:
    """Check if the user has admin role."""
    return user_data.get("role") == ADMIN_ROLE or user_data.get("role") == SUPER_ADMIN_ROLE

def is_super_admin(user_data: dict) -> bool:
    """Check if the user has super admin role."""
    return user_data.get("role") == SUPER_ADMIN_ROLE

async def verify_firebase_token(token: str) -> Dict[str, Any]:
    """Verify Firebase ID token and return decoded token data."""
    if not firebase_admin._DEFAULT_APP_NAME in firebase_admin._apps:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firebase authentication service not available.",
        )
    
    try:
        return auth.verify_id_token(token, check_revoked=True)
    except auth.RevokedIdTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except auth.UserDisabledError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except auth.InvalidIdTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Authentication error: {str(e)}",
        )

async def get_current_user(token: Optional[HTTPAuthorizationCredentials] = Depends(auth_scheme)) -> Dict[str, Any]:
    """Get the current authenticated user's data."""
    if not token or not token.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return await verify_firebase_token(token.credentials)

async def get_current_active_user(token: HTTPAuthorizationCredentials = Depends(auth_scheme)) -> Dict[str, Any]:
    """Get the current authenticated user's data and verify account is active."""
    user_data = await get_current_user(token)
    
    if user_data.get("disabled", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )
    
    return user_data

async def get_current_admin(user: Dict[str, Any] = Depends(get_current_active_user)) -> Dict[str, Any]:
    """Get the current authenticated admin user's data."""
    if not is_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return user

async def get_current_super_admin(user: Dict[str, Any] = Depends(get_current_active_user)) -> Dict[str, Any]:
    """Get the current authenticated super admin user's data."""
    if not is_super_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin privileges required",
        )
    return user

async def get_current_active_user_optional(token: Optional[HTTPAuthorizationCredentials] = Depends(auth_scheme)) -> Optional[Dict[str, Any]]:
    """Optionally get the current authenticated user's data."""
    if not token or not token.credentials:
        return None
    
    try:
        return await verify_firebase_token(token.credentials)
    except HTTPException:
        return None