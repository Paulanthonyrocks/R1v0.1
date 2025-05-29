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

# --- Dependency function to provide the application config ---
async def get_config() -> Dict[str, Any]: # Renamed for clarity if preferred, or keep as get_config
    """Dependency to get the currently loaded configuration dictionary."""
    config = get_current_config() # Call the getter from app.config
    return config

async def get_tss() -> TrafficSignalService: # tss for TrafficSignalService
    """Dependency to get the TrafficSignalService instance."""
    tss = get_traffic_signal_service()
    return tss

async def get_as() -> AnalyticsService: # as for AnalyticsService
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

<<<<<<< HEAD
# from .auth.auth_dev import DUMMY_TOKENS # Commenting out dummy token logic

async def get_current_active_user(token: HTTPAuthorizationCredentials = Depends(auth_scheme)) -> dict:
    """
    Authenticates a user via Firebase ID token.
    Used for production.
    """
    if not firebase_admin._DEFAULT_APP_NAME in firebase_admin._apps:
        # This should ideally be initialized at startup. 
        # Consider moving Firebase app initialization to main.py or a config module.
        # For now, we will log an error and raise an exception.
        # logger.error("Firebase Admin SDK default app not initialized.")
=======
async def get_current_active_user(token: HTTPAuthorizationCredentials = Depends(auth_scheme)) -> dict:
    """
    Dependency to get the current active user by verifying Firebase ID token.
    Extracts token from 'Authorization: Bearer <token>' header.
    """
    if not firebase_admin._DEFAULT_APP_NAME in firebase_admin._apps:
        # This might happen if Firebase Admin SDK failed to initialize
        # Or if it was initialized with a custom app name not stored in _DEFAULT_APP_NAME
        # Log an error and raise an internal server error or a specific HTTPException
        # For simplicity, raising a generic 503 Service Unavailable if Firebase app isn't ready.
        # In a production system, this should be monitored closely.
        # logger.error("Firebase Admin SDK default app not initialized. Cannot authenticate user.")
>>>>>>> 842672b3021dd5bce5734aa0d0c3de99ba171936
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firebase authentication service not available.",
        )

    if not token or not token.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
<<<<<<< HEAD
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    try:
        # Verify the ID token while checking if the token is revoked by passing check_revoked=True.
        decoded_token = auth.verify_id_token(token.credentials, check_revoked=True)
        # Token is valid and not revoked.
        # You can access user information from decoded_token, e.g., decoded_token['uid']
        return decoded_token
    except auth.RevokedIdTokenError:
        # Token has been revoked. Inform the user to reauthenticate or sign out.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked. Please reauthenticate.",
=======
            detail="Not authenticated or bearer token missing",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        # Verify the ID token while checking if the token is revoked by passing check_revoked=True.
        decoded_token = auth.verify_id_token(token.credentials, check_revoked=True)
        # You can add additional checks here, e.g., check user roles from the token if they are set as custom claims
        # For example: if not decoded_token.get("admin"): raise HTTPException(status_code=403, detail="Not authorized")
        return decoded_token
    except auth.RevokedIdTokenError:
        # Token has been revoked. Inform the user to reauthenticate.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked, please re-authenticate.",
>>>>>>> 842672b3021dd5bce5734aa0d0c3de99ba171936
            headers={"WWW-Authenticate": "Bearer"},
        )
    except auth.UserDisabledError:
        # Token belongs to a disabled user account.
        raise HTTPException(
<<<<<<< HEAD
            status_code=status.HTTP_401_UNAUTHORIZED,
=======
            status_code=status.HTTP_403_FORBIDDEN,
>>>>>>> 842672b3021dd5bce5734aa0d0c3de99ba171936
            detail="User account is disabled.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except auth.InvalidIdTokenError as e:
        # Token is invalid for other reasons (e.g., expired, malformed).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        # Catch any other Firebase admin errors or unexpected errors during token verification
        # logger.error(f"Error verifying Firebase ID token: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not validate credentials: {e}",
        )

<<<<<<< HEAD
    # Commenting out the DUMMY_TOKENS logic as Firebase auth is preferred for production
    # user_data = DUMMY_TOKENS.get(token.credentials)
    # if not user_data:
    #     raise HTTPException(
    #         status_code=status.HTTP_401_UNAUTHORIZED,
    #         detail="Invalid token",
    #         headers={"WWW-Authenticate": "Bearer"},
    #     )
    # return user_data

=======
>>>>>>> 842672b3021dd5bce5734aa0d0c3de99ba171936
async def get_current_active_user_optional(token: HTTPAuthorizationCredentials = Depends(auth_scheme, use_cache=False)) -> Optional[dict]:
    """
    Dependency to get the current user if authenticated, otherwise None.
    Does not raise HTTPException for missing or invalid tokens, returns None instead.
    """
    if not firebase_admin._DEFAULT_APP_NAME in firebase_admin._apps:
        # Log this issue but don't block unauthenticated access if that's intended
        # logger.error("Firebase Admin SDK default app not initialized. Cannot authenticate optional user.")
        return None # Or raise 503 if Firebase is critical even for optional auth paths

    if not token or not token.credentials:
        return None # No token provided, so no user
    
    try:
        # Verify the ID token
        decoded_token = auth.verify_id_token(token.credentials, check_revoked=True)
        return decoded_token
    except (auth.RevokedIdTokenError, auth.UserDisabledError, auth.InvalidIdTokenError):
        # Token is invalid, revoked, or user is disabled; treat as anonymous
        return None
    except Exception:
        # Unexpected error during token verification; treat as anonymous
        # logger.error(f"Unexpected error verifying Firebase ID token for optional user: {e}", exc_info=True)
        return None

<<<<<<< HEAD
security = HTTPBearer()

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
    """
    Verify Firebase ID token and get user info
    """
    try:
        if not credentials:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
            
        token = credentials.credentials
        # Verify the ID token
        decoded_token = auth.verify_id_token(token)
        
        return {
            "uid": decoded_token["uid"],
            "email": decoded_token.get("email"),
            "name": decoded_token.get("name")
        }
        
    except auth.InvalidIdTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication failed: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

=======
>>>>>>> 842672b3021dd5bce5734aa0d0c3de99ba171936
# You might also need get_connection_manager if used as a dependency
# async def get_cm():
#     cm = get_connection_manager()
#     return cm