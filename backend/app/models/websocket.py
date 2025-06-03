from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, Union, List
from datetime import datetime
import enum

from app.models.alerts import Alert
from app.models.signals import SignalState
from app.models.feeds import FeedStatusData # For feed status updates

# --- Specific Payload Models ---
class RealtimeMetricsUpdate(BaseModel):
    feed_id: str = Field(..., description="ID of the feed generating the metrics")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Timestamp of the metrics update (UTC)")
    metrics: Dict[str, Any] = Field(..., description="Key-value pairs of metrics", example={"vehicle_count": 15, "avg_speed_kmh": 45.6})

class GlobalRealtimeMetrics(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Timestamp of the metrics update (UTC)")
    metrics_source: Optional[str] = Field(None, description="Source of the metrics, e.g., 'FeedManagerGlobalKPIs', 'SystemOverall'")
    congestion_index: Optional[float] = Field(None, example=45.5, description="Overall congestion index for the monitored area")
    average_speed_kmh: Optional[float] = Field(None, example=30.2, description="Average speed across relevant feeds or areas")
    active_incidents_count: Optional[int] = Field(None, example=3, description="Current count of active incidents")
    total_flow: Optional[int] = Field(None, example=1250, description="Total vehicle flow (e.g., count) across all active feeds in the current interval")
    feed_statuses: Optional[Dict[str, int]] = Field(None, example={"running": 5, "stopped": 2, "error": 1}, description="Counts of feeds by status")
    custom_metrics: Optional[Dict[str, Any]] = Field(None, description="Flexible field for other global metrics")

class NewAlertNotification(BaseModel):
    alert_data: Alert # Embed the full Alert model

class SignalStateUpdate(BaseModel):
    signal_data: SignalState # Embed the full SignalState model

class FeedStatusUpdate(BaseModel):
    feed_status_data: FeedStatusData # Embed the full FeedStatusData model

class GeneralNotification(BaseModel):
    message_type: str = Field(..., example="system_maintenance_scheduled", description="Type of general notification")
    title: Optional[str] = Field(None, example="System Update")
    message: str = Field(..., example="System will undergo maintenance tonight from 2 AM to 3 AM UTC.")
    severity: str = Field(default="info", example="info|warning|error", description="Severity of the notification")
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class ErrorNotification(BaseModel):
    error_code: Optional[str] = Field(None, example="E5001_FEED_CONNECTION_FAILED")
    message: str = Field(..., example="Failed to connect to video feed XYZ.")
    details: Optional[str] = Field(None, description="Additional technical details about the error")
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class AlertStatusUpdatePayload(BaseModel):
    alert_id: Union[int, str] = Field(..., description="ID of the alert that was updated.")
    status: str = Field(..., example="acknowledged", description="New status of the alert (e.g., 'acknowledged', 'dismissed', 'resolved').")
    # Optional: include more details if needed by the frontend
    # details: Optional[Dict[str, Any]] = Field(None, description="Additional details about the status update.")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Timestamp of when the status update occurred.")

# For Node Congestion Updates via WebSocket
class NodeCongestionUpdateData(BaseModel):
    id: str = Field(..., description="Unique identifier for the node.")
    name: str = Field(..., description="Display name for the node.")
    latitude: float = Field(..., description="Latitude of the node.")
    longitude: float = Field(..., description="Longitude of the node.")
    congestion_score: Optional[float] = Field(None, description="Calculated congestion score for the node (0-100).")
    vehicle_count: Optional[int] = Field(None, description="Number of vehicles detected at the node.")
    average_speed: Optional[float] = Field(None, description="Average speed of vehicles at the node (km/h).")
    timestamp: datetime = Field(..., description="Timestamp of the latest data point for this node.")
    # Add other fields if needed, ensure consistency with NodeCongestionData in routers/analytics.py

class NodeCongestionUpdatePayload(BaseModel):
    nodes: List[NodeCongestionUpdateData] = Field(..., description="List of node congestion data updates.")


# --- WebSocket Message Wrapper ---
class WebSocketMessageTypeEnum(str, enum.Enum):
    METRICS_UPDATE = "metrics_update"
    GLOBAL_REALTIME_METRICS_UPDATE = "global_realtime_metrics_update"
    NEW_ALERT = "new_alert"
    SIGNAL_UPDATE = "signal_update"
    FEED_STATUS_UPDATE = "feed_status_update"
    GENERAL_NOTIFICATION = "general_notification"
    ERROR_NOTIFICATION = "error_notification"
    PREDICTION_ALERT = "prediction_alert"  # New type for predictions
    ALERT_STATUS_UPDATE = "alert_status_update" # For acknowledged, dismissed alerts
    NODE_CONGESTION_UPDATE = "node_congestion_update" # For broadcasting node congestion data
    # Add other specific event types as needed
    PONG = "pong" # For keep-alive
    AUTH_SUCCESS = "auth_success"
    AUTH_FAILURE = "auth_failure"

class WebSocketMessage(BaseModel):
    event_type: WebSocketMessageTypeEnum = Field(..., description="The type of event this message represents")
    payload: Union[
        RealtimeMetricsUpdate, 
        GlobalRealtimeMetrics,
        NewAlertNotification, 
        SignalStateUpdate, 
        FeedStatusUpdate,
        GeneralNotification, 
        ErrorNotification,
        AlertStatusUpdatePayload,
        NodeCongestionUpdatePayload, # Added new payload type
        Dict[str, Any] # For simple payloads like pong or auth status
    ]
    client_id: Optional[str] = Field(None, description="Identifier for a specific client if the message is targeted, otherwise None for broadcast.")
    correlation_id: Optional[str] = Field(None, description="Optional ID to correlate requests and responses if applicable")