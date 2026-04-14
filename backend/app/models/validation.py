from pydantic import BaseModel, Field, validator
from typing import Optional

class CoordinateModel(BaseModel):
    """Base model for GPS coordinates validation."""
    latitude: float = Field(..., ge=-90, le=90, description="Latitude between -90 and 90")
    longitude: float = Field(..., ge=-180, le=180, description="Longitude between -180 and 180")

class TimeRangeModel(BaseModel):
    """Base model for time range validation."""
    start_time: Optional[float] = None
    end_time: Optional[float] = None

    @validator("end_time")
    def end_after_start(cls, v, values):
        if v is not None and values.get("start_time") is not None:
            if v < values["start_time"]:
                raise ValueError("end_time must be after start_time")
        return v

class PaginationParams(BaseModel):
    """Base model for pagination parameters."""
    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)

class SearchQuery(BaseModel):
    """Base model for text search queries."""
    q: str = Field(..., min_length=1, max_length=100)
    limit: int = Field(10, ge=1, le=50)
