# app/database.py (Example)
import logging
from app.utils.utils import DatabaseManager as DBManagerClass, load_config, ConfigError # Assuming class is in utils
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

db_manager_instance: Optional[DBManagerClass] = None

def initialize_database(config: dict):
    global db_manager_instance
    if db_manager_instance is None:
        try:
            db_manager_instance = DBManagerClass(config)
            logger.info("DatabaseManager initialized successfully via app.database.")
        except Exception as e:
            logger.critical(f"Failed to initialize DatabaseManager in app.database: {e}", exc_info=True)
            db_manager_instance = None
            raise RuntimeError(f"Database Initialization Failed: {e}") from e
    return db_manager_instance

def get_database_manager() -> DBManagerClass:
    if db_manager_instance is None:
        # This case should ideally not happen if initialize is called at startup
        logger.error("Database accessed before initialization!")
        raise RuntimeError("Database not initialized.")
    return db_manager_instance

def close_database():
     global db_manager_instance
     if db_manager_instance:
          try:
               logger.info("Closing database connection from app.database...")
               db_manager_instance.close()
               db_manager_instance = None
          except Exception as e:
               logger.error(f"Error closing database from app.database: {e}")
     else:
          logger.info("Database already closed or not initialized.")