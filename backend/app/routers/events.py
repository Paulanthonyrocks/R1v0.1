from fastapi import APIRouter, HTTPException, Depends
from typing import List, Dict, Any
from app.services.event_service import EventService
from app.dependencies import get_event_service_api

router = APIRouter()

@router.get(
    "/current",
    response_model=List[Dict[str, Any]],
    summary="Get current events impacting routes",
    description="Get current events like roadwork, accidents, or other incidents that may impact routing"
)
async def get_current_events(
    event_service: EventService = Depends(get_event_service_api)
):
    """Get current events affecting routes"""
    return await event_service.get_events()

@router.get(
    "/impacts",
    response_model=List[Dict[str, Any]],
    summary="Get event impacts",
    description="Get formatted impact assessments of current events for route planning"
)
async def get_event_impacts(
    event_service: EventService = Depends(get_event_service_api)
):
    """Get event impact assessments"""
    return await event_service.get_event_impacts()
