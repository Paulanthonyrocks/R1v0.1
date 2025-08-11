from pydantic import BaseModel, Field, validator, constr, confloat, conint
from typing import Dict, Union, Literal
from datetime import datetime, timezone

from app.models.traffic import LocationModel


class RawTrafficDataInputModel(BaseModel):
    sensor_id: constr(regex=r'^sensor_') = Field(...) # Regex validation for sensor_id
    timestamp: datetime
    location: LocationModel
    vehicle_count: int = Field(..., ge=0)
    average_speed: float = Field(..., ge=0, le=300)
    congestion_level: float = Field(..., ge=0, le=100)


    @validator('timestamp')
    def timestamp_must_be_timezone_aware(cls, v):
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError('timestamp must be timezone aware')
        return v

    @validator('location')
    def location_must_be_valid(cls, v):
        if not (-90 <= v.lat <= 90 and -180 <= v.lon <= 180):
            raise ValueError('location latitude and longitude must be within valid ranges')
        return v

class ProcessedTrafficDataDBModel(RawTrafficDataInputModel):
    congestion_score: float = Field(..., ge=0, le=100)
    processing_timestamp: datetime
    status: Literal['validated', 'processed', 'error', 'dlq'] = "validated" # Validate status against allowed values
    hour_of_day: conint(ge=0, le=23)
    day_of_week: int
    is_weekend: bool
    road_type: str
    weather_conditions: Dict[str, Union[str, float]]
    truck_percentage: float
    is_outlier: bool
    incident_occurred: int = Field(..., ge=0, le=1)

    @validator('processing_timestamp')
    def processing_timestamp_must_be_timezone_aware(cls, v):
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError('processing_timestamp must be timezone aware')
        return v

    @validator('weather_conditions')
    def weather_conditions_must_have_expected_keys(cls, v):
        expected_keys = ['temperature', 'precipitation'] # Example keys, adjust as needed
        if not all(key in v for key in expected_keys):
            raise ValueError(f'weather_conditions must contain keys: {expected_keys}')
        return v


class RegionalAggregatedTrafficDBModel(BaseModel):
    region_id: constr(regex=r'_region$') = Field(...) # Regex validation for region_id
    window_start_time: datetime
    average_congestion_score: float = Field(..., ge=0, le=100)
    sensor_count_in_window: int = Field(..., ge=0)
    message_count_in_window: int = Field(..., ge=0)
