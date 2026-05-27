from fastapi import APIRouter, Depends, HTTPException, Body, Query, status
from typing import List, Dict, Optional, Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field  # Added for SuggestionFeedbackRequest
import logging

from app.models.traffic import LocationModel
from app.models.routing import (
    PersonalizedRouteRequest,
    PersonalizedRouteResponse,
    RouteHistoryEntry,
    UserRoutingProfile,
    SupportedAreasResponse,
)
from app.dependency_injection import get_current_active_user, get_personalized_routing_service, get_route_optimization_service
from app.services.personalized_routing_service import PersonalizedRoutingService
from app.services.route_optimization_service import RouteOptimizationService

router = APIRouter()
logger = logging.getLogger(__name__)


class RouteOptimizationRequest(BaseModel):
    start_location: LocationModel
    end_location: LocationModel
    departure_time: Optional[datetime] = None
    preferences: Optional[Dict[str, Any]] = Field(
        default={
            "include_alternatives": True,
            "avoid_highways": False,
            "minimize_congestion": True,
        }
    )


# Pydantic model for suggestion feedback request body
class SuggestionFeedbackRequest(BaseModel):
    suggestion_id: str
    interaction_status: str  # e.g., "accepted", "rejected", "ignored", "modified"
    feedback_text: Optional[str] = None
    rating: Optional[int] = Field(
        None, ge=1, le=5, description="User rating for the suggestion, 1-5"
    )


@router.post(
    "/personalized",
    response_model=PersonalizedRouteResponse,
    summary="Get Personalized Route",
    description="Get an AI-optimized route based on user preferences and historical patterns",
)
async def get_personalized_route(
    request: PersonalizedRouteRequest = Body(...),
    current_user: Dict = Depends(get_current_active_user),
    routing_service: PersonalizedRoutingService = Depends(get_personalized_routing_service),
) -> PersonalizedRouteResponse:
    logger.info(
        f"POST /personalized endpoint called by user: {current_user.get('uid')}"
    )
    """Get a personalized route based on user preferences"""
    try:
        # Ensure the user_id matches the authenticated user
        if request.user_id != current_user["uid"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User ID in request does not match authenticated user",
            )

        return await routing_service.get_personalized_route(request)

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting personalized route: {str(e)}",
        )


@router.post(
    "/history",
    status_code=201,
    summary="Record Route History",
    description="Record a completed route in user's history",
)
async def record_route_history(
    entry: RouteHistoryEntry = Body(...),
    current_user: Dict = Depends(get_current_active_user),
    routing_service: PersonalizedRoutingService = Depends(get_personalized_routing_service),
) -> Dict[str, str]:
    logger.info(f"POST /history endpoint called by user: {current_user.get('uid')}")
    """Record a route in user's history"""
    try:
        # Ensure the user_id matches the authenticated user
        if entry.user_id != current_user["uid"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User ID in entry does not match authenticated user",
            )

        await routing_service.record_route_history(entry)
        return {"message": "Route history recorded successfully"}

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error recording route history: {str(e)}",
        )


@router.get(
    "/profile",
    response_model=UserRoutingProfile,
    summary="Get User Routing Profile",
    description="Get user's routing preferences and learned patterns",
)
async def get_user_profile(
    current_user: Dict = Depends(get_current_active_user),
    routing_service: PersonalizedRoutingService = Depends(get_personalized_routing_service),
) -> UserRoutingProfile:
    logger.info(f"GET /profile endpoint called by user: {current_user.get('uid')}")
    """Get user's routing profile"""
    try:
        return await routing_service.get_user_profile(current_user["uid"])
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting user profile: {str(e)}",
        )


@router.get(
    "/history",
    response_model=List[RouteHistoryEntry],
    summary="Get Route History",
    description="Get user's route history",
)
async def get_route_history(
    limit: int = Query(default=50, ge=1, le=1000),
    current_user: Dict = Depends(get_current_active_user),
    routing_service: PersonalizedRoutingService = Depends(get_personalized_routing_service),
) -> List[RouteHistoryEntry]:
    logger.info(f"GET /history endpoint called by user: {current_user.get('uid')}")
    """Get user's route history"""
    try:
        return await routing_service.get_user_route_history(
            user_id=current_user["uid"], limit=limit
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting route history: {str(e)}",
        )


@router.get(
    "/history/analytics", 
    summary="Get route history analytics for the current user",
    description="Returns analytics on the user's route history, such as most common routes, time-of-day patterns, etc.",
)
async def get_route_history_analytics(
    current_user: dict = Depends(get_current_active_user),
    routing_service: PersonalizedRoutingService = Depends(get_personalized_routing_service),
    limit: int = Query(20, ge=1, le=100),
) -> Dict[str, Any]:
    logger.info(
        f"GET /routes/history/analytics endpoint called by user: {current_user.get('email')}"
    )
    try:
        analytics = await routing_service.get_route_history_analytics(
            user_id=current_user["uid"], limit=limit
        )
        return analytics
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to compute route history analytics: {e}",
        )


@router.post(
    "/suggestions/feedback",
    summary="Record Feedback on Proactive Suggestion",
    description="Allows users to submit feedback (acceptance, rejection, rating, text) on a proactive route suggestion they received.",
    status_code=200,
)
async def record_suggestion_feedback_endpoint(
    feedback_data: SuggestionFeedbackRequest = Body(...),
    current_user: Dict = Depends(get_current_active_user),
    routing_service: PersonalizedRoutingService = Depends(get_personalized_routing_service),
):
    """
    Records feedback for a given proactive suggestion.
    """
    try:
        success = await routing_service.record_suggestion_feedback(
            suggestion_id=feedback_data.suggestion_id,
            user_id=current_user["uid"], 
            interaction_status=feedback_data.interaction_status,
            feedback_text=feedback_data.feedback_text,
            rating=feedback_data.rating,
        )

        if success:
            return {"message": "Feedback recorded successfully"}
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Suggestion ID not found, user mismatch, or invalid data. Feedback not recorded.",
            )

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred while recording feedback: {str(e)}",
        )


@router.post(
    "/optimize",
    response_model=Dict[str, Any],
    summary="Get Optimized Route",
    description="Get an AI-optimized route with traffic predictions and recommendations",
)
async def optimize_route(
    request: RouteOptimizationRequest = Body(...),
    optimization_service: RouteOptimizationService = Depends(
        get_route_optimization_service
    ),
    current_user: Dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    logger.info(f"POST /optimize endpoint called by user: {current_user.get('uid')}")
    """Get an optimized route with traffic predictions"""
    try:
        return await optimization_service.get_optimized_route(
            start_location=request.start_location,
            end_location=request.end_location,
            departure_time=request.departure_time,
            preferences=request.preferences,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Route optimization failed: {str(e)}",
        )


@router.get(
    "/supported-areas",
    response_model=SupportedAreasResponse,
    summary="Get Supported Areas",
    description="Get areas where route optimization is available",
)
async def get_supported_areas(
    _: Dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """Get areas where route optimization is available"""
    return {
        "supported_areas": [
            {
                "name": "Downtown Area",
                "bounds": {
                    "north": 34.0522 + 0.1,
                    "south": 34.0522 - 0.1,
                    "east": -118.2437 + 0.1,
                    "west": -118.2437 - 0.1,
                },
                "coverage_level": "high",
            }
        ],
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
