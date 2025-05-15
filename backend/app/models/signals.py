from pydantic import BaseModel, Field
from typing import Optional, Dict, List
from datetime import datetime
import enum

from app.models.traffic import LocationModel # For SignalState location

class SignalPhaseEnum(str, enum.Enum):
    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"
    FLASHING_RED = "flashing_red"
    FLASHING_YELLOW = "flashing_yellow"
    OFF = "off"
    UNKNOWN = "unknown"

class SignalControlStatusEnum(str, enum.Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PENDING = "pending"
    NOT_SUPPORTED = "not_supported"
    ERROR = "error"
    TIMEOUT = "timeout"

class SignalOperationalStatusEnum(str, enum.Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    ERROR = "error"
    MAINTENANCE = "maintenance"
    UNKNOWN = "unknown"

class SignalControlCommandResponse(BaseModel):
    signal_id: str = Field(..., example="signal_intersection_12", description="Identifier of the traffic signal")
    requested_phase: Optional[SignalPhaseEnum] = Field(None, example=SignalPhaseEnum.GREEN, description="The phase that was requested to be set")
    status: SignalControlStatusEnum = Field(..., example=SignalControlStatusEnum.SUCCESS, description="Status of the command execution")
    message: str = Field(..., example="Phase change command accepted.", description="Detailed message about the outcome")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Timestamp of the command response (UTC)")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional details from the control system")

class SignalState(BaseModel):
    signal_id: str = Field(..., example="signal_intersection_12", description="Identifier of the traffic signal")
    current_phase: SignalPhaseEnum = Field(..., example=SignalPhaseEnum.RED, description="Current operational phase of the signal")
    operational_status: SignalOperationalStatusEnum = Field(..., example=SignalOperationalStatusEnum.ONLINE, description="Overall operational status of the signal hardware/software")
    last_updated: datetime = Field(..., description="Timestamp of the last state update from the signal (UTC)")
    location: Optional[LocationModel] = Field(None, description="Geographic location of the signal")
    next_scheduled_phases: Optional[List[Dict[str, Any]]] = Field(None, description="Upcoming scheduled phase changes, if available. Example: [{'phase': 'green', 'start_time': 'ISO_DATETIME_STR'}]")
    error_details: Optional[str] = Field(None, description="Specific error message if status is 'error'")
    capabilities: Optional[List[str]] = Field(None, example=["set_phase", "get_timing_plan"], description="List of capabilities supported by this signal interface") 