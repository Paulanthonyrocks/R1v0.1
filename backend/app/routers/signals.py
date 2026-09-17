from typing import Literal
from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, HTTPException, Depends
from app.dependency_injection import get_current_active_user, get_current_operator, get_traffic_signal_service
from app.services.traffic_signal_service import TrafficSignalService, TrafficSignalControlError
from app.models.signals import SignalControlStatusEnum
from app.models.user import User, UserRole

router = APIRouter()


class SignalPhaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phase: Literal["red", "yellow", "green", "flashing_red", "flashing_yellow", "off"]


@router.get("/")
async def get_signals(
    current_user: User = Depends(get_current_active_user),
    tss: TrafficSignalService = Depends(get_traffic_signal_service),
):
    try:
        return await tss.get_all_signal_states()
    except TrafficSignalControlError:
        raise HTTPException(status_code=503, detail="Signal service unavailable.") from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Unable to retrieve signals.") from None


@router.post("/{signal_id}/set_phase")
async def set_signal_phase(
    signal_id: str,
    request: SignalPhaseRequest,
    current_user: User = Depends(get_current_operator),
    tss: TrafficSignalService = Depends(get_traffic_signal_service),
):
    # No jurisdiction registry exists here. Use explicit server-managed resource
    # grants for non-admin controllers; never infer scope from a client request.
    if current_user.role != UserRole.ADMIN and signal_id not in current_user.signal_ids:
        raise HTTPException(status_code=403, detail="Signal outside assigned control scope.")
    try:
        response = await tss.set_signal_phase(signal_id, request.phase)
        if response.status != SignalControlStatusEnum.ACCEPTED:
            raise HTTPException(status_code=502, detail="Signal service did not accept the command.")
        return {"message": "Signal phase change accepted.", "signal_id": signal_id, "phase": request.phase}
    except HTTPException:
        raise
    except TrafficSignalControlError:
        raise HTTPException(status_code=503, detail="Signal service unavailable.") from None
    except Exception:
        raise HTTPException(status_code=500, detail="Unable to set signal phase.") from None
