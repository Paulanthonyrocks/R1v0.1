# /content/drive/MyDrive/R1v0.1/backend/app/main.py (Updated)

import logging
import logging.config
from pathlib import Path
# Removed unused imports: asyncio, time, typing.Dict, typing.Any, json, uuid

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request, WebSocketState
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
import firebase_admin
from firebase_admin import credentials

# --- Import application modules ---
# Routers
from app.routers import feeds, config as config_router, analysis, alerts, video, incidents
from . import api
# Initializers/Getters - Import config initializer now
from .config import initialize_config  # Removed get_current_config as it's not directly used here
from .database import initialize_database, close_database # Removed get_database_manager
from .services import initialize_services, shutdown_services, get_connection_manager
# Removed get_feed_manager, services_health_check as they are not directly used here
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum, ErrorNotification
# Logging will be reconfigured by initialize_config
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# --- FastAPI App Instance ---
app = FastAPI(
    title="Route One Hub - Backend API",
    version="1.0.0",
    description="API for managing traffic analysis feeds, data, and real-time updates.",
)

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
    loaded_config = None  # Ensure loaded_config is defined in the broader scope

    # 1. Initialize Configuration (Using imported initializer)
    try:
        # Define path relative to main.py's parent's parent -> backend/configs/config.yaml
        # kept parent.parent because config.yaml is not in the app dir
        config_file_path_obj = Path(
            __file__).parent.parent / "configs" / "config.yaml"
        loaded_config = initialize_config(str(config_file_path_obj.resolve()))
        # Logging is now configured within initialize_config
    except Exception as e:
        # Error is logged within initialize_config, just raise critical failure
        # Use specific error type if defined, e.g. ConfigError
        logger.critical(
            f"CRITICAL FAILURE during config initialization: {e}", exc_info=True)
        raise RuntimeError(f"Configuration Initialization Failed: {e}") from e

    # Check if config was loaded, if not, we cannot proceed with Firebase init that depends on it.
    if loaded_config is None:
        logger.critical(
            "Configuration was not loaded. Cannot initialize Firebase Admin SDK.")
        # This should ideally stop the application from starting or be handled based on app requirements
        raise RuntimeError(
            "Configuration loading failed, cannot proceed with startup.")

    # 2. Initialize Firebase Admin SDK (before Database and Services that might use it)
    try:
        firebase_config = loaded_config.get("firebase_admin", {})
        service_account_path_str = firebase_config.get(
            "service_account_key_path", "backend/configs/firebase-service-account.json"
        )

        # Path resolution for service account key:
        # Assumes path in config is relative to project root (one level above backend dir)
        # or an absolute path.
        key_path = Path(service_account_path_str)
        if not key_path.is_absolute():
            # Project root is two levels up from main.py (backend/app/main.py -> backend/ -> project_root/)
            project_root = Path(__file__).parent.parent.parent
            key_path = project_root / service_account_path_str

        if not key_path.exists():
            logger.error(f"Firebase service account key not found: {key_path.resolve()}")
            # Decide if critical: For now, log and continue; auth routes will fail.
            # raise RuntimeError(f"Firebase key not found: {key_path.resolve()}")
        else:
            cred = credentials.Certificate(str(key_path.resolve()))
            if not firebase_admin._apps: # Initialize only if no app exists
                firebase_admin.initialize_app(cred)
                logger.info(f"Firebase Admin SDK initialized with key: {key_path.resolve()}")
            else:
                logger.info("Firebase Admin SDK already initialized.")

    except Exception as e:
        logger.error(f"Firebase Admin SDK Initialization Failed: {e}", exc_info=True)
        # Depending on policy, might raise RuntimeError

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
        # raise RuntimeError(f"Service Initialization Failed: {e}") from e

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

# --- CORS Middleware ---
origins = [
    "http://localhost",
    "http://localhost:3000",
    "http://localhost:9002",  # Frontend port
]
app.add_middleware(CORSMiddleware, allow_origins=origins,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


# --- Include API Routers ---
# Now the imports within routers -> dependencies -> config should work without circular refs
try:
    app.include_router(feeds.router, prefix="/api/v1/feeds", tags=["Feeds"])
    app.include_router(config_router.router,
                       prefix="/api/v1/config", tags=["Configuration"])
    app.include_router(
        analysis.router, prefix="/api/v1/analysis", tags=["Analysis"])
    app.include_router(alerts.router, prefix="/api/v1/alerts", tags=["Alerts"])
    app.include_router(video.router, prefix="/api/v1/video",
                       tags=["Video"])  # Add video router
    app.include_router(
        incidents.router, prefix="/api/v1/incidents", tags=["Incidents"])
    logger.info("API routers included successfully.")
    app.include_router(api.router, prefix="/api", tags=["API"])
except Exception as e:
    logger.critical(f"Failed to include routers: {e}", exc_info=True)
    # Decide if startup should fail if routers can't be included
    # raise RuntimeError(f"Router inclusion failed: {e}") from e


# --- Define WebSocket Endpoint ---
@app.websocket("/ws")  # Original endpoint definition
async def websocket_endpoint_legacy(websocket: WebSocket):
    # This is the old endpoint, we might deprecate or remove it later.
    # For now, let's keep it but log its usage.
    logger.warning(
        "Legacy WebSocket endpoint /ws was accessed. Consider migrating to /ws/{client_id}")
    await websocket.accept()
    await websocket.send_text("This WebSocket endpoint is deprecated. Please use /ws/{client_id}.")
    await websocket.close(code=1000)


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    manager = get_connection_manager()
    if manager is None:
        logger.error(
            f"WebSocket connection for {client_id} rejected: ConnectionManager not initialized.")
        await websocket.close(code=1011, reason="ConnectionManager not initialized")
        return

    # The actual connection object (ActiveWebSocketConnection) is created inside manager.connect
    await manager.connect(websocket, client_id)
    # At this point, manager.active_connections[client_id] should be the ActiveWebSocketConnection instance
    # However, direct access might not be needed here if all logic is in ActiveWebSocketConnection

    active_connection = manager.active_connections.get(client_id)
    if not active_connection:  # Should not happen if manager.connect succeeded and didn't throw error
        logger.error(
            f"Failed to establish ActiveWebSocketConnection for {client_id} post-connect. Closing.")
        try:
            await websocket.close(code=1011, reason="Internal connection setup error")
        except Exception:
            pass  # Already trying to close
        return

    logger.info(f"Client {client_id} WebSocket connection established.")

    try:
        while True:
            # The websocket.receive_text() or receive_json() call will raise WebSocketDisconnect
            # websocket.receive_text() raises WebSocketDisconnect if client disconnects.
            data_raw = await websocket.receive_text()
            await active_connection.handle_incoming_message(data_raw)

    except WebSocketDisconnect as e:
        logger.info(f"Client {client_id} disconnected. Code: {e.code}, Reason: {e.reason}")
        manager.disconnect(client_id) # Ensure manager cleans up
    except Exception as e:
        logger.error(f"Unexpected error in WebSocket loop for {client_id}: {e}", exc_info=True)
        if active_connection and active_connection.websocket.client_state == WebSocketState.CONNECTED:
            error_payload = ErrorNotification(code="UNEXPECTED_SERVER_ERROR", message=str(e))
            ws_msg = WebSocketMessage(event_type=WebSocketMessageTypeEnum.ERROR, payload=error_payload)
            try:
                await active_connection.send_json_model(ws_msg)
            except Exception as send_err:
                logger.error(f"Failed to send error to {client_id} before closing: {send_err}")
            try:
                await active_connection.close(code=1011, reason=f"Server error: {str(e)[:100]}")
            except Exception as close_err:
                logger.error(f"Error closing connection for {client_id} after exception: {close_err}")
        manager.disconnect(client_id) # Ensure cleanup
    finally:
        logger.info(f"WebSocket connection for client {client_id} ending.")
        # manager.disconnect(client_id) # Already called in exception blocks

# --- Serve Sample Video Endpoint ---


@app.get("/api/v1/sample-video")
def get_sample_video():
    """Serve the sample video file directly"""
    video_path = Path(__file__).parent / "data" / "sample_traffic.mp4"
    if not video_path.exists():
        raise HTTPException(
            status_code=404, detail="Sample video file not found")
    return FileResponse(str(video_path), media_type="video/mp4")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=9002, reload=True)
