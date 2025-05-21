from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Dict, Any
from app.services.personalized_routing_service import PersonalizedRoutingService
from app.dependencies import get_current_active_user

router = APIRouter()

@router.get("/analytics", summary="Get route history analytics for the current user")
async def get_route_history_analytics(
    current_user: dict = Depends(get_current_active_user),
    limit: int = Query(20, ge=1, le=100)
) -> Dict[str, Any]:
    """
    Returns analytics on the user's route history, such as most common routes, time-of-day patterns, etc.
    """
    service = PersonalizedRoutingService()
    try:
        analytics = service.get_route_history_analytics(user_id=current_user["id"], limit=limit)
        return analytics
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to compute route history analytics: {e}")
