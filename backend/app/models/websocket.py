from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import Optional, Dict, Any, Union, List
from datetime import datetime, timezone
import enum

# Import FeedStatusData directly to avoid redefinition mismatch
# app.models.feeds does not import websocket, so this is safe.
from app.models.feeds import FeedStatusData

# --- 1. Standardization: Use ISO Strings for WS Timestamps ---
def get_utc_now_str() -> str:
    return datetime.now(timezone.utc).isoformat()

# --- 2. Local DTO definitions ---
# Defined locally to avoid circular imports and enforce string timestamps for WebSocket payloads.

class AlertData(BaseModel):
    id: str
    severity: str
    message: str
    timestamp: str

class SignalStateData(BaseModel):
    signal_id: str
    current_phase: str
    status: str
    timestamp: str

# FeedStatusData is imported from app.models.feeds

# --- 3. Specific Payload Models ---

class RealtimeMetricsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    feed_id: str = Field(..., description="ID of the feed generating the metrics")
    timestamp: str = Field(default_factory=get_utc_now_str)
    metrics: Dict[str, Any] = Field(
        ...,
        description="Key-value pairs of metrics",
        example={"vehicle_count": 15, "avg_speed_kmh": 45.6},
    )

class GlobalRealtimeMetrics(BaseModel):
    timestamp: str = Field(default_factory=get_utc_now_str)
    metrics_source: Optional[str] = Field(None, description="Source of the metrics")
    congestion_index: Optional[float] = Field(None, example=45.5)
    average_speed_kmh: Optional[float] = Field(None, example=30.2)
    active_incidents_count: Optional[int] = Field(None, example=3)
    total_flow: Optional[int] = Field(None, example=1250)
    global_health_score: Optional[float] = Field(None, example=88.5)
    feed_statuses: Optional[Dict[str, int]] = Field(
        None, example={"running": 5, "stopped": 2, "error": 1}
    )
    custom_metrics: Optional[Dict[str, Any]] = None

class NewAlertNotification(BaseModel):
    alert_data: AlertData

class SignalStateUpdate(BaseModel):
    signal_data: SignalStateData

class FeedStatusUpdate(BaseModel):
    feed_status_data: FeedStatusData

class GeneralNotification(BaseModel):
    message_type: str = Field(..., example="system_maintenance_scheduled")
    title: Optional[str] = None
    message: str
    severity: str = Field(default="info", pattern="^(info|warning|error)$")
    suggested_actions: Optional[List[str]] = None
    timestamp: str = Field(default_factory=get_utc_now_str)

class ErrorNotification(BaseModel):
    error_code: Optional[str] = None
    message: str
    details: Optional[str] = None
    timestamp: str = Field(default_factory=get_utc_now_str)

class AlertStatusUpdatePayload(BaseModel):
    alert_id: Union[int, str]
    status: str
    timestamp: str = Field(default_factory=get_utc_now_str)

class NodeCongestionUpdateData(BaseModel):
    id: str
    name: str
    latitude: float
    longitude: float
    congestion_score: Optional[float] = None
    vehicle_count: Optional[int] = None
    average_speed: Optional[float] = None
    timestamp: str = Field(default_factory=get_utc_now_str)

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
    issued_at: str = Field(default_factory=get_utc_now_str)

# --- 4. Specific Payload Models for WebSocket Communication ---

class PingData(BaseModel):
    timestamp: str = Field(default_factory=get_utc_now_str)

class PongData(BaseModel):
    timestamp: str = Field(default_factory=get_utc_now_str)

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
    model_config = ConfigDict(extra="forbid")

    feed_id: str
    frame: str = Field(..., description="Base64 encoded string of the JPEG frame")
    frame_index: int
    timestamp: str
    metrics: Optional[Dict[str, Any]] = None
    vehicles: Optional[List[Dict[str, Any]]] = None

class InitialFeedStatusesData(BaseModel):
    feeds: List[FeedStatusData]

class UpdateFeedConfigData(BaseModel):
    feed_id: str
    updates: Dict[str, Any]

    @model_validator(mode="after")
    def _validate_roi_shape(self) -> "UpdateFeedConfigData":
        """Reject malformed ROI payloads at the WS boundary.

        feed_manager.update_feed_config raises ValueError("ROI must be a list
        of [x1, y1, x2, y2] coordinates.") for a roi that is a list whose
        elements aren't 4-tuples. Earlier that error fired inside a
        fire-and-forget create_task and was logged-and-lost (asyncio
        "Task exception was never retrieved"). Validating here turns a silent
        failure into a concrete client-facing error.

        CONTRACT CORRECTION (2026-08-24 run): the real ROI wire format is a
        POLYGON of points — FeedConfigInfo.roi is List[Dict[str, float]],
        CoreModule._initialize_roi_mask feeds the points to cv2.fillPoly, and
        the dashboard sends [{x: float, y: float}, ...]. The original 4-tuple
        box rule here (and in feed_manager) was written against an imagined
        contract and rejected every legitimate ROI save. Validate the actual
        shape: a list of >=3 {x, y} dicts or [x, y] pairs.
        """
        roi = self.updates.get("roi")
        if roi is not None:
            if not isinstance(roi, list) or len(roi) < 3:
                raise ValueError(
                    "ROI must be a polygon: a list of at least 3 "
                    "{'x': float, 'y': float} points."
                )
            for pt in roi:
                if isinstance(pt, dict):
                    if "x" not in pt or "y" not in pt:
                        raise ValueError(
                            "ROI points must be {'x': float, 'y': float} dicts."
                        )
                elif isinstance(pt, (list, tuple)):
                    if len(pt) != 2:
                        raise ValueError(
                            "ROI points must be [x, y] pairs with exactly 2 values."
                        )
                else:
                    raise ValueError(
                        "ROI must be a polygon: a list of at least 3 "
                        "{'x': float, 'y': float} points."
                    )
        return self

# --- 5. WebSocket Message Wrapper ---

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
    UPDATE_FEED_CONFIG = "update_feed_config"
    REFRESH_FEED = "refresh_feed"
    RESTART_FEED = "restart_feed"
    START_FEED = "start_feed"
    STOP_FEED = "stop_feed"
    SUBSCRIBE_TO_FEED = "subscribe_to_feed"
    UNSUBSCRIBE_FROM_FEED = "unsubscribe_from_feed"
    GET_INITIAL_FEED_STATUSES = "get_initial_feed_statuses"
    
    # Snapshot / Incident Notifications
    SNAPSHOT_READY = "snapshot_ready"
    
    # Internal
    INTERNAL_PING = "__internal_ping"
    INTERNAL_PONG = "__internal_pong"

class WebSocketMessage(BaseModel):
    """
    Optimized Wrapper. 
    'data' is generic Any/Dict to prevent Pydantic from running 
    expensive Union validation on every frame.
    """
    type: WebSocketMessageTypeEnum = Field(..., description="The type of event")
    data: Optional[Dict[str, Any]] = None 
    client_id: Optional[str] = None
    correlation_id: Optional[str] = None
    timestamp: Optional[float] = None

    model_config = ConfigDict(use_enum_values=True, extra="allow")

    # Helper method to parse data only when needed
    def parse_data(self, model_class):
        if self.data is None:
            return None
        return model_class.model_validate(self.data)