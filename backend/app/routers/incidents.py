from fastapi import APIRouter, Depends
from datetime import datetime, timedelta
from typing import List
from app.dependencies import get_feed_manager
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

@router.get("/api/v1/incidents", summary="Get live incidents (real or mock)")
async def get_incidents(fm: FeedManager = Depends(get_feed_manager)) -> List[dict]:
    """Return a list of incidents generated from real feed analytics if available, else mock data."""
    # Try to get all feeds and their latest_metrics
    try:
        feeds = await fm.get_all_feed_statuses() if hasattr(fm, 'get_all_feed_statuses') else []
        incidents = []
        for feed in feeds:
            metrics = getattr(feed, 'latest_metrics', None)
            if not metrics:
                continue
            # Example: If congestion_index > 0.7, create a congestion incident
            congestion = metrics.get('congestion_index')
            if congestion is not None and congestion > 0.7:
                incidents.append({
                    "id": f"congestion-{feed.feed_id}",
                    "type": "congestion",
                    "description": f"High congestion detected (index: {congestion:.2f})",
                    "latitude": metrics.get('latitude', 37.7749),
                    "longitude": metrics.get('longitude', -122.4194),
                    "timestamp": datetime.utcnow().isoformat() + 'Z',
                })
            # Add more rules for other incident types as needed
        if incidents:
            return incidents
    except Exception as e:
        # Log error if needed
        pass
    # Fallback to mock data
    return MOCK_INCIDENTS 