from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timezone
import uuid
import enum


class LocationModel(BaseModel):
    latitude: float = Field(..., example=34.0522, description="Latitude coordinate")
    longitude: float = Field(..., example=-118.2437, description="Longitude coordinate")
    name: Optional[str] = Field(
        None,
        example="Downtown Intersection",
        description="Optional display name for the location",
    )


class TrafficData(BaseModel):
    timestamp: datetime = Field(
        ...,
        description="Timestamp of the data point, preferably UTC",
        example="2024-01-01T12:00:00Z",
    )
    sensor_id: str = Field(
        ..., example="sensor_123", description="Unique identifier for the sensor"
    )
    location: LocationModel
    speed: Optional[float] = Field(
        None, example=65.5, description="Average speed of vehicles in km/h"
    )
    occupancy: Optional[float] = Field(
        None, example=0.75, description="Lane occupancy rate (0.0 to 1.0)"
    )
    vehicle_count: Optional[int] = Field(
        None, example=15, description="Number of vehicles detected"
    )


class AggregatedTrafficTrend(BaseModel):
    region_id: str = Field(
        ...,
        example="downtown_sector_1",
        description="Identifier for the geographic region",
    )
    start_time: datetime = Field(
        ..., description="Start of the aggregation window (UTC)"
    )
    end_time: datetime = Field(..., description="End of the aggregation window (UTC)")
    average_congestion_score: float = Field(
        ...,
        ge=0,
        le=100,
        example=65.2,
        description="Average congestion score for the period",
    )
    contributing_sensors_count: int = Field(
        ...,
        ge=0,
        example=10,
        description="Number of sensors contributing to this aggregation",
    )
    total_vehicle_detections: Optional[int] = Field(
        None, ge=0, example=1205, description="Total vehicle detections in the window"
    )
    peak_hour: Optional[str] = Field(
        None,
        example="17:00",
        description="Identified peak hour within the window, if applicable",
    )


class IncidentTypeEnum(str, enum.Enum):
    ACCIDENT = "ACCIDENT"
    STALLED_VEHICLE = "STALLED_VEHICLE"
    STOPPED_VEHICLE = "STOPPED_VEHICLE" # Keep for backward compatibility with my recent change
    DEBRIS = "DEBRIS"
    ILLEGAL_PARKING = "ILLEGAL_PARKING"
    WRONG_WAY = "WRONG_WAY"
    PEDESTRIAN_HAZARD = "PEDESTRIAN_HAZARD"
    TRAFFIC_JAM = "TRAFFIC_JAM"
    CONGESTION = "CONGESTION"
    ROAD_WORK = "ROAD_WORK"
    WEATHER_HAZARD = "WEATHER_HAZARD"
    OTHER = "OTHER"


class IncidentSeverityEnum(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class IncidentStatusEnum(str, enum.Enum):
    NEW = "NEW"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    INVESTIGATING = "INVESTIGATING"
    RESOLVED = "RESOLVED"
    CLEARED = "CLEARED" # match old enum if needed
    FALSE_ALARM = "FALSE_ALARM"
    REPORTED = "REPORTED" # match old enum if needed


class IncidentReport(BaseModel):
    incident_id: uuid.UUID = Field(
        default_factory=uuid.uuid4, description="Unique identifier for the incident"
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of when the incident was reported or detected (UTC)",
    )
    location: LocationModel
    type: IncidentTypeEnum = Field(
        ..., example=IncidentTypeEnum.CONGESTION, description="Type of incident"
    )
    severity: IncidentSeverityEnum = Field(
        ..., example=IncidentSeverityEnum.HIGH, description="Severity of the incident"
    )
    description: str = Field(
        ...,
        example="Heavy traffic backup due to stalled vehicle.",
        description="Textual description of the incident",
    )
    source_feed_id: Optional[str] = Field(
        None,
        example="feed_traffic_cam_001",
        description="Optional ID of the data feed that triggered or reported the incident",
    )
    related_vehicle_ids: Optional[List[str]] = Field(
        None,
        example=["vehicle_track_123", "plate_ABC123"],
        description="Optional list of related vehicle identifiers",
    )
    status: IncidentStatusEnum = Field(
        default=IncidentStatusEnum.REPORTED,
        description="Current status of the incident",
    )
    last_updated: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of the last update to this incident report (UTC)",
    )
    estimated_clearance_time: Optional[datetime] = Field(
        None,
        description="Optional estimated time when the incident might be cleared (UTC)",
    )
    image_url: Optional[str] = Field(
        None,
        example="https://example.com/incident_image.jpg",
        description="Optional URL to an image related to the incident",
    )

    # Ensure last_updated is modified on updates
    # This would typically be handled in the business logic layer when an incident is updated,
    # rather than directly in the Pydantic model on instantiation of an existing record.
    # Pydantic v2 offers `model_validator(mode='before')` or specific field validators for more complex cases.


class IncidentReportUpdate(BaseModel):
    """Pydantic model for updating an existing IncidentReport."""

    #incident_id: Optional[uuid.UUID] = Field(None, description="Unique identifier for the incident") # Incident ID is usually in the path, not the body
    timestamp: Optional[datetime] = Field(
        None,
        description="Timestamp of when the incident was reported or detected (UTC)",
    )
    location: Optional[LocationModel] = None
    type: Optional[IncidentTypeEnum] = Field(
        None, example=IncidentTypeEnum.CONGESTION, description="Type of incident"
    )
    severity: Optional[IncidentSeverityEnum] = Field(
        None, example=IncidentSeverityEnum.HIGH, description="Severity of the incident"
    )
    description: Optional[str] = Field(
        None,
        example="Heavy traffic backup due to stalled vehicle.",
        description="Textual description of the incident",
    )
    source_feed_id: Optional[str] = Field(
        None,
        example="feed_traffic_cam_001",
        description="Optional ID of the data feed that triggered or reported the incident",
    )
    related_vehicle_ids: Optional[List[str]] = Field(
        None,
        example=["vehicle_track_123", "plate_ABC123"],
        description="Optional list of related vehicle identifiers",
    )
    status: Optional[IncidentStatusEnum] = Field(
        None, description="Current status of the incident"
    )
    # last_updated should be updated by the application logic, not the client
    # estimated_clearance_time: Optional[datetime] = Field(
    #    None, description="Optional estimated time when the incident might be cleared (UTC)"
    # )
    image_url: Optional[str] = Field(
        None, example="https://example.com/incident_image.jpg", description="Optional URL to an image related to the incident"
    )

class AllNodesCongestionResponse(BaseModel):
 message: str = Field(..., description="Placeholder message for congestion response")
