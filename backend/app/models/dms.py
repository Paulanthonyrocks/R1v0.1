from pydantic import BaseModel, Field
from typing import Optional, List, Any
from datetime import datetime
import enum

from app.models.traffic import LocationModel
from app.models.signals import SignalControlStatusEnum # Reusing for command response

class DmsStatusEnum(str, enum.Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    ERROR = "error"
    MAINTENANCE = "maintenance"
    UNKNOWN = "unknown"

class DmsMessage(BaseModel):
    text: str = Field(..., example="ROAD CLOSED AHEAD", description="Text content of the message page.")
    page_number: Optional[int] = Field(1, example=1, description="Page number for multi-page messages.")
    duration_seconds: Optional[int] = Field(None, example=30, description="How long this specific page should be displayed, if supported.")
    # Could add other VMS-specific fields like font, color, flashing, etc. later

class DmsState(BaseModel):
    dms_id: str = Field(..., example="dms_mains_001", description="Unique identifier for the DMS.")
    location: LocationModel = Field(..., description="Geographic location of the DMS.")
    current_messages: Optional[List[DmsMessage]] = Field(None, description="List of message pages currently displayed or queued.")
    operational_status: DmsStatusEnum = Field(..., example=DmsStatusEnum.ONLINE, description="Operational status of the DMS unit.")
    last_updated: datetime = Field(default_factory=datetime.utcnow, description="Timestamp of the last state update from the DMS (UTC).")
    capabilities: Optional[List[str]] = Field(None, example=["set_custom_message", "clear_message", "max_pages_3"], description="List of capabilities supported by this DMS interface.")
    target_roadway_segment_id: Optional[str] = Field(None, example="main_st_seg_04", description="ID of the roadway segment this DMS primarily informs traffic for.")
    viewable_directions: Optional[List[str]] = Field(None, example=["NB", "SB"], description="Cardinal directions from which the DMS is primarily viewable/relevant.")

class DmsCommandResponse(BaseModel):
    dms_id: str = Field(..., example="dms_mains_001", description="Identifier of the DMS to which the command was sent.")
    status: SignalControlStatusEnum = Field(..., example=SignalControlStatusEnum.SUCCESS, description="Status of the command execution (reusing SignalControlStatusEnum).")
    message: str = Field(..., example="Message set successfully.", description="Detailed message about the outcome.")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Timestamp of the command response (UTC).")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional details from the DMS control system, if any.")
