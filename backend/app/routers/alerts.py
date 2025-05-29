# backend/app/routers/alerts.py

from fastapi import APIRouter, Query, HTTPException, status
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field, validator

from app.dependencies import get_current_active_user, get_db
from app.utils.utils import DatabaseManager
from app.models.alerts import Alert as AlertModel, AlertSeverityEnum

import logging
logger = logging.getLogger(__name__)

router = APIRouter()

# Define allowed severity levels based on AlertSeverityEnum
SEVERITY_LEVELS = [s.value for s in AlertSeverityEnum]

class AlertsResponse(BaseModel):
    alerts: List[AlertModel]
    total_count: int
    page: int
    limit: int

@router.get(
    "/",
    response_model=AlertsResponse,
    summary="Get Alerts",
    description="Retrieves a paginated list of system and traffic alerts, with filtering options.",
)
async def get_alerts(
    severity: Optional[AlertSeverityEnum] = Query(None, description=f"Filter by severity level. Allowed: {', '.join(SEVERITY_LEVELS)}"),
    feed_id: Optional[str] = Query(None, description="Filter by specific Feed ID"),
    search: Optional[str] = Query(None, description="Search term within alert messages (case-insensitive)"),    page: int = Query(1, ge=1, description="Page number for pagination"),
    limit: int = Query(50, ge=1, le=200, description="Number of alerts per page"),
    current_user: dict = Depends(get_current_active_user)
>>>>>>> 842672b3021dd5bce5734aa0d0c3de99ba171936
) -> AlertsResponse:
    """
    Endpoint to fetch alerts with filtering and pagination. Requires authentication.
    """
<<<<<<< HEAD
=======
    from app.dependencies import get_db
>>>>>>> 842672b3021dd5bce5734aa0d0c3de99ba171936
    db: DatabaseManager = await get_db()

    filters = {
        "severity": severity.value if severity else None,
        "feed_id": feed_id,
        "search": search
    }
    offset = (page - 1) * limit

    logger.debug(f"Fetching alerts with filters: {filters}, page: {page}, limit: {limit}")

    try:
        alerts_data_from_db = await db.get_alerts_filtered(filters=filters, limit=limit, offset=offset)
        total_count = await db.count_alerts_filtered(filters=filters)
        
        alert_items = [AlertModel(**alert_data) for alert_data in alerts_data_from_db]

        return AlertsResponse(alerts=alert_items, total_count=total_count, page=page, limit=limit)
    except Exception as e:
        logger.error(f"Failed to retrieve alerts: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve alerts")