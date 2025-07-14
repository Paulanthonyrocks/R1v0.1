from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from enum import Enum

class ActionType(str, Enum):
    SET_SIGNAL_PHASE = "set_signal_phase"
    SET_DMS_MESSAGE = "set_dms_message"
    ADJUST_SPEED_LIMIT = "adjust_speed_limit"

class Action(BaseModel):
    action_type: ActionType
    target_ids: List[str]
    parameters: Dict[str, Any] = Field(default_factory=dict)
