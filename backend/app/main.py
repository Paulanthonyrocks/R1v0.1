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
from pathlib import Path # Ensure Path is imported
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
# This function is not currently used in startup_event, but keeping it for reference
# def initialize_firebase():
#     config = get_current_config()
#     if config.get("firebase", {}).get("auth_enabled", False):
#         try:
#             cred = credentials.Certificate(config["firebase"]["service_account_path"])
#             firebase_admin.initialize_app(cred)
#             logger.info("Firebase initialized successfully")
#         except Exception as e:
#             logger.error(f"Failed to initialize Firebase: {e}")
#             raise

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
    loaded_config = None

    # 1. Initialize Configuration
    try:
        config_file_path_obj = Path(__file__).parent.parent / "configs" / "config.yaml"
        loaded_config = initialize_config(str(config_file_path_obj.resolve()))
    except Exception as e:
        logger.critical(f"CRITICAL FAILURE during config initialization: {e}", exc_info=True)
        raise RuntimeError(f"Configuration Initialization Failed: {e}") from e

    if loaded_config is None:
        logger.critical("Configuration was not loaded. Cannot initialize Firebase Admin SDK.")
        raise RuntimeError("Configuration loading failed, cannot proceed with startup.")

    # 2. Initialize Firebase Admin SDK
    try:
        firebase_config = loaded_config.get("firebase_admin", {})

        if not firebase_config.get("auth_enabled", False):
            logger.info("Firebase authentication is disabled in config.")
            # Allow startup to continue if auth is disabled
            # return # Removed return to allow other startup tasks to run

        service_account_path_str = firebase_config.get("service_account_key_path")
        if not service_account_path_str:
            logger.warning("Firebase service account path not configured. Authentication will be disabled.")
            # Allow startup to continue if path is not configured
            # return # Removed return

        # Construct the absolute path relative to the backend directory
        backend_dir = Path(__file__).parent.parent
        key_path = backend_dir / service_account_path_str

        if not key_path.exists():
            logger.error(f"Firebase service account key not found at: {key_path.resolve()}")
            # Raise an exception to halt startup if the key file is missing
            raise FileNotFoundError(f"Firebase service account key not found at: {key_path.resolve()}")

        # Initialize Firebase Admin SDK
        try:
            cred = credentials.Certificate(str(key_path.resolve()))
            firebase_admin.initialize_app(cred)
            logger.info(f"Firebase Admin SDK initialized successfully using key: {key_path.resolve()}")
        except Exception as e:
            logger.error(f"Firebase Admin SDK Initialization Failed: {e}", exc_info=True)
            # Re-raise the exception to halt startup if Firebase init fails
            raise

    except Exception as e:
        # Catch any exceptions during Firebase initialization and re-raise
        logger.critical(f"CRITICAL FAILURE during Firebase Admin SDK initialization: {e}", exc_info=True)
        raise RuntimeError(f"Firebase Admin SDK Initialization Failed: {e}") from e


    # 3. Initialize Database
    try:
        initialize_database(loaded_config)
    except Exception as e:
        raise RuntimeError(f"Database Initialization Failed: {e}") from e

    # 4. Initialize Services
    try:
        initialize_services(loaded_config)
    except Exception as e:
        logger.error(f"Service Initialization Failed during startup: {e}")
        # Decide if service initialization failure should halt startup
        # raise RuntimeError(f"Service Initialization Failed: {e}") from e # Uncomment to halt

    # 5. Initialize Prediction Scheduler
    try:
        analytics_service = get_analytics_service()
        if analytics_service:
            scheduler = PredictionScheduler(analytics_service)
            await scheduler.start()
            app.state.prediction_scheduler = scheduler
            logger.info("Prediction scheduler initialized and started")
        else:
            logger.warning("AnalyticsService not available, prediction scheduler not started.")
    except Exception as e:
        logger.error(f"Prediction scheduler initialization failed: {e}", exc_info=True)
        # Decide if scheduler failure should halt startup
        # raise RuntimeError(f"Prediction scheduler initialization failed: {e}") from e # Uncomment to halt


    logger.info("Application startup complete.")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("--- Shutting down Route One Backend ---")
    # Stop the prediction scheduler if it was initialized
    if hasattr(app.state, 'prediction_scheduler') and app.state.prediction_scheduler:
        await app.state.prediction_scheduler.stop()
        logger.info("Prediction scheduler stopped.")

    # Shutdown services
    shutdown_services()
    logger.info("Services shut down.")

    # Close database connection
    close_database()
    logger.info("Database connection closed.")

    logger.info("--- Backend shutdown complete ---")

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
