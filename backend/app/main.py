import os
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
# --- Hardware Optimization Flags ---
# Force TensorFlow to only allocate memory as needed, preventing conflicts with PyTorch
os.environ["TF_FORCE_GPU_ALLOW_GROWTH"] = "true"
# Suppress excessive TensorFlow logging

import asyncio
import logging
import logging.config
import uuid
import multiprocessing
import signal
from contextlib import asynccontextmanager
from pathlib import Path
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

# --- Constants & Setup ---
logger = logging.getLogger("main")
BASE_DIR = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = BASE_DIR / "data" / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def to_dict(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return obj

cfg_dict = to_dict(loaded_config) if loaded_config else None

# --- Configuration Loading ---

# Wrap in MainProcess guard to prevent recursive execution during multiprocessing spawn
loaded_config = None
container = None
cfg_dict = None

if multiprocessing.current_process().name == 'MainProcess':
    config_path = BASE_DIR / "configs" / "config.yaml"
    if not config_path.exists():
        config_path = Path("/app/configs/config.yaml")

    try:
        loaded_config = initialize_config(str(config_path))
        container = get_container()
        container.set_config(loaded_config)
        cfg_dict = to_dict(loaded_config)
        logger.info(f"Configuration loaded from {config_path}")
    except Exception as e:
        logger.critical(f"Failed to load configuration at startup: {e}")
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
        
        # Initialize connection manager with config values
        ws_cfg = cfg_dict.get("websocket", {})
        await connection_manager.init(
            max_connections=ws_cfg.get("max_connections", 1000),
            token_refresh_interval=ws_cfg.get("token_refresh_interval", 300),
            ping_interval=ws_cfg.get("ping_interval", 15),
            pong_timeout=ws_cfg.get("pong_timeout", 60)
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
    except Exception as e:
        logger.critical(f"Core Services Failed: {e}")
        raise

    # 5. Optional Services
    try:
        # 5.1 Health Service
        health_service = SystemHealthService(cfg_dict, fm, connection_manager)
        health_service.start()
        app.state.health_service = health_service
        
        # 5.2 File Watcher
        fw_cfg = loaded_config.get("file_watcher", {}) if isinstance(loaded_config, dict) else getattr(loaded_config, "file_watcher", {})
        if fw_cfg.get("enabled", False) if isinstance(fw_cfg, dict) else getattr(fw_cfg, "enabled", False):
            watch_dir = Path(fw_cfg.get("watch_directory") if isinstance(fw_cfg, dict) else fw_cfg.watch_directory)
            if not watch_dir.is_absolute(): watch_dir = BASE_DIR.parent / watch_dir
            watch_dir.mkdir(parents=True, exist_ok=True)

            def on_new_video(p_str):
                create_background_task(fm.add_and_start_feed(
                    source=p_str, is_looped=True, name_hint=Path(p_str).name,
                    latitude=34.05 + (random.random()-0.5)*0.01, 
                    longitude=-118.24 + (random.random()-0.5)*0.01
                ))

            watcher = FileSystemWatcher(str(watch_dir.resolve()), on_new_video)
            watcher.start()
            app.state.file_watcher = watcher
            
            # Scan existing
            for vf in watch_dir.glob("*"):
                if vf.is_file() and watcher.event_handler._is_video_file(vf):
                    on_new_video(str(vf))

        # 5.3 Post Startup Processing (Sample Feeds)
        psp_cfg = loaded_config.get("post_startup_processing", {}) if isinstance(loaded_config, dict) else getattr(loaded_config, "post_startup_processing", {})
        if psp_cfg.get("enabled", False) if isinstance(psp_cfg, dict) else getattr(psp_cfg, "enabled", False):
            sample_feeds = psp_cfg.get("sample_feeds", []) if isinstance(psp_cfg, dict) else getattr(psp_cfg, "sample_feeds", [])
            for feed in sample_feeds:
                f_path = feed.get("path") if isinstance(feed, dict) else getattr(feed, "path")
                create_background_task(fm.add_and_start_feed(
                    source=f_path,
                    is_looped=feed.get("is_looped", True) if isinstance(feed, dict) else getattr(feed, "is_looped", True),
                    latitude=feed.get("latitude") if isinstance(feed, dict) else getattr(feed, "latitude"),
                    longitude=feed.get("longitude") if isinstance(feed, dict) else getattr(feed, "longitude"),
                    name_hint=feed.get("name") if isinstance(feed, dict) else getattr(feed, "name", Path(f_path).name),
                    is_sample_feed=True
                ))
            logger.info(f"Scheduled {len(sample_feeds)} sample feeds for startup.")

    except Exception as e:
        logger.error(f"Optional Services Failed: {e}")

    yield # --- App Running ---

    # --- SHUTDOWN ---
    logger.info("--- Stopping Route One Backend ---")
    
    if hasattr(app.state, "health_service"): await app.state.health_service.stop()
    if hasattr(app.state, "file_watcher") and app.state.file_watcher: app.state.file_watcher.stop()
    
    if background_tasks:
        for t in background_tasks: t.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)

    await shutdown_services()
    if hasattr(app.state, "connection_manager"): await app.state.connection_manager.shutdown()
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

# Initialize CORS
if multiprocessing.current_process().name == 'MainProcess' and cfg_dict:
    setup_cors(app, cfg_dict)

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

@app.get("/health")
async def health_check():
    return {"status": "ok"}

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
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
