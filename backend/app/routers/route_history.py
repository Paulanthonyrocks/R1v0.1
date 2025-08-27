from fastapi import APIRouter, Depends, Query, HTTPException, status
from typing import Dict, Any
import logging
from app.services.personalized_routing_service import PersonalizedRoutingService
from app.dependency_injection import get_current_active_user, get_prs

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/analytics", summary="Get route history analytics for the current user")
async def get_route_history_analytics(
    current_user: dict = Depends(get_current_active_user),
    limit: int = Query(20, ge=1, le=100),
) -> Dict[str, Any]:
    logger.info(
        f"GET /route-history/analytics endpoint called by user: {current_user.get('email')}"
    )
    """
    Returns analytics on the user's route history, such as most common routes, time-of-day patterns, etc.
    """
    service: PersonalizedRoutingService = Depends(get_prs)
    try:
        analytics = service.get_route_history_analytics(
            user_id=current_user["id"], limit=limit
        )
        return analytics
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to compute route history analytics: {e}",
        )
