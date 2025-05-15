from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid
import enum

class LocationModel(BaseModel):
    latitude: float = Field(..., example=34.0522, description="Latitude coordinate")
    longitude: float = Field(..., example=-118.2437, description="Longitude coordinate")

class TrafficData(BaseModel):
    timestamp: datetime = Field(..., description="Timestamp of the data point, preferably UTC", example="2024-01-01T12:00:00Z")
    sensor_id: str = Field(..., example="sensor_123", description="Unique identifier for the sensor")
    location: LocationModel
    speed: Optional[float] = Field(None, example=65.5, description="Average speed of vehicles in km/h")
    occupancy: Optional[float] = Field(None, example=0.75, description="Lane occupancy rate (0.0 to 1.0)")
    vehicle_count: Optional[int] = Field(None, example=15, description="Number of vehicles detected")

class AggregatedTrafficTrend(BaseModel):
    region_id: str = Field(..., example="downtown_sector_1", description="Identifier for the geographic region")
    start_time: datetime = Field(..., description="Start of the aggregation window (UTC)")
    end_time: datetime = Field(..., description="End of the aggregation window (UTC)")
    average_congestion_score: float = Field(..., ge=0, le=100, example=65.2, description="Average congestion score for the period")
    contributing_sensors_count: int = Field(..., ge=0, example=10, description="Number of sensors contributing to this aggregation")
    total_vehicle_detections: Optional[int] = Field(None, ge=0, example=1205, description="Total vehicle detections in the window")
    peak_hour: Optional[str] = Field(None, example="17:00", description="Identified peak hour within the window, if applicable")

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
    REPORTED = "reported" # Initial status when first logged

class IncidentReport(BaseModel):
    incident_id: uuid.UUID = Field(default_factory=uuid.uuid4, description="Unique identifier for the incident")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Timestamp of when the incident was reported or detected (UTC)")
    location: LocationModel
    type: IncidentTypeEnum = Field(..., example=IncidentTypeEnum.CONGESTION, description="Type of incident")
    severity: IncidentSeverityEnum = Field(..., example=IncidentSeverityEnum.HIGH, description="Severity of the incident")
    description: str = Field(..., example="Heavy traffic backup due to stalled vehicle.", description="Textual description of the incident")
    source_feed_id: Optional[str] = Field(None, example="feed_traffic_cam_001", description="Optional ID of the data feed that triggered or reported the incident")
    related_vehicle_ids: Optional[List[str]] = Field(None, example=["vehicle_track_123", "plate_ABC123"], description="Optional list of related vehicle identifiers")
    status: IncidentStatusEnum = Field(default=IncidentStatusEnum.REPORTED, description="Current status of the incident")
    last_updated: datetime = Field(default_factory=datetime.utcnow, description="Timestamp of the last update to this incident report (UTC)")
    estimated_clearance_time: Optional[datetime] = Field(None, description="Optional estimated time when the incident might be cleared (UTC)")
    image_url: Optional[str] = Field(None, example="https://example.com/incident_image.jpg", description="Optional URL to an image related to the incident")
    
    # Ensure last_updated is modified on updates
    # This would typically be handled in the business logic layer when an incident is updated,
    # rather than directly in the Pydantic model on instantiation of an existing record.
    # Pydantic v2 offers `model_validator(mode='before')` or specific field validators for more complex cases. 