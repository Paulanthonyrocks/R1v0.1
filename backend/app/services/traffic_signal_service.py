# backend/app/services/traffic_signal_service.py
import httpx
import logging
from typing import Dict, Any, Optional, List
from uuid import uuid4
from datetime import datetime # Added import
import asyncio # Added import

from app.models.signals import (
    SignalState, SignalPhaseEnum, SignalControlStatusEnum, 
    SignalOperationalStatusEnum, SignalControlCommandResponse
)
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum, SignalStateUpdate
from app.websocket.connection_manager import ConnectionManager # For broadcasting

logger = logging.getLogger(__name__)

EXTERNAL_CONTROLLER_API_URL = "http://localhost:8082/mock/signals"

class TrafficSignalService:
    def __init__(self, config: Dict[str, Any], connection_manager: ConnectionManager):
        self.config = config
        self.external_api_url = config.get("traffic_signal_controller", {}).get("api_base_url", EXTERNAL_CONTROLLER_API_URL)
        self._client = httpx.AsyncClient(base_url=self.external_api_url, timeout=10.0)
        self._connection_manager = connection_manager
        self._signal_states: Dict[str, SignalState] = {}
        self._initialize_default_signals()

    def _initialize_default_signals(self):
        default_signals_config = self.config.get("traffic_signal_controller", {}).get("default_signals", [])
        if not default_signals_config: 
            default_signals_config = [
                {"signal_id": "sig_101", "location_description": "Main St & First Ave"},
                {"signal_id": "sig_102", "location_description": "Oak Rd & Second Blvd"}
            ]

        for sig_conf in default_signals_config:
            signal_id = sig_conf.get("signal_id", str(uuid4()))
            if signal_id not in self._signal_states:
                self._signal_states[signal_id] = SignalState(
                    signal_id=signal_id,
                    location_description=sig_conf.get("location_description", "Unknown Location"),
                    current_phase=SignalPhaseEnum.UNKNOWN,
                    operational_status=SignalOperationalStatusEnum.OPERATIONAL,
                    last_updated=datetime.utcnow()
                )
        logger.info(f"Initialized {len(self._signal_states)} mock signals.")

    async def _broadcast_signal_state_update(self, signal_id: str, signal_state: SignalState):
        if not self._connection_manager:
            logger.warning(f"Cannot broadcast signal state for {signal_id}: ConnectionManager not available.")
            return

        ws_payload = SignalStateUpdate(signal_data=signal_state)
        message = WebSocketMessage(
            event_type=WebSocketMessageTypeEnum.SIGNAL_STATE_UPDATE,
            payload=ws_payload
        )
        topic = f"signal:{signal_id}"
        await self._connection_manager.broadcast_message_model(message, specific_topic=topic)
        logger.debug(f"Broadcasted signal state update for {signal_id} to topic {topic}")

    async def get_all_signal_states(self) -> List[SignalState]:
        return list(self._signal_states.values())

    async def get_signal_state(self, signal_id: str) -> Optional[SignalState]:
        return self._signal_states.get(signal_id)

    async def set_signal_phase(
        self, signal_id: str, phase: SignalPhaseEnum, duration_seconds: Optional[int] = None
    ) -> SignalControlCommandResponse:
        logger.info(f"Attempting to set phase for signal {signal_id} to {phase.value}")
        command_payload = {"phase": phase.value}
        if duration_seconds is not None:
            command_payload["duration_seconds"] = duration_seconds

        # --- MOCKING API RESPONSE ---
        await asyncio.sleep(0.1) 
        if signal_id not in self._signal_states:
             return SignalControlCommandResponse(
                signal_id=signal_id,
                status=SignalControlStatusEnum.REJECTED,
                message=f"Signal ID {signal_id} not found.",
                timestamp=datetime.utcnow()
            )
        # --- END MOCKING ---
        try:
            # Actual external API call would be here, e.g.:
            # response = await self._client.post(f"/{signal_id}/set_phase", json=command_payload)
            # response.raise_for_status()
            # api_response_data = response.json()
            
            # Mocking success for now
            api_response_data = {"status": "accepted", "message": "Phase change command accepted by controller."}

            if api_response_data.get("status") == "accepted":
                current_signal_state = self._signal_states[signal_id]
                current_signal_state.current_phase = phase
                current_signal_state.last_updated = datetime.utcnow()
                current_signal_state.operational_status = SignalOperationalStatusEnum.OPERATIONAL
                
                await self._broadcast_signal_state_update(signal_id, current_signal_state)
                
                return SignalControlCommandResponse(
                    signal_id=signal_id,
                    status=SignalControlStatusEnum.ACCEPTED,
                    message=api_response_data.get("message", "Command accepted."),
                    new_state=current_signal_state,
                    timestamp=datetime.utcnow()
                )
            else:
                return SignalControlCommandResponse(
                    signal_id=signal_id,
                    status=SignalControlStatusEnum(api_response_data.get("status", "error").lower()),
                    message=api_response_data.get("message", "Command failed at controller."),
                    timestamp=datetime.utcnow()
                )

        except httpx.HTTPStatusError as e:
            logger.error(f"External API error setting phase for {signal_id}: {e.response.status_code} - {e.response.text}")
            if signal_id in self._signal_states:
                self._signal_states[signal_id].operational_status = SignalOperationalStatusEnum.ERROR
                self._signal_states[signal_id].last_updated = datetime.utcnow()
                await self._broadcast_signal_state_update(signal_id, self._signal_states[signal_id])
            return SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.ERROR, message=f"External API error: {e.response.status_code}", timestamp=datetime.utcnow())
        except httpx.RequestError as e:
            logger.error(f"Request error setting phase for {signal_id}: {e}")
            if signal_id in self._signal_states:
                self._signal_states[signal_id].operational_status = SignalOperationalStatusEnum.ERROR
                self._signal_states[signal_id].last_updated = datetime.utcnow()
                await self._broadcast_signal_state_update(signal_id, self._signal_states[signal_id])
            return SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.ERROR, message="Request error", timestamp=datetime.utcnow())
        except Exception as e:
            logger.error(f"Unexpected error setting phase for {signal_id}: {e}", exc_info=True)
            return SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.ERROR, message="Unexpected server error", timestamp=datetime.utcnow())

    async def close(self):
        await self._client.aclose()
        logger.info("TrafficSignalService HTTP client closed.") 