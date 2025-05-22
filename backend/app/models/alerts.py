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
    timestamp: datetime = Field(..., description="Timestamp of alert generation (UTC)")
    severity: AlertSeverityEnum = Field(..., example=AlertSeverityEnum.WARNING,
                                      description="Severity level of the alert")
    feed_id: Optional[str] = Field(None, example="cam_feed_003",
                                   description="Identifier of the feed, if applicable")
    message: str = Field(..., example="Unusual traffic congestion on Main St.",
                         description="A concise message describing the alert")
    details: Optional[Dict[str, Any]] = Field(
        None, example={"avg_speed_kmh": 10, "expected_kmh": 50},
        description="Optional dictionary with detailed alert metrics"
    )
    acknowledged: bool = Field(default=False,
                               description="Has alert been acknowledged")
    acknowledged_by: Optional[str] = Field(
        None, example="user_admin_01",
        description="Identifier of user/system that acknowledged alert"
    )
    acknowledged_at: Optional[datetime] = Field(
        None, description="Timestamp of when alert was acknowledged (UTC)")
    source_component: Optional[str] = Field(
        None, example="anomaly_detection_service",
        description="Backend component that generated the alert"
    )
    tags: Optional[List[str]] = Field(
        None, example=["congestion", "high_priority"],
        description="Optional tags for categorizing/filtering alerts"
    )
