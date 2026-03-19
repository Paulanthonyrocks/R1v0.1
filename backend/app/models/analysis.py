from typing import List, Dict, Any, Optional, ClassVar
from datetime import datetime, timezone
from pydantic import BaseModel, Field

from app.models import traffic
from app.models.validation import SanitizedBaseModel


class TrendDataPoint(SanitizedBaseModel):
    timestamp: datetime
    total_vehicles: Optional[int] = None
    avg_speed: Optional[float] = None
    congestion_index: Optional[float] = None
    speeding_vehicles: Optional[int] = None
    high_density_lanes: Optional[int] = None


class LocationPredictionRequest(SanitizedBaseModel):
    location: traffic.LocationModel
    prediction_time: Optional[datetime] = None
    prediction_window_hours: Optional[int] = Field(default=24, ge=1, le=168)
    include_historical_context: Optional[bool] = Field(default=True)


class PredictionResponse(SanitizedBaseModel):
    location: traffic.LocationModel
    prediction_time: datetime
    incident_likelihood: float = Field(..., ge=0, le=1)
    confidence_score: float = Field(..., ge=0, le=1)
    contributing_factors: List[str]
    recommendations: List[str]
    historical_context: Optional[Dict[str, Any]]


class TrendQuery(SanitizedBaseModel):
    start_time: datetime
    end_time: datetime
    region_id: Optional[str] = None
    sensor_ids: Optional[List[str]] = None
    aggregation_interval_minutes: Optional[int] = Field(60, ge=5)


class AnomalyDetectionRequest(SanitizedBaseModel):
    traffic_data_points: List[traffic.TrafficData]


class IncidentPredictionRequest(SanitizedBaseModel):
    location: traffic.LocationModel
    prediction_time: Optional[datetime] = None


class NodeCongestionData(SanitizedBaseModel):
    id: str = Field(
        ...,
        description="Unique identifier for the node (e.g., 'lat,lon' string or a specific ID).",
    )
    name: str = Field(..., description="Display name for the node.")
    latitude: float = Field(..., description="Latitude of the node.")
    longitude: float = Field(..., description="Longitude of the node.")
    congestion_score: Optional[float] = Field(
        None, description="Calculated congestion score for the node (0-100)."
    )
    vehicle_count: Optional[int] = Field(
        None, description="Number of vehicles detected at the node."
    )
    average_speed: Optional[float] = Field(
        None, description="Average speed of vehicles at the node (km/h)."
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of the latest data point for this node.",
    )

    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "id": "34.0522,-118.2437",
                "name": "Node at (34.0522, -118.2437)",
                "latitude": 34.0522,
                "longitude": -118.2437,
                "congestion_score": 65.5,
                "vehicle_count": 50,
                "average_speed": 25.0,
                "timestamp": "2023-10-27T10:30:00Z",
            }
        }


class AllNodesCongestionResponse(SanitizedBaseModel):
    nodes: List[NodeCongestionData]
    schema_extra: ClassVar[dict] = {
        "example": {
            "nodes": [
                {
                    "id": "34.0522,-118.2437",
                    "name": "Node at (34.0522, -118.2437)",
                    "latitude": 34.0522,
                    "longitude": -118.2437,
                    "congestion_score": 65.5,
                    "vehicle_count": 50,
                    "average_speed": 25.0,
                    "timestamp": "2023-10-27T10:30:00Z",
                },
                {
                    "id": "40.7128,-74.0060",
                    "name": "Node at (40.7128, -74.0060)",
                    "latitude": 40.7128,
                    "longitude": -74.0060,
                    "congestion_score": 30.2,
                    "vehicle_count": 20,
                    "average_speed": 45.0,
                    "timestamp": "2023-10-27T10:35:00Z",
                },
            ]
        }
    }
