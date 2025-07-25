from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import datetime

from app.models.traffic import TrafficData
from app.dependencies import get_current_active_user
from app.database import get_database_manager

import asyncio
from bson import ObjectId
from fastapi import status

router = APIRouter()

@router.post("/traffic-data")
async def ingest_traffic_data(data: TrafficData, current_user: dict = Depends(get_current_active_user)):
    """Endpoint to ingest real-time traffic data. Requires authentication."""
    # user_email = current_user.get("email") # Example user info access
    # logger.info(f"Traffic data ingested by user: {user_email}")
    
    # Prepare data for MongoDB (timestamp as datetime object)
    mongo_data = {
        "timestamp": data.timestamp, # Keep as datetime object for MongoDB
        "sensor_id": data.sensor_id,
        "location": data.location, # Store as nested document
        "speed": data.speed,
        "occupancy": data.occupancy,
        "vehicle_count": data.vehicle_count,
    }

    db_manager = get_database_manager()
    try:
        if db_manager.mongo_db: # Prioritize MongoDB if available
            await asyncio.to_thread(db_manager.save_raw_traffic_data_mongo, mongo_data)
        elif db_manager.db_path: # Fallback to SQLite if MongoDB is not configured/available
            # logger.warning("MongoDB not available, falling back to SQLite for raw_traffic_data.")
            # Data for SQLite (timestamp as ISO string)
            sqlite_data = {
                "timestamp": data.timestamp.isoformat(),
                "sensor_id": data.sensor_id,
                "latitude": data.location["lat"],
                "longitude": data.location["lon"],
                "speed": data.speed,
                "occupancy": data.occupancy,
                "vehicle_count": data.vehicle_count,
            }
            with db_manager._get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO raw_traffic_data (timestamp, sensor_id, latitude, longitude, speed, occupancy, vehicle_count)
                    VALUES (:timestamp, :sensor_id, :latitude, :longitude, :speed, :occupancy, :vehicle_count)
                    """,
                    sqlite_data
                )
        else:
            raise HTTPException(status_code=500, detail="No database configured to save traffic data.")

    except Exception as e:
        # logger.error(f"Failed to save traffic data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to save traffic data: {str(e)}")

    return {"message": "Traffic data ingested successfully", "data": mongo_data}

@router.get("/traffic-data")
async def get_traffic_data(
    limit: int = Query(100, ge=1, le=1000),
    current_user: dict = Depends(get_current_active_user)
): # Added limit parameter
    """Endpoint to retrieve traffic data for visualization. Requires authentication."""
    db_manager = get_database_manager()
    try:
        if db_manager.mongo_db: # Prioritize MongoDB
            # Example: fetch recent data, sorted by timestamp descending
            sort_criteria = [("timestamp", -1)] # PyMongo sort order
            data = await asyncio.to_thread(db_manager.get_raw_traffic_data_mongo, {}, limit, sort_criteria)
            # Convert datetime objects to ISO strings for JSON response if necessary
            for item in data:
                if isinstance(item.get("_id"), ObjectId):
                    item["_id"] = str(item["_id"]) # Convert ObjectId to string
                if isinstance(item.get("timestamp"), datetime):
                    item["timestamp"] = item["timestamp"].isoformat()
            return data
        elif db_manager.db_path: # Fallback to SQLite
            # logger.warning("MongoDB not available, falling back to SQLite for get_traffic_data.")
            with db_manager._get_sqlite_connection() as conn:
                cursor = conn.cursor()
                # SQLite does not have native ObjectId or datetime object handling like Mongo for Pydantic conversion
                cursor.execute("SELECT id, timestamp, sensor_id, latitude, longitude, speed, occupancy, vehicle_count FROM raw_traffic_data ORDER BY timestamp DESC LIMIT ?", (limit,))
                sqlite_data = cursor.fetchall()
                return [dict(row) for row in sqlite_data]
        else:
            raise HTTPException(status_code=500, detail="No database configured to retrieve traffic data.")
            
    except Exception as e:
        # logger.error(f"Failed to retrieve traffic data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve traffic data: {str(e)}")
