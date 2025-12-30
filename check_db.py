
import asyncio
import yaml
from backend.app.utils.database import DatabaseManager
from pathlib import Path

async def test_db_init():
    config_path = Path("backend/configs/config.yaml")
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    
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
