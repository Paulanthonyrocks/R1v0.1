# models.py
from pydantic import BaseModel, Field # Removed validator
# Removed Dict, Union, List as they are not used
from datetime import datetime

from app.models.traffic import LocationModel


class RawTrafficDataInputModel(BaseModel):
    sensor_id: str
    timestamp: datetime
    location: LocationModel # Assuming LocationModel is defined elsewhere and imported
    vehicle_count: int = Field(..., ge=0)
    average_speed: float = Field(..., ge=0, le=300) # Max speed example
    congestion_level: float = Field(..., ge=0, le=100) # Percentage


class ProcessedTrafficDataDBModel(RawTrafficDataInputModel):
    congestion_score: float = Field(..., ge=0, le=100) # Calculated score
    processing_timestamp: datetime # When this record was processed
    status: str = 'validated' # Example status


class RegionalAggregatedTrafficDBModel(BaseModel):
    region_id: str
    window_start_time: datetime # Start of the aggregation window
    average_congestion_score: float = Field(..., ge=0, le=100)
    sensor_count_in_window: int = Field(..., ge=0) # Number of unique sensors
    message_count_in_window: int = Field(..., ge=0) # Total messages aggregated
