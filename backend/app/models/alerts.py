from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime
import enum

class AlertSeverityEnum(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

class Alert(BaseModel):
    id: int = Field(..., example=101, description="Unique identifier for the alert")
    timestamp: datetime = Field(..., description="Timestamp of when the alert was generated (UTC)")
    severity: AlertSeverityEnum = Field(..., example=AlertSeverityEnum.WARNING, description="Severity level of the alert")
    feed_id: Optional[str] = Field(None, example="cam_feed_003", description="Identifier of the feed that generated the alert, if applicable")
    message: str = Field(..., example="Unusual traffic congestion detected on Main St.", description="A concise message describing the alert")
    details: Optional[Dict[str, Any]] = Field(None, example={"average_speed_kmh": 10, "expected_speed_kmh": 50}, description="Optional dictionary containing detailed information or metrics related to the alert")
    acknowledged: bool = Field(default=False, description="Whether the alert has been acknowledged by a user or system")
    acknowledged_by: Optional[str] = Field(None, example="user_admin_01", description="Identifier of the user/system that acknowledged the alert")
    acknowledged_at: Optional[datetime] = Field(None, description="Timestamp of when the alert was acknowledged (UTC)")
    source_component: Optional[str] = Field(None, example="anomaly_detection_service", description="The backend component that generated the alert")
    tags: Optional[List[str]] = Field(None, example=["congestion", "high_priority"], description="Optional tags for categorizing or filtering alerts") 