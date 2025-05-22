from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Any # Added Any
from datetime import datetime
import enum

from app.models.traffic import LocationModel  # For SignalState location


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
    signal_id: str = Field(..., example="signal_intersection_12", description="Traffic signal ID")
    requested_phase: Optional[SignalPhaseEnum] = Field(
        None, example=SignalPhaseEnum.GREEN, description="Requested phase"
    )
    status: SignalControlStatusEnum = Field(..., example=SignalControlStatusEnum.SUCCESS,
                                           description="Status of the command execution")
    message: str = Field(..., example="Phase change command accepted.", description="Outcome message")
    timestamp: datetime = Field(default_factory=datetime.utcnow,
                                description="Command response timestamp (UTC)")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional control system details")


class SignalState(BaseModel):
    signal_id: str = Field(..., example="signal_intersection_12", description="Traffic signal ID")
    current_phase: SignalPhaseEnum = Field(..., example=SignalPhaseEnum.RED,
                                           description="Current operational signal phase")
    operational_status: SignalOperationalStatusEnum = Field(
        ..., example=SignalOperationalStatusEnum.ONLINE,
        description="Overall operational status of signal hardware/software"
    )
    last_updated: datetime = Field(..., description="Last state update timestamp (UTC)")
    location: Optional[LocationModel] = Field(None, description="Geographic signal location")
    next_scheduled_phases: Optional[List[Dict[str, Any]]] = Field(
        None, description="Upcoming scheduled phase changes (e.g., [{'phase': 'green', 'start_time': 'ISO_STR'}])"
    )
    error_details: Optional[str] = Field(None, description="Error message if status is 'error'")
    capabilities: Optional[List[str]] = Field(
        None, example=["set_phase", "get_timing_plan"],
        description="Capabilities supported by this signal interface"
    )
