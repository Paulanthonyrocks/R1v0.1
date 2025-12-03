from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, Union, List
from datetime import datetime, timezone
import enum

# Ensure these imports exist in your project structure
# or replace them with generic Dicts if those files aren't created yet.
try:
    from app.models.alerts import Alert
    from app.models.signals import SignalState
    from app.models.feeds import FeedStatusData
except ImportError:
    # Fallback placeholders for standalone testing
    class Alert(BaseModel):
        id: str
        severity: str
        message: str
        timestamp: datetime

    class SignalState(BaseModel):
        intersection_id: str
        current_phase: str

    class FeedStatusData(BaseModel):
        feed_id: str
        status: str
        fps: Optional[float] = 0.0

# --- Specific Payload Models ---

class RealtimeMetricsUpdate(BaseModel):
    feed_id: str = Field(..., description="ID of the feed generating the metrics")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metrics: Dict[str, Any] = Field(
        ...,
        description="Key-value pairs of metrics",
        example={"vehicle_count": 15, "avg_speed_kmh": 45.6},
    )

class GlobalRealtimeMetrics(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metrics_source: Optional[str] = Field(None, description="Source of the metrics")
    congestion_index: Optional[float] = Field(None, example=45.5)
    average_speed_kmh: Optional[float] = Field(None, example=30.2)
    active_incidents_count: Optional[int] = Field(None, example=3)
    total_flow: Optional[int] = Field(None, example=1250)
    feed_statuses: Optional[Dict[str, int]] = Field(
        None, example={"running": 5, "stopped": 2, "error": 1}
    )
    custom_metrics: Optional[Dict[str, Any]] = None

class NewAlertNotification(BaseModel):
    alert_data: Alert

class SignalStateUpdate(BaseModel):
    signal_data: SignalState

class FeedStatusUpdate(BaseModel):
    feed_status_data: FeedStatusData

class GeneralNotification(BaseModel):
    message_type: str = Field(..., example="system_maintenance_scheduled")
    title: Optional[str] = None
    message: str
    severity: str = Field(default="info", pattern="^(info|warning|error)$")
    suggested_actions: Optional[List[str]] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class ErrorNotification(BaseModel):
    error_code: Optional[str] = None
    message: str
    details: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class AlertStatusUpdatePayload(BaseModel):
    alert_id: Union[int, str]
    status: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class NodeCongestionUpdateData(BaseModel):
    id: str
    name: str
    latitude: float
    longitude: float
    congestion_score: Optional[float] = None
    vehicle_count: Optional[int] = None
    average_speed: Optional[float] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class NodeCongestionUpdatePayload(BaseModel):
    nodes: List[NodeCongestionUpdateData]

class UserSpecificConditionAlert(BaseModel):
    user_id: str
    alert_type: str
    title: str
    message: str
    severity: str = "info"
    suggested_actions: Optional[List[str]] = None
    route_context: Optional[Dict[str, Any]] = None
    issued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# --- Specific Payload Models for WebSocket Communication ---

class PingData(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class PongData(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class AuthSuccessData(BaseModel):
    message: str = "Authentication successful."
    user_info: Optional[Dict[str, Any]] = None

class AuthFailureData(ErrorNotification):
    pass

class AuthenticateData(BaseModel):
    token: str

class SubscribeData(BaseModel):
    topic: str

class UnsubscribeData(BaseModel):
    topic: str

class FeedIdData(BaseModel):
    feed_id: str

class VideoFrameData(BaseModel):
    feed_id: str
    frame: str = Field(..., description="Base64 encoded string of the JPEG frame")
    frame_index: int
    timestamp: str
    metrics: Optional[Dict[str, Any]] = None
    vehicles: Optional[List[Dict[str, Any]]] = None

class InitialFeedStatusesData(BaseModel):
    feeds: List[FeedStatusData]

# --- WebSocket Message Wrapper ---

class WebSocketMessageTypeEnum(str, enum.Enum):
    # Data Pushes
    METRICS_UPDATE = "metrics_update"
    KPI_UPDATE = "kpi_update"
    NEW_ALERT = "new_alert"
    SIGNAL_UPDATE = "signal_update"
    VIDEO_FRAME = "video_frame"
    FEED_METRICS = "feed_metrics"
    FEED_STATUS_UPDATE = "feed_status_update"
    GENERAL_NOTIFICATION = "general_notification"
    ERROR_NOTIFICATION = "error_notification"
    PREDICTION_ALERT = "prediction_alert"
    ALERT_STATUS_UPDATE = "alert_status_update"
    NODE_CONGESTION_UPDATE = "node_congestion_update"
    USER_SPECIFIC_ALERT = "user_specific_alert"
    INITIAL_FEED_STATUSES = "initial_feed_statuses"
    
    # Connection / Auth
    PONG = "pong"
    AUTH_SUCCESS = "auth_success"
    AUTH_FAILURE = "auth_failure"
    
    # Client Requests (Incoming to Backend)
    AUTHENTICATE = "authenticate"
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    PING = "ping"
    TOKEN_REFRESH_REQUEST = "token_refresh_request"
    
    # Feed Control (Incoming to Backend)
    REFRESH_FEED = "refresh_feed"
    RESTART_FEED = "restart_feed"
    START_FEED = "start_feed"
    STOP_FEED = "stop_feed"
    SUBSCRIBE_TO_FEED = "subscribe_to_feed"
    UNSUBSCRIBE_FROM_FEED = "unsubscribe_from_feed"
    GET_INITIAL_FEED_STATUSES = "get_initial_feed_statuses"
    
    # Internal
    INTERNAL_PING = "__internal_ping"
    INTERNAL_PONG = "__internal_pong"

class WebSocketMessage(BaseModel):
    type: WebSocketMessageTypeEnum = Field(..., description="The type of event")
    data: Optional[
        Union[
            # Payload Models
            RealtimeMetricsUpdate,
            GlobalRealtimeMetrics,
            NewAlertNotification,
            SignalStateUpdate,
            FeedStatusUpdate,
            GeneralNotification,
            ErrorNotification,
            AlertStatusUpdatePayload,
            NodeCongestionUpdatePayload,
            UserSpecificConditionAlert,
            VideoFrameData,
            InitialFeedStatusesData,
            
            # Protocol Models
            PingData,
            PongData,
            AuthSuccessData,
            AuthFailureData,
            AuthenticateData,
            SubscribeData,
            UnsubscribeData,
            
            # Control Models
            FeedIdData, # Used for Start/Stop/Restart/SubscribeToFeed
        ]
    ] = None
    client_id: Optional[str] = None
    correlation_id: Optional[str] = None

    class Config:
        # This helps Pydantic serialize enums to strings in the JSON output
        use_enum_values = True