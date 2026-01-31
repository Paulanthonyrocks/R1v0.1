# backend/app/utils/database.py

import asyncio
import sqlite3
import threading
import logging
import time
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
            self.async_engine = create_async_engine(
                f"sqlite+aiosqlite:///{self.sqlite_db_path}"
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

            self.sqlite_db_path = path_obj.resolve()

            if not self.sqlite_db_path.parent.exists():
                try:
                    self.sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
                    logger.info(
                        f"Created database directory: {self.sqlite_db_path.parent}"
                    )
                except OSError as e:
                    raise ConfigError(
                        f"Failed to create database directory {self.sqlite_db_path.parent}: {e}"
                    ) from e

            logger.info(f"SQLite database path configured to: {self.sqlite_db_path}")

            timescale_config = config.get("timescaledb", {})
            if timescale_config.get("enabled", False):
                self.timescale_url = timescale_config.get("url", "postgresql+asyncpg://postgres:password@localhost:5432/traffic_hub")
                logger.info("TimescaleDB (PostgreSQL) configured.")
            else:
                self.timescale_url = None

            mongo_config = config.get("mongodb") or {}
            if mongo_config.get("uri") and mongo_config.get("database_name"):
                self.mongo_uri = mongo_config["uri"]
                
                # Auto-adjust if in Docker and uri points to localhost
                import os
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
            
            # Auto-prune on startup
            self.prune_old_data()
            
        except (sqlite3.Error, DatabaseError) as e:
            logger.error(f"DB init error: {e}", exc_info=True)
            raise DatabaseError(f"DB schema init fail: {e}") from e

    def _migrate_sqlite_database(self, cursor: sqlite3.Cursor):
        """Adds missing columns to existing tables if they don't exist."""
        # Check vehicle_tracks columns
        cursor.execute("PRAGMA table_info(vehicle_tracks)")
        columns = [row[1] for row in cursor.fetchall()]
        
        required_columns = [
            ("car_model", "TEXT"),
            ("car_model_confidence", "REAL"),
            ("car_color", "TEXT")
        ]
        
        for col_name, col_type in required_columns:
            if col_name not in columns:
                logger.info(f"Adding missing column '{col_name}' to vehicle_tracks table.")
                try:
                    cursor.execute(f"ALTER TABLE vehicle_tracks ADD COLUMN {col_name} {col_type}")
                except sqlite3.Error as e:
                    logger.error(f"Failed to add column {col_name}: {e}")

    def _create_sqlite_tables(self, cursor: sqlite3.Cursor):
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
        cursor.execute("""CREATE TABLE IF NOT EXISTS identified_vehicles (
                license_plate TEXT PRIMARY KEY,
                vehicle_type TEXT,
                make TEXT,
                model TEXT,
                color TEXT,
                first_seen REAL,
                last_seen REAL,
                total_detections INTEGER DEFAULT 1,
                flags TEXT)""")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_iv_last_seen ON identified_vehicles(last_seen DESC);"
        )
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

        # ... and other table creation statements ...
        logger.debug("SQLite DB table creation check finished.")

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

    def prune_old_data(self, retention_days: int = 7) -> int:
        """Prunes vehicle tracks older than the specified number of days."""
        cutoff_time = time.time() - (retention_days * 24 * 3600)
        sql = "DELETE FROM vehicle_tracks WHERE timestamp < ?"
        try:
            with self.lock:
                with self._get_sqlite_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(sql, (cutoff_time,))
                    deleted_count = cursor.rowcount
                    conn.commit()
            if deleted_count > 0:
                logger.info(f"Pruned {deleted_count} old records from vehicle_tracks (older than {retention_days} days).")
            return deleted_count
        except Exception as e:
            logger.error(f"Error pruning old data: {e}")
            return 0

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
        """Validates query for potential SQL injection risks."""
        unsafe_keywords = ['DROP ', 'TRUNCATE ', 'DELETE FROM', 'ALTER TABLE']
        upper_query = query.upper()
        if any(keyword in upper_query for keyword in unsafe_keywords):
            if not params:
                logger.error(f"Potentially unsafe query without parameters: {query}")
                raise ValueError("Unsafe query detected: potentially destructive command without parameters")

    def _execute_write(self, sql: str, params: tuple):
        """Helper for synchronous write operations."""
        self._validate_query(sql, params)
        with self.lock:
            with self._get_sqlite_connection() as conn:
                conn.execute(sql, params)
                conn.commit()

    db_write_retry_decorator = retry(
        wait=wait_exponential(multiplier=0.2, min=0.2, max=3),
        stop=stop_after_attempt(4),
        retry=retry_if_exception_type(sqlite3.OperationalError),
    )

    @db_write_retry_decorator
    def save_vehicle_data_batch(self, vehicle_data_list: List[Dict]) -> int:
        """Saves a batch of vehicle tracking data in a single transaction."""
        if not vehicle_data_list:
            return 0
        
        sql = """INSERT OR REPLACE INTO vehicle_tracks (
            feed_id, track_id, timestamp, global_vehicle_id, class_id, confidence,
            bbox_x1, bbox_y1, bbox_x2, bbox_y2, center_x, center_y,
            speed, acceleration, lane, direction, license_plate, ocr_confidence, 
            car_model, car_model_confidence, car_color, flags
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
        
        batch_params = []
        for vd in vehicle_data_list:
            bbox = vd.get("bbox", [None] * 4)
            center = vd.get("centroid") or vd.get("center") or [None] * 2
            flags_val = vd.get("flags", "")
            if isinstance(flags_val, (set, list)):
                flags_str = ",".join(sorted(list(flags_val)))
            else:
                flags_str = str(flags_val)

            params = (
                vd.get("feed_id", "unknown"),
                vd.get("track_id") or vd.get("vehicle_id"),
                vd.get("timestamp", time.time()),
                vd.get("global_vehicle_id"),
                vd.get("class_id"),
                vd.get("confidence"),
                bbox[0], bbox[1], bbox[2], bbox[3],
                center[0], center[1],
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
            batch_params.append(params)

        try:
            with self.lock:
                with self._get_sqlite_connection() as conn:
                    conn.executemany(sql, batch_params)
                    conn.commit()
            
            # --- DUAL WRITE TO TIMESCALEDB ---
            if self.timescale_engine:
                asyncio.create_task(self._save_to_timescale_batch(vehicle_data_list))
                
            return len(batch_params)
        except Exception as e:
            logger.error(f"DB batch save failed: {e}")
            if isinstance(e, sqlite3.OperationalError):
                raise
            return 0

    async def _save_to_timescale_batch(self, vehicle_data_list: List[Dict]):
        """Asynchronously saves a batch of data to TimescaleDB."""
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
        except Exception as e:
            logger.error(f"TimescaleDB batch save failed: {e}")

    async def save_location_metrics_batch(self, metrics_list: List[Dict]):
        """Saves a batch of aggregated location metrics to TimescaleDB."""
        if not self.timescale_engine:
            return

        sql = text("""
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
                params = []
                for m in metrics_list:
                    ts = m.get("timestamp")
                    if isinstance(ts, (int, float)):
                        ts = datetime.fromtimestamp(ts, tz=timezone.utc)
                    elif isinstance(ts, str):
                        try:
                            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        except ValueError:
                            ts = datetime.now(timezone.utc)
                    
                    params.append({
                        "location_id": m.get("id"),
                        "timestamp": ts,
                        "vehicle_count": m.get("vehicle_count"),
                        "average_speed": m.get("average_speed"),
                        "congestion_score": m.get("congestion_score"),
                        "latitude": m.get("latitude"),
                        "longitude": m.get("longitude")
                    })
                
                await conn.execute(sql, params)
                logger.info(f"Saved {len(params)} location metrics to TimescaleDB.")
        except Exception as e:
            logger.error(f"Failed to save location metrics to TimescaleDB: {e}")

    async def get_location_metrics(self, location_id: str, hours: int = 24) -> List[Dict]:
        """Retrieves historical location metrics from TimescaleDB."""
        if not self.timescale_engine:
            return []

        start_time = datetime.now(timezone.utc) - timedelta(hours=hours)
        sql = text("""
            SELECT * FROM location_metrics 
            WHERE location_id = :location_id 
            AND timestamp >= :start_time
            ORDER BY timestamp ASC
        """)

        try:
            async with self.timescale_engine.connect() as conn:
                result = await conn.execute(sql, {"location_id": location_id, "start_time": start_time})
                return [dict(row._mapping) for row in result]
        except Exception as e:
            logger.error(f"Failed to query location metrics from TimescaleDB: {e}")
            return []

    async def get_history_stats(self, feed_id: str, hours: int = 24) -> List[Dict]:
        """
        Retrieves historical statistics (vehicle count, speed).
        Tries TimescaleDB 'location_metrics' first.
        Falls back to aggregating 'vehicle_tracks' in SQLite.
        """
        # 1. Try TimescaleDB (if configured and data exists)
        if self.timescale_engine:
            try:
                metrics = await self.get_location_metrics(feed_id, hours)
                if metrics:
                    return metrics
            except Exception as e:
                logger.warning(f"TimescaleDB history fetch failed, falling back to SQLite: {e}")

        # 2. Fallback to SQLite Aggregation
        if not self.sqlite_db_path:
             return []

        # Calculate start timestamp
        start_ts = time.time() - (hours * 3600)
        
        sql = """
            SELECT 
                cast(timestamp / 3600 as int) * 3600 as time_bucket, -- Group by hour
                count(distinct track_id) as vehicle_count,
                avg(speed) as average_speed
            FROM vehicle_tracks
            WHERE feed_id = ? AND timestamp >= ?
            GROUP BY time_bucket
            ORDER BY time_bucket ASC
        """
        
        try:
            results = []
            with self.lock:
                with self._get_sqlite_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(sql, (feed_id, start_ts))
                    rows = cursor.fetchall()
                    
                    for row in rows:
                        # Row keys depend on row_factory, usually case-insensitive or index
                        # Using numeric indices is safer if row_factory varies
                        ts = row[0]
                        count = row[1]
                        speed = row[2] if row[2] is not None else 0.0
                        
                        results.append({
                            "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc),
                            "vehicle_count": count,
                            "average_speed": speed,
                            "congestion_score": 0.0 # Not easily calculable from raw tracks
                        })
            return results
        except Exception as e:
            logger.error(f"SQLite aggregation failed: {e}")
            return []

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
    def upsert_identified_vehicle(self, vehicle_data: Dict) -> bool:
        """Upserts a vehicle identification record based on license plate."""
        sql = """
        INSERT INTO identified_vehicles (
            license_plate, vehicle_type, make, model, color, first_seen, last_seen, total_detections, flags
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(license_plate) DO UPDATE SET
            vehicle_type = COALESCE(excluded.vehicle_type, identified_vehicles.vehicle_type),
            make = COALESCE(excluded.make, identified_vehicles.make),
            model = COALESCE(excluded.model, identified_vehicles.model),
            color = COALESCE(excluded.color, identified_vehicles.color),
            last_seen = excluded.last_seen,
            total_detections = identified_vehicles.total_detections + 1,
            flags = COALESCE(excluded.flags, identified_vehicles.flags)
        """
        try:
            lp = vehicle_data.get("license_plate")
            if not lp or lp == "Unknown":
                return False
                
            now = vehicle_data.get("timestamp", time.time())
            params = (
                lp,
                vehicle_data.get("vehicle_type"),
                vehicle_data.get("make"),
                vehicle_data.get("model"),
                vehicle_data.get("color"),
                now, # first_seen (if insert)
                now, # last_seen
                vehicle_data.get("flags")
            )
            with self.lock:
                with self._get_sqlite_connection() as conn:
                    conn.execute(sql, params)
            return True
        except Exception as e:
            logger.error(f"Error upserting identified vehicle {vehicle_data.get('license_plate')}: {e}")
            return False

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
        if not updates: return True
        
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
        """Helper for synchronous query execution."""
        self._validate_query(query, params)
        with self._get_sqlite_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

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
