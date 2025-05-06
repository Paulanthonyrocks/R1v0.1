# backend/app/routers/alerts.py

# backend/app/models/alerts.py
from typing import List
from datetime import datetime
from pydantic import BaseModel, Field, validator

class AlertItem(BaseModel):
    """
    Represents a single alert item.
    """
    id: int = Field(..., description="Unique identifier for the alert.")
    timestamp: datetime = Field(..., description="Timestamp when the alert occurred.")
    severity: str = Field(..., description="Severity level of the alert (low, medium, high).")
    feed_id: str = Field(..., description="Identifier of the feed that generated the alert.")
    message: str = Field(..., description="Descriptive message of the alert.")

    @validator('severity')
    def validate_severity(cls, value):
        if value not in ['low', 'medium', 'high']:
            raise ValueError("Severity must be one of: low, medium, high")
        return value

class AlertsResponse(BaseModel):
    alerts: List[AlertItem]
    total_count: int
    page: int
    limit: int
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
# Remove the direct import causing the cycle
# from app.dependencies import get_db
from app.utils.utils import DatabaseManager # Import the manager class for type hint
# Remove the redundant/incorrect model import
# from ..models.alerts import AlertItem, AlertsResponse

# Add logging import and setup
import logging
logger = logging.getLogger(__name__)

router = APIRouter()

# Define allowed severity levels based on your logging/alerting system
SEVERITY_LEVELS = ["INFO", "WARNING", "ERROR", "CRITICAL"]

@router.get(
    "/",
    response_model=AlertsResponse,
    summary="Get Alerts",
    description="Retrieves a paginated list of system and traffic alerts, with filtering options.",
)
async def get_alerts(
    severity: Optional[str] = Query(None, description=f"Filter by severity level ({', '.join(SEVERITY_LEVELS)})"),
    feed_id: Optional[str] = Query(None, description="Filter by specific Feed ID"),
    search: Optional[str] = Query(None, description="Search term within alert messages (case-insensitive)"),
    page: int = Query(1, ge=1, description="Page number for pagination"),
    limit: int = Query(50, ge=1, le=200, description="Number of alerts per page"),
    # Remove Depends(get_db) here
) -> AlertsResponse:
    """
    Endpoint to fetch alerts with filtering and pagination.
    """
    # Import get_db locally inside the function
    from app.dependencies import get_db
    db: DatabaseManager = await get_db() # Call the dependency function

    if severity and severity.upper() not in SEVERITY_LEVELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid severity level. Allowed values are: {', '.join(SEVERITY_LEVELS)}"
        )

    filters = {
        "severity": severity.upper() if severity else None,
        "feed_id": feed_id,
        "search": search
    }
    # Calculate offset for pagination
    offset = (page - 1) * limit

    # Use the db instance obtained locally
    print(f"Fetching alerts with filters: {filters}, page: {page}, limit: {limit}")

    try:
        # Use the actual async method from DatabaseManager
        alerts_list = await db.get_alerts_filtered(filters=filters, limit=limit, offset=offset)
        # TODO: Implement a method to count total alerts based on filters
        # total_count = await db.count_alerts_filtered(filters=filters)
        total_count = len(alerts_list) # Placeholder count

        # Convert DB results (dicts) to AlertItem models if necessary
        # This assumes get_alerts_filtered returns dicts matching AlertItem fields
        alert_items = [AlertItem(**alert_data) for alert_data in alerts_list]

        return AlertsResponse(alerts=alert_items, total_count=total_count, page=page, limit=limit)
    except Exception as e:
        logger.error(f"Failed to retrieve alerts: {e}", exc_info=True) # Add logging
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve alerts")