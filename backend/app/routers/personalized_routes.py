from fastapi import APIRouter, Depends, HTTPException, Body, Query, status
from typing import List, Dict, Optional
from pydantic import BaseModel, Field  # Added for SuggestionFeedbackRequest
import logging

from app.models.routing import (
    PersonalizedRouteRequest,
    PersonalizedRouteResponse,
    RouteHistoryEntry,
    UserRoutingProfile,
)
from app.dependency_injection import get_current_active_user, get_prs
from app.services.personalized_routing_service import PersonalizedRoutingService

router = APIRouter()
logger = logging.getLogger(__name__)


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
    routing_service: PersonalizedRoutingService = Depends(get_prs),
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
    routing_service: PersonalizedRoutingService = Depends(get_prs),
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
    routing_service: PersonalizedRoutingService = Depends(get_prs),
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
    routing_service: PersonalizedRoutingService = Depends(get_prs),
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


@router.post(
    "/suggestions/feedback",
    summary="Record Feedback on Proactive Suggestion",
    description="Allows users to submit feedback (acceptance, rejection, rating, text) on a proactive route suggestion they received.",
    status_code=200,  # Default, can be overridden
)
async def record_suggestion_feedback_endpoint(
    feedback_data: SuggestionFeedbackRequest = Body(...),
    current_user: Dict = Depends(get_current_active_user),
    routing_service: PersonalizedRoutingService = Depends(get_prs),
):
    """
    Records feedback for a given proactive suggestion.
    """
    try:
        success = await routing_service.record_suggestion_feedback(
            suggestion_id=feedback_data.suggestion_id,
            user_id=current_user["uid"],  # Use authenticated user's ID
            interaction_status=feedback_data.interaction_status,
            feedback_text=feedback_data.feedback_text,
            rating=feedback_data.rating,
        )

        if success:
            return {"message": "Feedback recorded successfully"}
        else:
            # The service returns False for known issues like "not found" or "user mismatch"
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Suggestion ID not found, user mismatch, or invalid data. Feedback not recorded.",
            )

    except HTTPException as http_exc:
        # Re-raise HTTPException directly to let FastAPI handle it
        raise http_exc
    except Exception as e:
        # Catch any other unexpected errors from the service layer or elsewhere
        # Log the error server-side for diagnosis
        # logger.error(f"Unexpected error recording suggestion feedback: {e}", exc_info=True) # Assuming logger is available
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred while recording feedback: {str(e)}",
        )
