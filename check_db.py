
import asyncio
import yaml
import logging
import logging.config
from backend.app.utils.database import DatabaseManager
from pathlib import Path

async def test_db_init():
    config_path = Path("backend/configs/config.yaml")
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    
    # Configure logging
    if "logging" in config:
        logging.config.dictConfig(config["logging"])
    else:
        logging.basicConfig(level=logging.INFO)
        
    db = DatabaseManager(config)
    print("Database initialized.")
    
    # Check if table exists
    with db._get_sqlite_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='identified_vehicles';")
        result = cursor.fetchone()
        if result:
            print("Table 'identified_vehicles' exists.")
        else:
            print("Table 'identified_vehicles' DOES NOT exist.")

if __name__ == "__main__":
    asyncio.run(test_db_init())
