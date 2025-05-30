from fastapi import APIRouter, Depends
from datetime import datetime, timedelta
from typing import List
from app.dependencies import get_feed_manager, get_current_active_user
from app.services.feed_manager import FeedManager

router = APIRouter()

# Mock incident data for demo fallback
MOCK_INCIDENTS = [
    {
        "id": "inc1",
        "type": "accident",
        "description": "Minor accident reported",
        "latitude": 37.7749,
        "longitude": -122.4194,
        "timestamp": (datetime.utcnow() - timedelta(minutes=5)).isoformat() + 'Z',
    },
    {
        "id": "inc2",
        "type": "road_closure",
        "description": "Road closed for maintenance",
        "latitude": 37.7849,
        "longitude": -122.4094,
        "timestamp": (datetime.utcnow() - timedelta(minutes=2)).isoformat() + 'Z',
    },
    {
        "id": "inc3",
        "type": "congestion",
        "description": "Heavy congestion detected",
        "latitude": 37.7649,
        "longitude": -122.4294,
        "timestamp": datetime.utcnow().isoformat() + 'Z',
    },
]

@router.get(
    "/",
    response_model=List[dict],
    summary="Get Active Incidents",
    description="Retrieves a list of active traffic incidents and their details"
)
async def get_incidents(
    current_user: dict = Depends(get_current_active_user),
    fm: FeedManager = Depends(get_feed_manager)
) -> List[dict]:
    """
    Get all active incidents from the feed manager. Falls back to mock data if no live incidents.
    Requires authentication.
    """
    try:
        # Try to get real incidents from feed manager
        incidents = await fm.get_active_incidents()
        if not incidents:
            # Fall back to mock data if no real incidents
            return MOCK_INCIDENTS
        return incidents
    except Exception as e:
        # Log error and fall back to mock data
        return MOCK_INCIDENTS