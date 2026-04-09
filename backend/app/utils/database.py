# backend/app/utils/database.py

import asyncio
import sqlite3
import threading
import logging
import time
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional, Any
from tenacity import (
    retry,
    wait_exponential,
    stop_after_attempt,
    retry_if_exception_type,
    RetryError,
)
from contextlib import asynccontextmanager, contextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError as SQLAlchemyOperationalError
from pymongo import MongoClient
from pymongo.database import Database as MongoDatabase
from pymongo.errors import (
    ConnectionFailure,
    ConfigurationError as MongoConfigurationError,
    ServerSelectionTimeoutError,
)
from app.models.alerts import Alert  # Import the Alert model
import json  # Import json for serializing details
import math


# Attempt to import TrafficMonitor from where it's planned to be

from app.utils.config import ConfigError

logger = logging.getLogger(__name__)

# Register SQLite adapters for numpy types to prevent them from being stored as blobs
def _register_sqlite_adapters():
    try:
        import numpy as np
        def adapt_numpy_float32(np_float32): return float(np_float32)
        def adapt_numpy_float64(np_float64): return float(np_float64)
        def adapt_numpy_int32(np_int32): return int(np_int32)
        def adapt_numpy_int64(np_int64): return int(np_int64)

        sqlite3.register_adapter(np.float32, adapt_numpy_float32)
        sqlite3.register_adapter(np.float64, adapt_numpy_float64)
        sqlite3.register_adapter(np.int32, adapt_numpy_int32)
        sqlite3.register_adapter(np.int64, adapt_numpy_int64)
        logger.debug("Registered SQLite adapters for numpy types.")
    except ImportError:
        pass

_register_sqlite_adapters()

class DatabaseError(Exception):
    """Custom exception for database operation errors."""

    pass


# --- DatabaseManager (Merged and Corrected) ---
# --- Retry Decorators ---
from sqlalchemy.exc import OperationalError as SAOperationalError
db_write_retry_decorator = retry(
    wait=wait_exponential(multiplier=0.2, min=0.2, max=3),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type((sqlite3.OperationalError, SAOperationalError)),
    reraise=True
)

class DatabaseManager:
    def __init__(self, config: Dict):
        self.sqlite_db_path: Optional[Path] = None
        self.timescale_url: Optional[str] = None
        self.mongo_uri: Optional[str] = None
        self.mongo_db_name: Optional[str] = None
        self.raw_traffic_collection_name: str = (
            "raw_traffic_data"  # Default, can be from config
        )
        self.mongo_client: Optional[MongoClient] = None
        self.mongo_db: Optional[MongoDatabase] = None
        self.async_engine = None  # Corrected attribute name
        self.async_session_factory = None  # Corrected attribute name
        self.timescale_engine = None
        self.timescale_session_factory = None

        # Initialize database connections
        self._init_from_config(config)
        self.lock = threading.Lock()
        self._needs_vacuum = False  # S4: Background VACUUM scheduling flag

        # Initialize databases
        if self.sqlite_db_path:
            self._initialize_sqlite_database()

        if self.timescale_url:
            self._initialize_timescale_database()

        if self.mongo_uri and self.mongo_db_name:
            self._initialize_mongodb()

        # Async SQLAlchemy setup (only if sqlite_db_path is valid)
        if self.sqlite_db_path:
            # --- THIS IS THE FIX ---
            # The "sqlite+aiosqlite" prefix tells SQLAlchemy to use the async aiosqlite driver.
            # Increased timeout to 30s to handle "database is locked" errors in concurrent environments.
            self.async_engine = create_async_engine(
                f"sqlite+aiosqlite:///{self.sqlite_db_path}",
                connect_args={"timeout": 30.0}
            )
            # ---------------------

            self.async_session_factory = sessionmaker(
                self.async_engine, class_=AsyncSession, expire_on_commit=False
            )
        else:
            logger.warning(
                "SQLite path not configured. Async SQLAlchemy engine not created."
            )

    @asynccontextmanager
    async def transaction(self):
        """Context manager for database transactions (Async SQLAlchemy)."""
        if not self.async_session_factory:
            raise DatabaseError("Async session factory not initialized.")
            
        async with self.async_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error(f"Transaction rolled back: {e}")
                raise
            finally:
                await session.close()

    def _init_from_config(self, config: Dict[str, Any]):
        """Initialize database path and MongoDB URI from configuration."""
        try:
            db_config = config.get("database", {})
            self.sqlite_db_path_str = db_config.get("db_path", "data/vehicle_data.db")

            path_obj = Path(self.sqlite_db_path_str)
            if not path_obj.is_absolute():
                # Correctly resolve project root: up 4 levels from backend/app/utils/database.py is project root
                project_root = Path(__file__).resolve().parent.parent.parent.parent
                
                # Check if the path starts with 'backend/' or 'data/' and adjust accordingly
                # If we are in /home/user/R1v0.1/backend/app/utils/database.py, project_root is R1v0.1
                path_obj = project_root / self.sqlite_db_path_str

            self.sqlite_db_path = str(path_obj.resolve())

            if not os.path.exists(os.path.dirname(self.sqlite_db_path)):
                try:
                    os.makedirs(os.path.dirname(self.sqlite_db_path), exist_ok=True)
                    logger.info(
                        f"Created database directory: {os.path.dirname(self.sqlite_db_path)}"
                    )
                except OSError as e:
                    raise ConfigError(
                        f"Failed to create database directory {os.path.dirname(self.sqlite_db_path)}: {e}"
                    ) from e

            logger.info(f"SQLite database path configured to: {self.sqlite_db_path}")

            timescale_config = config.get("timescaledb", {})
            if timescale_config.get("enabled", False):
                self.timescale_url = timescale_config.get("url", "postgresql+asyncpg://postgres:password@localhost:5432/traffic_hub")
                logger.info("TimescaleDB (PostgreSQL) configured.")
            else:
                self.timescale_url = None

            mongo_config = config.get("mongodb") or {}
            if mongo_config.get("enabled", True) and mongo_config.get("uri") and mongo_config.get("database_name"):
                self.mongo_uri = mongo_config["uri"]
                
                # Auto-adjust if in Docker and uri points to localhost
                is_colab = "COLAB_RELEASE_TAG" in os.environ
                if Path("/.dockerenv").exists() and "localhost" in self.mongo_uri and not is_colab:
                    logger.info("Docker environment detected (non-Colab). Adjusting MongoDB URI from localhost to mongodb service name.")
                    self.mongo_uri = self.mongo_uri.replace("localhost", "mongodb")

                self.mongo_db_name = mongo_config["database_name"]
                self.raw_traffic_collection_name = mongo_config.get(
                    "raw_traffic_collection", "raw_traffic_data"
                )
                logger.info(
                    f"MongoDB configured: URI='{self.mongo_uri}', DB='{self.mongo_db_name}'"
                )
            else:
                logger.info(
                    "MongoDB not fully configured (URI or database_name missing). MongoDB will not be used."
                )
                self.mongo_uri = None
                self.mongo_db_name = None

        except ConfigError as e:
            raise e
        except KeyError as e:
            logger.error(f"Missing expected key in database configuration: {e}")
            raise ConfigError(f"Database configuration missing key: {e}") from e
        except Exception as e:
            logger.error(
                f"Failed to initialize database configuration paths: {e}", exc_info=True
            )
            raise ValueError(f"Invalid database configuration: {e}") from e

    @asynccontextmanager
    async def get_session(self):
        """Get an async database session."""
        if not self.async_session_factory:
            raise DatabaseError(
                "Async session factory not initialized. Check SQLite configuration."
            )
        session: AsyncSession = self.async_session_factory()
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"Async session error: {e}", exc_info=True)
            raise DatabaseError(f"Async session failed: {e}") from e
        finally:
            await session.close()

    @contextmanager
    def get_session_sync(self) -> Any:
        """Get a synchronous database session (for SQLite)."""
        if not self.sqlite_db_path:
            raise DatabaseError("SQLite database path not configured.")
        from sqlalchemy.orm import (
            Session as SyncSession,
            sessionmaker as sync_sessionmaker,
        )
        from sqlalchemy import create_engine as create_sync_engine

        engine = create_sync_engine(f"sqlite:///{self.sqlite_db_path}")
        SessionLocal = sync_sessionmaker(autocommit=False, autoflush=False, bind=engine)
        session: SyncSession = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Sync session error: {e}", exc_info=True)
            raise DatabaseError(f"Sync session failed: {e}") from e
        finally:
            session.close()

    def _initialize_timescale_database(self):
        """Initializes the TimescaleDB (PostgreSQL) engine and creates hypertables."""
        logger.info("Initializing TimescaleDB...")
        try:
            self.timescale_engine = create_async_engine(self.timescale_url)
            self.timescale_session_factory = sessionmaker(
                self.timescale_engine, class_=AsyncSession, expire_on_commit=False
            )
            # Create hypertables (synchronously for schema setup)
            asyncio.create_task(self._create_timescale_hypertables())
        except Exception as e:
            logger.error(f"Failed to initialize TimescaleDB: {e}")

    async def _create_timescale_hypertables(self):
        """Sets up the schema and hypertables in TimescaleDB."""
        # We use a raw connection for TimescaleDB specific commands
        try:
            async with self.timescale_engine.begin() as conn:
                # 1. Create standard table if not exists
                # Note: We use TIMESTAMPTZ for TimescaleDB
                await conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS vehicle_tracks (
                        feed_id TEXT NOT NULL,
                        track_id TEXT NOT NULL,
                        timestamp TIMESTAMPTZ NOT NULL,
                        global_vehicle_id TEXT,
                        class_id INTEGER,
                        confidence FLOAT,
                        center_x FLOAT,
                        center_y FLOAT,
                        speed FLOAT,
                        lane INTEGER,
                        direction TEXT,
                        license_plate TEXT
                    );
                """))
                
                # 2. Convert to hypertable (this will fail if already a hypertable, so we check)
                try:
                    await conn.execute(text("SELECT create_hypertable('vehicle_tracks', 'timestamp', if_not_exists => TRUE);"))
                    logger.info("TimescaleDB 'vehicle_tracks' hypertable verified/created.")
                except Exception as e:
                    # 'already a hypertable' is fine
                    pass

                # 3. Create location_metrics table
                await conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS location_metrics (
                        location_id TEXT NOT NULL,
                        timestamp TIMESTAMPTZ NOT NULL,
                        vehicle_count INTEGER,
                        average_speed FLOAT,
                        congestion_score FLOAT,
                        latitude FLOAT,
                        longitude FLOAT
                    );
                """))

                # 4. Convert to hypertable
                try:
                    await conn.execute(text("SELECT create_hypertable('location_metrics', 'timestamp', if_not_exists => TRUE);"))
                    logger.info("TimescaleDB 'location_metrics' hypertable verified/created.")
                except Exception as e:
                    pass
                    
        except Exception as e:
            logger.error(f"Error creating TimescaleDB hypertables: {e}")

    def _get_sqlite_connection(self) -> sqlite3.Connection:
        if not self.sqlite_db_path:
            raise DatabaseError("SQLite database path not configured.")
        try:
            conn = sqlite3.connect(str(self.sqlite_db_path), timeout=10.0)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
            except sqlite3.Error as e:
                logger.warning(f"Could not set WAL mode on {self.sqlite_db_path}: {e}")
            return conn
        except sqlite3.Error as e:
            logger.error(
                f"Failed to connect to DB {self.sqlite_db_path}: {e}", exc_info=True
            )
            raise DatabaseError(f"DB connect fail: {e}") from e

    def _initialize_sqlite_database(self):
        if not self.sqlite_db_path:
            logger.error("Cannot initialize SQLite DB: path not set.")
            return
        logger.info(f"Initializing SQLite DB schema at {self.sqlite_db_path}...")
        try:
            with self._get_sqlite_connection() as conn:
                cursor = conn.cursor()
                self._create_sqlite_tables(cursor)
                self._migrate_sqlite_database(cursor)
                conn.commit()
            logger.info("SQLite DB schema initialization check complete.")
            
        except (sqlite3.Error, DatabaseError) as e:
            logger.error(f"DB init error in _initialize_sqlite_database: {e}", exc_info=True)
            raise DatabaseError(f"DB schema init fail: {e}") from e

    def _migrate_sqlite_database(self, cursor: sqlite3.Cursor):
        """Adds missing columns to existing tables if they don't exist."""
        # Check vehicle_tracks columns
        cursor.execute("PRAGMA table_info(vehicle_tracks)")
        columns = [row[1] for row in cursor.fetchall()]
        
        required_columns = [
            ("car_model", "TEXT"),
            ("car_model_confidence", "REAL"),
            ("car_color", "TEXT"),
            ("appearance_id", "TEXT"),
            ("reid_gallery", "BLOB")
        ]
        
        for col_name, col_type in required_columns:
            if col_name not in columns:
                logger.info(f"Adding missing column '{col_name}' to vehicle_tracks table.")
                try:
                    cursor.execute(f"ALTER TABLE vehicle_tracks ADD COLUMN {col_name} {col_type}")
                except sqlite3.Error as e:
                    logger.error(f"Failed to add column {col_name}: {e}")

    def _create_sqlite_tables(self, cursor: sqlite3.Cursor):
        logger.info("Calling _create_sqlite_tables")
        try:
            # ... (This method remains unchanged)
            cursor.execute("""CREATE TABLE IF NOT EXISTS vehicle_tracks (
                    feed_id TEXT NOT NULL, track_id INTEGER NOT NULL, timestamp REAL NOT NULL, 
                    global_vehicle_id TEXT, class_id INTEGER, confidence REAL,
                    bbox_x1 REAL, bbox_y1 REAL, bbox_x2 REAL, bbox_y2 REAL, center_x REAL, center_y REAL, speed REAL,
                    acceleration REAL, lane INTEGER, direction REAL, license_plate TEXT, ocr_confidence REAL, 
                    car_model TEXT, car_model_confidence REAL, car_color TEXT, flags TEXT,
                    PRIMARY KEY (feed_id, track_id, timestamp))""")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_vt_timestamp ON vehicle_tracks(timestamp DESC);"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_vt_global_id ON vehicle_tracks(global_vehicle_id);"
            )
            # S3: Compound index for cross-feed vehicle lookups
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_vt_global_timestamp ON vehicle_tracks(global_vehicle_id, timestamp DESC);"
            )
            cursor.execute("""CREATE TABLE IF NOT EXISTS identified_vehicles (
                    license_plate TEXT PRIMARY KEY,
                    appearance_id TEXT,
                    vehicle_type TEXT,
                    make TEXT,
                    model TEXT,
                    color TEXT,
                    first_seen REAL,
                    last_seen REAL,
                    total_detections INTEGER DEFAULT 1,
                    reid_gallery BLOB,
                    flags TEXT)""")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_iv_appearance ON identified_vehicles(appearance_id);"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_iv_last_seen ON identified_vehicles(last_seen DESC);"
            )
            cursor.execute("""CREATE TABLE IF NOT EXISTS reid_identities (
                    global_id TEXT PRIMARY KEY,
                    embeddings BLOB, -- Serialized numpy array
                    metadata TEXT,   -- JSON metadata
                    last_seen REAL NOT NULL
            )""")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_reid_last_seen ON reid_identities(last_seen DESC);")
            cursor.execute("""CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL, -- Store as Unix timestamp (float)
                    severity TEXT NOT NULL CHECK(severity IN ('INFO', 'WARNING', 'CRITICAL', 'ERROR')),
                    feed_id TEXT, -- Allow NULL for system alerts
                    
                    message TEXT NOT NULL, details TEXT, acknowledged INTEGER DEFAULT 0 NOT NULL CHECK(acknowledged IN (0, 1)))""")
            
            cursor.execute("""CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    feed_id TEXT,
                    type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    description TEXT,
                    status TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    latitude REAL,
                    longitude REAL,
                    snapshot_path TEXT,
                    assigned_to TEXT,
                    resolution_notes TEXT
            )""")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_timestamp ON incidents(timestamp DESC);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);")
            
            cursor.execute("""CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    action TEXT NOT NULL,
                    resource_type TEXT,
                    resource_id TEXT,
                    details TEXT,
                    ip_address TEXT,
                    timestamp REAL NOT NULL
            )""")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp DESC);")

            cursor.execute("""CREATE TABLE IF NOT EXISTS location_metrics (
                    location_id TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    vehicle_count INTEGER,
                    average_speed REAL,
                    congestion_score REAL,
                    latitude REAL,
                    longitude REAL,
                    PRIMARY KEY (location_id, timestamp))""")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_lm_timestamp ON location_metrics(timestamp DESC);")
            
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_vt_feed_timestamp ON vehicle_tracks(feed_id, timestamp DESC);")

            # ... and other table creation statements ...
            logger.debug("SQLite DB table creation check finished.")
        except Exception as e:
            logger.error(f"Error in _create_sqlite_tables: {e}")

    def _initialize_mongodb(self):
        """Initializes the MongoDB connection with a retry mechanism."""
        if not self.mongo_uri or not self.mongo_db_name:
            logger.info(
                "MongoDB URI or database name not configured. Skipping MongoDB initialization."
            )
            return

        @retry(
            wait=wait_exponential(multiplier=1, min=2, max=10),
            stop=stop_after_attempt(3),
            retry=retry_if_exception_type((ConnectionFailure, MongoConfigurationError, ServerSelectionTimeoutError)),
            reraise=True, # Reraise so we can catch it in the outer try-except
        )
        def connect_with_retry():
            logger.info(f"Attempting to connect to MongoDB at {self.mongo_uri}...")
            client = MongoClient(self.mongo_uri, serverSelectionTimeoutMS=5000)
            # The 'ismaster' command is a cheap way to verify the connection
            client.admin.command("ismaster")
            return client

        try:
            self.mongo_client = connect_with_retry()
            if self.mongo_client:
                self.mongo_db = self.mongo_client[self.mongo_db_name]
                logger.info(
                    f"Successfully connected to MongoDB server. Database: '{self.mongo_db_name}'"
                )
        except (RetryError, ConnectionFailure, ServerSelectionTimeoutError) as e:
            logger.warning(f"MongoDB connection failed (it may be down or unreachable): {e}")
            logger.info("MongoDB features will be disabled for this session.")
            self.mongo_client = None
            self.mongo_db = None
        except Exception as e:
            logger.error(
                f"Unexpected error during MongoDB initialization: {e}",
                exc_info=True,
            )
            self.mongo_client = None
            self.mongo_db = None

    def prune_old_data(self, config: Optional[Dict] = None, retention_days: int = 7) -> Dict[str, int]:
        """
        Prunes old records and files to reclaim space.
        If config is provided, it also prunes snapshots and hard negatives.
        """
        results = {"db_pruned": 0, "snapshots_pruned": 0, "hard_negatives_pruned": 0}
        cutoff_time = time.time() - (retention_days * 24 * 3600)
        
        # 1. Prune Database
        sql = "DELETE FROM vehicle_tracks WHERE timestamp < ?"
        try:
            with self.lock:
                with self._get_sqlite_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(sql, (cutoff_time,))
                    results["db_pruned"] = cursor.rowcount
                    conn.commit()
                    
                    # S4 Fix: Don't VACUUM inline — set flag for background scheduling
                    if results["db_pruned"] > 1000:
                        self._needs_vacuum = True
            
            if results["db_pruned"] > 0:
                logger.info(f"Pruned {results['db_pruned']} old records from vehicle_tracks.")
        except Exception as e:
            logger.error(f"Error pruning database records: {e}")

        # 2. Prune Files (if config provided)
        if config:
            data_dir = Path(config.get("data_dir", "backend/data"))
            
            # Prune Snapshots
            snap_dir = Path(config.get("snapshots_dir", data_dir / "snapshots"))
            if snap_dir.exists():
                snap_retention = config.get("snapshot_retention_days", 7)
                results["snapshots_pruned"] = self._prune_directory(snap_dir, snap_retention)
                
            # Prune Hard Negatives
            hn_dir = data_dir / "hard_negatives"
            if hn_dir.exists():
                hn_retention = config.get("hard_negative_retention_days", 3)
                results["hard_negatives_pruned"] = self._prune_directory(hn_dir, hn_retention)
                
        return results

    def _prune_directory(self, directory: Path, retention_days: int) -> int:
        """Helper to remove old files in a directory."""
        count = 0
        cutoff = time.time() - (retention_days * 24 * 3600)
        try:
            for item in directory.glob("**/*"):
                if item.is_file() and item.stat().st_mtime < cutoff:
                    try:
                        item.unlink()
                        count += 1
                    except Exception as e:
                        logger.warning(f"Failed to delete {item}: {e}")
            
            if count > 0:
                logger.info(f"Pruned {count} files from {directory} (older than {retention_days} days).")
        except Exception as e:
            logger.error(f"Error pruning directory {directory}: {e}")
        return count

    # --- Incident Management ---
    
    async def create_incident(self, incident_data: Dict) -> bool:
        """Creates a new incident record."""
        sql = """
        INSERT INTO incidents (
            id, feed_id, type, severity, description, status, timestamp, 
            created_at, updated_at, latitude, longitude, snapshot_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        try:
            params = (
                incident_data["id"],
                incident_data.get("feed_id"),
                incident_data["type"],
                incident_data["severity"],
                incident_data["description"],
                incident_data["status"],
                incident_data["timestamp"],
                incident_data["created_at"].isoformat(),
                incident_data["updated_at"].isoformat(),
                incident_data.get("latitude"),
                incident_data.get("longitude"),
                incident_data.get("snapshot_path")
            )
            await asyncio.to_thread(self._execute_write, sql, params)
            return True
        except Exception as e:
            logger.error(f"Error creating incident: {e}")
            return False

    async def update_incident(self, incident_id: str, updates: Dict) -> bool:
        """Updates an existing incident."""
        if not updates:
            return True
            
        set_clauses = []
        params = []
        
        for key, value in updates.items():
            set_clauses.append(f"{key} = ?")
            params.append(value)
            
        params.append(incident_id)
        sql = f"UPDATE incidents SET {', '.join(set_clauses)} WHERE id = ?"
        
        try:
            await asyncio.to_thread(self._execute_write, sql, tuple(params))
            return True
        except Exception as e:
            logger.error(f"Error updating incident {incident_id}: {e}")
            return False

    async def get_incidents(self, limit: int = 100, offset: int = 0, filters: Dict = None) -> List[Dict]:
        """Retrieves a list of incidents with optional filtering."""
        base_query = "SELECT * FROM incidents WHERE 1=1"
        params = []
        
        if filters:
            if filters.get("status"):
                base_query += " AND status = ?"
                params.append(filters["status"])
            if filters.get("severity"):
                base_query += " AND severity = ?"
                params.append(filters["severity"])
            if filters.get("type"):
                base_query += " AND type = ?"
                params.append(filters["type"])
                
        query = f"{base_query} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        try:
            return await asyncio.to_thread(self._execute_query, query, tuple(params))
        except Exception as e:
            logger.error(f"Error getting incidents: {e}")
            return []

    async def get_incident_by_id(self, incident_id: str) -> Optional[Dict]:
        query = "SELECT * FROM incidents WHERE id = ?"
        try:
            results = await asyncio.to_thread(self._execute_query, query, (incident_id,))
            return results[0] if results else None
        except Exception as e:
            logger.error(f"Error getting incident {incident_id}: {e}")
            return None

    def _validate_query(self, query: str, params: tuple):
        """S2 Fix: Validates that destructive queries use parameterized values.
        Parameterized queries with placeholders (?) are safe — SQL injection
        only happens when user input is concatenated into query strings."""
        destructive_keywords = ['DROP ', 'TRUNCATE ', 'ALTER TABLE']
        upper_query = query.upper()
        for keyword in destructive_keywords:
            if keyword in upper_query:
                logger.error(f"Blocked potentially destructive query: {keyword.strip()}")
                raise ValueError(f"Destructive SQL command detected: {keyword.strip()}")

    def _execute_write(self, sql: str, params: tuple):
        """Helper for synchronous write operations."""
        self._validate_query(sql, params)
        with self.lock:
            with self._get_sqlite_connection() as conn:
                conn.execute(sql, params)
                conn.commit()

    from sqlalchemy.exc import OperationalError as SAOperationalError
    db_write_retry_decorator = retry(
        wait=wait_exponential(multiplier=0.2, min=0.2, max=3),
        stop=stop_after_attempt(4),
        retry=retry_if_exception_type((sqlite3.OperationalError, SAOperationalError)),
    )

    @db_write_retry_decorator
    async def save_vehicle_data_batch(self, vehicle_data_list: List[Dict]) -> int:
        """Saves a batch of vehicle tracking data in a single transaction via aiosqlite."""
        if not vehicle_data_list:
            return 0
        
        sql = text("""INSERT INTO vehicle_tracks (
            feed_id, track_id, timestamp, global_vehicle_id, class_id, confidence,
            bbox_x1, bbox_y1, bbox_x2, bbox_y2, center_x, center_y,
            speed, acceleration, lane, direction, license_plate, ocr_confidence, 
            car_model, car_model_confidence, car_color, flags
        ) VALUES (
            :feed_id, :track_id, :timestamp, :global_vehicle_id, :class_id, :confidence,
            :bbox_x1, :bbox_y1, :bbox_x2, :bbox_y2, :center_x, :center_y,
            :speed, :acceleration, :lane, :direction, :license_plate, :ocr_confidence, 
            :car_model, :car_model_confidence, :car_color, :flags
        )
        ON CONFLICT(feed_id, track_id, timestamp) DO UPDATE SET
            global_vehicle_id = COALESCE(excluded.global_vehicle_id, vehicle_tracks.global_vehicle_id),
            class_id = excluded.class_id,
            confidence = excluded.confidence,
            bbox_x1 = excluded.bbox_x1, bbox_y1 = excluded.bbox_y1,
            bbox_x2 = excluded.bbox_x2, bbox_y2 = excluded.bbox_y2,
            center_x = excluded.center_x, center_y = excluded.center_y,
            speed = excluded.speed, acceleration = excluded.acceleration,
            lane = excluded.lane, direction = excluded.direction,
            license_plate = COALESCE(excluded.license_plate, vehicle_tracks.license_plate),
            ocr_confidence = COALESCE(excluded.ocr_confidence, vehicle_tracks.ocr_confidence),
            car_model = COALESCE(excluded.car_model, vehicle_tracks.car_model),
            car_model_confidence = COALESCE(excluded.car_model_confidence, vehicle_tracks.car_model_confidence),
            car_color = COALESCE(excluded.car_color, vehicle_tracks.car_color),
            flags = excluded.flags
        """)
        
        batch_params = []
        for vd in vehicle_data_list:
            bbox = vd.get("bbox", [None] * 4)
            center = vd.get("centroid") or vd.get("center") or [None] * 2
            flags_val = vd.get("flags", "")
            if isinstance(flags_val, (set, list)):
                flags_str = ",".join(sorted(list(flags_val)))
            else:
                flags_str = str(flags_val)

            params = {
                "feed_id": vd.get("feed_id", "unknown"),
                "track_id": vd.get("track_id") or vd.get("vehicle_id"),
                "timestamp": vd.get("timestamp", time.time()),
                "global_vehicle_id": vd.get("global_vehicle_id"),
                "class_id": vd.get("class_id"),
                "confidence": vd.get("confidence"),
                "bbox_x1": bbox[0], "bbox_y1": bbox[1], "bbox_x2": bbox[2], "bbox_y2": bbox[3],
                "center_x": center[0], "center_y": center[1],
                "speed": vd.get("speed"),
                "acceleration": vd.get("acceleration"),
                "lane": vd.get("lane"),
                "direction": vd.get("direction"),
                "license_plate": vd.get("license_plate"),
                "ocr_confidence": vd.get("ocr_confidence"),
                "car_model": vd.get("car_model"),
                "car_model_confidence": vd.get("car_model_confidence"),
                "car_color": vd.get("car_color"),
                "flags": flags_str,
            }
            batch_params.append(params)

        try:
            async with self.async_engine.begin() as conn:
                await conn.execute(sql, batch_params)
            
            # --- DUAL WRITE TO TIMESCALEDB ---
            if self.timescale_engine:
                asyncio.create_task(self._save_to_timescale_batch(vehicle_data_list))
                
            return len(batch_params)
        except Exception as e:
            logger.error(f"DB batch save failed: {e}")
            raise

    async def _save_to_timescale_batch(self, vehicle_data_list: List[Dict]):
        """Asynchronously saves a batch of data to TimescaleDB (S5: with retry)."""
        if not self.timescale_engine:
            return

        sql = text("""
            INSERT INTO vehicle_tracks (
                feed_id, track_id, timestamp, global_vehicle_id, class_id, 
                confidence, center_x, center_y, speed, lane, direction, license_plate
            ) VALUES (
                :feed_id, :track_id, :timestamp, :global_vehicle_id, :class_id,
                :confidence, :center_x, :center_y, :speed, :lane, :direction, :license_plate
            )
        """)
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with self.timescale_engine.begin() as conn:
                    params = []
                    for vd in vehicle_data_list:
                        center = vd.get("centroid") or vd.get("center") or [None, None]
                        ts = datetime.fromtimestamp(vd.get("timestamp", time.time()), tz=timezone.utc)
                        
                        params.append({
                            "feed_id": vd.get("feed_id", "unknown"),
                            "track_id": str(vd.get("track_id") or vd.get("vehicle_id")),
                            "timestamp": ts,
                            "global_vehicle_id": vd.get("global_vehicle_id"),
                            "class_id": vd.get("class_id"),
                            "confidence": vd.get("confidence"),
                            "center_x": center[0],
                            "center_y": center[1],
                            "speed": vd.get("speed"),
                            "lane": vd.get("lane"),
                            "direction": vd.get("direction"),
                            "license_plate": vd.get("license_plate")
                        })
                    
                    await conn.execute(sql, params)
                return  # Success, exit retry loop
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"TimescaleDB batch save attempt {attempt + 1} failed: {e}. Retrying...")
                    await asyncio.sleep(0.5 * (attempt + 1))  # Exponential backoff
                else:
                    logger.error(f"TimescaleDB batch save failed after {max_retries} attempts: {e}")

    def _get_location_key(self, latitude: float, longitude: float) -> str:
        """Create a unique key for a location, rounding to 4 decimal places for nearby grouping"""
        return f"{round(latitude, 4)},{round(longitude, 4)}"

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type((sqlite3.OperationalError, SQLAlchemyOperationalError)),
        reraise=True
    )
    async def save_location_metrics_batch(self, metrics_list: List[Dict]):
        """Saves a batch of aggregated location metrics to SQLite and TimescaleDB."""
        if not metrics_list:
            return

        # --- 1. SAVE TO SQLITE ---
        sql = text("""INSERT OR REPLACE INTO location_metrics (
            location_id, timestamp, vehicle_count, average_speed, 
            congestion_score, latitude, longitude
        ) VALUES (
            :location_id, :timestamp, :vehicle_count, :average_speed, 
            :congestion_score, :latitude, :longitude
        )""")

        sqlite_params = []
        for m in metrics_list:
            ts = m.get("timestamp")
            if isinstance(ts, datetime):
                ts = ts.timestamp()
            elif isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    ts = time.time()

            sqlite_params.append({
                "location_id": m.get("id"),
                "timestamp": ts,
                "vehicle_count": m.get("vehicle_count"),
                "average_speed": m.get("average_speed"),
                "congestion_score": m.get("congestion_score"),
                "latitude": m.get("latitude"),
                "longitude": m.get("longitude")
            })

        try:
            async with self.async_engine.begin() as conn:
                await conn.execute(sql, sqlite_params)
            logger.info(f"Saved {len(sqlite_params)} location metrics to SQLite via async_engine.")
        except (sqlite3.OperationalError, SQLAlchemyOperationalError) as e:
            logger.warning(f"Retrying: Failed to save location metrics to SQLite: {e}")
            raise # RE-RAISE so @retry can catch it
        except Exception as e:
            logger.error(f"Unexpected error saving location metrics to SQLite: {e}")
            # Don't re-raise generic exceptions unless you want them to trigger retries too

        # --- 2. SAVE TO TIMESCALEDB ---
        if not self.timescale_engine:
            return

        ts_sql = text("""
            INSERT INTO location_metrics (
                location_id, timestamp, vehicle_count, average_speed, 
                congestion_score, latitude, longitude
            ) VALUES (
                :location_id, :timestamp, :vehicle_count, :average_speed,
                :congestion_score, :latitude, :longitude
            )
        """)

        try:
            async with self.timescale_engine.begin() as conn:
                ts_params = []
                for m in metrics_list:
                    ts = m.get("timestamp")
                    if isinstance(ts, (int, float)):
                        ts = datetime.fromtimestamp(ts, tz=timezone.utc)
                    elif isinstance(ts, str):
                        try:
                            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        except ValueError:
                            ts = datetime.now(timezone.utc)

                    ts_params.append({
                        "location_id": m.get("id"),
                        "timestamp": ts,
                        "vehicle_count": m.get("vehicle_count"),
                        "average_speed": m.get("average_speed"),
                        "congestion_score": m.get("congestion_score"),
                        "latitude": m.get("latitude"),
                        "longitude": m.get("longitude")
                    })

                await conn.execute(ts_sql, ts_params)
                logger.info(f"Saved {len(ts_params)} location metrics to TimescaleDB.")
        except Exception as e:
            logger.error(f"Failed to save location metrics to TimescaleDB: {e}")

    async def get_location_metrics(self, location_id: str, hours: int = 24) -> List[Dict]:
        """Retrieves historical location metrics from TimescaleDB (if available) or SQLite."""
        start_time_ts = time.time() - (hours * 3600)

        # 1. Try TimescaleDB
        if self.timescale_engine:
            start_time_dt = datetime.now(timezone.utc) - timedelta(hours=hours)
            sql = text("""
                SELECT * FROM location_metrics 
                WHERE location_id = :location_id 
                AND timestamp >= :start_time
                ORDER BY timestamp ASC
            """)

            try:
                async with self.timescale_engine.connect() as conn:
                    result = await conn.execute(sql, {"location_id": location_id, "start_time": start_time_dt})
                    rows = [dict(row._mapping) for row in result]
                    if rows:
                        return rows
            except Exception as e:
                logger.error(f"Failed to query location metrics from TimescaleDB: {e}")

        # 2. Try SQLite pre-aggregated metrics
        sql = """
            SELECT timestamp, vehicle_count, average_speed, congestion_score
            FROM location_metrics
            WHERE location_id = ? AND timestamp >= ?
            ORDER BY timestamp ASC
        """
        try:
            # FIX: Use asyncio.to_thread for queries too if they might be slow or block on lock
            rows = await asyncio.to_thread(self._execute_query, sql, (location_id, start_time_ts))
            if rows:
                for row in rows:
                    if isinstance(row["timestamp"], (int, float)):
                        row["timestamp"] = datetime.fromtimestamp(row["timestamp"], tz=timezone.utc).isoformat()
                return rows
        except Exception as e:
            logger.error(f"Failed to query location metrics from SQLite: {e}")

        return []

    async def get_history_stats(self, feed_id: str, hours: int = 24, latitude: Optional[float] = None, longitude: Optional[float] = None) -> List[Dict]:
        """
        Retrieves historical statistics (vehicle count, speed).
        Tries TimescaleDB/SQLite 'location_metrics' first.
        Falls back to aggregating 'vehicle_tracks' in SQLite.
        """
        # 1. Try pre-aggregated metrics first (Fastest)
        try:
            # First try with feed_id directly as location_id
            metrics = await self.get_location_metrics(feed_id, hours)

            # Fallback: AnalyticsService uses coordinate-based keys. Try that too.
            if not metrics and latitude is not None and longitude is not None:
                coord_id = self._get_location_key(latitude, longitude)
                if coord_id != feed_id:
                    logger.info(f"Retrying history fetch with coordinate-based ID: {coord_id}")
                    metrics = await self.get_location_metrics(coord_id, hours)

            if metrics:
                # Map to format expected by dashboard
                return [{
                    "timestamp": m.get("timestamp"),
                    "vehicle_count": m.get("vehicle_count"),
                    "average_speed": m.get("average_speed"),
                    "congestion_score": m.get("congestion_score", 0.0)
                } for m in metrics]
        except Exception as e:
            logger.warning(f"Pre-aggregated history fetch failed: {e}")

        # 2. Fallback to SQLite Aggregation (SLOW on large tables)
        if not self.sqlite_db_path:
             return []

        # Calculate start timestamp
        start_ts = time.time() - (hours * 3600)

        # Optimization: Use COUNT(track_id) instead of COUNT(DISTINCT track_id)
        # In this context (per-hour buckets), the difference is usually negligible 
        # but the performance gain is massive because SQLite doesn't need a temporary table for distinct values.
        sql = """
            SELECT 
                cast(timestamp / 3600 as int) * 3600 as time_bucket, -- Group by hour
                count(track_id) / 3600.0 as vehicle_count, -- Approximate count per second * 3600 (actually just counts update rows)
                avg(speed) as average_speed
            FROM vehicle_tracks
            WHERE feed_id = ? AND timestamp >= ?
            GROUP BY time_bucket
            ORDER BY time_bucket ASC
        """
        # Better query for unique vehicles:
        sql_refined = """
            SELECT 
                cast(timestamp / 3600 as int) * 3600 as time_bucket,
                count(distinct track_id) as vehicle_count,
                avg(speed) as average_speed
            FROM vehicle_tracks
            WHERE feed_id = ? AND timestamp >= ?
            GROUP BY time_bucket
            ORDER BY time_bucket ASC
        """
        # We'll use the refined one but with a strict timeout

        try:
            # Use a smaller timeout for the aggregation fallback to avoid 500 errors
            # Reduced from 15s to 5s to ensure event loop remains responsive and proxy doesn't timeout
            return await asyncio.wait_for(
                asyncio.to_thread(self._execute_history_query, sql_refined, (feed_id, start_ts)),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.warning(f"History aggregation timed out for {feed_id} after 5s")
            return []
        except Exception as e:
            logger.error(f"SQLite aggregation failed: {e}")
            return []
    def _execute_history_query(self, sql: str, params: tuple) -> List[Dict]:
        results = []
        with self.lock:
            with self._get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
                rows = cursor.fetchall()
                
                for row in rows:
                    ts = row[0]
                    count = row[1]
                    speed = row[2] if row[2] is not None else 0.0
                    
                    results.append({
                        "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                        "vehicle_count": count,
                        "average_speed": speed,
                        "congestion_score": 0.0
                    })
        return results

    @db_write_retry_decorator
    def save_vehicle_data(self, vd: Dict) -> bool:
        # ... (This method remains unchanged)
        sql = """INSERT OR REPLACE INTO vehicle_tracks (
            feed_id,track_id,timestamp,class_id,confidence,bbox_x1,bbox_y1,bbox_x2,bbox_y2,center_x,center_y,
            speed,acceleration,lane,direction,license_plate,ocr_confidence, car_model, car_model_confidence, car_color, flags
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
        try:
            bbox = vd.get("bbox", [None] * 4)
            center = vd.get("center", [None] * 2)
            flags_str = ",".join(sorted(list(vd.get("flags", set()))))
            params = (
                vd.get("feed_id", "unknown"),
                vd.get("track_id"),
                vd.get("timestamp", time.time()),
                vd.get("class_id"),
                vd.get("confidence"),
                bbox[0],
                bbox[1],
                bbox[2],
                bbox[3],
                center[0],
                center[1],
                vd.get("speed"),
                vd.get("acceleration"),
                vd.get("lane"),
                vd.get("direction"),
                vd.get("license_plate"),
                vd.get("ocr_confidence"),
                vd.get("car_model"),
                vd.get("car_model_confidence"),
                vd.get("car_color"),
                flags_str,
            )
            with self.lock:
                with self._get_sqlite_connection() as conn:
                    conn.execute(sql, params)
            return True
        except RetryError as e:
            logger.error(
                f"DB save_vehicle_data failed retries: {e}. TrackID: {vd.get('track_id')}"
            )
            return False
        except sqlite3.Error as e:
            logger.error(
                f"DB error saving vehicle: {e} - TrackID: {vd.get('track_id')}",
                exc_info=True,
            )
            if isinstance(e, sqlite3.OperationalError):
                raise  # Re-raise to be caught by tenacity
            else:
                raise DatabaseError(f"Failed save vehicle: {e}") from e

    @db_write_retry_decorator
    async def upsert_identified_vehicle(self, vehicle_data: Dict) -> bool:
        """Upserts a vehicle identification record based on license plate."""
        sql = text("""
        INSERT INTO identified_vehicles (
            license_plate, appearance_id, vehicle_type, make, model, color, 
            first_seen, last_seen, reid_gallery, flags, total_detections
        ) VALUES (
            :license_plate, :appearance_id, :vehicle_type, :make, :model, :color, 
            :first_seen, :last_seen, :reid_gallery, :flags, 1
        )
        ON CONFLICT(license_plate) DO UPDATE SET
            appearance_id = COALESCE(excluded.appearance_id, identified_vehicles.appearance_id),
            vehicle_type = COALESCE(excluded.vehicle_type, identified_vehicles.vehicle_type),
            make = COALESCE(excluded.make, identified_vehicles.make),
            model = COALESCE(excluded.model, identified_vehicles.model),
            color = COALESCE(excluded.color, identified_vehicles.color),
            reid_gallery = COALESCE(excluded.reid_gallery, identified_vehicles.reid_gallery),
            last_seen = excluded.last_seen,
            total_detections = identified_vehicles.total_detections + 1,
            flags = COALESCE(excluded.flags, identified_vehicles.flags)
        """)
        try:
            lp = vehicle_data.get("license_plate")
            if not lp or lp == "Unknown":
                return False
                
            gallery_blob = None
            gallery = vehicle_data.get("embedding_gallery")
            if gallery:
                try:
                    import numpy as np
                    gallery_blob = np.array(gallery, dtype=np.float32).tobytes()
                except Exception as e:
                    logger.warning(f"Failed to serialize ReID gallery for {lp}: {e}")

            now = vehicle_data.get("timestamp", time.time())
            params = {
                "license_plate": lp,
                "appearance_id": vehicle_data.get("appearance_id") or vehicle_data.get("global_vehicle_id"),
                "vehicle_type": vehicle_data.get("vehicle_type"),
                "make": vehicle_data.get("make"),
                "model": vehicle_data.get("model"),
                "color": vehicle_data.get("color"),
                "first_seen": now,
                "last_seen": now,
                "reid_gallery": gallery_blob,
                "flags": vehicle_data.get("flags")
            }
            
            async with self.async_engine.begin() as conn:
                await conn.execute(sql, params)
            return True
        except Exception as e:
            logger.error(f"Error upserting identified vehicle {vehicle_data.get('license_plate')}: {e}")
            return False

    @db_write_retry_decorator
    def save_reid_identity(self, global_id: str, embeddings: Any, metadata: Dict, last_seen: float) -> bool:
        """Saves a ReID identity with its gallery of embeddings."""
        sql = """
        INSERT INTO reid_identities (global_id, embeddings, metadata, last_seen)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(global_id) DO UPDATE SET
            embeddings = excluded.embeddings,
            metadata = excluded.metadata,
            last_seen = excluded.last_seen
        """
        try:
            import numpy as np
            if isinstance(embeddings, np.ndarray):
                emb_bytes = embeddings.tobytes()
            elif isinstance(embeddings, (list, tuple)):
                emb_bytes = np.array(embeddings, dtype=np.float32).tobytes()
            else:
                emb_bytes = bytes(embeddings)
                
            params = (
                global_id,
                emb_bytes,
                json.dumps(metadata),
                last_seen
            )
            with self.lock:
                with self._get_sqlite_connection() as conn:
                    conn.execute(sql, params)
            return True
        except Exception as e:
            logger.error(f"Error saving ReID identity {global_id}: {e}")
            return False

    def get_recent_reid_identities(self, limit: int = 1000) -> List[Dict]:
        """Retrieves recent ReID identities for warm start."""
        sql = "SELECT * FROM reid_identities ORDER BY last_seen DESC LIMIT ?"
        try:
            results = []
            with self.lock:
                with self._get_sqlite_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(sql, (limit,))
                    rows = cursor.fetchall()
                    for row in rows:
                        results.append({
                            "global_id": row["global_id"],
                            "embeddings": row["embeddings"], # bytes
                            "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                            "last_seen": row["last_seen"]
                        })
            return results
        except Exception as e:
            logger.error(f"Error getting recent ReID identities: {e}")
            return []

    async def get_reid_identity(self, global_id: str) -> Optional[Dict]:
        """Retrieves a specific ReID identity by global_id."""
        sql = "SELECT * FROM reid_identities WHERE global_id = ?"
        try:
            results = await asyncio.to_thread(self._execute_query, sql, (global_id,))
            if results:
                row = results[0]
                return {
                    "global_id": row["global_id"],
                    "embeddings": row["embeddings"], # bytes
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                    "last_seen": row["last_seen"]
                }
            return None
        except Exception as e:
            logger.error(f"Error getting ReID identity {global_id}: {e}")
            return None

    async def save_alert(self, alert: Alert):
        """
        Saves an Alert object to the SQLite database.
        """
        logger.info(f"Saving alert with severity {alert.severity} to database.")
        sql = """INSERT INTO alerts (timestamp, severity, feed_id, message, latitude, longitude, details, acknowledged, acknowledged_by, acknowledged_at, source_component, tags)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

        try:
            # Convert timestamp to Unix timestamp (float)
            timestamp_float = alert.timestamp.timestamp() if alert.timestamp else None
            acknowledged_at_float = alert.acknowledged_at.timestamp() if alert.acknowledged_at else None

            # Serialize details and tags to JSON strings
            details_json = json.dumps(alert.details) if alert.details is not None else None
            tags_json = json.dumps(alert.tags) if alert.tags is not None else None

            params = (
                timestamp_float,
                alert.severity.value, # Use the enum value
                alert.feed_id,
                alert.message,
                alert.latitude,
                alert.longitude,
                details_json,
                int(alert.acknowledged), # Convert boolean to integer
                alert.acknowledged_by,
                acknowledged_at_float,
                alert.source_component,
                tags_json,
            )
            log_id = await asyncio.to_thread(self._execute_save_alert, sql, params)
            logger.info(f"Alert saved successfully with log_id: {log_id}")
            return log_id
        except Exception as e:
            logger.error(f"Error saving alert to database: {e}", exc_info=True)
            raise DatabaseError(f"Failed to save alert: {e}") from e
    # ... (all your other synchronous and asynchronous database methods like get_alerts_filtered,
    #      save_alert, acknowledge_alert, get_raw_traffic_data_mongo, etc., remain here unchanged)
    async def insert_alerts(self, alerts: List[Dict]):
        """Inserts a batch of alerts into the database."""
        sql = """INSERT INTO alerts (timestamp, severity, feed_id, message, details, acknowledged)
                 VALUES (?, ?, ?, ?, ?, ?)"""
        params = []
        for alert in alerts:
            params.append((
                alert.get("timestamp", time.time()),
                alert.get("severity", "INFO"),
                alert.get("feed_id"),
                alert.get("message", ""),
                json.dumps(alert.get("details", {})),
                0
            ))
        
        try:
            await asyncio.to_thread(self._execute_write_many, sql, params)
        except Exception as e:
            logger.error(f"Error inserting alerts batch: {e}")
            raise DatabaseError(f"Failed to insert alerts batch: {e}") from e

    def _execute_write_many(self, sql: str, params: List[tuple]):
        """Helper for synchronous write many operations."""
        self._validate_query(sql, ())
        with self.lock:
            with self._get_sqlite_connection() as conn:
                conn.executemany(sql, params)
                conn.commit()

    async def get_alerts_filtered(
        self, filters: Dict, limit: int = 100, offset: int = 0
    ) -> List[Dict]:
        try:
            return await asyncio.to_thread(
                self._execute_get_alerts_filtered, filters, limit, offset
            )
        except sqlite3.Error as e:
            logger.error(f"DB error get_alerts_filtered: {e}", exc_info=True)
            return []
        except Exception as e:
            logger.error(
                f"Unexpected error in get_alerts_filtered via thread: {e}",
                exc_info=True,
            )
            raise DatabaseError(f"Failed to get alerts: {e}") from e
            return []

    def _execute_get_alerts_filtered(
        self, filters: Dict, limit: int, offset: int
    ) -> List[Dict]:
        # ... (implementation unchanged)
        base_q = "SELECT id, timestamp, severity, feed_id, message, latitude, longitude, details, acknowledged, acknowledged_by, acknowledged_at, source_component, tags FROM alerts WHERE 1=1"
        params = []
        conds = []
        if filters.get("acknowledged") is not None:
            conds.append("acknowledged = ?")
            params.append(1 if filters["acknowledged"] else 0)
        # Add other filters based on your needs, similar to below
        if filters.get("severity_in") is not None and isinstance(filters["severity_in"], list):
             placeholders = ','.join('?' * len(filters["severity_in"]))
             conds.append(f"severity IN ({placeholders})")
             params.extend(filters["severity_in"])

        if filters.get("latitude") is not None and filters.get("longitude") is not None and filters.get("radius_km") is not None:
             # This is a very simplified bounding box approach; not a true radius
             # For simplicity, assuming filters['radius_km'] is used to create a bounding box            
             delta_lat = filters["radius_km"] / 111.0 # Approx degrees per km latitude
             delta_lon = filters["radius_km"] / (111.0 * abs(math.cos(math.radians(filters["latitude"])))) # Approx degrees per km longitude
             min_lat = filters["latitude"] - delta_lat
             max_lat = filters["latitude"] + delta_lat
             min_lon = filters["longitude"] - delta_lon
             max_lon = filters["longitude"] + delta_lon
             conds.append("(latitude BETWEEN ? AND ?) AND (longitude BETWEEN ? AND ?)")
             params.extend([min_lat, max_lat, min_lon, max_lon])
        # ... other filters
        if conds:
            base_q += " AND " + " AND ".join(conds)
        query = f"{base_q} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._get_sqlite_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def _execute_save_alert(self, sql: str, params: tuple):
        """Synchronous execution of saving an alert."""
        self._validate_query(sql, params)
        try:
            with self.lock:
                with self._get_sqlite_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(sql, params)
                    conn.commit()
        except sqlite3.Error as e:
            logger.error(f"SQLite error during _execute_save_alert: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Error during _execute_save_alert: {e}", exc_info=True)
            raise

    async def get_vehicle_tracks(self, limit: int = 500, offset: int = 0, filters: Dict = None) -> List[Dict]:
        """Returns raw vehicle tracking data with optional filtering."""
        base_query = "SELECT * FROM vehicle_tracks WHERE 1=1"
        params = []
        
        if filters:
            if filters.get("feed_id"):
                base_query += " AND feed_id = ?"
                params.append(filters["feed_id"])
            if filters.get("license_plate"):
                base_query += " AND license_plate = ?"
                params.append(filters["license_plate"])
            if filters.get("class_id"):
                base_query += " AND class_id = ?"
                params.append(filters["class_id"])
            if filters.get("start_time"):
                base_query += " AND timestamp >= ?"
                params.append(filters["start_time"])
            if filters.get("end_time"):
                base_query += " AND timestamp <= ?"
                params.append(filters["end_time"])

        query = f"{base_query} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        try:
            return await asyncio.to_thread(self._execute_query, query, tuple(params))
        except Exception as e:
            logger.error(f"Error querying vehicle tracks: {e}")
            return []

    async def get_identified_vehicles(self, limit: int = 100, offset: int = 0, filters: Dict = None) -> List[Dict]:
        """Returns a list of identified vehicles with optional filtering."""
        base_query = "SELECT * FROM identified_vehicles WHERE 1=1"
        params = []
        
        if filters:
            if filters.get("make"):
                base_query += " AND make = ?"
                params.append(filters["make"])
            if filters.get("model"):
                base_query += " AND model = ?"
                params.append(filters["model"])
            if filters.get("vehicle_type"):
                base_query += " AND vehicle_type = ?"
                params.append(filters["vehicle_type"])
        
        query = f"{base_query} ORDER BY last_seen DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        try:
            return await asyncio.to_thread(self._execute_query, query, tuple(params))
        except Exception as e:
            logger.error(f"Error querying identified vehicles: {e}")
            return []

    async def get_vehicle_by_plate(self, license_plate: str) -> Optional[Dict]:
        """Returns a single vehicle record by license plate."""
        query = "SELECT * FROM identified_vehicles WHERE license_plate = ?"
        try:
            results = await asyncio.to_thread(self._execute_query, query, (license_plate,))
            return results[0] if results else None
        except Exception as e:
            logger.error(f"Error querying vehicle by plate {license_plate}: {e}")
            return None

    async def get_vehicle_global_history(self, global_id: str) -> List[Dict]:
        """Returns the history of a vehicle across all feeds using its global ID."""
        query = """
        SELECT feed_id, track_id, timestamp, class_id, confidence, speed, lane, direction, license_plate, car_model
        FROM vehicle_tracks 
        WHERE global_vehicle_id = ?
        ORDER BY timestamp ASC
        """
        try:
            return await asyncio.to_thread(self._execute_query, query, (global_id,))
        except Exception as e:
            logger.error(f"Error querying vehicle global history {global_id}: {e}")
            return []

    async def list_global_vehicles(self, limit: int = 100) -> List[Dict]:
        """Returns a list of unique global vehicle IDs seen recently."""
        query = """
        SELECT global_vehicle_id, MAX(timestamp) as last_seen, COUNT(DISTINCT feed_id) as feeds_count
        FROM vehicle_tracks
        WHERE global_vehicle_id IS NOT NULL
        GROUP BY global_vehicle_id
        ORDER BY last_seen DESC
        LIMIT ?
        """
        try:
            return await asyncio.to_thread(self._execute_query, query, (limit,))
        except Exception as e:
            logger.error(f"Error listing global vehicles: {e}")
            return []

    async def record_prediction_log(self, log_data: Dict) -> Optional[str]:
        """Records a new prediction log entry."""
        import uuid
        log_id = str(uuid.uuid4())
        sql = """
        INSERT INTO prediction_logs (
            id, prediction_made_at, location_name, location_latitude, location_longitude,
            predicted_event_start_time, predicted_event_end_time, prediction_type,
            predicted_value, source_of_prediction, outcome_verified
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """
        try:
            params = (
                log_id,
                datetime.now(timezone.utc).isoformat(),
                log_data.get("location_name"),
                log_data["location_latitude"],
                log_data["location_longitude"],
                log_data["predicted_event_start_time"].isoformat(),
                log_data["predicted_event_end_time"].isoformat(),
                log_data["prediction_type"],
                json.dumps(log_data["predicted_value"]),
                log_data["source_of_prediction"]
            )
            await asyncio.to_thread(self._execute_write, sql, params)
            return log_id
        except Exception as e:
            logger.error(f"Error recording prediction log: {e}")
            return None

    async def update_prediction_log(self, log_id: str, updates: Dict) -> bool:
        """Updates an existing prediction log entry (e.g. with outcomes)."""
        if not updates:
            return True
        
        set_clauses = []
        params = []
        for key, value in updates.items():
            set_clauses.append(f"{key} = ?")
            if isinstance(value, (dict, list)):
                params.append(json.dumps(value))
            elif isinstance(value, datetime):
                params.append(value.isoformat())
            else:
                params.append(value)
        
        params.append(log_id)
        sql = f"UPDATE prediction_logs SET {', '.join(set_clauses)} WHERE id = ?"
        
        try:
            await asyncio.to_thread(self._execute_write, sql, tuple(params))
            return True
        except Exception as e:
            logger.error(f"Error updating prediction log {log_id}: {e}")
            return False

    def _execute_query(self, query: str, params: tuple) -> List[Dict]:
        """Helper for synchronous query execution with numeric sanitization."""
        self._validate_query(query, params)
        import struct
        with self._get_sqlite_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            results = []
            for row in rows:
                d = dict(row)
                # Sanitize: Convert any bytes to numbers if they seem to be blobs from numpy
                # This is necessary for legacy data stored before adapters were registered.
                for k, v in d.items():
                    if isinstance(v, bytes):
                        # Heuristic: try to parse as float/int based on length
                        try:
                            if len(v) == 8: # double (REAL in SQLite)
                                d[k] = struct.unpack('d', v)[0]
                            elif len(v) == 4: # float or int
                                # Try int for specific columns, float for others
                                if k in ['class_id', 'track_id', 'lane', 'frame_index']:
                                    d[k] = struct.unpack('i', v)[0]
                                else:
                                    d[k] = struct.unpack('f', v)[0]
                        except Exception as e:
                            logger.warning(f"Failed to sanitize binary blob in column {k}: {e}")
                results.append(d)
            return results

    async def get_pool_stats(self) -> Dict[str, Any]:
        """Returns statistics about the database connection pools."""
        stats = {
            "sqlite": {"size": 0, "checked_in": 0, "checked_out": 0, "overflow": 0},
            "timescale": {"size": 0, "checked_in": 0, "checked_out": 0, "overflow": 0}
        }
        
        if self.async_engine:
            pool = self.async_engine.pool
            stats["sqlite"] = {
                "size": pool.size(),
                "checked_in": pool.checkedin(),
                "checked_out": pool.checkedout(),
                "overflow": pool.overflow() if hasattr(pool, 'overflow') else 0
            }
            
        if self.timescale_engine:
            pool = self.timescale_engine.pool
            stats["timescale"] = {
                "size": pool.size(),
                "checked_in": pool.checkedin(),
                "checked_out": pool.checkedout(),
                "overflow": pool.overflow() if hasattr(pool, 'overflow') else 0
            }
            
        return stats

    async def close(self):
        logger.info("DatabaseManager close called.")
        if self.async_engine:
            try:
                await self.async_engine.dispose()
                logger.info("Async SQLAlchemy engine disposed.")
            except Exception as e:
                logger.error(
                    f"Error disposing async SQLAlchemy engine: {e}", exc_info=True
                )
            finally:
                self.async_engine = None
                self.async_session_factory = None

        if self.mongo_client:
            try:
                self.mongo_client.close()
                logger.info("MongoDB client connection closed.")
            except Exception as e:
                logger.error(f"Error closing MongoDB client: {e}", exc_info=True)
            finally:
                self.mongo_client = None
                self.mongo_db = None
        else:
            logger.info("MongoDB client was not initialized or already closed.")
