from fastapi import APIRouter, Depends, HTTPException, Body, Query
from typing import List, Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel, Field # Added for SuggestionFeedbackRequest

from app.models.routing import (
    PersonalizedRouteRequest,
    PersonalizedRouteResponse,
    RouteHistoryEntry,
    UserRoutingProfile
)
from app.services.personalized_routing_service import PersonalizedRoutingService
from app.dependencies import (
    get_current_active_user,
    get_analytics_service,
    get_personalized_routing_service
)

router = APIRouter()

# Pydantic model for suggestion feedback request body
class SuggestionFeedbackRequest(BaseModel):
    suggestion_id: str
    interaction_status: str  # e.g., "accepted", "rejected", "ignored", "modified"
    feedback_text: Optional[str] = None
    rating: Optional[int] = Field(None, ge=1, le=5, description="User rating for the suggestion, 1-5")


@router.post(
    "/personalized",
    response_model=PersonalizedRouteResponse,
    summary="Get Personalized Route",
    description="Get an AI-optimized route based on user preferences and historical patterns"
)
async def get_personalized_route(
    request: PersonalizedRouteRequest = Body(...),
    current_user: Dict = Depends(get_current_active_user),
    routing_service: PersonalizedRoutingService = Depends(get_personalized_routing_service)
) -> PersonalizedRouteResponse:
    """Get a personalized route based on user preferences"""
    try:
        # Ensure the user_id matches the authenticated user
        if request.user_id != current_user['uid']:
            raise HTTPException(
                status_code=403,
                detail="User ID in request does not match authenticated user"
            )
            
        return await routing_service.get_personalized_route(request)
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error getting personalized route: {str(e)}"
        )

@router.post(
    "/history",
    status_code=201,
    summary="Record Route History",
    description="Record a completed route in user's history"
)
async def record_route_history(
    entry: RouteHistoryEntry = Body(...),
    current_user: Dict = Depends(get_current_active_user),
    routing_service: PersonalizedRoutingService = Depends(get_personalized_routing_service)
) -> Dict[str, str]:
    """Record a route in user's history"""
    try:
        # Ensure the user_id matches the authenticated user
        if entry.user_id != current_user['uid']:
            raise HTTPException(
                status_code=403,
                detail="User ID in entry does not match authenticated user"
            )
            
        await routing_service.record_route_history(entry)
        return {"message": "Route history recorded successfully"}
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error recording route history: {str(e)}"
        )

@router.get(
    "/profile",
    response_model=UserRoutingProfile,
    summary="Get User Routing Profile",
    description="Get user's routing preferences and learned patterns"
)
async def get_user_profile(
    current_user: Dict = Depends(get_current_active_user),
    routing_service: PersonalizedRoutingService = Depends(get_personalized_routing_service)
) -> UserRoutingProfile:
    """Get user's routing profile"""
    try:
        return await routing_service.get_user_profile(current_user['uid'])
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error getting user profile: {str(e)}"
        )

@router.get(
    "/history",
    response_model=List[RouteHistoryEntry],
    summary="Get Route History",
    description="Get user's route history"
)
async def get_route_history(
    limit: int = Query(default=50, ge=1, le=1000),
    current_user: Dict = Depends(get_current_active_user),
    routing_service: PersonalizedRoutingService = Depends(get_personalized_routing_service)
) -> List[RouteHistoryEntry]:
    """Get user's route history"""
    try:
        return await routing_service.get_user_route_history(
            user_id=current_user['uid'],
            limit=limit
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error getting route history: {str(e)}"
        )

@router.post(
    "/suggestions/feedback",
    summary="Record Feedback on Proactive Suggestion",
    description="Allows users to submit feedback (acceptance, rejection, rating, text) on a proactive route suggestion they received.",
    status_code=200 # Default, can be overridden
)
async def record_suggestion_feedback_endpoint(
    feedback_data: SuggestionFeedbackRequest = Body(...),
    current_user: Dict = Depends(get_current_active_user),
    routing_service: PersonalizedRoutingService = Depends(get_personalized_routing_service)
):
    """
    Records feedback for a given proactive suggestion.
    """
    try:
        success = await routing_service.record_suggestion_feedback(
            suggestion_id=feedback_data.suggestion_id,
            user_id=current_user['uid'], # Use authenticated user's ID
            interaction_status=feedback_data.interaction_status,
            feedback_text=feedback_data.feedback_text,
            rating=feedback_data.rating
        )

        if success:
            return {"message": "Feedback recorded successfully"}
        else:
            # The service returns False for known issues like "not found" or "user mismatch"
            raise HTTPException(
                status_code=404, # Or 400 depending on specific failure reason if distinguishable
                detail="Suggestion ID not found, user mismatch, or invalid data. Feedback not recorded."
            )

    except HTTPException as http_exc:
        # Re-raise HTTPException directly to let FastAPI handle it
        raise http_exc
    except Exception as e:
        # Catch any other unexpected errors from the service layer or elsewhere
        # Log the error server-side for diagnosis
        # logger.error(f"Unexpected error recording suggestion feedback: {e}", exc_info=True) # Assuming logger is available
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred while recording feedback: {str(e)}"
        )
