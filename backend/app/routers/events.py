from fastapi import APIRouter, HTTPException, Depends, status
from typing import List, Dict, Any
import logging
from app.services.event_service import EventService
from app.dependency_injection import get_event_service_api

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get(
    "/current",
    response_model=List[Dict[str, Any]],
    summary="Get current events impacting routes",
    description="Get current events like roadwork, accidents, or other incidents that may impact routing",
)
async def get_current_events(
    event_service: EventService = Depends(get_event_service_api),
):
    try:
        return await event_service.get_events()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch current events: {str(e)}",
        )


@router.get(
    "/impacts",
    response_model=List[Dict[str, Any]],
    summary="Get event impacts",
    description="Get formatted impact assessments of current events for route planning",
)
async def get_event_impacts(
    event_service: EventService = Depends(get_event_service_api),
):
    try:
        return await event_service.get_event_impacts()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch event impacts: {str(e)}",
        )
