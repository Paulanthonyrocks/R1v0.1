from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from datetime import datetime
from enum import Enum
from app.models.traffic import IncidentTypeEnum, IncidentSeverityEnum, IncidentStatusEnum

# Use existing Enums from traffic.py to maintain consistency
IncidentType = IncidentTypeEnum
IncidentSeverity = IncidentSeverityEnum
IncidentStatus = IncidentStatusEnum

class IncidentBase(BaseModel):
    feed_id: Optional[str] = None
    type: IncidentType
    severity: IncidentSeverity
    description: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    snapshot_path: Optional[str] = None # Path to image file
    
class IncidentCreate(IncidentBase):
    pass

class IncidentUpdate(BaseModel):
    status: Optional[IncidentStatus] = None
    description: Optional[str] = None
    assigned_to: Optional[str] = None
    resolution_notes: Optional[str] = None

class Incident(IncidentBase):
    id: str
    status: IncidentStatus = IncidentStatus.REPORTED # Default to REPORTED matches traffic.py
    timestamp: float
    created_at: datetime
    updated_at: datetime
    assigned_to: Optional[str] = None
    resolution_notes: Optional[str] = None
    
    class Config:
        from_attributes = True
