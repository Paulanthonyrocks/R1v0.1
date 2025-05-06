from typing import List, Dict, Any
from enum import Enum
from fastapi import FastAPI, APIRouter, HTTPException, Depends
from pydantic import BaseModel
from datetime import datetime

from app.services.services import get_feed_manager
from app.services.feed_manager import FeedManager
from app.database import get_database_manager


class StatusEnum(str, Enum):
    error = "error"
    stopped = "stopped"
    running = "running"
    starting = "starting"
    
class FeedStatusData:
    def __init__(self, id: str, source: str, name: str, status: StatusEnum):
        self.id = id
        self.source = source
        self.name = name
        self.status = status

class TrafficData(BaseModel):
    timestamp: datetime
    sensor_id: str
    location: Dict[str, float]
    speed: float | None = None
    occupancy: float | None = None
    vehicle_count: int | None = None
        
router = APIRouter()

@router.get("/v1/feeds", response_model=List[FeedStatusData])
async def get_feeds():
    """Test endpoint to get the list of feeds"""
    test_feeds = [
        FeedStatusData(id="feed1", source="source1", name="Feed One", status=StatusEnum.running),
        FeedStatusData(id="feed2", source="source2", name="Feed Two", status=StatusEnum.stopped),
        FeedStatusData(id="feed3", source="source3", name="Feed Three", status=StatusEnum.starting),
    ]
    return test_feeds

@router.get("/v1/sample-feed-data")
async def get_sample_feed_data(feed_manager: FeedManager = Depends(get_feed_manager)) -> Dict[str, Any]:
    """Returns the latest_metrics for the sample feed."""
    if not feed_manager._sample_feed_id or not feed_manager.process_registry.get(feed_manager._sample_feed_id):
        raise HTTPException(status_code=404, detail="Sample feed not found.")
    
    sample_feed_entry = feed_manager.process_registry[feed_manager._sample_feed_id]

    if sample_feed_entry["status"] != "running":
        raise HTTPException(status_code=404, detail="Sample feed is not running.")

    return sample_feed_entry["latest_metrics"]

@router.post("/v1/traffic-data")
async def ingest_traffic_data(data: TrafficData):
    """Endpoint to ingest real-time traffic data."""
    # Validate and process the data
    processed_data = {
        "timestamp": data.timestamp.isoformat(),
        "sensor_id": data.sensor_id,
        "latitude": data.location["lat"],
        "longitude": data.location["lon"],
        "speed": data.speed,
        "occupancy": data.occupancy,
        "vehicle_count": data.vehicle_count,
    }

    # Save raw traffic data to the database
    db_manager = get_database_manager()
    try:
        with db_manager._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO raw_traffic_data (timestamp, sensor_id, latitude, longitude, speed, occupancy, vehicle_count)
                VALUES (:timestamp, :sensor_id, :latitude, :longitude, :speed, :occupancy, :vehicle_count)
                """,
                processed_data
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save traffic data: {str(e)}")

    return {"message": "Traffic data ingested successfully", "data": processed_data}

@router.get("/v1/traffic-data")
async def get_traffic_data():
    """Endpoint to retrieve traffic data for visualization."""
    db_manager = get_database_manager()
    try:
        with db_manager._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT latitude, longitude, vehicle_count FROM raw_traffic_data")
            data = cursor.fetchall()
            return [dict(row) for row in data]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve traffic data: {str(e)}")

@router.get("/v1/signals")
async def get_signals():
    """Endpoint to retrieve the list of traffic signals."""
    # Placeholder data for signals
    signals = [
        {"id": "signal_1", "current_phase": "red"},
        {"id": "signal_2", "current_phase": "green"},
        {"id": "signal_3", "current_phase": "yellow"},
    ]
    return signals

@router.post("/v1/signals/{signal_id}/set_phase")
async def set_signal_phase(signal_id: str, phase: str):
    """Endpoint to update the phase of a traffic signal."""
    valid_phases = ["red", "yellow", "green"]
    if phase not in valid_phases:
        raise HTTPException(status_code=400, detail="Invalid phase")

    # Placeholder logic to update the signal phase
    # In a real implementation, this would interact with the traffic signal system
    return {"message": f"Signal {signal_id} updated to phase {phase}"}