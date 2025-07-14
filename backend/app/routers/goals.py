from fastapi import APIRouter, Depends, HTTPException
from typing import Any, Dict

from app.dependencies import get_agent_core
from app.core.agent_core import AgentCore
from app.core.dynamic_planner import Goal

router = APIRouter()

@router.post("/goals", response_model=Goal)
def set_goal(goal: Goal, agent: AgentCore = Depends(get_agent_core)) -> Any:
    """
    Set a new goal for the agent.
    """
    agent.set_goal(goal)
    return goal

@router.delete("/goals", response_model=Dict[str, str])
def clear_goal(agent: AgentCore = Depends(get_agent_core)) -> Any:
    """
    Clear the current goal from the agent.
    """
    agent.clear_goal()
    return {"message": "Goal cleared successfully"}
