from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from app.dependency_injection import get_feed_manager, get_current_active_user, get_db
from app.services.feed_manager import FeedManager
from app.models.traffic import IncidentReport  # Import IncidentReport and update model
from app.models.common import APIResponse
from app.utils.database import DatabaseManager
import logging

router = APIRouter()

logger = logging.getLogger(__name__)


@router.get(
    "/",
    response_model=APIResponse[List[dict]],
    summary="Get Active Incidents", # Changed from List[dict]
    description="Retrieves a list of active traffic incidents and their details",
)
async def get_incidents(
    current_user: dict = Depends(get_current_active_user),
    fm: FeedManager = Depends(get_feed_manager),
) -> APIResponse[List[IncidentReport]]:  # Changed return type hint
    logger.info(f"GET /incidents endpoint called by user: {current_user.get('email')}") # Ensure consistent logging
    """
    Get all active incidents from the feed manager.
    Requires authentication.
    """
    incidents = await fm.get_active_incidents()
    return APIResponse.success(
        data=incidents, message="Active incidents retrieved successfully."
    )


@router.post(
    "/",
    response_model=APIResponse[IncidentReport],
    summary="Report New Incident",
    description="Allows authenticated users to report a new traffic incident.",
    status_code=status.HTTP_201_CREATED,
)
async def report_incident(
    incident: IncidentReport,
    db: DatabaseManager = Depends(get_db),
    current_user: dict = Depends(get_current_active_user),
) -> APIResponse[IncidentReport]:
    logger.info(f"POST /incidents/ endpoint called by user: {current_user.get('email')}")
    """
    Report a new incident.
    Requires authentication.
    """
    # The save_incident method should handle generating the incident_id and timestamp
    saved_incident = await db.save_incident(incident)
    return APIResponse.success(
        data=saved_incident, message="Incident reported successfully."
    )


@router.get(
    "/{incident_id}",
    response_model=APIResponse[IncidentReport],
    summary="Get Incident Details",
    description="Retrieves details for a specific incident by ID.",
)
async def get_incident(
    incident_id: str,
    db: DatabaseManager = Depends(get_db),
    current_user: dict = Depends(get_current_active_user),
) -> APIResponse[IncidentReport]:
    logger.info(f"GET /incidents/{incident_id} endpoint called by user: {current_user.get('email')}")
    """
    Get details of a specific incident.
    Requires authentication.
    """
    incident = await db.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return APIResponse.success(data=incident, message="Incident details retrieved successfully.")


# TODO: Implement PUT /incidents/{incident_id} for updating incidents
# This would require a Pydantic model for update data (e.g., IncidentReportUpdate)
# and a corresponding update method in the DatabaseManager.
