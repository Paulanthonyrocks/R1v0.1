# /content/drive/MyDrive/R1v0.1/backend/app/main.py (Updated)

import logging
import logging.config
from pathlib import Path
from typing import Dict, Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import json  # Import the json module

# --- Import application modules ---
# Routers
from app.routers import feeds, config as config_router, analysis, alerts
# Initializers/Getters - Import config initializer now
from app.config import initialize_config, get_current_config  # Import config init/getter
from app.database import initialize_database, close_database, get_database_manager
from app.services import initialize_services, shutdown_services, get_feed_manager, get_connection_manager

# --- Logging setup (Basic initial setup) ---
# Logging will be reconfigured by initialize_config
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# --- FastAPI App Instance ---
app = FastAPI(
    title="Route One Hub - Backend API",
    version="1.0.0",
    description="API for managing traffic analysis feeds, data, and real-time updates.",
)

# --- Event Handlers ---
@app.on_event("startup")
async def startup_event():
    logger.info("--- Starting Route One Backend ---")

    # 1. Initialize Configuration (Using imported initializer)
    try:
        # Define path relative to main.py's parent's parent -> backend/configs/config.yaml
        config_file_path_obj = Path(__file__).parent.parent / "configs" / "config.yaml"
        loaded_config = initialize_config(str(config_file_path_obj.resolve()))
        # Logging is now configured within initialize_config
    except Exception as e:
        # Error is logged within initialize_config, just raise critical failure
        # Use specific error type if defined, e.g. ConfigError
        logger.critical(f"CRITICAL FAILURE during config initialization: {e}", exc_info=True)
        raise RuntimeError(f"Configuration Initialization Failed: {e}") from e

    # 2. Initialize Database (Pass the loaded config)
    try:
        initialize_database(loaded_config)
    except Exception as e:
        raise RuntimeError(f"Database Initialization Failed: {e}") from e

    # 3. Initialize Services (Pass the loaded config)
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
    logger.info("--- Shutdown complete ---")

# --- CORS Middleware ---
origins = [
    "http://localhost",
    "http://localhost:3000",
    # Add deployed frontend URL
]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


# --- Include API Routers ---
# Now the imports within routers -> dependencies -> config should work without circular refs
try:
    app.include_router(feeds.router, prefix="/api/v1/feeds", tags=["Feeds"])
    app.include_router(config_router.router, prefix="/api/v1/config", tags=["Configuration"])
    app.include_router(analysis.router, prefix="/api/v1/analysis", tags=["Analysis"])
    app.include_router(alerts.router, prefix="/api/v1/alerts", tags=["Alerts"])
    logger.info("API routers included successfully.")
except Exception as e:
    logger.critical(f"Failed to include routers: {e}", exc_info=True)
    # Decide if startup should fail if routers can't be included
    # raise RuntimeError(f"Router inclusion failed: {e}") from e


# --- Define WebSocket Endpoint ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    manager = get_connection_manager()  # Get via getter
    if manager is None:  # Check if manager is None
        logger.error("WebSocket connection rejected: ConnectionManager not initialized.")
        await websocket.close(code=1011, reason="ConnectionManager not initialized")
        return

    await manager.connect(websocket)  # Proceed to connect if manager is valid

    try:
        while True:
            try:
                json_data = await websocket.receive_text()
                message = json.loads(json_data)
                logger.debug(f"Received WS message: {message}")

                message_type = message.get("type")
                data = message.get("data")
                feed_manager = get_feed_manager()

                if message_type == "start_feed":
                    feed_id = data.get("feed_id")
                    logger.info(f"Received request to start feed: {feed_id}")
                    if feed_manager:
                        await feed_manager.handle_start_feed(feed_id)
                    else:
                        logger.error("FeedManager not available.")
                        # Optionally send an error back to the client

                elif message_type == "stop_feed":
                    feed_id = data.get("feed_id")
                    logger.info(f"Received request to stop feed: {feed_id}")
                    if feed_manager:
                        await feed_manager.handle_stop_feed(feed_id)
                    else:
                        logger.error("FeedManager not available.")
                        # Optionally send an error back to the client
                else:
                    logger.warning(f"Unknown message type: {message_type}")
                    # Optionally send an error back to the client
                    # await websocket.send_text(json.dumps({"type": "error", "data": {"message": f"Unknown message type: {message_type}"}}))

            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON format: {e}")
                # Optionally send an error back to the client
                # await websocket.send_text(json.dumps({"type": "error", "data": {"message": "Invalid JSON format"}}))
            except Exception as e:
                logger.error(f"Error processing WebSocket message: {e}", exc_info=True)
                # Optionally send an error back to the client
                # await websocket.send_text(json.dumps({"type": "error", "data": {"message": "Error processing message"}}))

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {websocket.client}")
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket Error: {e}", exc_info=True)
        manager.disconnect(websocket)
        try:
            await websocket.close(code=1011)
        except RuntimeError:
            pass

# --- Root / Health Endpoints ---
@app.get("/", tags=["Root"])
async def read_root():
    db_status = "OK" if get_database_manager() else "Not Initialized"
    fm_status = "OK" if get_feed_manager() else "Not Initialized"
    return {"message": "Route One Hub Backend API is running.", "db_status": db_status, "feed_manager_status": fm_status}

@app.get("/health", tags=["Health"])
async def health_check():
    try:
        get_database_manager()  # Basic check
        get_feed_manager()     # Basic check
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail=f"Service Unavailable: {e}")
