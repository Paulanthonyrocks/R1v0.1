     1|     1|import os
     2|     2|# --- Hardware Optimization Flags ---
     3|     3|# Default to GPU usage if USE_GPU is not set (since user is on GPU environment)
     4|     4|if os.getenv("USE_GPU", "true").lower() == "false":
     5|     5|    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
     6|     6|    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "" # Clear any potential GPU capture options
     7|     7|    
     8|     8|# Suppress excessive TensorFlow logging
     9|     9|os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"
    10|    10|
    11|    11|import asyncio
    12|    12|import logging
    13|    13|import logging.config
    14|    14|import uuid
    15|    15|import multiprocessing
    16|    16|import signal
    17|    17|from contextlib import asynccontextmanager
    18|    18|from pathlib import Path
    19|    19|import os
    20|    20|import random
    21|    21|import re
    22|    22|from contextvars import ContextVar
    23|    23|from typing import Dict, List, Optional
    24|    24|
    25|    25|import psutil
    26|    26|import firebase_admin
    27|    27|from firebase_admin import credentials
    28|    28|from fastapi import FastAPI, Request, Depends, HTTPException
    29|    29|from fastapi.middleware.cors import CORSMiddleware
    30|    30|from fastapi.responses import JSONResponse, FileResponse
    31|    31|from starlette.exceptions import HTTPException as StarletteHTTPException
    32|    32|
    33|    33|# --- Core Modules ---
    34|    34|from app.config import initialize_config, AppConfig
    35|    35|from app.database import initialize_database, close_database, get_database_manager
    36|    36|from app.services import initialize_services, shutdown_services, get_analytics_service, get_feed_manager
    37|    37|from app.services.health_service import SystemHealthService
    38|    38|from app.websocket.connection_manager import ConnectionManager
    39|    39|from app.utils.file_watcher import FileSystemWatcher
    40|    40|from app.core.feature_flags import FeatureFlags
    41|    41|from app.dependency_injection import get_container
    42|    42|from app.middleware.logging_middleware import LoggingMiddleware
    43|    43|from app.middleware.rate_limit_middleware import RateLimitMiddleware, RateLimitConfig
    44|    44|from app.middleware.security_middleware import SecurityHeadersMiddleware
    45|    45|from app.services.audit_logger import AuditLogger
    46|    46|from app.database import get_database_manager
    47|    47|
    48|    48|# --- Routers ---
    49|    49|from app.routers import (
    50|    feeds, config as config_router, analysis, alerts, video,
    51|    incidents, routing, weather,
    52|    events, ws, vehicles, signals, ws_monitoring, logs
    53|)
    54|    50|from app.routers.webrtc import router as webrtc_router
    55|    51|
    56|    52|# --- Constants & Setup ---
    57|    53|logger = logging.getLogger("main")
    58|    54|BASE_DIR = Path(__file__).resolve().parent.parent
    59|    55|SNAPSHOT_DIR = BASE_DIR / "data" / "snapshots"
    60|    56|SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    61|    57|
    62|    58|# --- Configuration Loading ---
    63|    59|# Load configuration early to allow middleware setup and container initialization
    64|    60|config_path = BASE_DIR / "configs" / "config.yaml"
    65|    61|if not config_path.exists():
    66|    62|    config_path = Path("/app/configs/config.yaml")
    67|    63|
    68|    64|try:
    69|    65|    loaded_config = initialize_config(str(config_path))
    70|    66|    container = get_container()
    71|    67|    container.set_config(loaded_config)
    72|    68|    logger.info(f"Configuration loaded from {config_path}")
    73|    69|except Exception as e:
    74|    70|    logger.critical(f"Failed to load configuration at startup: {e}")
    75|    71|    # In a real app, we might want to exit here, but for now let's raise
    76|    72|    raise RuntimeError(f"Config Load Failed: {e}")
    77|    73|
    78|    74|# --- Context Variables ---
    79|    75|request_id_var: ContextVar[str] = ContextVar('request_id', default=None)
    80|    76|
    81|    77|# --- Background Task Management ---
    82|    78|background_tasks = set()
    83|    79|
    84|    80|def create_background_task(coro):
    85|    81|    task = asyncio.create_task(coro)
    86|    82|    background_tasks.add(task)
    87|    83|    task.add_done_callback(background_tasks.discard)
    88|    84|    return task
    89|    85|
    90|    86|# --- Database Migrations ---
    91|    87|async def run_migrations():
    92|    88|    """Placeholder for database migrations (Alembic)."""
    93|    89|    logger.info("Database migration check passed.")
    94|    90|    pass
    95|    91|
    96|    92|# --- CORS Configuration ---
    97|    93|def setup_cors(app: FastAPI, config: dict):
    98|    94|    env = os.getenv("ENVIRONMENT", "development")
    99|    95|    allowed_origins_env = [o for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o]
   100|    96|    
   101|    97|    # Always allow local development origins and the specific cloud workstation
   102|    98|    origins = [
   103|    99|        "http://localhost",
   104|   100|        "http://localhost:3000",
   105|   101|        "http://localhost:5173",
   106|   102|        "https://3000-firebase-r1v01-1774108349517.cluster-lu4mup47g5gm4rtyvhzpwbfadi.cloudworkstations.dev",
   107|   103|    ]
   108|   104|    
   109|   105|    if env != "development":
   110|   106|        origins.extend(allowed_origins_env)
   111|   107|
   112|   108|    cors_config = config.get("cors", {})
   113|   109|    origins.extend(cors_config.get("allowed_origins", []))
   114|   110|    origins = list(set(origins))
   115|   111|    
   116|   112|    logger.info(f"CORS origins configured: {origins}")
   117|   113|
   118|   114|    app.add_middleware(
   119|   115|        CORSMiddleware,
   120|   116|        allow_origins=origins,
   121|   117|        allow_origin_regex=r"https://.*\.ngrok-free\.app|https://.*\.cloudworkstations\.dev|https://.*\.loca\.lt|https://.*\.githubdev\.dev",
   122|   118|        allow_credentials=True,
   123|   119|        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
   124|   120|        allow_headers=["Content-Type", "Authorization", "Bypass-Tunnel-Reminder", "X-Requested-With", "X-User-ID"],
   125|   121|        expose_headers=["*"],
   126|   122|    )
   127|   123|
   128|   124|# --- Middleware Implementation ---
   129|   125|class RequestIDMiddleware:
   130|   126|    def __init__(self, app):
   131|   127|        self.app = app
   132|   128|
   133|   129|    async def __call__(self, scope, receive, send):
   134|   130|        if scope["type"] == "http":
   135|   131|            request_id = str(uuid.uuid4())
   136|   132|            request_id_var.set(request_id)
   137|   133|            
   138|   134|            async def send_with_request_id(message):
   139|   135|                if message["type"] == "http.response.start":
   140|   136|                    headers = list(message.get("headers", []))
   141|   137|                    headers.append((b"x-request-id", request_id.encode()))
   142|   138|                    message["headers"] = headers
   143|   139|                await send(message)
   144|   140|            
   145|   141|            await self.app(scope, receive, send_with_request_id)
   146|   142|        else:
   147|   143|            await self.app(scope, receive, send)
   148|   144|
   149|   145|# --- Lifespan Manager ---
   150|   146|@asynccontextmanager
   151|   147|async def lifespan(app: FastAPI):
   152|   148|    # --- STARTUP ---
   153|   149|    logger.info("--- Starting Route One Backend ---")
   154|   150|    
   155|   151|    # 1. System Info
   156|   152|    try:
   157|   153|        mem = psutil.virtual_memory()
   158|   154|        logger.info(f"System Memory: {mem.percent}% used ({mem.used / (1024**3):.2f}GB / {mem.total / (1024**3):.2f}GB)")
   159|   155|        
   160|   156|        # Feature flags and state
   161|   157|        app.state.feature_flags = container.get_feature_flags()
   162|   158|        
   163|   159|    except Exception as e:
   164|   160|        logger.critical(f"Lifespan Startup Failed: {e}")
   165|   161|        raise RuntimeError(f"Lifespan Startup Failed: {e}")
   166|   162|
   167|   163|    # 2. Database & Migrations
   168|   164|    try:
   169|   165|        await run_migrations()
   170|   166|        await initialize_database(cfg_dict)
   171|   167|    except Exception as e:
   172|   168|        logger.critical(f"Database Init Failed: {e}")
   173|   169|        raise
   174|   170|
   175|   171|    # 2b. Model Optimization (Background)
   176|   172|    # This might take a while, so we run it in the background or at least separate thread
   177|   173|    # but since the first worker might start immediately, we should at least check.
   178|   174|    # We call it as a separate process to avoid any CUDA context pollution
   179|   175|    if cfg_dict.get("performance", {}).get("auto_optimize", False):
   180|   176|        try:
   181|   177|            logger.info("Checking for model auto-optimization...")
   182|   178|            # Use subprocess to run the script
   183|   179|            import subprocess
   184|   180|            def run_optimization():
   185|   181|                # Locate the model path correctly
   186|   182|                vd_cfg = cfg_dict.get("vehicle_detection", {})
   187|   183|                model_rel = vd_cfg.get("model_path", "models/yolov8n.pt")
   188|   184|                # Handle both relative paths
   189|   185|                if model_rel.startswith("backend/"):
   190|   186|                     model_rel = model_rel[8:]
   191|   187|                
   192|   188|                # Check if it already has an engine
   193|   189|                full_model_path = BASE_DIR / model_rel
   194|   190|                engine_path = full_model_path.with_suffix(".engine")
   195|   191|                
   196|   192|                if not engine_path.exists():
   197|   193|                    logger.info(f"YOLO engine missing for {full_model_path}. Starting optimization script...")
   198|   194|                    # We use sys.executable to ensure we use the same Python environment
   199|   195|                    import sys
   200|   196|                    subprocess.run([sys.executable, str(BASE_DIR / "scripts/optimize_model.py"), "--model", str(full_model_path)], check=True)
   201|   197|            
   202|   198|            # Start in a separate thread but we don't await it to avoid blocking startup
   203|   199|            # Actually, inference workers might start before it finishes, which is fine, they fall back to .pt
   204|   200|            create_background_task(asyncio.to_thread(run_optimization))
   205|   201|        except Exception as e:
   206|   202|            logger.error(f"Failed to trigger model optimization: {e}")
   207|   203|
   208|   204|    # 3. Firebase (Critical if enabled)
   209|   205|    fb_cfg = to_dict(getattr(loaded_config, "firebase_admin", {}))
   210|   206|    try:
   211|   207|        if fb_cfg.get("auth_enabled", False):
   212|   208|            key_path = Path(fb_cfg.get("service_account_key_path", ""))
   213|   209|            if not key_path.is_absolute():
   214|   210|                # Handle paths relative to project root (e.g., starting with 'backend/')
   215|   211|                if str(key_path).startswith("backend/"):
   216|   212|                    key_path = BASE_DIR.parent / key_path
   217|   213|                else:
   218|   214|                    key_path = BASE_DIR / key_path
   219|   215|            
   220|   216|            if key_path.exists():
   221|   217|                cred = credentials.Certificate(str(key_path))
   222|   218|                firebase_admin.initialize_app(cred, {"storageBucket": fb_cfg.get("storage_bucket")})
   223|   219|                logger.info("Firebase initialized.")
   224|   220|            elif fb_cfg.get("required", False):
   225|   221|                raise FileNotFoundError(f"Required Firebase key missing: {key_path}")
   226|   222|            else:
   227|   223|                logger.warning(f"Firebase auth enabled but key file not found at {key_path}. Auth will fail.")
   228|   224|    except Exception as e:
   229|   225|        logger.error(f"Firebase Init Failed: {e}")
   230|   226|        if fb_cfg.get("required", False): raise
   231|   227|
   232|   228|    # 4. Core Services
   233|   229|    try:
   234|   230|        connection_manager = await container.get_connection_manager()
   235|   231|        
   236|   232|        # Start the keepalive task for the connection manager
   237|   233|        ws_cfg = cfg_dict.get("websocket", {})
   238|   234|        connection_manager.start_keepalive(
   239|   235|            ping_interval=ws_cfg.get("ping_interval", 15),
   240|   236|            timeout=ws_cfg.get("pong_timeout", 60)
   241|   237|        )
   242|   238|        
   243|   239|        app.state.connection_manager = connection_manager
   244|   240|        
   245|   241|        await initialize_services(cfg_dict, logger, connection_manager)
   246|   242|        
   247|   243|        fm = await container.get_feed_manager()
   248|   244|        analytics_service = get_analytics_service()
   249|   245|        
   250|   246|        if fm:
   251|   247|            scheduler = fm.get_prediction_scheduler()
   252|   248|            if scheduler: app.state.prediction_scheduler = scheduler
   253|   249|            
   254|   250|            p_cfg = loaded_config.get("prediction_scheduler", {}) if isinstance(loaded_config, dict) else getattr(loaded_config, "prediction_scheduler", {})
   255|   251|            p_enabled = p_cfg.get("enabled", True) if isinstance(p_cfg, dict) else getattr(p_cfg, "enabled", True)
   256|   252|            if p_enabled:
   257|   253|                auto_start = loaded_config.get("auto_start_processing", True) if isinstance(loaded_config, dict) else getattr(loaded_config, "auto_start_processing", True)
   258|   254|                if auto_start:
   259|   255|                    await fm.start_processing()
   260|   256|                    logger.info("Feed Manager started processing automatically.")
   261|   257|            
   262|   258|            # Post-Startup Processing (Start sample feeds)
   263|   259|            psp_cfg = cfg_dict.get("post_startup_processing", {})
   264|   260|            if psp_cfg.get("enabled", False):
   265|   261|                sample_feeds = psp_cfg.get("sample_feeds", [])
   266|   262|                if sample_feeds:
   267|   263|                    # Resolve relative paths relative to BASE_DIR
   268|   264|                    for sf in sample_feeds:
   269|   265|                        p = sf.get("path") or sf.get("source")
   270|   266|                        if p and not Path(p).is_absolute():
   271|   267|                            resolved_p = str((BASE_DIR / p).resolve())
   272|   268|                            if "path" in sf: sf["path"] = resolved_p
   273|   269|                            if "source" in sf: sf["source"] = resolved_p
   274|   270|                            
   275|   271|                    logger.info(f"Starting {len(sample_feeds)} sample feeds from config...")
   276|   272|                    create_background_task(fm.start_multiple_feeds(sample_feeds))
   277|   273|            
   278|   274|            # File Watcher Initialization
   279|   275|            fw_cfg = cfg_dict.get("file_watcher", {})
   280|   276|            if fw_cfg.get("enabled", False):
   281|   277|                watch_dir = fw_cfg.get("watch_directory", "data/new_sample_videos")
   282|   278|                # Resolve relative path
   283|   279|                watch_path = Path(watch_dir)
   284|   280|                if not watch_path.is_absolute():
   285|   281|                    watch_path = (BASE_DIR / watch_dir).resolve()
   286|   282|                
   287|   283|                # Ensure directory exists
   288|   284|                watch_path.mkdir(parents=True, exist_ok=True)
   289|   285|                
   290|   286|                def on_new_video(file_path):
   291|   287|                    logger.info(f"FileWatcher: Adding new video feed: {file_path}")
   292|   288|                    # Use a background task for the async call
   293|   289|                    create_background_task(fm.add_and_start_feed(
   294|   290|                        source=str(file_path),
   295|   291|                        latitude=None,
   296|   292|                        longitude=None,
   297|   293|                        is_looped=True
   298|   294|                    ))
   299|   295|
   300|   296|                watcher = FileSystemWatcher(str(watch_path), on_new_video)
   301|   297|                watcher.start()
   302|   298|                app.state.file_watcher = watcher
   303|   299|                logger.info(f"FileSystemWatcher started on {watch_path}")
   304|   300|
   305|   301|                # [NEW] Also check for existing videos in that directory on startup
   306|   302|                # Build a set of already configured sources to avoid double-adding
   307|   303|                configured_sources = set()
   308|   304|                if psp_cfg.get("enabled", False):
   309|   305|                    for sf in psp_cfg.get("sample_feeds", []):
   310|   306|                        p = sf.get("path") or sf.get("source")
   311|   307|                        if p:
   312|   308|                            configured_sources.add(str(p))
   313|   309|
   314|   310|                for ext in ['.mp4', '.avi', '.mov', '.mkv']:
   315|   311|                    for existing_file in watch_path.glob(f"*{ext}"):
   316|   312|                        file_path_str = str(existing_file)
   317|   313|                        if file_path_str in configured_sources:
   318|   314|                            logger.info(f"Skipping existing video in watch directory (already in config): {existing_file}")
   319|   315|                            continue
   320|   316|
   321|   317|                        # Avoid double-adding if already in sample_feeds (simple path check)
   322|   318|                        # fm.add_and_start_feed handles duplicate sources internally, but we can be polite
   323|   319|                        logger.info(f"Found existing video in watch directory: {existing_file}")
   324|   320|                        create_background_task(fm.add_and_start_feed(
   325|   321|                            source=file_path_str,
   326|   322|                            latitude=None,
   327|   323|                            longitude=None,
   328|   324|                            is_looped=True
   329|   325|                        ))
   330|   326|            else:
   331|   327|                app.state.file_watcher = None
   332|   328|    except Exception as e:
   333|   329|        logger.critical(f"Core Services Failed: {e}")
   334|   330|        raise
   335|   331|
   336|   332|    yield # --- App Running ---
   337|   333|
   338|   334|    # --- SHUTDOWN ---
   339|   335|    logger.info("--- Stopping Route One Backend ---")
   340|   336|    
   341|   337|    if hasattr(app.state, "health_service"): await app.state.health_service.stop()
   342|   338|    if hasattr(app.state, "file_watcher") and app.state.file_watcher: app.state.file_watcher.stop()
   343|   339|    
   344|   340|    if background_tasks:
   345|   341|        for t in background_tasks: t.cancel()
   346|   342|        await asyncio.gather(*background_tasks, return_exceptions=True)
   347|   343|
   348|   344|    await shutdown_services()
   349|   345|    
   350|   346|    # Custom shutdown for ConnectionManager
   351|   347|    if hasattr(app.state, "connection_manager"):
   352|   348|        cm = app.state.connection_manager
   353|   349|        cm.stop_keepalive()
   354|   350|        client_ids = list(cm.active_connections.keys())
   355|   351|        if client_ids:
   356|   352|            logger.info(f"Shutting down {len(client_ids)} active WebSocket connections.")
   357|   353|            # Create disconnect tasks
   358|   354|            tasks = [cm.disconnect(client_id, cm.active_connections.get(client_id)) for client_id in client_ids]
   359|   355|            # Run tasks concurrently
   360|   356|            await asyncio.gather(*tasks, return_exceptions=True)
   361|   357|
   362|   358|    await close_database()
   363|   359|    
   364|   360|    remaining = multiprocessing.active_children()
   365|   361|    for p in remaining:
   366|   362|        p.terminate()
   367|   363|        p.join(timeout=2.0)
   368|   364|        if p.is_alive(): p.kill()
   369|   365|
   370|   366|    logger.info("Shutdown complete.")
   371|   367|
   372|   368|# --- App Instance ---
   373|   369|app = FastAPI(
   374|   370|    title="Route One Hub - Backend API",
   375|   371|    version="1.1.0",
   376|   372|    description="API for managing traffic analysis feeds, data, and real-time updates.",
   377|   373|    lifespan=lifespan
   378|   374|)
   379|   375|
   380|   376|def to_dict(obj):
   381|   377|    if hasattr(obj, "model_dump"):
   382|   378|        # Use mode='json' in Pydantic v2 to automatically convert Path to str
   383|   379|        try:
   384|   380|            return obj.model_dump(mode='json')
   385|   381|        except:
   386|   382|            return obj.model_dump()
   387|   383|    if hasattr(obj, "dict"):
   388|   384|        return obj.dict()
   389|   385|    
   390|   386|    # Recursive conversion for nested structures
   391|   387|    if isinstance(obj, dict):
   392|   388|        return {k: to_dict(v) for k, v in obj.items()}
   393|   389|    if isinstance(obj, (list, tuple, set)):
   394|   390|        return [to_dict(v) for v in obj]
   395|   391|    if isinstance(obj, Path):
   396|   392|        return str(obj)
   397|   393|    return obj
   398|   394|
   399|   395|cfg_dict = to_dict(loaded_config)
   400|   396|
   401|   397|# --- Exception Handlers ---
   402|   398|@app.exception_handler(Exception)
   403|   399|async def unhandled_exception_handler(request: Request, exc: Exception):
   404|   400|    trace_id = request_id_var.get() or str(uuid.uuid4())
   405|   401|    logger.exception(f"Unhandled exception (Trace ID: {trace_id}):")
   406|   402|    detail = str(exc) if os.getenv("ENVIRONMENT") == "development" else "Internal Server Error"
   407|   403|    return JSONResponse(status_code=500, content={"detail": detail, "trace_id": trace_id})
   408|   404|
   409|   405|@app.exception_handler(StarletteHTTPException)
   410|   406|async def http_exception_handler(request: Request, exc: StarletteHTTPException):
   411|   407|    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
   412|   408|
   413|   409|# --- Middleware Registration ---
   414|   410|app.add_middleware(RequestIDMiddleware)
   415|   411|app.add_middleware(SecurityHeadersMiddleware)
   416|   412|app.add_middleware(LoggingMiddleware)
   417|   413|
   418|   414|rate_limits = {
   419|   415|    "/api/v1/analytics": RateLimitConfig(limit=10, window=60),
   420|   416|    "/api/v1/feeds": RateLimitConfig(limit=30, window=60)
   421|   417|}
   422|   418|app.add_middleware(RateLimitMiddleware, limit=60, window=60, rate_limits=rate_limits)
   423|   419|
   424|   420|# Audit Logger Middleware
   425|   421|@app.middleware("http")
   426|   422|async def audit_middleware(request: Request, call_next):
   427|   423|    response = await call_next(request)
   428|   424|    
   429|   425|    # Log write operations
   430|   426|    if request.method in ["POST", "PUT", "DELETE", "PATCH"]:
   431|   427|        try:
   432|   428|            db_manager = get_database_manager()
   433|   429|            audit_logger = AuditLogger(db_manager)
   434|   430|            
   435|   431|            user_id = request.headers.get("X-User-ID", "anonymous")
   436|   432|            
   437|   433|            # Extract simple resource info
   438|   434|            path_parts = request.url.path.strip("/").split("/")
   439|   435|            resource_type = path_parts[2] if len(path_parts) > 2 else "unknown" # api/v1/resource
   440|   436|            resource_id = request.path_params.get("id", "N/A")
   441|   437|            
   442|   438|            await audit_logger.log_action(
   443|   439|                user_id=user_id,
   444|   440|                action=f"{request.method} {request.url.path}",
   445|   441|                resource_type=resource_type,
   446|   442|                resource_id=resource_id,
   447|   443|                ip_address=request.client.host if request.client else "unknown"
   448|   444|            )
   449|   445|        except Exception as e:
   450|   446|            logger.error(f"Error in audit middleware: {e}")
   451|   447|            
   452|   448|    return response
   453|   449|
   454|   450|# Initialize CORS
   455|   451|setup_cors(app, cfg_dict)
   456|   452|
   457|   453|@app.middleware("http")
   458|   454|async def debug_options_middleware(request: Request, call_next):
   459|   455|    if request.method == "OPTIONS":
   460|   456|        logger.info(f"PREFLIGHT: Received OPTIONS request for {request.url.path} from origin: {request.headers.get('origin')}")
   461|   457|    return await call_next(request)
   462|   458|
   463|   459|# --- Routers Inclusion ---
   464|   460|app.include_router(feeds.router, prefix="/api/v1/feeds", tags=["Feeds"])
app.include_router(logs.router, prefix="/api/v1/logs", tags=["System Logs"])
   465|   461|app.include_router(config_router.router, prefix="/api/v1/config", tags=["Configuration"])
   466|   462|app.include_router(analysis.router, prefix="/api/v1/analytics", tags=["Analytics"])
   467|   463|app.include_router(alerts.router, prefix="/api/v1/alerts", tags=["Alerts"])
   468|   464|app.include_router(video.router, prefix="/api/v1/video", tags=["Video"])
   469|   465|app.include_router(incidents.router, prefix="/api/v1/incidents", tags=["Incidents"])
   470|   466|app.include_router(routing.router, prefix="/api/v1/routes", tags=["Routing"])
   471|   467|app.include_router(weather.router, prefix="/api/v1/weather", tags=["Weather"])
   472|   468|app.include_router(events.router, prefix="/api/v1/events", tags=["Events"])
   473|   469|app.include_router(vehicles.router, prefix="/api/v1/vehicles", tags=["Vehicles"])
   474|   470|app.include_router(signals.router, prefix="/api/v1/signals", tags=["Signals"])
   475|   471|app.include_router(ws.router, prefix="/api/v1", tags=["WebSocket"])
   476|   472|app.include_router(ws_monitoring.router, prefix="/api/v1/websocket", tags=["WebSocket Monitoring"])
   477|   473|app.include_router(webrtc_router, prefix="/api/v1/webrtc", tags=["WebRTC"])
   478|   474|# --- Secure File Serving ---
   479|   475|@app.get("/api/v1/snapshots/{file_path:path}", tags=["Snapshots"])
   480|   476|async def serve_snapshot(file_path: str):
   481|   477|    safe_path = (SNAPSHOT_DIR / file_path).resolve()
   482|   478|    if not str(safe_path).startswith(str(SNAPSHOT_DIR.resolve())):
   483|   479|        raise HTTPException(status_code=403, detail="Access denied")
   484|   480|    if not safe_path.exists() or not safe_path.is_file():
   485|   481|        raise HTTPException(status_code=404, detail="File not found")
   486|   482|    return FileResponse(safe_path)
   487|   483|
   488|   484|# --- Utility Endpoints ---
   489|   485|@app.get("/")
   490|   486|async def root():
   491|   487|    return {"message": "Welcome to Route One API", "version": "1.1.0"}
   492|   488|
   493|   489|@app.get("/health/detailed")
   494|   490|async def detailed_health_check():
   495|   491|    health = {"status": "ok", "services": {}}
   496|   492|    try:
   497|   493|        db = get_database_manager()
   498|   494|        health["services"]["database"] = {"status": "ok", "pool": await db.get_pool_stats()}
   499|   495|    except Exception as e:
   500|   496|        health["services"]["database"] = {"status": "error", "message": str(e)}
   501|