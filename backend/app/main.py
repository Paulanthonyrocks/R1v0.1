# /content/drive/MyDrive/R1v0.1/backend/app/main.py (Updated)

import logging
import logging.config
from pathlib import Path
import time
from typing import Dict, Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
from starlette.websockets import WebSocketState
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
import json
import firebase_admin
from firebase_admin import credentials
import uuid # For generating unique client IDs for WebSockets
import asyncio

# --- Import application modules ---
# Routers
from app.routers import (
    feeds, 
    config as config_router, 
    analysis, 
    alerts, 
    video, 
    incidents,
    personalized_routes,
    pavement
)
from . import api
# Initializers/Getters - Import config initializer now
from .config import initialize_config, get_current_config  # Import config init/getter
from .database import initialize_database, close_database, get_database_manager
from .services import initialize_services, shutdown_services, get_feed_manager, get_connection_manager, get_analytics_service
from .services.services import health_check as services_health_check # Import directly
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum, ErrorNotification # Added imports
from app.tasks.prediction_scheduler import PredictionScheduler # Import the new scheduler
# Logging will be reconfigured by initialize_config
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# --- FastAPI App Instance ---
app = FastAPI(
    title="Route One Hub - Backend API",
    version="1.0.0",
    description="API for managing traffic analysis feeds, data, and real-time updates.",
)

# --- Initialize Firebase ---
def initialize_firebase():
    config = get_current_config()
    if config.get("firebase", {}).get("auth_enabled", False):
        try:
            cred = credentials.Certificate(config["firebase"]["service_account_path"])
            firebase_admin.initialize_app(cred)
            logger.info("Firebase initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Firebase: {e}")
            raise

# --- Exception Handlers ---
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Ensure all unhandled exceptions return JSON rather than HTML"""
    logger.exception("Unhandled exception occurred:")
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "type": "Internal Server Error"}
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Convert HTTPExceptions to JSON format"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "type": "HTTP Exception"}
    )

# --- Event Handlers ---
@app.on_event("startup")
async def startup_event():
    logger.info("--- Starting Route One Backend ---")
    loaded_config = None # Ensure loaded_config is defined in the broader scope

    # 1. Initialize Configuration (Using imported initializer)
    try:
        # Define path relative to main.py's parent's parent -> backend/configs/config.yaml
        config_file_path_obj = Path(__file__).parent.parent / "configs" / "config.yaml" # kept parent.parent because config.yaml is not in the app dir
        loaded_config = initialize_config(str(config_file_path_obj.resolve()))
        # Logging is now configured within initialize_config
    except Exception as e:
        # Error is logged within initialize_config, just raise critical failure
        # Use specific error type if defined, e.g. ConfigError
        logger.critical(f"CRITICAL FAILURE during config initialization: {e}", exc_info=True)
        raise RuntimeError(f"Configuration Initialization Failed: {e}") from e

    # Check if config was loaded, if not, we cannot proceed with Firebase init that depends on it.
    if loaded_config is None:
        logger.critical("Configuration was not loaded. Cannot initialize Firebase Admin SDK.")
        # This should ideally stop the application from starting or be handled based on app requirements
        raise RuntimeError("Configuration loading failed, cannot proceed with startup.")    # 2. Initialize Firebase Admin SDK (before Database and Services that might use it)
    try:
        firebase_config = loaded_config.get("firebase_admin", {})
        
        if not firebase_config.get("auth_enabled", False):
            logger.info("Firebase authentication is disabled in config.")
            return

        service_account_path = firebase_config.get("service_account_key_path")
        if not service_account_path:
            logger.warning("Firebase service account path not configured. Authentication will be disabled.")
            return

        # Get the config directory path
        config_dir = Path(__file__).parent.parent / "configs"
        key_path = config_dir / service_account_path.lstrip("configs/")

        if not key_path.exists():
            logger.error(f"Firebase service account key not found at: {key_path}")
            raise RuntimeError(f"Firebase service account key not found: {key_path}")
            
        cred = credentials.Certificate(str(key_path))
        firebase_admin.initialize_app(cred)
        logger.info("Firebase Admin SDK initialized successfully")
        # If service_account_path is absolute, project_root.parent / service_account_path might not be what you want.
        # A better approach for paths in config is to make them relative to config file or always absolute.
        # For now, let's assume service_account_path in config is relative to project root or absolute.
        # If it's relative to backend/configs, then
        # Let's refine path resolution: Assume path in config is relative to project root (one level above backend dir)
        # The current __file__ is backend/app/main.py
        # project_root for firebase key should be where manage.py or similar top-level script is.
        # Let's assume config.yaml is in backend/configs/ and service_account_key_path is relative to that dir or absolute.
        
        key_path = Path(service_account_path)
        if not key_path.is_absolute():
            # If relative, assume it's relative to the config directory (backend/configs)
            config_dir = Path(__file__).parent.parent / "configs"
            key_path = config_dir / service_account_path

        if not key_path.exists():
            logger.error(f"Firebase service account key not found at: {key_path.resolve()}")
            # Decide if this is critical. For now, log error and continue, 
            # but auth-dependent routes will fail.
            # raise RuntimeError(f"Firebase service account key not found: {key_path.resolve()}")
        else:
            cred = credentials.Certificate(str(key_path.resolve()))
            firebase_admin.initialize_app(cred)
            logger.info(f"Firebase Admin SDK initialized successfully using key: {key_path.resolve()}")

    except Exception as e:
        logger.error(f"Firebase Admin SDK Initialization Failed: {e}", exc_info=True)
        # Depending on policy, might raise RuntimeError here

    # 3. Initialize Database (Pass the loaded config)
    try:
        initialize_database(loaded_config)
    except Exception as e:
        raise RuntimeError(f"Database Initialization Failed: {e}") from e

    # 4. Initialize Services (Pass the loaded config)
    try:
        initialize_services(loaded_config)
    except Exception as e:
        logger.error(f"Service Initialization Failed during startup: {e}")
        # Decide if this should halt startup
        # raise RuntimeError(f"Service Initialization Failed: {e}") from e    # 5. Initialize Prediction Scheduler - Ensure this runs in the event loop
    try:
        loop = asyncio.get_running_loop()
        analytics_service = get_analytics_service()
        if analytics_service:
            # Initialize scheduler with required analytics_service
            scheduler = PredictionScheduler(analytics_service)
            loop.create_task(scheduler.start())  # Assuming start() is the method to run the scheduler
            logger.info("Prediction scheduler initialized and started.")
        else:
            logger.warning("Analytics service not available, skipping prediction scheduler initialization")
    except Exception as e:
        logger.error(f"Failed to initialize Prediction Scheduler: {e}", exc_info=True)
        # Non-critical error, continue startup

    logger.info("--- Startup complete ---")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("--- Shutting down Route One Backend ---")
    await shutdown_services()  # Handles services + WS connections
    close_database()
    # if firebase_admin.get_app(): # Check if default app exists
    #     firebase_admin.delete_app(firebase_admin.get_app())
    #     logger.info("Firebase Admin SDK app deleted.")
    logger.info("--- Shutdown complete ---")

# Global scheduler instance
prediction_scheduler: Optional[PredictionScheduler] = None

async def start_prediction_scheduler():
    """Start the prediction scheduler as a background task"""
    global prediction_scheduler
    analytics_service = get_analytics_service()
    if analytics_service:
        prediction_scheduler = PredictionScheduler(analytics_service)
        asyncio.create_task(prediction_scheduler.run())
        logger.info("Prediction scheduler started")
    else:
        logger.error("Could not start prediction scheduler: analytics service not initialized")

# --- CORS Middleware ---
origins = [
    "http://localhost",
    "http://localhost:3000",
    "http://localhost:9002",  # Frontend port
]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


# --- Include API Routers ---
# Now the imports within routers -> dependencies -> config should work without circular refs
try:
    app.include_router(feeds.router, prefix="/api/v1/feeds", tags=["Feeds"])
    app.include_router(config_router.router, prefix="/api/v1/config", tags=["Configuration"])
    app.include_router(analysis.router, prefix="/api/v1/analysis", tags=["Analysis"])
    app.include_router(alerts.router, prefix="/api/v1/alerts", tags=["Alerts"])
    app.include_router(video.router, prefix="/api/v1/video", tags=["Video"])  # Add video router
    app.include_router(incidents.router, prefix="/api/v1/incidents", tags=["Incidents"])
    app.include_router(
        personalized_routes.router, 
        prefix="/api/routes", 
        tags=["personalized-routing"]
    )
    # Register weather and events routers
    from app.routers import weather, events
    app.include_router(weather.router, prefix="/api/v1/weather", tags=["Weather"])
    app.include_router(events.router, prefix="/api/v1/events", tags=["Events"])
    from app.routers import route_history
    app.include_router(route_history.router, prefix="/api/v1/route-history", tags=["RouteHistory"])
    logger.info("API routers included successfully.")
    app.include_router(api.router, prefix="/api", tags=["API"])
except Exception as e:
    logger.critical(f"Failed to include routers: {e}", exc_info=True)
    # Decide if startup should fail if routers can't be included
    # raise RuntimeError(f"Router inclusion failed: {e}") from e


# --- Define WebSocket Endpoint ---
@app.websocket("/ws") # Original endpoint definition
async def websocket_endpoint_legacy(websocket: WebSocket):
    # This is the old endpoint, we might deprecate or remove it later.
    # For now, let's keep it but log its usage.
    logger.warning("Legacy WebSocket endpoint /ws was accessed. Consider migrating to /ws/{client_id}")
    await websocket.accept()
    await websocket.send_text("This WebSocket endpoint is deprecated. Please use /ws/{client_id}.")
    await websocket.close(code=1000)

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    manager = get_connection_manager()
    if manager is None:
        logger.error(f"WebSocket connection for {client_id} rejected: ConnectionManager not initialized.")
        await websocket.close(code=1011, reason="ConnectionManager not initialized")
        return

    # The actual connection object (ActiveWebSocketConnection) is created inside manager.connect
    await manager.connect(websocket, client_id)
    # At this point, manager.active_connections[client_id] should be the ActiveWebSocketConnection instance
    # However, direct access might not be needed here if all logic is in ActiveWebSocketConnection
    
    active_connection = manager.active_connections.get(client_id)
    if not active_connection: # Should not happen if manager.connect succeeded and didn't throw error
        logger.error(f"Failed to establish ActiveWebSocketConnection for {client_id} post-connect. Closing.")
        try:
            await websocket.close(code=1011, reason="Internal connection setup error")
        except Exception:
            pass # Already trying to close
        return

    logger.info(f"Client {client_id} WebSocket connection established.")

    try:
        while True:
            # The websocket.receive_text() or receive_json() call will raise WebSocketDisconnect
            # if the client disconnects.
            data_raw = await websocket.receive_text() # Or receive_json() if clients always send JSON
            # active_connection should be self.active_connections.get(client_id) from manager
            # which is now passed to handle_incoming_message.
            # No, handle_incoming_message is a method of ActiveWebSocketConnection itself.
            await active_connection.handle_incoming_message(data_raw)

    except WebSocketDisconnect as e:
        logger.info(f"Client {client_id} disconnected. Code: {e.code}, Reason: {e.reason}")
        # ActiveWebSocketConnection.close() is responsible for calling manager.disconnect()
        # So, we should call active_connection.close() here, or ensure manager.disconnect() is robustly called.
        # If WebSocketDisconnect is raised, the socket is already considered closed by FastAPI.
        # We just need to ensure our manager cleans up.
        manager.disconnect(client_id) # Explicitly tell manager to clean up this client_id
    except Exception as e:
        logger.error(f"Unexpected error in WebSocket loop for client {client_id}: {e}", exc_info=True)
        # Attempt to close the connection gracefully from server-side if an error occurs
        if active_connection and active_connection.websocket.client_state == WebSocketState.CONNECTED:
            error_payload = ErrorNotification(code="UNEXPECTED_SERVER_ERROR", message=str(e))
            ws_msg = WebSocketMessage(event_type=WebSocketMessageTypeEnum.ERROR, payload=error_payload)
            try:
                await active_connection.send_json_model(ws_msg)
            except Exception as send_err:
                logger.error(f"Failed to send error to client {client_id} before closing: {send_err}")
            try:
                await active_connection.close(code=1011, reason=f"Server error: {str(e)[:100]}") # Reason has length limit
            except Exception as close_err:
                logger.error(f"Error trying to close connection for {client_id} after exception: {close_err}")
        # Ensure cleanup even if close fails
        manager.disconnect(client_id)
    finally:
        # This block might not be strictly necessary if disconnects are handled well in exceptions
        # but serves as a final check.
        logger.info(f"WebSocket connection for client {client_id} is ending.")
        # manager.disconnect(client_id) # Called in exception blocks

# --- Serve Sample Video Endpoint ---
@app.get("/api/v1/sample-video")
def get_sample_video():
    """Serve the sample video file directly"""
    video_path = Path(__file__).parent / "data" / "sample_traffic.mp4"
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Sample video file not found")
    return FileResponse(str(video_path), media_type="video/mp4")

import asyncio

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=9002, reload=True)
