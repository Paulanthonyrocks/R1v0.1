# models.py
from pydantic import BaseModel, validator, Field
from typing import Dict, Union, List

class LocationModel(BaseModel):
    latitude: float
    longitude: float

class RawTrafficDataInputModel(BaseModel):
    sensor_id: str
    timestamp: float  # Unix timestamp
    location: LocationModel
    vehicle_count: int = Field(..., ge=0)  # Must be greater than or equal to 0
    average_speed: float = Field(..., ge=0, le=300) # Speed limit, adjust as needed
    congestion_level: float = Field(..., ge=0, le=100) # Assuming raw congestion_level

    @validator('timestamp')
    def timestamp_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError('Timestamp must be a positive value representing a valid time.')
        return v

class ProcessedTrafficDataDBModel(RawTrafficDataInputModel):
    # Inherits all fields from RawTrafficDataInputModel
    # We will generate a unique _id for MongoDB upserts
    # _id: str # Will be sensor_id + timestamp
    congestion_score: float = Field(..., ge=0, le=100)
    processing_timestamp: int # Unix timestamp of when this record was processed
    status: str = 'validated'

class RegionalAggregatedTrafficDBModel(BaseModel):
    # _id: str # Will be region_id + window_start_time
    region_id: str
    window_start_time: int # Unix timestamp, start of the window
    average_congestion_score: float = Field(..., ge=0, le=100)
    sensor_count_in_window: int = Field(..., ge=0)
    message_count_in_window: int = Field(..., ge=0)