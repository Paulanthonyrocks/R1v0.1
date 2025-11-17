# /content/drive/MyDrive/R1v0.1/backend/app/main.py (Updated)

import asyncio
import logging
import logging.config
from pathlib import Path


from fastapi import FastAPI, HTTPException, WebSocket, Request
from app.exceptions import (
    ResourceNotFound,
    OperationFailed,
    BadRequest,
    Unauthorized,
    Forbidden,
    ConnectionLimitExceeded, # Import the new exception
)

from fastapi.middleware.cors import CORSMiddleware
from app.middleware.logging_middleware import LoggingMiddleware
from fastapi.responses import JSONResponse
import firebase_admin
from firebase_admin import credentials
import uuid  # For generating unique client IDs for WebSockets
from app.dependency_injection import (
    verify_firebase_token,
    get_token_from_query,
)  # Import verify_firebase_token and auth_scheme
from fastapi import Depends  # Separate import for Query and Depends

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
    traffic_data,
    weather,
    events,
    route_history,
    ws,
    video_ws,
)
from app.routers import routes

# Initializers/Getters - Import config initializer now
from .config import initialize_config  # Import config init/getter
from .database import initialize_database, close_database
from .services import initialize_services, get_analytics_service, feed_manager_instance

from app.websocket.connection_manager import ConnectionManager

from app.tasks.prediction_scheduler import (
    PredictionScheduler,
)  # Import the new scheduler
from app.utils.file_watcher import FileSystemWatcher # Import the new file watcher

# Logging will be reconfigured by initialize_config
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
        status_code=500, content={"detail": str(exc), "type": "Internal Server Error"}
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Convert HTTPExceptions to JSON format"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "type": "HTTP Exception"},
    )


@app.exception_handler(ResourceNotFound)
async def resource_not_found_exception_handler(request: Request, exc: ResourceNotFound):
    trace_id = str(uuid.uuid4())
    logger.warning(f"Resource Not Found (Trace ID: {trace_id}): {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "type": "Resource Not Found",
            "trace_id": trace_id,
        },
    )


@app.exception_handler(OperationFailed)
async def operation_failed_exception_handler(request: Request, exc: OperationFailed):
    trace_id = str(uuid.uuid4())
    logger.error(f"Operation Failed (Trace ID: {trace_id}): {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "type": "Operation Failed",
            "trace_id": trace_id,
        },
    )


@app.exception_handler(BadRequest)
async def bad_request_exception_handler(request: Request, exc: BadRequest):
    trace_id = str(uuid.uuid4())
    logger.warning(f"Bad Request (Trace ID: {trace_id}): {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "type": "Bad Request", "trace_id": trace_id},
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


# --- Event Handlers ---
@app.on_event("startup")
async def startup_event():
    logger.info("--- Starting Route One Backend ---")
    loaded_config = None
    print("Attempting to initialize logging...")

    # 1. Initialize Configuration
    try:
        config_file_path_obj = Path(__file__).parent.parent / "configs" / "config.yaml"
        loaded_config = initialize_config(str(config_file_path_obj.resolve()))
        # ...existing code...
    except Exception as e:
        logger.critical(
            f"CRITICAL FAILURE during config initialization: {e}", exc_info=True
        )
        raise RuntimeError(f"Configuration Initialization Failed: {e}") from e
    if loaded_config is None:
        print("Failed to initialize logging.")
        logger.critical(
            "Configuration was not loaded. Cannot initialize Firebase Admin SDK."
        )
        raise RuntimeError("Configuration loading failed, cannot proceed with startup.")

    # 2. Initialize Firebase Admin SDK
    try:
        firebase_config = loaded_config.get("firebase_admin", {})
        if not firebase_config.get("auth_enabled", False):
            logger.info("Firebase authentication is disabled in config.")
        service_account_path_str = firebase_config.get(
            "service_account_key_path", "configs/firebase/service-account-key.json"
        )
        storage_bucket = firebase_config.get("storage_bucket")

        if not service_account_path_str:
            logger.warning(
                "Firebase service account path not configured. Authentication will be disabled."
            )
        backend_dir = Path(__file__).parent.parent
        key_path = (
            backend_dir / service_account_path_str if service_account_path_str else None
        )
        if key_path and not key_path.exists():
            # Fallback to project root if not found in backend directory
            key_path = Path.cwd() / service_account_path_str
        if key_path:
            try:
                cred = credentials.Certificate(str(key_path.resolve()))
                firebase_admin.initialize_app(
                    cred, {"storageBucket": storage_bucket}
                )
                logger.info(
                    f"Firebase Admin SDK initialized successfully using key: {key_path.resolve()}"
                )
            except Exception as e:
                logger.error(
                    f"Firebase Admin SDK Initialization Failed: {e}", exc_info=True
                )
                raise
    except Exception as e:
        logger.critical(
            f"CRITICAL FAILURE during Firebase Admin SDK initialization: {e}",
            exc_info=True,
        )
        raise RuntimeError(f"Firebase Admin SDK Initialization Failed: {e}") from e

    # 3. Initialize Database
    try:
        logging.getLogger("app.utils.database").info(
            f"SQLite database path configured to: {loaded_config['database']['db_path']}"
        )
        logging.getLogger("app.utils.database").info(
            "MongoDB not fully configured (URI or database_name missing). MongoDB will not be used."
        )
        logging.getLogger("app.utils.database").info(
            "Initializing SQLite DB schema at {loaded_config['database']['db_path']}..."
        )
        await initialize_database(loaded_config)
        logging.getLogger("app.utils.database").info(
            "SQLite DB schema initialization check complete."
        )
    except Exception as e:
        raise RuntimeError(f"Database Initialization Failed: {e}") from e

    # 4. Initialize Services, including ConnectionManager
    try:
        logger.info("Initializing application services...")
        # Initialize ConnectionManager
        connection_manager = ConnectionManager()
        await connection_manager.init(
            max_connections=loaded_config.get("websocket", {}).get("max_connections", 1000),
            token_refresh_interval=loaded_config.get("websocket", {}).get("token_refresh_interval", 300),
            ping_interval=loaded_config.get("websocket", {}).get("ping_interval", 15),
        )
        app.state.connection_manager = connection_manager
        logger.info("WebSocket ConnectionManager initialized and stored in app.state.")

        await initialize_services(loaded_config, logger=logger, connection_manager=connection_manager)
        
        # Retrieve FeedManager and AnalyticsService instances after initialization
        from app.services import get_feed_manager
        fm = get_feed_manager()
        analytics_service = get_analytics_service()

        if fm is None:
            logger.error("FeedManager instance is None after initialization.")
            raise RuntimeError("FeedManager not initialized.")

        logger.info("FeedManager initialized via app.services.")
        
        fm.set_connection_manager(connection_manager)

        if analytics_service:
            logger.info("AnalyticsService initialized successfully.")
            await analytics_service.initialize_prediction_log_table()
            # Start analytics service background tasks
            await analytics_service.start_background_tasks()
            logger.info("AnalyticsService background tasks started.")
        else:
            logger.warning(
                "AnalyticsService not available during startup; cannot initialize prediction log table."
            )
        logger.info("Application services initialized.")
    except Exception as e:
        logger.error(f"Service Initialization Failed during startup: {e}")
        raise RuntimeError(f"Service Initialization Failed: {e}") from e

    # 5. Initialize Prediction Scheduler (but don't start it yet)
    try:
        if analytics_service:
            scheduler = PredictionScheduler(analytics_service, loaded_config)
            app.state.prediction_scheduler = scheduler
            
            # Ensure fm is not None before setting scheduler and analytics service
            if fm is None:
                logger.error("FeedManager instance is None. Cannot set prediction scheduler or analytics service.")
            else:
                fm.set_prediction_scheduler(scheduler)
                fm.set_analytics_service(analytics_service)
                # Start processing after initialization
                await fm.start_processing()
                logger.info(
                    "Prediction scheduler initialized and injected into FeedManager."
                )
            # Start processing after initialization, regardless of scheduler initialization
            if not loaded_config.get("prediction_scheduler", {}).get("enabled", True):
                logger.info("Prediction scheduler is disabled in config. Skipping startup.")
        else:
            logger.warning(
                "AnalyticsService not available, prediction scheduler not initialized."
            )
    except Exception as e:
        logger.error(f"Prediction scheduler initialization failed: {e}", exc_info=True)
        raise RuntimeError(f"Prediction scheduler initialization failed: {e}") from e

    # 6. Initialize and Start File System Watcher (Optional)
    app.state.file_watcher = None
    if loaded_config.get("file_watcher", {}).get("enabled", False):
        watch_directory = loaded_config["file_watcher"]["watch_directory"]
        # Resolve relative path to absolute path based on backend directory
        abs_watch_directory = (Path(__file__).parent.parent / watch_directory).resolve()
        
        # Ensure the directory exists
        if not abs_watch_directory.is_dir():
            logger.warning(f"File watcher directory does not exist: {abs_watch_directory}. Creating it.")
            abs_watch_directory.mkdir(parents=True, exist_ok=True)

        # Define a wrapper for the async callback
        def new_video_callback_wrapper(video_path: str):
            """Synchronous wrapper to schedule the async callback."""
            logger.info(
                f"File watcher triggered for new video: {video_path}. Scheduling for processing."
            )
            asyncio.create_task(
                fm.add_dynamic_sample_feed(video_path, is_looped=True, latitude=None, longitude=None)
            )

        try:
            app.state.file_watcher = FileSystemWatcher(
                path=str(abs_watch_directory),
                on_new_video_callback=new_video_callback_wrapper,
            )
            app.state.file_watcher.start()
            logger.info(
                f"File system watcher started for directory: {abs_watch_directory}"
            )
        except Exception as e:
            logger.error(f"Failed to start file system watcher: {e}", exc_info=True)

    # 7. Process sample feeds from config
    post_startup_config = loaded_config.get("post_startup_processing", {})
    if post_startup_config.get("enabled", False):
        logger.info("Post-startup processing is enabled. Processing sample feeds...")
        sample_feeds = post_startup_config.get("sample_feeds", [])
        if not sample_feeds:
            logger.info("No sample feeds configured in 'post_startup_processing'.")
        else:
            for feed_config in sample_feeds:
                try:
                    video_path = feed_config.get("path")
                    if not video_path:
                        logger.warning(
                            "Skipping a sample feed due to missing 'path'."
                        )
                        continue

                    # Resolve path relative to project root if it's not absolute
                    abs_video_path = Path(video_path)
                    if not abs_video_path.is_absolute():
                        abs_video_path = (
                            Path(__file__).parent.parent.parent / video_path
                        ).resolve()

                    if not abs_video_path.exists():
                        logger.warning(
                            f"Sample feed video not found at path: {abs_video_path}"
                        )
                        continue

                    is_looped = feed_config.get("is_looped", True)
                    latitude = feed_config.get("latitude")
                    longitude = feed_config.get("longitude")

                    logger.info(
                        f"Scheduling processing for sample feed: {abs_video_path}"
                    )
                    # Schedule the addition of the feed as a background task
                    asyncio.create_task(
                        fm.add_dynamic_sample_feed(
                            video_path=str(abs_video_path),
                            is_looped=is_looped,
                            latitude=latitude,
                            longitude=longitude,
                        )
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to schedule processing for sample feed '{feed_config.get('path')}': {e}",
                        exc_info=True,
                    )
    else:
        logger.info(
            "Post-startup sample feed processing is disabled in the configuration."
        )

    logger.info("Application startup complete.")




@app.on_event("shutdown")
async def shutdown_event():
    logger.info("--- Shutting down Route One Backend ---")
    
    # Shutdown ConnectionManager
    if hasattr(app.state, "connection_manager") and app.state.connection_manager:
        await app.state.connection_manager.shutdown()
        logger.info("WebSocket ConnectionManager shut down.")

    from app.services import get_feed_manager, get_analytics_service
    fm = get_feed_manager()
    if fm:
        await fm.shutdown()
    
    # Shutdown AnalyticsService background tasks
    analytics_service = get_analytics_service()
    if analytics_service:
        await analytics_service.stop_background_tasks()
        logger.info("AnalyticsService background tasks stopped.")
    if app.state.file_watcher:
        app.state.file_watcher.stop()
    logging.getLogger("app.utils.database").info("DatabaseManager close called.")
    logging.getLogger("app.utils.database").info(
        "MongoDB client was not initialized or already closed."
    )
    await close_database()
    logger.info("Database connection closed.")
    logger.info("--- Backend shutdown complete ---")


# --- CORS Middleware ---
origins = [
    "http://localhost",
    "http://localhost:3000",  # Frontend port
    "https://*.ngrok-free.app", # Allow ngrok origins
    "https://*.loca.lt",
    "https://*.cloudworkstations.dev", # Allow cloud workstations
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(LoggingMiddleware)

# --- Global State for API Connection Tracking ---
# This will be initialized in startup_event
app.state.realtime_connections_count = 0
app.state.realtime_connections_lock = asyncio.Lock()


# --- Include API Routers ---
try:
    app.include_router(feeds.router, prefix="/api/v1/feeds", tags=["Feeds"])
    app.include_router(
        config_router.router, prefix="/api/v1/config", tags=["Configuration"]
    )
    app.include_router(analysis.router, prefix="/api/v1/analytics", tags=["Analytics"])
    app.include_router(alerts.router, prefix="/api/v1/alerts", tags=["Alerts"])
    app.include_router(video.router, prefix="/api/v1", tags=["Video"])
    app.include_router(incidents.router, prefix="/api/v1/incidents", tags=["Incidents"])
    app.include_router(
        personalized_routes.router, prefix="/api/v1/routes", tags=["personalized-routing"]
    )
    app.include_router(routes.router, prefix="/api/v1", tags=["API"])
    app.include_router(weather.router, prefix="/api/v1/weather", tags=["Weather"])
    app.include_router(events.router, prefix="/api/v1/events", tags=["Events"])
    app.include_router(
        route_history.router, prefix="/api/v1/route-history", tags=["RouteHistory"]
    )
    app.include_router(
        traffic_data.router, prefix="/api/v1/traffic-data", tags=["TrafficData"]
    )
    app.include_router(ws.router, prefix="/api/v1", tags=["WebSocket"])
    app.include_router(video_ws.router, prefix="/api/v1", tags=["Video WebSocket"])
    logger.info("API routers included successfully.")
except Exception as e:
    logger.critical(f"Failed to include routers: {e}", exc_info=True)


@app.get("/firebase-status")
async def firebase_status():
    """Checks if the Firebase Admin SDK is initialized."""
    try:
        firebase_admin.get_app()  # This will raise an error if the default app is not initialized
        return {"status": "Firebase Admin SDK initialized successfully"}
    except ValueError:
        return {"status": "Firebase Admin SDK not initialized"}
    except Exception as e:
        return {"status": f"Error checking Firebase status: {e}"}


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def catch_all(request: Request, full_path: str):
    logger.warning(
        f"Catch-all route hit for path: {full_path}. Method: {request.method}"
    )
    return JSONResponse(
        status_code=404,
        content={"detail": f"Endpoint not found: /{full_path}", "type": "NotFound"},
    )


# --- Define WebSocket Endpoint ---
@app.websocket("/ws")
async def websocket_endpoint_legacy(websocket: WebSocket):
    """
    LEGACY WebSocket Endpoint: /ws

    This is an older WebSocket endpoint and is primarily kept for backward compatibility.
    It does not include the client_id path parameter, making it less suitable for
    managing multiple distinct client connections or topic-based subscriptions.
    Authentication is not explicitly handled here (it expects a token in query params,
    but the primary handler _handle_websocket_connection is not used by this endpoint directly).

    Frontend clients should use the `/api/v1/ws/{client_id}` endpoint instead.
    """
    logger.warning(
        "Legacy WebSocket endpoint /ws was accessed. Consider migrating to /ws/{client_id}"
    )
    await websocket.accept()
    await websocket.send_text(
 "This WebSocket endpoint is deprecated. Please use /api/v1/ws/{client_id}."
    )
    await websocket.close(code=1000)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, log_level="debug")
