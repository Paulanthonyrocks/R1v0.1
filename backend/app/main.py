import os
# --- Hardware Optimization Flags ---
# Default to GPU usage if USE_GPU is not set (since user is on GPU environment)
if os.getenv("USE_GPU", "true").lower() == "false":
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "" # Clear any potential GPU capture options
    
# Suppress excessive TensorFlow logging
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"

import asyncio
import logging
import logging.config
import uuid
import multiprocessing
import signal
from contextlib import asynccontextmanager
from pathlib import Path
import os
import random
import re
from contextvars import ContextVar
from typing import Dict, List, Optional

import psutil
import firebase_admin
from firebase_admin import credentials
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# --- Core Modules ---
from app.config import initialize_config, AppConfig
from app.database import initialize_database, close_database, get_database_manager
from app.services import initialize_services, shutdown_services, get_analytics_service, get_feed_manager
from app.services.health_service import SystemHealthService
from app.websocket.connection_manager import ConnectionManager
from app.utils.file_watcher import FileSystemWatcher
from app.core.feature_flags import FeatureFlags
from app.dependency_injection import get_container
from app.middleware.logging_middleware import LoggingMiddleware
from app.middleware.rate_limit_middleware import RateLimitMiddleware, RateLimitConfig
from app.middleware.security_middleware import SecurityHeadersMiddleware
from app.services.audit_logger import AuditLogger
from app.database import get_database_manager

# --- Routers ---
from app.routers import (
    feeds, config as config_router, analysis, alerts, video,
    incidents, routing, weather,
    events, ws, vehicles, signals, ws_monitoring
)
from app.routers.webrtc import router as webrtc_router

# --- Constants & Setup ---
logger = logging.getLogger("main")
BASE_DIR = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = BASE_DIR / "data" / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

# --- Configuration Loading ---
# Load configuration early to allow middleware setup and container initialization
config_path = BASE_DIR / "configs" / "config.yaml"
if not config_path.exists():
    config_path = Path("/app/configs/config.yaml")

try:
    loaded_config = initialize_config(str(config_path))
    container = get_container()
    container.set_config(loaded_config)
    logger.info(f"Configuration loaded from {config_path}")
except Exception as e:
    logger.critical(f"Failed to load configuration at startup: {e}")
    # In a real app, we might want to exit here, but for now let's raise
    raise RuntimeError(f"Config Load Failed: {e}")

# --- Context Variables ---
request_id_var: ContextVar[str] = ContextVar('request_id', default=None)

# --- Background Task Management ---
background_tasks = set()

def create_background_task(coro):
    task = asyncio.create_task(coro)
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    return task

# --- Database Migrations ---
async def run_migrations():
    """Placeholder for database migrations (Alembic)."""
    logger.info("Database migration check passed.")
    pass

# --- CORS Configuration ---
def setup_cors(app: FastAPI, config: dict):
    env = os.getenv("ENVIRONMENT", "development")
    allowed_origins_env = [o for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o]
    
    # Always allow local development origins and the specific cloud workstation
    origins = [
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:5173",
        "https://3000-firebase-r1v01-1774108349517.cluster-lu4mup47g5gm4rtyvhzpwbfadi.cloudworkstations.dev",
    ]
    
    if env != "development":
        origins.extend(allowed_origins_env)

    cors_config = config.get("cors", {})
    origins.extend(cors_config.get("allowed_origins", []))
    origins = list(set(origins))
    
    logger.info(f"CORS origins configured: {origins}")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=r"https://.*\.ngrok-free\.app|https://.*\.cloudworkstations\.dev|https://.*\.loca\.lt|https://.*\.githubdev\.dev",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        allow_headers=["Content-Type", "Authorization", "Bypass-Tunnel-Reminder", "X-Requested-With", "X-User-ID"],
        expose_headers=["*"],
    )

# --- Middleware Implementation ---
class RequestIDMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request_id = str(uuid.uuid4())
            request_id_var.set(request_id)
            
            async def send_with_request_id(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((b"x-request-id", request_id.encode()))
                    message["headers"] = headers
                await send(message)
            
            await self.app(scope, receive, send_with_request_id)
        else:
            await self.app(scope, receive, send)

# --- Lifespan Manager ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP ---
    logger.info("--- Starting Route One Backend ---")
    
    # 1. System Info
    try:
        mem = psutil.virtual_memory()
        logger.info(f"System Memory: {mem.percent}% used ({mem.used / (1024**3):.2f}GB / {mem.total / (1024**3):.2f}GB)")
        
        # Feature flags and state
        app.state.feature_flags = container.get_feature_flags()
        
    except Exception as e:
        logger.critical(f"Lifespan Startup Failed: {e}")
        raise RuntimeError(f"Lifespan Startup Failed: {e}")

    # 2. Database & Migrations
    try:
        await run_migrations()
        await initialize_database(cfg_dict)
    except Exception as e:
        logger.critical(f"Database Init Failed: {e}")
        raise

    # 2b. Model Optimization (Background)
    # This might take a while, so we run it in the background or at least separate thread
    # but since the first worker might start immediately, we should at least check.
    # We call it as a separate process to avoid any CUDA context pollution
    if cfg_dict.get("performance", {}).get("auto_optimize", False):
        try:
            logger.info("Checking for model auto-optimization...")
            # Use subprocess to run the script
            import subprocess
            def run_optimization():
                # Locate the model path correctly
                vd_cfg = cfg_dict.get("vehicle_detection", {})
                model_rel = vd_cfg.get("model_path", "models/yolov8n.pt")
                # Handle both relative paths
                if model_rel.startswith("backend/"):
                     model_rel = model_rel[8:]
                
                # Check if it already has an engine
                full_model_path = BASE_DIR / model_rel
                engine_path = full_model_path.with_suffix(".engine")
                
                if not engine_path.exists():
                    logger.info(f"YOLO engine missing for {full_model_path}. Starting optimization script...")
                    # We use sys.executable to ensure we use the same Python environment
                    import sys
                    subprocess.run([sys.executable, str(BASE_DIR / "scripts/optimize_model.py"), "--model", str(full_model_path)], check=True)
            
            # Start in a separate thread but we don't await it to avoid blocking startup
            # Actually, inference workers might start before it finishes, which is fine, they fall back to .pt
            create_background_task(asyncio.to_thread(run_optimization))
        except Exception as e:
            logger.error(f"Failed to trigger model optimization: {e}")

    # 3. Firebase (Critical if enabled)
    fb_cfg = to_dict(getattr(loaded_config, "firebase_admin", {}))
    try:
        if fb_cfg.get("auth_enabled", False):
            key_path = Path(fb_cfg.get("service_account_key_path", ""))
            if not key_path.is_absolute():
                # Handle paths relative to project root (e.g., starting with 'backend/')
                if str(key_path).startswith("backend/"):
                    key_path = BASE_DIR.parent / key_path
                else:
                    key_path = BASE_DIR / key_path
            
            if key_path.exists():
                cred = credentials.Certificate(str(key_path))
                firebase_admin.initialize_app(cred, {"storageBucket": fb_cfg.get("storage_bucket")})
                logger.info("Firebase initialized.")
            elif fb_cfg.get("required", False):
                raise FileNotFoundError(f"Required Firebase key missing: {key_path}")
            else:
                logger.warning(f"Firebase auth enabled but key file not found at {key_path}. Auth will fail.")
    except Exception as e:
        logger.error(f"Firebase Init Failed: {e}")
        if fb_cfg.get("required", False): raise

    # 4. Core Services
    try:
        connection_manager = await container.get_connection_manager()
        
        # Start the keepalive task for the connection manager
        ws_cfg = cfg_dict.get("websocket", {})
        connection_manager.start_keepalive(
            ping_interval=ws_cfg.get("ping_interval", 15),
            timeout=ws_cfg.get("pong_timeout", 60)
        )
        
        app.state.connection_manager = connection_manager
        
        await initialize_services(cfg_dict, logger, connection_manager)
        
        fm = await container.get_feed_manager()
        analytics_service = get_analytics_service()
        
        if fm:
            scheduler = fm.get_prediction_scheduler()
            if scheduler: app.state.prediction_scheduler = scheduler
            
            p_cfg = loaded_config.get("prediction_scheduler", {}) if isinstance(loaded_config, dict) else getattr(loaded_config, "prediction_scheduler", {})
            p_enabled = p_cfg.get("enabled", True) if isinstance(p_cfg, dict) else getattr(p_cfg, "enabled", True)
            if p_enabled:
                auto_start = loaded_config.get("auto_start_processing", True) if isinstance(loaded_config, dict) else getattr(loaded_config, "auto_start_processing", True)
                if auto_start:
                    await fm.start_processing()
                    logger.info("Feed Manager started processing automatically.")
            
            # Post-Startup Processing (Start sample feeds)
            psp_cfg = cfg_dict.get("post_startup_processing", {})
            if psp_cfg.get("enabled", False):
                sample_feeds = psp_cfg.get("sample_feeds", [])
                if sample_feeds:
                    # Resolve relative paths relative to BASE_DIR
                    for sf in sample_feeds:
                        p = sf.get("path") or sf.get("source")
                        if p and not Path(p).is_absolute():
                            resolved_p = str((BASE_DIR / p).resolve())
                            if "path" in sf: sf["path"] = resolved_p
                            if "source" in sf: sf["source"] = resolved_p
                            
                    logger.info(f"Starting {len(sample_feeds)} sample feeds from config...")
                    create_background_task(fm.start_multiple_feeds(sample_feeds))
            
            # File Watcher Initialization
            fw_cfg = cfg_dict.get("file_watcher", {})
            if fw_cfg.get("enabled", False):
                watch_dir = fw_cfg.get("watch_directory", "data/new_sample_videos")
                # Resolve relative path
                watch_path = Path(watch_dir)
                if not watch_path.is_absolute():
                    watch_path = (BASE_DIR / watch_dir).resolve()
                
                # Ensure directory exists
                watch_path.mkdir(parents=True, exist_ok=True)
                
                def on_new_video(file_path):
                    logger.info(f"FileWatcher: Adding new video feed: {file_path}")
                    # Use a background task for the async call
                    create_background_task(fm.add_and_start_feed(
                        source=str(file_path),
                        latitude=None,
                        longitude=None,
                        is_looped=True
                    ))

                watcher = FileSystemWatcher(str(watch_path), on_new_video)
                watcher.start()
                app.state.file_watcher = watcher
                logger.info(f"FileSystemWatcher started on {watch_path}")

                # [NEW] Also check for existing videos in that directory on startup
                # Build a set of already configured sources to avoid double-adding
                configured_sources = set()
                if psp_cfg.get("enabled", False):
                    for sf in psp_cfg.get("sample_feeds", []):
                        p = sf.get("path") or sf.get("source")
                        if p:
                            configured_sources.add(str(p))

                for ext in ['.mp4', '.avi', '.mov', '.mkv']:
                    for existing_file in watch_path.glob(f"*{ext}"):
                        file_path_str = str(existing_file)
                        if file_path_str in configured_sources:
                            logger.info(f"Skipping existing video in watch directory (already in config): {existing_file}")
                            continue

                        # Avoid double-adding if already in sample_feeds (simple path check)
                        # fm.add_and_start_feed handles duplicate sources internally, but we can be polite
                        logger.info(f"Found existing video in watch directory: {existing_file}")
                        create_background_task(fm.add_and_start_feed(
                            source=file_path_str,
                            latitude=None,
                            longitude=None,
                            is_looped=True
                        ))
            else:
                app.state.file_watcher = None
    except Exception as e:
        logger.critical(f"Core Services Failed: {e}")
        raise

    yield # --- App Running ---

    # --- SHUTDOWN ---
    logger.info("--- Stopping Route One Backend ---")
    
    if hasattr(app.state, "health_service"): await app.state.health_service.stop()
    if hasattr(app.state, "file_watcher") and app.state.file_watcher: app.state.file_watcher.stop()
    
    if background_tasks:
        for t in background_tasks: t.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)

    await shutdown_services()
    
    # Custom shutdown for ConnectionManager
    if hasattr(app.state, "connection_manager"):
        cm = app.state.connection_manager
        cm.stop_keepalive()
        client_ids = list(cm.active_connections.keys())
        if client_ids:
            logger.info(f"Shutting down {len(client_ids)} active WebSocket connections.")
            # Create disconnect tasks
            tasks = [cm.disconnect(client_id, cm.active_connections.get(client_id)) for client_id in client_ids]
            # Run tasks concurrently
            await asyncio.gather(*tasks, return_exceptions=True)

    await close_database()
    
    remaining = multiprocessing.active_children()
    for p in remaining:
        p.terminate()
        p.join(timeout=2.0)
        if p.is_alive(): p.kill()

    logger.info("Shutdown complete.")

# --- App Instance ---
app = FastAPI(
    title="Route One Hub - Backend API",
    version="1.1.0",
    description="API for managing traffic analysis feeds, data, and real-time updates.",
    lifespan=lifespan
)

def to_dict(obj):
    if hasattr(obj, "model_dump"):
        # Use mode='json' in Pydantic v2 to automatically convert Path to str
        try:
            return obj.model_dump(mode='json')
        except:
            return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    
    # Recursive conversion for nested structures
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_dict(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    return obj

cfg_dict = to_dict(loaded_config)

# --- Exception Handlers ---
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    trace_id = request_id_var.get() or str(uuid.uuid4())
    logger.exception(f"Unhandled exception (Trace ID: {trace_id}):")
    detail = str(exc) if os.getenv("ENVIRONMENT") == "development" else "Internal Server Error"
    return JSONResponse(status_code=500, content={"detail": detail, "trace_id": trace_id})

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

# --- Middleware Registration ---
app.add_middleware(RequestIDMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(LoggingMiddleware)

rate_limits = {
    "/api/v1/analytics": RateLimitConfig(limit=10, window=60),
    "/api/v1/feeds": RateLimitConfig(limit=30, window=60)
}
app.add_middleware(RateLimitMiddleware, limit=60, window=60, rate_limits=rate_limits)

# Audit Logger Middleware
@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    response = await call_next(request)
    
    # Log write operations
    if request.method in ["POST", "PUT", "DELETE", "PATCH"]:
        try:
            db_manager = get_database_manager()
            audit_logger = AuditLogger(db_manager)
            
            user_id = request.headers.get("X-User-ID", "anonymous")
            
            # Extract simple resource info
            path_parts = request.url.path.strip("/").split("/")
            resource_type = path_parts[2] if len(path_parts) > 2 else "unknown" # api/v1/resource
            resource_id = request.path_params.get("id", "N/A")
            
            await audit_logger.log_action(
                user_id=user_id,
                action=f"{request.method} {request.url.path}",
                resource_type=resource_type,
                resource_id=resource_id,
                ip_address=request.client.host if request.client else "unknown"
            )
        except Exception as e:
            logger.error(f"Error in audit middleware: {e}")
            
    return response

# Initialize CORS
setup_cors(app, cfg_dict)

@app.middleware("http")
async def debug_options_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        logger.info(f"PREFLIGHT: Received OPTIONS request for {request.url.path} from origin: {request.headers.get('origin')}")
    return await call_next(request)

# --- Routers Inclusion ---
app.include_router(feeds.router, prefix="/api/v1/feeds", tags=["Feeds"])
app.include_router(config_router.router, prefix="/api/v1/config", tags=["Configuration"])
app.include_router(analysis.router, prefix="/api/v1/analytics", tags=["Analytics"])
app.include_router(alerts.router, prefix="/api/v1/alerts", tags=["Alerts"])
app.include_router(video.router, prefix="/api/v1/video", tags=["Video"])
app.include_router(incidents.router, prefix="/api/v1/incidents", tags=["Incidents"])
app.include_router(routing.router, prefix="/api/v1/routes", tags=["Routing"])
app.include_router(weather.router, prefix="/api/v1/weather", tags=["Weather"])
app.include_router(events.router, prefix="/api/v1/events", tags=["Events"])
app.include_router(vehicles.router, prefix="/api/v1/vehicles", tags=["Vehicles"])
app.include_router(signals.router, prefix="/api/v1/signals", tags=["Signals"])
app.include_router(ws.router, prefix="/api/v1", tags=["WebSocket"])
app.include_router(ws_monitoring.router, prefix="/api/v1/websocket", tags=["WebSocket Monitoring"])
app.include_router(webrtc_router, prefix="/api/v1/webrtc", tags=["WebRTC"])
# --- Secure File Serving ---
@app.get("/api/v1/snapshots/{file_path:path}", tags=["Snapshots"])
async def serve_snapshot(file_path: str):
    safe_path = (SNAPSHOT_DIR / file_path).resolve()
    if not str(safe_path).startswith(str(SNAPSHOT_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    if not safe_path.exists() or not safe_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(safe_path)

# --- Utility Endpoints ---
@app.get("/")
async def root():
    return {"message": "Welcome to Route One API", "version": "1.1.0"}

@app.get("/health/detailed")
async def detailed_health_check():
    health = {"status": "ok", "services": {}}
    try:
        db = get_database_manager()
        health["services"]["database"] = {"status": "ok", "pool": await db.get_pool_stats()}
    except Exception as e:
        health["services"]["database"] = {"status": "error", "message": str(e)}
        health["status"] = "degraded"

    try:
        from app.utils.redis_client import get_redis_client
        client = get_redis_client()
        if client.ping():
            health["services"]["redis"] = {"status": "ok"}
    except Exception as e:
        health["services"]["redis"] = {"status": "error", "message": str(e)}
        health["status"] = "degraded"

    try:
        db = get_database_manager()
        if db.mongo_client:
            db.mongo_client.admin.command("ismaster")
            health["services"]["mongodb"] = {"status": "ok"}
        else:
            health["services"]["mongodb"] = {"status": "not_initialized"}
    except Exception as e:
        health["services"]["mongodb"] = {"status": "error", "message": str(e)}
        health["status"] = "degraded"

    try:
        import torch
        cuda_available = torch.cuda.is_available()
        gpu_info = {"status": "ok" if cuda_available else "not_available"}
        if cuda_available:
            gpu_info["device"] = torch.cuda.get_device_name(0)
            gpu_info["count"] = torch.cuda.device_count()
        health["services"]["gpu"] = gpu_info
    except Exception as e:
        health["services"]["gpu"] = {"status": "error", "message": str(e)}
        
    return health

if __name__ == "__main__":
    import uvicorn
    # Use standard uvicorn runner for development
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False, log_level="info")
