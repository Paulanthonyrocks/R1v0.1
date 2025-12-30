# backend/app/utils/database.py

import asyncio
import sqlite3
import threading
import logging
import time
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

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from pymongo import MongoClient
from pymongo.database import Database as MongoDatabase
from pymongo.errors import (
    ConnectionFailure,
    ConfigurationError as MongoConfigurationError,)
from app.models.alerts import Alert  # Import the Alert model
import json  # Import json for serializing details
import math


# Attempt to import TrafficMonitor from where it's planned to be

from app.utils import ConfigError  # Use re-exported config symbols

logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    """Custom exception for database operation errors."""

    pass


# --- DatabaseManager (Merged and Corrected) ---
class DatabaseManager:
    def __init__(self, config: Dict):
        self.sqlite_db_path: Optional[Path] = None
        self.mongo_uri: Optional[str] = None
        self.mongo_db_name: Optional[str] = None
        self.raw_traffic_collection_name: str = (
            "raw_traffic_data"  # Default, can be from config
        )
        self.mongo_client: Optional[MongoClient] = None
        self.mongo_db: Optional[MongoDatabase] = None
        self.async_engine = None  # Corrected attribute name
        self.async_session_factory = None  # Corrected attribute name

        # Initialize database connections
        self._init_from_config(config)  # This might raise ConfigError or ValueError
        self.lock = (
            threading.Lock()
        )  # Lock for thread-safe operations on SQLite connection

        # Initialize databases
        if self.sqlite_db_path:
            self._initialize_sqlite_database()  # Renamed for clarity

        if self.mongo_uri and self.mongo_db_name:  # Ensure both are set
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

    def _init_from_config(self, config: Dict[str, Any]):
        """Initialize database path and MongoDB URI from configuration."""
        try:
            db_config = config.get("database", {})
            self.sqlite_db_path_str = db_config.get("db_path", "data/vehicle_data.db")

            path_obj = Path(self.sqlite_db_path_str)
            if not path_obj.is_absolute():
                project_root = Path(__file__).resolve().parent.parent.parent
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

            mongo_config = config.get("mongodb", {})
            if mongo_config.get("uri") and mongo_config.get("database_name"):
                self.mongo_uri = mongo_config["uri"]
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
                self._create_sqlite_tables(conn.cursor())
            logger.info("SQLite DB schema initialization check complete.")
        except (sqlite3.Error, DatabaseError) as e:
            logger.error(f"DB init error: {e}", exc_info=True)
            raise DatabaseError(f"DB schema init fail: {e}") from e

    def _create_sqlite_tables(self, cursor: sqlite3.Cursor):
        # ... (This method remains unchanged)
        cursor.execute("""CREATE TABLE IF NOT EXISTS vehicle_tracks (
                feed_id TEXT NOT NULL, track_id INTEGER NOT NULL, timestamp REAL NOT NULL, class_id INTEGER, confidence REAL,
                bbox_x1 REAL, bbox_y1 REAL, bbox_x2 REAL, bbox_y2 REAL, center_x REAL, center_y REAL, speed REAL,
                acceleration REAL, lane INTEGER, direction REAL, license_plate TEXT, ocr_confidence REAL, flags TEXT,
                PRIMARY KEY (feed_id, track_id, timestamp))""")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_vt_timestamp ON vehicle_tracks(timestamp DESC);"
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
        # ... and other table creation statements ...
        logger.debug("SQLite DB table creation check finished.")

    def _initialize_mongodb(self):
        # ... (This method remains unchanged)
        if not self.mongo_uri or not self.mongo_db_name:
            logger.error(
                "MongoDB URI or database name not configured. Skipping MongoDB initialization."
            )
            return
        try:
            self.mongo_client = MongoClient(
                self.mongo_uri, serverSelectionTimeoutMS=5000
            )
            self.mongo_client.admin.command("ismaster")
            self.mongo_db = self.mongo_client[self.mongo_db_name]
            logger.info(
                f"Successfully connected to MongoDB server. Database: '{self.mongo_db_name}'"
            )
        except (ConnectionFailure, MongoConfigurationError) as e:
            logger.error(
                f"MongoDB connection or configuration failed for {self.mongo_uri}: {e}",
                exc_info=True,
            )
            self.mongo_client = None
            self.mongo_db = None

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
            feed_id, track_id, timestamp, class_id, confidence,
            bbox_x1, bbox_y1, bbox_x2, bbox_y2, center_x, center_y,
            speed, acceleration, lane, direction, license_plate, ocr_confidence, flags
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
        
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
                flags_str,
            )
            batch_params.append(params)

        try:
            with self.lock:
                with self._get_sqlite_connection() as conn:
                    conn.executemany(sql, batch_params)
                    conn.commit()
            return len(batch_params)
        except Exception as e:
            logger.error(f"DB batch save failed: {e}")
            if isinstance(e, sqlite3.OperationalError):
                raise
            return 0

    @db_write_retry_decorator
    def save_vehicle_data(self, vd: Dict) -> bool:
        # ... (This method remains unchanged)
        sql = """INSERT OR REPLACE INTO vehicle_tracks (feed_id,track_id,timestamp,class_id,confidence,bbox_x1,bbox_y1,bbox_x2,bbox_y2,center_x,center_y,speed,acceleration,lane,direction,license_plate,ocr_confidence,flags) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
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
    async def get_identified_vehicles(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        """Returns a list of identified vehicles."""
        query = "SELECT * FROM identified_vehicles ORDER BY last_seen DESC LIMIT ? OFFSET ?"
        try:
            return await asyncio.to_thread(self._execute_query, query, (limit, offset))
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

    def _execute_query(self, query: str, params: tuple) -> List[Dict]:
        """Helper for synchronous query execution."""
        with self._get_sqlite_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

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
