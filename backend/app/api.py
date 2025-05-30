from typing import List, Dict, Any
from enum import Enum
from fastapi import FastAPI, APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from datetime import datetime

# Import models from the new location
from app.models.feeds import FeedStatusData, FeedOperationalStatusEnum, FeedConfigInfo # Updated imports
from app.models.traffic import TrafficData, LocationModel # Added LocationModel if needed directly by API, TrafficData moved here

from app.dependencies import get_current_active_user, get_tss
from app.services.traffic_signal_service import TrafficSignalService, TrafficSignalControlError
from app.services.feed_manager import FeedManager
from app.services.services import get_feed_manager
from app.database import get_database_manager

import asyncio
from bson import ObjectId
from fastapi import status

from app.routers.analytics import router as analytics_router


# StatusEnum is now FeedOperationalStatusEnum in app.models.feeds
# class StatusEnum(str, Enum):
#     error = "error"
#     stopped = "stopped"
#     running = "running"
#     starting = "starting"

# TrafficData model is now imported from app.models.traffic
# class TrafficData(BaseModel):
#     timestamp: datetime
#     sensor_id: str
#     location: Dict[str, float] # This was Dict, but should be LocationModel
#     speed: float | None = None
#     occupancy: float | None = None
#     vehicle_count: int | None = None
        
router = APIRouter()

@router.get("/v1/feeds", response_model=List[FeedStatusData])
async def get_feeds():
    """Test endpoint to get the list of feeds. Uses the new comprehensive FeedStatusData model."""
    test_feeds_data = [
        FeedStatusData(
            feed_id="feed1", 
            config=FeedConfigInfo(name="Feed One", source_type="test_source", source_identifier="source1"), 
            status=FeedOperationalStatusEnum.RUNNING,
            status_message="Processing normally.",
            start_time=datetime.utcnow(),
            latest_metrics={"fps": 30}
        ),
        FeedStatusData(
            feed_id="feed2", 
            config=FeedConfigInfo(name="Feed Two", source_type="test_source", source_identifier="source2"), 
            status=FeedOperationalStatusEnum.STOPPED,
            status_message="Manually stopped."
        ),
        FeedStatusData(
            feed_id="feed3", 
            config=FeedConfigInfo(name="Feed Three", source_type="test_source", source_identifier="source3"), 
            status=FeedOperationalStatusEnum.STARTING,
            status_message="Initializing feed processor..."
        ),
        FeedStatusData(
            feed_id="feed4", 
            config=FeedConfigInfo(name="Feed Error", source_type="test_source", source_identifier="source4_error"), 
            status=FeedOperationalStatusEnum.ERROR,
            status_message="Failed to connect to source.",
            error_details="Connection timeout after 3 attempts."
        ),
    ]
    return test_feeds_data

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
async def ingest_traffic_data(data: TrafficData, current_user: dict = Depends(get_current_active_user)):
    """Endpoint to ingest real-time traffic data. Requires authentication."""
    user_email = current_user.get("email") # Example user info access
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

@router.get("/v1/traffic-data")
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

@router.get("/v1/signals")
async def get_signals(
    current_user: dict = Depends(get_current_active_user),
    tss: TrafficSignalService = Depends(get_tss)
):
    """Endpoint to retrieve the list of traffic signals. Requires authentication."""
    user_email = current_user.get("email")
    # logger.info(f"Signal list retrieved by user: {user_email}")
    try:
        signals = await tss.get_all_signals()
        return signals
    except TrafficSignalControlError as e:
        # logger.error(f"Error retrieving signals for user {user_email}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except Exception as e:
        # logger.error(f"Unexpected error retrieving signals for user {user_email}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred while retrieving signals.")

@router.post("/v1/signals/{signal_id}/set_phase")
async def set_signal_phase(
    signal_id: str, 
    phase: str, 
    current_user: dict = Depends(get_current_active_user),
    tss: TrafficSignalService = Depends(get_tss)
):
    """Endpoint to update the phase of a traffic signal. Requires authentication."""
    valid_phases = ["red", "yellow", "green", "flashing_red", "flashing_yellow", "off"] # Example phases
    if phase.lower() not in valid_phases:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid phase. Valid phases are: {', '.join(valid_phases)}")

    user_email = current_user.get("email")
    # logger.info(f"User {user_email} attempting to set phase for signal {signal_id}.")

    try:
        success = await tss.set_signal_phase(signal_id, phase.lower())
        if success:
            return {"message": f"Signal {signal_id} phase change to {phase.lower()} initiated successfully by user {user_email}"}
        else:
            # This case might be hit if the service internally decides not to proceed (e.g. base_url not set and returns False)
            # Or if the external API call was made but indicated failure in a way that didn't raise an exception in the service.
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to set phase for signal {signal_id}. The control service reported an issue.")
    except TrafficSignalControlError as e:
        # logger.error(f"Control error setting phase for signal {signal_id} by user {user_email}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except Exception as e:
        # logger.error(f"Unexpected error setting phase for signal {signal_id} by user {user_email}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An unexpected error occurred while setting signal phase for {signal_id}.")

@router.get("/v1/test-auth", summary="Test authentication")
async def test_auth(current_user: dict = Depends(get_current_active_user)):
    """
    Test endpoint to verify authentication is working.
    Returns user information if authentication is successful.
    """
    return {
        "message": "Authentication successful",
        "user": {
            "uid": current_user.get("uid"),
            "email": current_user.get("email"),
            "name": current_user.get("name"),
        }
    }

app = FastAPI()
app.include_router(analytics_router, prefix="/v1/analytics")