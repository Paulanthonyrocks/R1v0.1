# /content/drive/MyDrive/R1v0.1/backend/app/main.py (Updated)

import asyncio
import logging
import logging.config
import uuid
from pathlib import Path

import firebase_admin
from firebase_admin import credentials
from fastapi import FastAPI, HTTPException, WebSocket, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# --- Custom Exceptions & Middleware ---
from app.exceptions import (
    ResourceNotFound,
    OperationFailed,
    BadRequest,
    Unauthorized,
    Forbidden,
    ConnectionLimitExceeded,
)
from app.middleware.logging_middleware import LoggingMiddleware

# --- Application Modules ---
from app.routers import (
    feeds,
    config as config_router,
    analysis,
    alerts,
    video,
    incidents,
    personalized_routes,
    traffic_data,
    weather,
    events,
    route_history,
    ws,
    video_ws,
    routes,
)

# --- Initializers & Core Services ---
from app.config import initialize_config
from app.database import initialize_database, close_database
from app.services import (
    initialize_services, 
    get_analytics_service, 
    get_feed_manager, # Use getter instead of direct instance import
)
from app.websocket.connection_manager import ConnectionManager
from app.tasks.prediction_scheduler import PredictionScheduler
from app.utils.file_watcher import FileSystemWatcher

# Logger placeholder (reconfigured during startup)
logger = logging.getLogger("main")

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
        content={"detail": "Internal Server Error", "type": "InternalServerError", "message": str(exc)}
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "type": "HTTPException"},
    )

@app.exception_handler(ResourceNotFound)
async def resource_not_found_exception_handler(request: Request, exc: ResourceNotFound):
    trace_id = str(uuid.uuid4())
    logger.warning(f"Resource Not Found (Trace ID: {trace_id}): {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "type": "ResourceNotFound", "trace_id": trace_id},
    )

@app.exception_handler(OperationFailed)
async def operation_failed_exception_handler(request: Request, exc: OperationFailed):
    trace_id = str(uuid.uuid4())
    logger.error(f"Operation Failed (Trace ID: {trace_id}): {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "type": "OperationFailed", "trace_id": trace_id},
    )

@app.exception_handler(BadRequest)
async def bad_request_exception_handler(request: Request, exc: BadRequest):
    trace_id = str(uuid.uuid4())
    logger.warning(f"Bad Request (Trace ID: {trace_id}): {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "type": "BadRequest", "trace_id": trace_id},
    )

@app.exception_handler(Unauthorized)
async def unauthorized_exception_handler(request: Request, exc: Unauthorized):
    trace_id = str(uuid.uuid4())
    logger.warning(f"Unauthorized Access (Trace ID: {trace_id}): {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "type": "Unauthorized", "trace_id": trace_id},
    )

@app.exception_handler(Forbidden)
async def forbidden_exception_handler(request: Request, exc: Forbidden):
    trace_id = str(uuid.uuid4())
    logger.warning(f"Forbidden Access (Trace ID: {trace_id}): {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "type": "Forbidden", "trace_id": trace_id},
    )

# --- Startup & Shutdown ---

@app.on_event("startup")
async def startup_event():
    logger.info("--- Starting Route One Backend ---")
    loaded_config = None

    # 1. Initialize Configuration
    try:
        # Locate config file relative to this file
        config_path = Path(__file__).parent.parent / "configs" / "config.yaml"
        if not config_path.exists():
            # Fallback for Docker/Production paths if needed
            config_path = Path("/app/configs/config.yaml")
        
        loaded_config = initialize_config(str(config_path.resolve()))
        logger.info(f"Configuration loaded from: {config_path}")
    except Exception as e:
        logger.critical(f"CRITICAL FAILURE during config initialization: {e}", exc_info=True)
        raise RuntimeError(f"Configuration Initialization Failed: {e}")

    # 2. Initialize Firebase Admin SDK
    try:
        firebase_config = loaded_config.get("firebase_admin", {})
        if not firebase_config.get("auth_enabled", False):
            logger.info("Firebase authentication is disabled in config.")
        else:
            service_account_path_str = firebase_config.get("service_account_key_path")
            storage_bucket = firebase_config.get("storage_bucket")

            if not service_account_path_str:
                logger.warning("Firebase service account path missing. Auth disabled.")
            else:
                key_path = Path(service_account_path_str)
                if not key_path.is_absolute():
                    key_path = (Path(__file__).parent.parent / service_account_path_str).resolve()
                
                if key_path.exists():
                    cred = credentials.Certificate(str(key_path))
                    firebase_admin.initialize_app(cred, {"storageBucket": storage_bucket})
                    logger.info("Firebase Admin SDK initialized successfully.")
                else:
                    logger.error(f"Firebase key not found at {key_path}")
    except Exception as e:
        logger.critical(f"Firebase Initialization Failed: {e}", exc_info=True)
        # We don't raise here to allow the app to run in "offline" mode if Firebase fails

    # 3. Initialize Database
    try:
        logger.info("Initializing Database...")
        await initialize_database(loaded_config)
        logger.info("Database initialized.")
    except Exception as e:
        raise RuntimeError(f"Database Initialization Failed: {e}") from e

    # 4. Initialize Services
    try:
        logger.info("Initializing Core Services...")
        
        # ConnectionManager for WebSockets
        connection_manager = ConnectionManager()
        await connection_manager.init(
            max_connections=loaded_config.get("websocket", {}).get("max_connections", 1000),
            token_refresh_interval=loaded_config.get("websocket", {}).get("token_refresh_interval", 300),
            ping_interval=loaded_config.get("websocket", {}).get("ping_interval", 15),
        )
        app.state.connection_manager = connection_manager

        # Initialize FeedManager, Analytics, etc.
        await initialize_services(loaded_config, logger=logger, connection_manager=connection_manager)
        
        # Retrieve instances
        fm = get_feed_manager()
        analytics_service = get_analytics_service()

        if not fm:
            raise RuntimeError("FeedManager failed to initialize.")
            
        fm.set_connection_manager(connection_manager)

        if analytics_service:
            await analytics_service.initialize_prediction_log_table()
            await analytics_service.start_background_tasks()
            fm.set_analytics_service(analytics_service)
            
            # 5. Initialize Prediction Scheduler
            scheduler = PredictionScheduler(analytics_service, loaded_config)
            app.state.prediction_scheduler = scheduler
            fm.set_prediction_scheduler(scheduler)
            
            if loaded_config.get("prediction_scheduler", {}).get("enabled", True):
                await fm.start_processing()
                logger.info("Prediction Scheduler started.")

        logger.info("Application services initialized.")
    except Exception as e:
        logger.critical(f"Service Initialization Failed: {e}", exc_info=True)
        raise RuntimeError(f"Service Initialization Failed: {e}")

    # 6. File System Watcher
    app.state.file_watcher = None
    if loaded_config.get("file_watcher", {}).get("enabled", False):
        try:
            watch_dir_str = loaded_config["file_watcher"]["watch_directory"]
            watch_dir = Path(watch_dir_str)
            if not watch_dir.is_absolute():
                watch_dir = (Path(__file__).parent.parent / watch_dir_str).resolve()
            
            watch_dir.mkdir(parents=True, exist_ok=True)

            def on_new_video(path_str):
                logger.info(f"New video detected: {path_str}")
                asyncio.create_task(fm.add_and_start_feed(
                    source=path_str, 
                    is_looped=True, 
                    latitude=None, 
                    longitude=None,
                    name_hint=Path(path_str).name
                ))

            watcher = FileSystemWatcher(str(watch_dir), on_new_video)
            watcher.start()
            app.state.file_watcher = watcher
            logger.info(f"File Watcher started on {watch_dir}")
        except Exception as e:
            logger.error(f"Failed to start File Watcher: {e}")

    # 7. Post-Startup Sample Feeds
    post_startup = loaded_config.get("post_startup_processing", {})
    if post_startup.get("enabled", False):
        logger.info("Processing sample feeds...")
        for feed_cfg in post_startup.get("sample_feeds", []):
            try:
                path_str = feed_cfg.get("path")
                if not path_str: continue
                
                vid_path = Path(path_str)
                if not vid_path.is_absolute():
                    vid_path = (Path(__file__).parent.parent.parent / path_str).resolve()

                if vid_path.exists():
                    logger.info(f"Starting sample feed: {vid_path}")
                    asyncio.create_task(fm.add_and_start_feed(
                        source=str(vid_path),
                        is_looped=feed_cfg.get("is_looped", True),
                        latitude=feed_cfg.get("latitude"),
                        longitude=feed_cfg.get("longitude"),
                        name_hint=vid_path.name
                    ))
                else:
                    logger.warning(f"Sample feed not found: {vid_path}")
            except Exception as e:
                logger.error(f"Error starting sample feed: {e}")

    logger.info("--- Startup Complete ---")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("--- Shutting down Route One Backend ---")
    
    # 1. WebSockets
    if hasattr(app.state, "connection_manager") and app.state.connection_manager:
        await app.state.connection_manager.shutdown()
        logger.info("WebSocket Manager shutdown.")

    # 2. Feed Manager (Stops processes)
    fm = get_feed_manager()
    if fm:
        await fm.shutdown()
        logger.info("FeedManager shutdown.")

    # 3. Analytics
    analytics = get_analytics_service()
    if analytics:
        await analytics.stop_background_tasks()
        logger.info("Analytics background tasks stopped.")

    # 4. File Watcher
    if app.state.file_watcher:
        app.state.file_watcher.stop()
        logger.info("File Watcher stopped.")

    # 5. Database
    await close_database()
    logger.info("Database connection closed.")
    logger.info("--- Shutdown Complete ---")


# --- Middleware ---
origins = [
    "http://localhost",
    "http://localhost:3000",
    "http://localhost:5173", # Vite default
    "https://*.ngrok-free.app",
    "*" # Be careful with this in production
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(LoggingMiddleware)

# --- Routers ---
app.include_router(feeds.router, prefix="/api/v1/feeds", tags=["Feeds"])
app.include_router(config_router.router, prefix="/api/v1/config", tags=["Configuration"])
app.include_router(analysis.router, prefix="/api/v1/analytics", tags=["Analytics"])
app.include_router(alerts.router, prefix="/api/v1/alerts", tags=["Alerts"])
app.include_router(video.router, prefix="/api/v1/video", tags=["Video"]) # Adjusted prefix
app.include_router(incidents.router, prefix="/api/v1/incidents", tags=["Incidents"])
app.include_router(personalized_routes.router, prefix="/api/v1/routes", tags=["Routes"])
app.include_router(weather.router, prefix="/api/v1/weather", tags=["Weather"])
app.include_router(events.router, prefix="/api/v1/events", tags=["Events"])
app.include_router(route_history.router, prefix="/api/v1/route-history", tags=["History"])
app.include_router(traffic_data.router, prefix="/api/v1/traffic-data", tags=["TrafficData"])
# WebSocket Routers
app.include_router(ws.router, prefix="/api/v1", tags=["WebSocket"])
app.include_router(video_ws.router, prefix="/api/v1", tags=["VideoWebSocket"])
# General Router
app.include_router(routes.router, prefix="/api/v1", tags=["General"])


# --- Utility Endpoints ---
@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "Route One Backend"}

@app.get("/firebase-status")
async def firebase_status():
    try:
        firebase_admin.get_app()
        return {"status": "initialized"}
    except ValueError:
        return {"status": "not_initialized"}

# Catch-all for debugging 404s
@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def catch_all(request: Request, full_path: str):
    logger.warning(f"404 Not Found: {request.method} /{full_path}")
    return JSONResponse(status_code=404, content={"detail": "Endpoint not found"})

if __name__ == "__main__":
    import uvicorn
    # Use standard uvicorn run arguments
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")