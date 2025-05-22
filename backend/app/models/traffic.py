from pydantic import BaseModel, Field
from typing import List, Optional # Removed Dict, Any
from datetime import datetime
import uuid
import enum


class LocationModel(BaseModel):
    latitude: float = Field(..., example=34.0522,
                            description="Latitude coordinate")
    longitude: float = Field(..., example=-118.2437,
                             description="Longitude coordinate")


class TrafficData(BaseModel):
    timestamp: datetime = Field(..., description="Timestamp of data point (UTC)",
                                example="2024-01-01T12:00:00Z")
    sensor_id: str = Field(..., example="sensor_123", description="Unique sensor ID")
    location: LocationModel
    speed: Optional[float] = Field(None, example=65.5, description="Avg vehicle speed (km/h)")
    occupancy: Optional[float] = Field(None, example=0.75, description="Lane occupancy (0.0-1.0)")
    vehicle_count: Optional[int] = Field(None, example=15, description="Detected vehicle count")


class AggregatedTrafficTrend(BaseModel):
    region_id: str = Field(..., example="downtown_sector_1", description="Geographic region ID")
    start_time: datetime = Field(..., description="Start of aggregation window (UTC)")
    end_time: datetime = Field(..., description="End of aggregation window (UTC)")
    average_congestion_score: float = Field(..., ge=0, le=100, example=65.2,
                                            description="Average congestion score (0-100)")
    contributing_sensors_count: int = Field(..., ge=0, example=10,
                                            description="Number of sensors in aggregation")
    total_vehicle_detections: Optional[int] = Field(None, ge=0, example=1205,
                                                    description="Total vehicle detections")
    peak_hour: Optional[str] = Field(None, example="17:00",
                                     description="Identified peak hour, if applicable")


class IncidentTypeEnum(str, enum.Enum):
    CONGESTION = "congestion"
    ACCIDENT = "accident"
    STOPPED_VEHICLE = "stopped_vehicle"
    ROAD_WORK = "road_work"
    WEATHER_HAZARD = "weather_hazard"
    OTHER = "other"


class IncidentSeverityEnum(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentStatusEnum(str, enum.Enum):
    ACTIVE = "active"
    CLEARED = "cleared"
    INVESTIGATING = "investigating"
    REPORTED = "reported"  # Initial status when first logged


class IncidentReport(BaseModel):
    incident_id: uuid.UUID = Field(default_factory=uuid.uuid4, description="Unique incident ID")
    timestamp: datetime = Field(default_factory=datetime.utcnow,
                                description="Incident report/detection timestamp (UTC)")
    location: LocationModel
    type: IncidentTypeEnum = Field(..., example=IncidentTypeEnum.CONGESTION, description="Incident type")
    severity: IncidentSeverityEnum = Field(..., example=IncidentSeverityEnum.HIGH,
                                          description="Incident severity")
    description: str = Field(..., example="Heavy traffic backup due to stalled vehicle.",
                             description="Textual incident description")
    source_feed_id: Optional[str] = Field(
        None, example="feed_traffic_cam_001",
        description="Optional ID of the data feed that reported/triggered the incident"
    )
    related_vehicle_ids: Optional[List[str]] = Field(
        None, example=["vehicle_track_123", "plate_ABC123"],
        description="Optional list of related vehicle identifiers"
    )
    status: IncidentStatusEnum = Field(default=IncidentStatusEnum.REPORTED,
                                       description="Current incident status")
    last_updated: datetime = Field(default_factory=datetime.utcnow,
                                   description="Last update timestamp for this report (UTC)")
    estimated_clearance_time: Optional[datetime] = Field(
        None, description="Optional estimated incident clearance time (UTC)"
    )
    image_url: Optional[str] = Field(
        None, example="https://example.com/incident_image.jpg",
        description="Optional URL to an image related to the incident"
    )
    # Note on last_updated: Pydantic models don't auto-update fields on modification post-init.
    # This field should be updated by application logic when an incident record is changed.
