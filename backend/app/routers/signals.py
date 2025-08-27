from fastapi import APIRouter, HTTPException, Depends

from app.dependency_injection import get_current_active_user, get_tss
from app.services.traffic_signal_service import (
    TrafficSignalService,
    TrafficSignalControlError,
)

from fastapi import status

router = APIRouter()


@router.get("/signals")
async def get_signals(
    current_user: dict = Depends(get_current_active_user),
    tss: TrafficSignalService = Depends(get_tss),
):
    """Endpoint to retrieve the list of traffic signals. Requires authentication."""

    try:
        signals = await tss.get_all_signals()
        return signals
    except TrafficSignalControlError as e:
        # logger.error(f"Error retrieving signals for user {user_email}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)
        )
    except Exception:
        # logger.error(f"Unexpected error retrieving signals for user {user_email}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving signals.",
        )


@router.post("/signals/{signal_id}/set_phase")
async def set_signal_phase(
    signal_id: str,
    phase: str,
    current_user: dict = Depends(get_current_active_user),
    tss: TrafficSignalService = Depends(get_tss),
):
    """Endpoint to update the phase of a traffic signal. Requires authentication."""
    valid_phases = [
        "red",
        "yellow",
        "green",
        "flashing_red",
        "flashing_yellow",
        "off",
    ]  # Example phases
    if phase.lower() not in valid_phases:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid phase. Valid phases are: {', '.join(valid_phases)}",
        )

    user_email = current_user.get("email")
    # logger.info(f"User {user_email} attempting to set phase for signal {signal_id}.")

    try:
        success = await tss.set_signal_phase(signal_id, phase.lower())
        if success:
            return {
                "message": f"Signal {signal_id} phase change to {phase.lower()} initiated successfully by user {user_email}"
            }
        else:
            # This case might be hit if the service internally decides not to proceed (e.g. base_url not set and returns False)
            # Or if the external API call was made but indicated failure in a way that didn't raise an exception in the service.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to set phase for signal {signal_id}. The control service reported an issue.",
            )
    except TrafficSignalControlError as e:
        # logger.error(f"Control error setting phase for signal {signal_id} by user {user_email}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)
        )
    except Exception:
        # logger.error(f"Unexpected error setting phase for signal {signal_id} by user {user_email}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred while setting signal phase for {signal_id}.",
        )
