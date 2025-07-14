from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from enum import Enum

class GoalStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"

class Goal(BaseModel):
    id: str
    description: str
    status: GoalStatus = GoalStatus.PENDING
    target_kpis: Dict[str, Any] = Field(default_factory=dict)

class AgentPlanner:
    def __init__(self, agent_core):
        self.agent_core = agent_core

    def create_plan(self, goal: Goal, current_state: Dict[str, Any]) -> List[Dict[str, Any]]:
        if "reduce congestion" in goal.description.lower():
            return self._create_congestion_reduction_plan(goal, current_state)
        return []

    def _create_congestion_reduction_plan(self, goal: Goal, current_state: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Simple rule: if congestion is high, extend green light on main street
        if current_state.get("overall_congestion_level") == "HIGH":
            return [
                {
                    "step_id": "step1",
                    "description": "Extend green light on main street",
                    "actions": [
                        {
                            "action_type": "SET_SIGNAL_PHASE",
                            "target_ids": ["TS001"],
                            "parameters": {"phase": "green", "duration_seconds": 120},
                        }
                    ],
                }
            ]
        return []
