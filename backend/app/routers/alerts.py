# backend/app/routers/alerts.py

from typing import List, Optional
# Removed unused datetime, BaseModel, Field, validator
from fastapi import APIRouter, Depends, HTTPException, Query, status # Added imports

from app.dependencies import get_current_active_user
from app.utils.utils import DatabaseManager # This might need to be async if get_db is async
from app.models.alerts import Alert as AlertModel, AlertSeverityEnum

import logging
logger = logging.getLogger(__name__)

router = APIRouter()

# Define allowed severity levels based on AlertSeverityEnum
SEVERITY_LEVELS = [s.value for s in AlertSeverityEnum]

# Pydantic model for the response
class AlertsResponse(AlertModel): # Assuming AlertModel can serve as base or define new
    alerts: List[AlertModel]
    total_count: int
    page: int
    limit: int


class AlertsResponse(BaseModel):
    alerts: List[AlertModel]
    total_count: int
    page: int
    limit: int


@router.get(
    "/",
    response_model=AlertsResponse,
    summary="Get Alerts",
    description="Retrieves a paginated list of system and traffic alerts.",
)
async def get_alerts(
    severity: Optional[AlertSeverityEnum] = Query(
        None, description=f"Filter by severity. Allowed: {', '.join(SEVERITY_LEVELS)}"
    ),
    feed_id: Optional[str] = Query(None, description="Filter by specific Feed ID"),
    search: Optional[str] = Query(None, description="Search term in alert messages"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=200, description="Alerts per page"),
    current_user: dict = Depends(get_current_active_user) # noqa F841 current_user for auth
) -> AlertsResponse:
    """
    Fetches alerts with filtering and pagination. Requires authentication.
    """
    # Assuming get_db is an async dependency if DatabaseManager methods are async
    from app.dependencies import get_db
    db: DatabaseManager = await get_db() # type: ignore

    filters = {}
    if severity: filters["severity"] = severity.value
    if feed_id: filters["feed_id"] = feed_id
    if search: filters["search"] = search

    offset = (page - 1) * limit
    logger.debug(f"Fetching alerts: filters={filters}, page={page}, limit={limit}")

    try:
        alerts_db = await db.get_alerts_filtered(filters=filters, limit=limit, offset=offset)
        total_alerts = await db.count_alerts_filtered(filters=filters)
        # Ensure data from DB is correctly mapped to AlertModel if needed
        # If get_alerts_filtered already returns dicts that match AlertModel:
        alert_models = [AlertModel(**alert) for alert in alerts_db]
        return AlertsResponse(
            alerts=alert_models, total_count=total_alerts, page=page, limit=limit
        )
    except Exception as e:
        logger.error(f"Failed to retrieve alerts: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Failed to retrieve alerts")
