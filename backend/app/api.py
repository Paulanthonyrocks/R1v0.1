from typing import List, Dict, Any
from enum import Enum
from fastapi import FastAPI, APIRouter, HTTPException, Depends

from app.services.services import get_feed_manager
from app.services.feed_manager import FeedManager


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