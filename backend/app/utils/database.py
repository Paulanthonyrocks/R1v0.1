import asyncio
import sqlite3
import threading
import logging
import time
from pathlib import Path
from typing import List, Dict, Optional, Any
from functools import lru_cache
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type, RetryError
from contextlib import asynccontextmanager, contextmanager

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from pymongo import MongoClient
from pymongo.database import Database as MongoDatabase
from pymongo.errors import ConnectionFailure, ConfigurationError as MongoConfigurationError

# Attempt to import TrafficMonitor from where it's planned to be
from ..monitoring import TrafficMonitor
# No longer need the placeholder class TrafficMonitor here

from ..config import DEFAULT_CONFIG, ConfigError # ConfigError might be needed if _init_from_config raises it

logger = logging.getLogger(__name__)

class DatabaseError(Exception):
    """Custom exception for database operation errors."""
    pass

# --- DatabaseManager (Simplified for SQLite) ---
class DatabaseManager:
    def __init__(self, config: Dict):
        self.sqlite_db_path: Optional[Path] = None
        self.mongo_uri: Optional[str] = None
        self.mongo_db_name: Optional[str] = None
        self.raw_traffic_collection_name: str = "raw_traffic_data" # Default, can be from config
        self.mongo_client: Optional[MongoClient] = None
        self.mongo_db: Optional[MongoDatabase] = None
        self._async_engine = None
        self._async_session_factory = None

        # Initialize database connections
        self._init_from_config(config) # This might raise ConfigError or ValueError
        self.lock = threading.Lock() # Lock for thread-safe operations on SQLite connection

        # Initialize databases
        if self.sqlite_db_path:
            self._initialize_sqlite_database() # Renamed for clarity

        if self.mongo_uri and self.mongo_db_name: # Ensure both are set
            self._initialize_mongodb()

        # Async SQLAlchemy setup (only if sqlite_db_path is valid)
        if self.sqlite_db_path:
            self.async_engine = create_async_engine(f"sqlite+aiosqlite:///{self.sqlite_db_path}")
            self.async_session_factory = sessionmaker(
                self.async_engine, class_=AsyncSession, expire_on_commit=False
            )
        else:
            logger.warning("SQLite path not configured. Async SQLAlchemy engine not created.")


    def _init_from_config(self, config: Dict[str, Any]):
        """Initialize database path and MongoDB URI from configuration."""
        try:
            db_config = config.get("database", {})
            self.sqlite_db_path_str = db_config.get("db_path", "data/vehicle_data.db") # Get path string from config

            # Resolve the path (assuming it might be relative to project root or a specific data directory)
            # This logic might need adjustment based on your project structure and where config path is resolved.
            # For now, let's assume it's relative to the project root if not absolute.
            path_obj = Path(self.sqlite_db_path_str)
            if not path_obj.is_absolute():
                # Assuming this script is in backend/app/utils, project root is ../../..
                # This is fragile. Better to pass absolute paths or a base_path in config.
                project_root = Path(__file__).resolve().parent.parent.parent
                path_obj = project_root / self.sqlite_db_path_str

            self.sqlite_db_path = path_obj.resolve()

            if not self.sqlite_db_path.parent.exists():
                try:
                    self.sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
                    logger.info(f"Created database directory: {self.sqlite_db_path.parent}")
                except OSError as e:
                    # If directory creation fails, it's a configuration/permission issue
                    raise ConfigError(f"Failed to create database directory {self.sqlite_db_path.parent}: {e}") from e

            logger.info(f"SQLite database path configured to: {self.sqlite_db_path}")

            mongo_config = config.get("mongodb", {})
            if mongo_config.get("uri") and mongo_config.get("database_name"):
                self.mongo_uri = mongo_config["uri"]
                self.mongo_db_name = mongo_config["database_name"]
                self.raw_traffic_collection_name = mongo_config.get("raw_traffic_collection", "raw_traffic_data")
                logger.info(f"MongoDB configured: URI='{self.mongo_uri}', DB='{self.mongo_db_name}'")
            else:
                logger.info("MongoDB not fully configured (URI or database_name missing). MongoDB will not be used.")
                self.mongo_uri = None # Ensure it's None if not fully configured
                self.mongo_db_name = None

        except ConfigError: # Re-raise ConfigError if it was about directory creation
            raise
        except KeyError as e:
            logger.error(f"Missing expected key in database configuration: {e}")
            raise ConfigError(f"Database configuration missing key: {e}") from e
        except Exception as e:
            logger.error(f"Failed to initialize database configuration paths: {e}", exc_info=True)
            # Use ValueError for other general issues during this phase if not a ConfigError
            raise ValueError(f"Invalid database configuration: {e}") from e


    @asynccontextmanager
    async def get_session(self) -> AsyncContextManager[AsyncSession]: # Corrected return type hint
        """Get an async database session."""
        if not self.async_session_factory:
            raise DatabaseError("Async session factory not initialized. Check SQLite configuration.")
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
    def get_session_sync(self) -> Any: # Return type can be more specific if only one type of session is returned
        """Get a synchronous database session (for SQLite)."""
        if not self.sqlite_db_path:
            raise DatabaseError("SQLite database path not configured.")
        # This import is here because sqlalchemy might not be a hard dependency if only mongo is used.
        from sqlalchemy.orm import Session as SyncSession, sessionmaker as sync_sessionmaker
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
            # Using self.sqlite_db_path which is now a Path object
            conn = sqlite3.connect(str(self.sqlite_db_path), timeout=10.0)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
            except sqlite3.Error as e:
                logger.warning(f"Could not set WAL mode on {self.sqlite_db_path}: {e}")
            return conn
        except sqlite3.Error as e:
            logger.error(f"Failed to connect to DB {self.sqlite_db_path}: {e}", exc_info=True)
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
        except sqlite3.Error as e:
            logger.error(f"DB init error: {e}", exc_info=True)
            raise DatabaseError(f"DB schema init fail: {e}") from e
        except Exception as e: # Catch other potential errors like Path issues if not caught earlier
            logger.error(f"Unexpected DB init error: {e}", exc_info=True)
            raise DatabaseError(f"Unexpected DB init error: {e}") from e

    def _create_sqlite_tables(self, cursor: sqlite3.Cursor):
        cursor.execute('''CREATE TABLE IF NOT EXISTS vehicle_tracks (
                feed_id TEXT NOT NULL, track_id INTEGER NOT NULL, timestamp REAL NOT NULL, class_id INTEGER, confidence REAL,
                bbox_x1 REAL, bbox_y1 REAL, bbox_x2 REAL, bbox_y2 REAL, center_x REAL, center_y REAL, speed REAL,
                acceleration REAL, lane INTEGER, direction REAL, license_plate TEXT, ocr_confidence REAL, flags TEXT,
                PRIMARY KEY (feed_id, track_id, timestamp))''')
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vt_timestamp ON vehicle_tracks(timestamp DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vt_feed_track ON vehicle_tracks(feed_id, track_id);")
        cursor.execute('''CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL DEFAULT (unixepoch('now', 'subsec')),
                severity TEXT NOT NULL CHECK(severity IN ('INFO', 'WARNING', 'CRITICAL')), feed_id TEXT NOT NULL,
                message TEXT NOT NULL, details TEXT, acknowledged INTEGER DEFAULT 0 NOT NULL CHECK(acknowledged IN (0, 1)))''')
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_feed_severity ON alerts(feed_id, severity);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_acknowledged ON alerts(acknowledged);")

        cursor.execute('''CREATE TABLE IF NOT EXISTS raw_traffic_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            sensor_id TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            speed REAL,
            occupancy REAL,
            vehicle_count INTEGER
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS processed_traffic_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            segment_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            congestion_level REAL NOT NULL
        )''')
        logger.debug("SQLite DB table creation check finished.")

    def _initialize_mongodb(self):
        if not self.mongo_uri or not self.mongo_db_name:
            logger.error("MongoDB URI or database name not configured. Skipping MongoDB initialization.")
            return
        try:
            self.mongo_client = MongoClient(self.mongo_uri, serverSelectionTimeoutMS=5000)
            self.mongo_client.admin.command('ismaster')
            self.mongo_db = self.mongo_client[self.mongo_db_name]
            logger.info(f"Successfully connected to MongoDB server. Database: '{self.mongo_db_name}'")
            # Example: self.mongo_db[self.raw_traffic_collection_name].create_index([("timestamp", -1)], background=True)
        except ConnectionFailure as e:
            logger.error(f"MongoDB connection failed to {self.mongo_uri}: {e}", exc_info=True)
            self.mongo_client = None; self.mongo_db = None
        except MongoConfigurationError as e: # Corrected exception name
            logger.error(f"MongoDB configuration error for {self.mongo_uri}: {e}", exc_info=True)
            self.mongo_client = None; self.mongo_db = None
        except Exception as e:
            logger.error(f"An unexpected error occurred during MongoDB initialization for {self.mongo_uri}: {e}", exc_info=True)
            self.mongo_client = None; self.mongo_db = None


    db_write_retry_decorator = retry(
        wait=wait_exponential(multiplier=0.2,min=0.2,max=3),
        stop=stop_after_attempt(4),
        retry=retry_if_exception_type(sqlite3.OperationalError)
    )

    @db_write_retry_decorator
    def save_vehicle_data(self, vd: Dict) -> bool:
        # ... (rest of the method is identical to original in utils.py)
        sql = '''INSERT OR REPLACE INTO vehicle_tracks (feed_id,track_id,timestamp,class_id,confidence,bbox_x1,bbox_y1,bbox_x2,bbox_y2,center_x,center_y,speed,acceleration,lane,direction,license_plate,ocr_confidence,flags) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'''
        try:
            bbox=vd.get('bbox',[None]*4); center=vd.get('center',[None]*2); flags_str=','.join(sorted(list(vd.get('flags',set()))))
            params=(vd.get('feed_id','unknown'),vd.get('track_id'),vd.get('timestamp',time.time()),vd.get('class_id'),vd.get('confidence'),bbox[0],bbox[1],bbox[2],bbox[3],center[0],center[1],vd.get('speed'),vd.get('acceleration'),vd.get('lane'),vd.get('direction'),vd.get('license_plate'),vd.get('ocr_confidence'),flags_str)
            with self.lock:
                with self._get_sqlite_connection() as conn: conn.execute(sql, params)
            logger.debug(f"Saved track: Feed={params[0]},Track={params[1]},Time={params[2]:.2f}")
            return True
        except RetryError as e: logger.error(f"DB save_vehicle_data failed retries: {e}. TrackID: {vd.get('track_id')}"); return False
        except sqlite3.Error as e:
            logger.error(f"DB error saving vehicle: {e} - TrackID: {vd.get('track_id')}", exc_info=True)
            if not isinstance(e, sqlite3.OperationalError): raise DatabaseError(f"Failed save vehicle: {e}") from e
            return False # Redundant due to raise, but for clarity if raise is removed.
        except Exception as e: logger.error(f"Unexpected error saving vehicle: {e} - TrackID: {vd.get('track_id')}", exc_info=True); raise DatabaseError(f"Unexpected save vehicle: {e}") from e


    @db_write_retry_decorator
    def save_vehicle_data_batch(self, data_list: List[Dict]) -> bool:
        # ... (rest of the method is identical to original in utils.py)
        if not data_list: return True
        sql = '''INSERT OR REPLACE INTO vehicle_tracks (feed_id,track_id,timestamp,class_id,confidence,bbox_x1,bbox_y1,bbox_x2,bbox_y2,center_x,center_y,speed,acceleration,lane,direction,license_plate,ocr_confidence,flags) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'''
        prepared = []
        try:
            for vd in data_list:
                bbox=vd.get('bbox',[None]*4); center=vd.get('center',[None]*2); flags_str=','.join(sorted(list(vd.get('flags',set()))))
                prepared.append((vd.get('feed_id','unknown'),vd.get('track_id'),vd.get('timestamp',time.time()),vd.get('class_id'),vd.get('confidence'),bbox[0],bbox[1],bbox[2],bbox[3],center[0],center[1],vd.get('speed'),vd.get('acceleration'),vd.get('lane'),vd.get('direction'),vd.get('license_plate'),vd.get('ocr_confidence'),flags_str))
            if not prepared: return True
            with self.lock:
                with self._get_sqlite_connection() as conn: conn.executemany(sql, prepared)
            logger.debug(f"Saved batch of {len(prepared)} vehicle records.")
            return True
        except RetryError as e: logger.error(f"DB save_vehicle_data_batch failed retries: {e}."); return False
        except sqlite3.Error as e:
            logger.error(f"DB error saving vehicle batch: {e}", exc_info=True)
            if not isinstance(e, sqlite3.OperationalError): raise DatabaseError(f"Failed save vehicle batch: {e}") from e
            return False
        except Exception as e: logger.error(f"Unexpected error saving vehicle batch: {e}", exc_info=True); raise DatabaseError(f"Unexpected save vehicle batch: {e}") from e

    def get_recent_tracks(self, feed_id: Optional[str] = None, limit: int = 100) -> List[Dict]:
        # ... (rest of the method is identical to original in utils.py)
        try:
            with self._get_sqlite_connection() as conn:
                cursor=conn.cursor()
                if feed_id: cursor.execute("SELECT * FROM vehicle_tracks WHERE feed_id=? ORDER BY timestamp DESC LIMIT ?", (feed_id,limit))
                else: cursor.execute("SELECT * FROM vehicle_tracks ORDER BY timestamp DESC LIMIT ?", (limit,))
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e: logger.error(f"DB error get recent tracks (feed={feed_id}): {e}", exc_info=True); return []

    def get_track_history(self, feed_id: str, track_id: int, limit: int = 50) -> List[Dict]:
        # ... (rest of the method is identical to original in utils.py)
        try:
            with self._get_sqlite_connection() as conn:
                cursor=conn.cursor(); cursor.execute("SELECT * FROM vehicle_tracks WHERE feed_id=? AND track_id=? ORDER BY timestamp DESC LIMIT ?", (feed_id,track_id,limit))
                return [dict(row) for row in reversed(cursor.fetchall())]
        except sqlite3.Error as e: logger.error(f"DB error get track history (feed={feed_id},track={track_id}): {e}", exc_info=True); return []

    @lru_cache(maxsize=4)
    def get_vehicle_stats(self, time_window_secs: int = 300) -> Dict:
        try:
            with self._get_sqlite_connection() as conn:
                cursor=conn.cursor(); min_ts = time.time()-time_window_secs
                # Use DEFAULT_CONFIG from app.utils.config
                stopped_threshold = DEFAULT_CONFIG.get('stopped_speed_threshold_kmh', 5)
                cursor.execute("SELECT COUNT(*) as total_vehicles, AVG(speed) as average_speed_kmh, SUM(CASE WHEN speed < ? THEN 1 ELSE 0 END) as stopped_vehicles FROM vehicle_tracks WHERE timestamp > ?", (stopped_threshold, min_ts))
                res=cursor.fetchone(); stats=dict(res) if res else {'total_vehicles':0,'average_speed_kmh':0.0,'stopped_vehicles':0}
                stats['total_vehicles']=stats.get('total_vehicles') or 0; stats['average_speed_kmh']=stats.get('average_speed_kmh') or 0.0; stats['stopped_vehicles']=stats.get('stopped_vehicles') or 0
                return stats
        except sqlite3.Error as e: logger.error(f"DB error get vehicle stats: {e}", exc_info=True); return {}

    @lru_cache(maxsize=4)
    def get_vehicle_counts_by_type(self, time_window_secs: int = 300) -> Dict[str, int]:
        try:
            with self._get_sqlite_connection() as conn:
                cursor=conn.cursor(); min_ts = time.time()-time_window_secs
                cursor.execute("WITH LT AS (SELECT feed_id,track_id,class_id,MAX(timestamp) as mt FROM vehicle_tracks WHERE timestamp > ? GROUP BY feed_id,track_id) SELECT vt.class_id,COUNT(DISTINCT lt.track_id) as count FROM vehicle_tracks vt JOIN LT ON vt.feed_id=LT.feed_id AND vt.track_id=LT.track_id AND vt.timestamp=LT.mt GROUP BY vt.class_id", (min_ts,))
                results=cursor.fetchall()
                # Use TrafficMonitor from app.utils.monitoring (placeholder for now)
                type_map=TrafficMonitor.vehicle_type_map
                counts={name:0 for name in type_map.values()}; counts['unknown']=0 # Ensure 'unknown' is in counts
                for row in results:
                    counts[type_map.get(row['class_id'], 'unknown')] = row['count'] # Use .get for safety
                return counts
        except sqlite3.Error as e: logger.error(f"DB error get vehicle counts by type: {e}", exc_info=True); return {}

    # _execute_get_alerts_filtered, get_alerts_filtered (async version),
    # _execute_count_alerts_filtered, count_alerts_filtered (async version)
    # save_alert, _execute_acknowledge_alert, acknowledge_alert (async version)
    # _execute_delete_alert, delete_alert (async version)
    # _execute_get_alert_by_id, get_alert_by_id (async version)
    # save_raw_traffic_data_mongo, get_raw_traffic_data_mongo, close
    # are mostly identical to original in utils.py.
    # I will include them for completeness.

    def _execute_get_alerts_filtered(self, filters: Dict, limit: int, offset: int) -> List[Dict]:
        base_q = "SELECT id, timestamp, severity, feed_id, message, details, acknowledged FROM alerts WHERE 1=1"
        params = []
        conds = []
        allowed_exact_match = {"feed_id"}
        if filters.get("acknowledged") is not None:
            conds.append(f"acknowledged = ?"); params.append(1 if filters["acknowledged"] else 0)
        if filters.get("severity"):
            conds.append(f"severity = ?"); params.append(filters["severity"])
        if filters.get("severity_in") and isinstance(filters["severity_in"], list) and len(filters["severity_in"]) > 0:
            placeholders = ", ".join("?" for _ in filters["severity_in"])
            conds.append(f"severity IN ({placeholders})"); params.extend(filters["severity_in"])
        for k, v in filters.items():
            if k in allowed_exact_match and v is not None:
                conds.append(f"{k} = ?"); params.append(v)
            elif k == "search" and isinstance(v, str) and v.strip():
                conds.append("message LIKE ?"); params.append(f"%{v.strip()}%")
            elif k == "start_time" and isinstance(v, (int, float)):
                conds.append("timestamp >= ?"); params.append(v)
            elif k == "end_time" and isinstance(v, (int, float)):
                conds.append("timestamp <= ?"); params.append(v)
        if conds: base_q += " AND " + " AND ".join(conds)
        query = f"{base_q} ORDER BY timestamp DESC LIMIT ? OFFSET ?"; params.extend([limit, offset])
        with self._get_sqlite_connection() as conn:
            cursor = conn.cursor(); cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    async def get_alerts_filtered(self, filters: Dict, limit: int = 100, offset: int = 0) -> List[Dict]:
        try:
            return await asyncio.to_thread(self._execute_get_alerts_filtered, filters, limit, offset)
        except sqlite3.Error as e: logger.error(f"DB error get_alerts_filtered: {e}", exc_info=True); return []
        except Exception as e: logger.error(f"Unexpected error in get_alerts_filtered via thread: {e}", exc_info=True); return []

    def _execute_count_alerts_filtered(self, filters: Dict) -> int:
        base_q = "SELECT COUNT(*) FROM alerts WHERE 1=1"; params = []; conds = []
        allowed_exact_match = {"feed_id"}
        if filters.get("acknowledged") is not None:
            conds.append(f"acknowledged = ?"); params.append(1 if filters["acknowledged"] else 0)
        if filters.get("severity"):
            conds.append(f"severity = ?"); params.append(filters["severity"])
        if filters.get("severity_in") and isinstance(filters["severity_in"], list) and len(filters["severity_in"]) > 0:
            placeholders = ", ".join("?" for _ in filters["severity_in"])
            conds.append(f"severity IN ({placeholders})"); params.extend(filters["severity_in"])
        for k, v in filters.items():
            if k in allowed_exact_match and v is not None:
                conds.append(f"{k} = ?"); params.append(v)
            elif k == "search" and isinstance(v, str) and v.strip():
                conds.append("message LIKE ?"); params.append(f"%{v.strip()}%")
            elif k == "start_time" and isinstance(v, (int, float)):
                conds.append("timestamp >= ?"); params.append(v)
            elif k == "end_time" and isinstance(v, (int, float)):
                conds.append("timestamp <= ?"); params.append(v)
        if conds: base_q += " AND " + " AND ".join(conds)
        with self._get_sqlite_connection() as conn:
            cursor = conn.cursor(); cursor.execute(base_q, params)
            count_result = cursor.fetchone()
            return count_result[0] if count_result else 0

    async def count_alerts_filtered(self, filters: Dict) -> int:
        try:
            return await asyncio.to_thread(self._execute_count_alerts_filtered, filters)
        except sqlite3.Error as e: logger.error(f"DB error count_alerts_filtered: {e}", exc_info=True); return 0
        except Exception as e: logger.error(f"Unexpected error in count_alerts_filtered via thread: {e}", exc_info=True); return 0

    @db_write_retry_decorator
    def save_alert(self, severity: str, feed_id: str, message: str, details: Optional[str]=None) -> bool:
        if severity not in ('INFO','WARNING','CRITICAL'): logger.error(f"Invalid alert sev: {severity}"); return False
        sql='INSERT INTO alerts (severity,feed_id,message,details) VALUES (?,?,?,?)'
        try:
            params=(severity,feed_id,message,details)
            with self.lock:
                with self._get_sqlite_connection() as conn: conn.execute(sql,params)
            logger.info(f"Saved alert: Sev={severity},Feed={feed_id},Msg='{message[:60]}...'")
            return True
        except RetryError as e: logger.error(f"DB save_alert failed retries: {e}."); return False
        except sqlite3.Error as e:
            logger.error(f"DB error saving alert: {e}", exc_info=True)
            if not isinstance(e, sqlite3.OperationalError): raise DatabaseError(f"Failed save alert: {e}") from e
            return False
        except Exception as e: logger.error(f"Unexpected error saving alert: {e}", exc_info=True); raise DatabaseError(f"Unexpected save alert: {e}") from e

    @retry(wait=wait_exponential(multiplier=0.2,min=0.2,max=3), stop=stop_after_attempt(4), retry=retry_if_exception_type(sqlite3.OperationalError))
    async def acknowledge_alert(self, alert_id: int, acknowledge: bool = True) -> bool:
        try:
            return await asyncio.to_thread(self._execute_acknowledge_alert, alert_id, acknowledge)
        except sqlite3.Error as e:
            logger.error(f"DB error ack alert {alert_id} (async wrapper): {e}", exc_info=True)
            if not isinstance(e, sqlite3.OperationalError): raise DatabaseError(f"Failed ack alert: {e}") from e
            return False
        except Exception as e:
            logger.error(f"Unexpected error ack alert {alert_id} (async wrapper): {e}", exc_info=True)
            raise DatabaseError(f"Unexpected ack alert: {e}") from e

    def _execute_acknowledge_alert(self, alert_id: int, acknowledge: bool) -> bool:
        sql="UPDATE alerts SET acknowledged = ? WHERE id = ?"; ack_val = 1 if acknowledge else 0
        with self.lock:
            with self._get_sqlite_connection() as conn:
                cursor=conn.cursor(); cursor.execute(sql,(ack_val,alert_id)); conn.commit()
                if cursor.rowcount==0:
                    logger.warning(f"Alert ID {alert_id} not found for ack."); return False
        logger.info(f"Alert ID {alert_id} ack status set to {acknowledge}.")
        return True

    @retry(wait=wait_exponential(multiplier=0.2,min=0.2,max=3), stop=stop_after_attempt(4), retry=retry_if_exception_type(sqlite3.OperationalError))
    async def delete_alert(self, alert_id: int) -> bool:
        try:
            return await asyncio.to_thread(self._execute_delete_alert, alert_id)
        except sqlite3.Error as e:
            logger.error(f"DB error deleting alert ID {alert_id} (async wrapper): {e}", exc_info=True)
            if not isinstance(e, sqlite3.OperationalError): raise DatabaseError(f"Failed to delete alert ID {alert_id}: {e}") from e
            return False
        except Exception as e:
            logger.error(f"Unexpected error deleting alert ID {alert_id} (async wrapper): {e}", exc_info=True)
            raise DatabaseError(f"Unexpected error deleting alert ID {alert_id}: {e}") from e

    def _execute_delete_alert(self, alert_id: int) -> bool:
        sql = "DELETE FROM alerts WHERE id = ?"
        with self.lock:
            with self._get_sqlite_connection() as conn:
                cursor = conn.cursor(); cursor.execute(sql, (alert_id,)); conn.commit()
                if cursor.rowcount > 0:
                    logger.info(f"Alert ID {alert_id} deleted successfully."); return True
                else:
                    logger.warning(f"Alert ID {alert_id} not found for deletion."); return False

    async def get_alert_by_id(self, alert_id: int) -> Optional[Dict]:
        try:
            return await asyncio.to_thread(self._execute_get_alert_by_id, alert_id)
        except sqlite3.Error as e:
            logger.error(f"DB error fetching alert ID {alert_id} (async wrapper): {e}", exc_info=True); return None
        except Exception as e:
            logger.error(f"Unexpected error fetching alert ID {alert_id} (async wrapper): {e}", exc_info=True); return None

    def _execute_get_alert_by_id(self, alert_id: int) -> Optional[Dict]:
        sql = "SELECT id, timestamp, severity, feed_id, message, details, acknowledged FROM alerts WHERE id = ?"
        with self._get_sqlite_connection() as conn:
            cursor = conn.cursor(); cursor.execute(sql, (alert_id,))
            row = cursor.fetchone()
            if row: return dict(row)
            else: logger.info(f"Alert ID {alert_id} not found."); return None

    @retry(wait=wait_exponential(multiplier=0.2,min=0.2,max=3), stop=stop_after_attempt(3), retry=retry_if_exception_type(Exception))
    def save_raw_traffic_data_mongo(self, data: Dict) -> bool:
        if not self.mongo_db:
            # Fallback or error if MongoDB not initialized.
            # This behavior might need adjustment based on requirements.
            # For instance, if SQLite is a mandatory fallback:
            # logger.warning("MongoDB not available. Saving raw_traffic_data to SQLite as fallback.")
            # return self.save_raw_traffic_data_sqlite(data) # Requires such a method
            raise DatabaseError("MongoDB not available for saving traffic data.")

        try:
            collection = self.mongo_db[self.raw_traffic_collection_name]
            result = collection.insert_one(data)
            logger.debug(f"Saved raw traffic data to MongoDB with id: {result.inserted_id}")
            return True
        except Exception as e: # Includes pymongo.errors.PyMongoError
            logger.error(f"Failed to save raw traffic data to MongoDB: {e}", exc_info=True)
            raise DatabaseError(f"Failed to save to MongoDB: {e}") from e


    def get_raw_traffic_data_mongo(self, query: Dict, limit: int = 1000, sort_criteria: Optional[List[Tuple[str, int]]] = None) -> List[Dict]:
        if not self.mongo_db:
            logger.warning("MongoDB not initialized. Cannot get raw_traffic_data.")
            return [] # Or raise DatabaseError, depending on desired strictness
        try:
            collection = self.mongo_db[self.raw_traffic_collection_name]
            cursor = collection.find(query).limit(limit)
            if sort_criteria:
                cursor = cursor.sort(sort_criteria)
            return list(cursor)
        except Exception as e: # Includes pymongo.errors.PyMongoError
            logger.error(f"Failed to retrieve raw_traffic_data from MongoDB: {e}", exc_info=True)
            return []

    def close(self):
        logger.info("DatabaseManager close called.")
        # Async engine disposal (if initialized)
        # Note: dispose() is an async operation, ideally called from an async context.
        # If close() must be sync, this might need `asyncio.run()` or be handled at app shutdown.
        # For now, assuming it's called from a place where blocking for a short while is OK.
        if self._async_engine:
            try:
                # Running dispose in a new event loop if called from a synchronous context
                async def _dispose_engine():
                    await self._async_engine.dispose()

                try:
                    loop = asyncio.get_running_loop()
                    if loop.is_running(): # If called from within an existing event loop
                        # Schedule it and hope it runs, or use a more sophisticated sync mechanism
                        asyncio.create_task(_dispose_engine())
                        logger.info("Scheduled async SQLAlchemy engine disposal.")
                    else: # If no loop is running, create one
                        raise RuntimeError("No running event loop")
                except RuntimeError: # No running event loop or other asyncio issue
                     asyncio.run(_dispose_engine())
                     logger.info("Disposed async SQLAlchemy engine via asyncio.run().")

            except Exception as e:
                logger.error(f"Error disposing async SQLAlchemy engine: {e}", exc_info=True)
            finally:
                self._async_engine = None
                self._async_session_factory = None

        # Synchronous SQLite connections are typically managed per-call using 'with', so no global pool to close here by default.
        # If a persistent synchronous SQLAlchemy engine (for self.get_session_sync) were stored on self, it would be disposed here.

        # Close MongoDB client (if initialized)
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

# Placeholder for ConfigError if not imported from ..config - should be defined in config.py
# class ConfigError(Exception):
#     """Custom exception for configuration errors."""
#     pass
