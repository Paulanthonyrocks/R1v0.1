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

# Scheme for API key header
auth_scheme = HTTPBearer()

async def get_current_active_user(token: HTTPAuthorizationCredentials = Depends(auth_scheme)) -> dict:
    """
    Authenticates a user via Firebase ID token.
    Used for production.
    """
    if not firebase_admin._DEFAULT_APP_NAME in firebase_admin._apps:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firebase authentication service not available.",
        )

    if not token or not token.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated or bearer token missing",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        # Verify the ID token while checking if the token is revoked
        decoded_token = auth.verify_id_token(token.credentials, check_revoked=True)
        return decoded_token
    except auth.RevokedIdTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked, please re-authenticate.",
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
            detail=f"Invalid token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not validate credentials: {e}",
        )

async def get_current_active_user_optional(token: Optional[HTTPAuthorizationCredentials] = Depends(auth_scheme)) -> Optional[dict]:
    """
    Dependency to get the current user if authenticated, otherwise None.
    Does not raise HTTPException for missing or invalid tokens, returns None instead.
    """
    if not firebase_admin._DEFAULT_APP_NAME in firebase_admin._apps:
        return None

    if not token or not token.credentials:
        return None
    
    try:
        decoded_token = auth.verify_id_token(token.credentials, check_revoked=True)
        return decoded_token
    except (auth.RevokedIdTokenError, auth.UserDisabledError, auth.InvalidIdTokenError):
        return None
    except Exception:
        return None