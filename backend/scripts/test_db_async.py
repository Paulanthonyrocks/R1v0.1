import asyncio
import sys
import os
from pathlib import Path

# Add backend to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.utils.config import load_config
from app.utils.database import DatabaseManager

async def test_async_db():
    print("Loading config...")
    config = load_config(Path("backend/configs/config.yaml"))
    print("Initializing DatabaseManager...")
    db = DatabaseManager(config)
    
    # Wait a bit for async db initialized if necessary
    await asyncio.sleep(1)
    
    test_batch = [
        {
            "feed_id": "test_feed_1",
            "track_id": 9991,
            "timestamp": 12345678.9,
            "class_id": 2,
            "confidence": 0.95,
            "bbox": [10.0, 10.0, 50.0, 50.0],
            "center": [30.0, 30.0],
            "speed": 60.5,
            "lane": 1,
            "direction": "North"
        },
        {
            "feed_id": "test_feed_1",
            "track_id": 9992,
            "timestamp": 12345678.9,
            "class_id": 2,
            "confidence": 0.90,
            "bbox": [20.0, 20.0, 60.0, 60.0],
            "center": [40.0, 40.0],
            "speed": 65.0,
            "lane": 2,
            "direction": "North"
        }
    ]
    
    print("Testing save_vehicle_data_batch (async)...")
    try:
        inserted = await db.save_vehicle_data_batch(test_batch)
        print(f"Success! Inserted {inserted} records via async aiosqlite.")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_async_db())
