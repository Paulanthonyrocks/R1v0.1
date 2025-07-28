from sqlalchemy import Column, String, DateTime, JSON, Boolean, Float
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from pydantic import BaseModel

PredictionLogBase = declarative_base()


class PredictionLogModel(PredictionLogBase):
    __tablename__ = "prediction_logs"

    id = Column(String, primary_key=True, index=True)
    prediction_made_at = Column(DateTime, default=datetime.now(timezone.utc))
    location_name = Column(String, nullable=True)
    location_latitude = Column(Float)
    location_longitude = Column(Float)
    predicted_event_start_time = Column(DateTime)
    predicted_event_end_time = Column(DateTime)
    prediction_type = Column(String)  # e.g., "incident_likelihood", "congestion_level"
    predicted_value = Column(JSON)  # Store the full prediction dictionary
    source_of_prediction = Column(String)  # e.g., "PredictionScheduler", "Manual"
    outcome_verified = Column(Boolean, default=False)
    actual_outcome_type = Column(
        String, nullable=True
    )  # e.g., "incident_occurred", "no_event_detected"
    actual_outcome_details = Column(
        JSON, nullable=True
    )  # Details about the actual outcome
    verified_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<PredictionLog(id='{self.id}', type='{self.prediction_type}', location='{self.location_name}')>"


# Pydantic model for data transfer (optional, but good practice)
class PredictionLogCreate(BaseModel):
    location_name: Optional[str] = None
    location_latitude: float
    location_longitude: float
    predicted_event_start_time: datetime
    predicted_event_end_time: datetime
    prediction_type: str
    predicted_value: Dict[str, Any]
    source_of_prediction: str


class PredictionLogResponse(PredictionLogCreate):
    id: str
    prediction_made_at: datetime
    outcome_verified: bool
    actual_outcome_type: Optional[str] = None
    actual_outcome_details: Optional[Dict[str, Any]] = None
    verified_at: Optional[datetime] = None

    class Config:
        from_attributes = True
