from fastapi import APIRouter, Depends
from datetime import datetime, timedelta
from typing import List
from app.dependencies import get_feed_manager, get_current_active_user
from app.services.feed_manager import FeedManager
from app.models.common import APIResponse
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get(
    "/",
    response_model=APIResponse[List[dict]],
    summary="Get Active Incidents",
    description="Retrieves a list of active traffic incidents and their details"
)
async def get_incidents(
    current_user: dict = Depends(get_current_active_user),
    fm: FeedManager = Depends(get_feed_manager)
) -> APIResponse[List[dict]]:
    logger.info(f"GET /incidents endpoint called by user: {current_user.get('email')}")
    """
    Get all active incidents from the feed manager.
    Requires authentication.
    """
    incidents = await fm.get_active_incidents()
    return APIResponse.success(data=incidents, message="Active incidents retrieved successfully.")