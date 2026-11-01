from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any, List
from datetime import datetime
import enum
import json


class AlertSeverityEnum(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    ERROR = "ERROR"


class Alert(BaseModel):
    id: int = Field(..., example=101, description="Unique identifier for the alert")
    timestamp: datetime = Field(
        ..., description="Timestamp of when the alert was generated (UTC)"
    )
    severity: AlertSeverityEnum = Field(
        ...,
        example=AlertSeverityEnum.WARNING,
        description="Severity level of the alert",
    )
    feed_id: Optional[str] = Field(
        None,
        example="cam_feed_003",
        description="Identifier of the feed that generated the alert, if applicable",
    )
    message: str = Field(
        ...,
        example="Unusual traffic congestion detected on Main St.",
        description="A concise message describing the alert",
    )
    latitude: Optional[float] = Field(
        None,
        example=34.0522,
        description="Latitude of the alert location, if applicable",
    )
    longitude: Optional[float] = Field(
        None,
        example=-118.2437,
        description="Longitude of the alert location, if applicable",
    )
    details: Optional[Dict[str, Any]] = Field(
        None,
        example={"average_speed_kmh": 10, "expected_speed_kmh": 50},
        description="Optional dictionary containing detailed information or metrics related to the alert",
    )
    acknowledged: bool = Field(
        default=False,
        description="Whether the alert has been acknowledged by a user or system",
    )
    acknowledged_by: Optional[str] = Field(
        None,
        example="user_admin_01",
        description="Identifier of the user/system that acknowledged the alert",
    )
    acknowledged_at: Optional[datetime] = Field(
        None, description="Timestamp of when the alert was acknowledged (UTC)"
    )

    @field_validator("details", mode="before")
    @classmethod
    def _parse_details(cls, v):
        # The alerts.details column is TEXT holding JSON; rows arrive here as
        # str, and pydantic v2 does NOT coerce str -> dict in lax mode, so
        # without this every Alert(**row) with a non-null details raised
        # ValidationError (surfaced as a 500 on GET /api/v1/alerts).
        if isinstance(v, str) and v.strip():
            try:
                return json.loads(v)
            except (json.JSONDecodeError, ValueError):
                return {"raw": v}
        return v
    source_component: Optional[str] = Field(
        None,
        example="anomaly_detection_service",
        description="The backend component that generated the alert",
    )
    tags: Optional[List[str]] = Field(
        None,
        example=["congestion", "high_priority"],
        description="Optional tags for categorizing or filtering alerts",
    )
