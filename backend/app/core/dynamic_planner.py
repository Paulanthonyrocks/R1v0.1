from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from enum import Enum

class GoalStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"

class Objective(BaseModel):
    kpi: str
    target_value: Any
    weight: float = 1.0

class Goal(BaseModel):
    id: str
    description: str
    status: GoalStatus = GoalStatus.PENDING
    objectives: List[Objective] = Field(default_factory=list)

class AgentPlanner:
    def __init__(self, agent_core):
        self.agent_core = agent_core

    def create_plan(self, goal: Goal, current_state: Dict[str, Any]) -> List[Dict[str, Any]]:
        return self.a_star_planner(goal, current_state)

    def a_star_planner(self, goal: Goal, current_state: Dict[str, Any]) -> List[Dict[str, Any]]:
        # This is a placeholder for the actual A* planning logic.
        # In a real implementation, this would involve a more complex search process
        # with a priority queue and a cost function that considers the weighted objectives.
        plan = []
        for objective in goal.objectives:
            if "congestion" in objective.kpi:
                plan.extend(self._create_congestion_reduction_plan(goal, current_state))
            elif "incident" in objective.kpi:
                plan.extend(self._create_incident_clearance_plan(goal, current_state))
        return plan

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
                },
                {
                    "step_id": "step2",
                    "description": "Display congestion warning on DMS",
                    "actions": [
                        {
                            "action_type": "SET_DMS_MESSAGE",
                            "target_ids": ["DMS001"],
                            "parameters": {"message": "High congestion on Main St. Expect delays."},
                        }
                    ],
                },
            ]
        return []

    def _create_incident_clearance_plan(self, goal: Goal, current_state: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Simple rule: if there is an incident, dispatch emergency services and set up a detour
        if current_state.get("active_incidents_count") > 0:
            return [
                {
                    "step_id": "step1",
                    "description": "Dispatch emergency services",
                    "actions": [
                        {
                            "action_type": "DISPATCH_EMERGENCY_SERVICES",
                            "target_ids": [current_state.get("active_incidents")[0]["id"]],
                            "parameters": {},
                        }
                    ],
                },
                {
                    "step_id": "step2",
                    "description": "Set up a detour",
                    "actions": [
                        {
                            "action_type": "SET_DETOUR",
                            "target_ids": [current_state.get("active_incidents")[0]["id"]],
                            "parameters": {},
                        }
                    ],
                },
            ]
        return []
