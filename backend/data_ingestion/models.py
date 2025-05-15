# models.py
from pydantic import BaseModel, validator, Field
from typing import Dict, Union, List
from datetime import datetime

from app.models.traffic import LocationModel

class RawTrafficDataInputModel(BaseModel):
    sensor_id: str
    timestamp: datetime
    location: LocationModel
    vehicle_count: int = Field(..., ge=0)
    average_speed: float = Field(..., ge=0, le=300)
    congestion_level: float = Field(..., ge=0, le=100)

class ProcessedTrafficDataDBModel(RawTrafficDataInputModel):
    congestion_score: float = Field(..., ge=0, le=100)
    processing_timestamp: datetime
    status: str = 'validated'

class RegionalAggregatedTrafficDBModel(BaseModel):
    region_id: str
    window_start_time: datetime
    average_congestion_score: float = Field(..., ge=0, le=100)
    sensor_count_in_window: int = Field(..., ge=0)
    message_count_in_window: int = Field(..., ge=0)