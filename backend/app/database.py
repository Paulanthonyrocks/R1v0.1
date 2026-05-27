# app/database.py
import asyncio
import logging
from app.utils import DatabaseManager as DBManagerClass
from typing import Optional

logger = logging.getLogger("app.database")

_db_manager: Optional[DBManagerClass] = None
_init_lock = asyncio.Lock()
_closing = False


async def initialize_database(config: dict):
    global _db_manager, _closing
    async with _init_lock:
        if _db_manager is not None:
            return _db_manager
        
        if _closing:
            raise RuntimeError("Database is shutting down")

        try:
            # Initialize the manager. 
            # Since DatabaseManager.__init__ is synchronous and handles basic setup, 
            # we use to_thread to avoid blocking the event loop during initial I/O.
            _db_manager = await asyncio.to_thread(DBManagerClass, config)
            logger.info("DatabaseManager initialized successfully.")
        except Exception as e:
            logger.critical(
                f"Failed to initialize DatabaseManager: {e}",
                exc_info=True,
            )
            _db_manager = None
            raise RuntimeError(f"Database Initialization Failed: {e}") from e
            
    return _db_manager


def get_database_manager() -> DBManagerClass:
    if _closing:
        raise RuntimeError("Database is shutting down")
    if _db_manager is None:
        logger.error("Database accessed before initialization!")
        raise RuntimeError("Database not initialized.")
    return _db_manager


async def close_database():
    global _db_manager, _closing
    async with _init_lock:
        if _closing or _db_manager is None:
            return
        
        _closing = True
        try:
            logger.info("Closing database connections...")
            await asyncio.wait_for(_db_manager.close(), timeout=10.0)
        except Exception as e:
            logger.error(f"Error closing database: {e}")
        finally:
            _db_manager = None
            _closing = False
            logger.info("Database closed.")
