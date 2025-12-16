import asyncio
import logging
import logging.config
import uuid
import multiprocessing
from contextlib import asynccontextmanager
from pathlib import Path

# Ensure spawn method for multiprocessing compatibility
try:
    if multiprocessing.get_start_method(allow_none=True) is None:
        multiprocessing.set_start_method("spawn")
except RuntimeError:
    pass

import firebase_admin
from firebase_admin import credentials
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

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

# --- Initializers & Services ---
from app.config import initialize_config
from app.database import initialize_database, close_database
from app.services import initialize_services, get_analytics_service, get_feed_manager
from app.websocket.connection_manager import ConnectionManager
from app.tasks.prediction_scheduler import PredictionScheduler
from app.utils.file_watcher import FileSystemWatcher

# Setup Logger
logger = logging.getLogger("main")
BASE_DIR = Path(__file__).resolve().parent.parent

# --- Lifespan Manager (Replaces startup/shutdown events) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP ---
    logger.info("--- Starting Route One Backend ---")
    
    # 1. Configuration
    try:
        config_path = BASE_DIR / "configs" / "config.yaml"
        if not config_path.exists():
            config_path = Path("/app/configs/config.yaml") # Docker fallback
        
        loaded_config = initialize_config(str(config_path))
        logger.info(f"Configuration loaded from: {config_path}")
    except Exception as e:
        logger.critical(f"Config Init Failed: {e}")
        raise RuntimeError(f"Config Init Failed: {e}")

    # 2. Firebase
    try:
        firebase_config = loaded_config.get("firebase_admin", {})
        if firebase_config.get("auth_enabled", False):
            key_path_str = firebase_config.get("service_account_key_path")
            if key_path_str:
                key_path = Path(key_path_str)
                if not key_path.is_absolute():
                    key_path = BASE_DIR / key_path_str
                
                if key_path.exists():
                    cred = credentials.Certificate(str(key_path))
                    firebase_admin.initialize_app(cred, {
                        "storageBucket": firebase_config.get("storage_bucket")
                    })
                    logger.info("Firebase initialized.")
                else:
                    logger.error(f"Firebase key missing: {key_path}")
    except Exception as e:
        logger.error(f"Firebase Init Failed: {e}")

    # 3. Database
    await initialize_database(loaded_config)

    # 4. Services
    connection_manager = ConnectionManager()
    await connection_manager.init(
        max_connections=loaded_config.get("websocket", {}).get("max_connections", 1000),
        token_refresh_interval=loaded_config.get("websocket", {}).get("token_refresh_interval", 300),
        ping_interval=loaded_config.get("websocket", {}).get("ping_interval", 15),
    )
    app.state.connection_manager = connection_manager

    await initialize_services(loaded_config, logger=logger, connection_manager=connection_manager)
    
    fm = get_feed_manager()
    analytics_service = get_analytics_service()
    
    if fm:
        fm.set_connection_manager(connection_manager)
        if analytics_service:
            await analytics_service.initialize_prediction_log_table()
            await analytics_service.start_background_tasks()
            fm.set_analytics_service(analytics_service)
            
            scheduler = PredictionScheduler(analytics_service, loaded_config)
            app.state.prediction_scheduler = scheduler
            fm.set_prediction_scheduler(scheduler)
            
            if loaded_config.get("prediction_scheduler", {}).get("enabled", True):
                await fm.start_processing()
                logger.info("Prediction Scheduler started.")

    # 5. File Watcher
    app.state.file_watcher = None
    if loaded_config.get("file_watcher", {}).get("enabled", False):
        try:
            watch_dir = Path(loaded_config["file_watcher"]["watch_directory"])
            if not watch_dir.is_absolute():
                watch_dir = BASE_DIR / watch_dir
            watch_dir.mkdir(parents=True, exist_ok=True)

            def on_new_video(path_str):
                logger.info(f"New video detected: {path_str}")
                asyncio.create_task(fm.add_and_start_feed(
                    source=path_str, is_looped=True, name_hint=Path(path_str).name,
                    latitude=None, longitude=None
                ))

            watcher = FileSystemWatcher(str(watch_dir), on_new_video)
            watcher.start()
            app.state.file_watcher = watcher
            logger.info(f"File Watcher started on {watch_dir}")
        except Exception as e:
            logger.error(f"File Watcher Error: {e}")

    # 6. Post-Startup Feeds
    post_startup = loaded_config.get("post_startup_processing", {})
    if post_startup.get("enabled", False) and fm:
        logger.info("Processing sample feeds...")
        for feed_cfg in post_startup.get("sample_feeds", []):
            try:
                path_str = feed_cfg.get("path")
                if not path_str: continue

                p = Path(path_str)
                if not p.is_absolute():
                    # Adjust based on repo structure: BASE_DIR is backend/, so parent is root
                    p = BASE_DIR.parent / path_str 
                
                if p.exists():
                    logger.info(f"Starting sample feed: {p}")
                    asyncio.create_task(fm.add_and_start_feed(
                        source=str(p), is_looped=feed_cfg.get("is_looped", True),
                        latitude=feed_cfg.get("latitude"), longitude=feed_cfg.get("longitude"),
                        name_hint=p.name
                    ))
                else:
                    logger.warning(f"Sample feed not found: {p}")
            except Exception as e:
                logger.error(f"Sample feed error: {e}")

    yield # --- Application Runs Here ---

    # --- SHUTDOWN ---
    logger.info("--- Shutting down Route One Backend ---")
    
    if hasattr(app.state, "connection_manager") and app.state.connection_manager:
        await app.state.connection_manager.shutdown()
        logger.info("WebSocket Manager shutdown.")
    
    if get_feed_manager():
        await get_feed_manager().shutdown()
        logger.info("FeedManager shutdown.")
        
    if get_analytics_service():
        await get_analytics_service().stop_background_tasks()
        logger.info("Analytics background tasks stopped.")
        
    if app.state.file_watcher:
        app.state.file_watcher.stop()
        logger.info("File Watcher stopped.")
        
    await close_database()
    logger.info("Database connection closed.")
    logger.info("Shutdown complete.")

# --- App Instance ---
app = FastAPI(
    title="Route One Hub - Backend API",
    version="1.0.0",
    description="API for managing traffic analysis feeds, data, and real-time updates.",
    lifespan=lifespan
)

# --- Exception Handlers ---

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception:")
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error", "type": "InternalServerError", "message": str(exc)})

# Handle 404s specifically (Replacement for Catch-All)
@app.exception_handler(404)
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc):
    if isinstance(exc, StarletteHTTPException) and exc.status_code == 404:
        logger.warning(f"404 Not Found: {request.method} {request.url.path}")
        return JSONResponse(status_code=404, content={"detail": "Endpoint not found", "type": "NotFound"})
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "type": "HTTPException"})

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

# --- Middleware ---
origins = [
    "http://localhost",
    "http://localhost:3000",
    "http://localhost:5173",
    "https://*.ngrok-free.app",
]
# If you need wildcard, you MUST set allow_credentials=False
# If you need credentials, you MUST remove "*" from origins

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
app.include_router(video.router, prefix="/api/v1/video", tags=["Video"])
app.include_router(incidents.router, prefix="/api/v1/incidents", tags=["Incidents"])
app.include_router(personalized_routes.router, prefix="/api/v1/routes", tags=["Routes"])
app.include_router(weather.router, prefix="/api/v1/weather", tags=["Weather"])
app.include_router(events.router, prefix="/api/v1/events", tags=["Events"])
app.include_router(route_history.router, prefix="/api/v1/route-history", tags=["History"])
app.include_router(traffic_data.router, prefix="/api/v1/traffic-data", tags=["TrafficData"])
app.include_router(ws.router, prefix="/api/v1", tags=["WebSocket"])
app.include_router(video_ws.router, prefix="/api/v1", tags=["VideoWebSocket"])
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
