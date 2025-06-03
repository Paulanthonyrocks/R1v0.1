# backend/app/routers/alerts.py

from fastapi import APIRouter, Query, HTTPException, status, Depends
from typing import List, Optional, Union
from datetime import datetime
from pydantic import BaseModel, Field, validator

from app.dependencies import get_current_active_user, get_db, get_connection_manager # Added get_connection_manager
from app.utils.utils import DatabaseManager
from app.models.alerts import Alert as AlertModel, AlertSeverityEnum
from app.websocket.connection_manager import ConnectionManager # Added ConnectionManager
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum, AlertStatusUpdatePayload # Added WS models

import logging
logger = logging.getLogger(__name__)

router = APIRouter()

# Pydantic model for the PATCH request body
class AcknowledgeRequest(BaseModel):
    acknowledged: bool

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
    search: Optional[str] = Query(None, description="Search term within alert messages (case-insensitive)"),
    page: int = Query(1, ge=1, description="Page number for pagination"),
    limit: int = Query(50, ge=1, le=200, description="Number of alerts per page"),
    current_user: dict = Depends(get_current_active_user)
) -> AlertsResponse:
    """
    Endpoint to fetch alerts with filtering and pagination. Requires authentication.
    """
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
        # Assuming count_alerts_filtered exists or will be added to DatabaseManager if total_count is crucial here.
        # For now, focusing on getting the alerts. A full count might be intensive.
        # total_count = await db.count_alerts_filtered(filters=filters)
        # Simplified total_count for now, or it needs to be implemented in DBManager
        total_count = len(alerts_data_from_db) # This is only count for current page if not all fetched.
                                               # For a proper total_count, db.count_alerts_filtered is needed.
                                               # Assuming it exists for the sake of this example.
        
        # Placeholder for actual count method:
        try:
            actual_total_count = await db.count_alerts_filtered(filters=filters)
        except AttributeError: # If count_alerts_filtered doesn't exist
            logger.warning("DatabaseManager.count_alerts_filtered method not found. Total count might be inaccurate.")
            actual_total_count = len(alerts_data_from_db) # Fallback

        alert_items = [AlertModel(**alert_data) for alert_data in alerts_data_from_db]

        return AlertsResponse(alerts=alert_items, total_count=actual_total_count, page=page, limit=limit)
    except Exception as e:
        logger.error(f"Failed to retrieve alerts: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve alerts")


@router.delete(
    "/{alert_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an Alert",
    description="Deletes a specific alert by its ID.",
)
async def delete_alert_endpoint(
    alert_id: int,
    db: DatabaseManager = Depends(get_db),
    current_user: dict = Depends(get_current_active_user), # Assuming admin/specific role check might be here or decorator
    conn_manager: ConnectionManager = Depends(get_connection_manager)
):
    logger.info(f"User '{current_user.get('username')}' attempting to delete alert ID: {alert_id}")

    # TODO: Add role-based access control if needed, e.g., only admins can delete.

    deleted = await db.delete_alert(alert_id)
    if not deleted:
        logger.warning(f"Alert ID {alert_id} not found for deletion by user '{current_user.get('username')}'.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Alert with ID {alert_id} not found.")

    logger.info(f"Alert ID {alert_id} successfully deleted by user '{current_user.get('username')}'.")

    # Broadcast alert status update
    status_payload = AlertStatusUpdatePayload(alert_id=alert_id, status="dismissed")
    ws_message = WebSocketMessage(
        event_type=WebSocketMessageTypeEnum.ALERT_STATUS_UPDATE,
        payload=status_payload
    )
    try:
        await conn_manager.broadcast_message_model(ws_message)
        logger.info(f"Broadcasted alert status update for dismissed alert ID {alert_id}.")
    except Exception as e:
        logger.error(f"Failed to broadcast alert status update for dismissed alert ID {alert_id}: {e}", exc_info=True)

    return # Returns 204 No Content automatically


@router.patch(
    "/{alert_id}/acknowledge",
    response_model=AlertModel,
    summary="Acknowledge or Unacknowledge an Alert",
    description="Updates the acknowledgement status of a specific alert by its ID.",
)
async def acknowledge_alert_endpoint(
    alert_id: int,
    ack_request: AcknowledgeRequest,
    db: DatabaseManager = Depends(get_db),
    current_user: dict = Depends(get_current_active_user),
    conn_manager: ConnectionManager = Depends(get_connection_manager)
) -> AlertModel:
    logger.info(f"User '{current_user.get('username')}' attempting to set_acknowledge for alert ID: {alert_id} to {ack_request.acknowledged}")

    # TODO: Add role-based access control if needed.

    success = await db.acknowledge_alert(alert_id=alert_id, acknowledge=ack_request.acknowledged)
    if not success:
        logger.warning(f"Alert ID {alert_id} not found for acknowledge by user '{current_user.get('username')}'.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Alert with ID {alert_id} not found.")

    updated_alert_data = await db.get_alert_by_id(alert_id)
    if not updated_alert_data:
        # This should ideally not happen if acknowledge_alert returned True
        logger.error(f"Alert ID {alert_id} was acknowledged but could not be refetched by user '{current_user.get('username')}'.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Alert with ID {alert_id} not found after update.")

    logger.info(f"Alert ID {alert_id} acknowledgement status set to {ack_request.acknowledged} by user '{current_user.get('username')}'.")

    # Broadcast alert status update
    status_str = "acknowledged" if ack_request.acknowledged else "unacknowledged"
    status_payload = AlertStatusUpdatePayload(alert_id=alert_id, status=status_str)
    ws_message = WebSocketMessage(
        event_type=WebSocketMessageTypeEnum.ALERT_STATUS_UPDATE,
        payload=status_payload
    )
    try:
        await conn_manager.broadcast_message_model(ws_message)
        logger.info(f"Broadcasted alert status update for {status_str} alert ID {alert_id}.")
    except Exception as e:
        logger.error(f"Failed to broadcast alert status update for {status_str} alert ID {alert_id}: {e}", exc_info=True)

    return AlertModel(**updated_alert_data)