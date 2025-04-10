# /content/drive/MyDrive/R1v0.1/backend/app/dependencies.py (Updated)

from typing import Dict, Any
from app.database import get_database_manager
from app.services import get_feed_manager, get_connection_manager
# --- Import config getter from the new config module ---
from app.config import get_current_config

async def get_db():
    """Dependency to get the database manager instance."""
    # Note: If DatabaseManager methods become async, this might need changes
    db = get_database_manager()
    return db

async def get_fm():
    """Dependency to get the feed manager instance."""
    fm = get_feed_manager()
    return fm

# --- Dependency function to provide the application config ---
async def get_config() -> Dict[str, Any]: # Renamed for clarity if preferred, or keep as get_config
    """Dependency to get the currently loaded configuration dictionary."""
    config = get_current_config() # Call the getter from app.config
    return config

# You might also need get_connection_manager if used as a dependency
# async def get_cm():
#     cm = get_connection_manager()
#     return cm