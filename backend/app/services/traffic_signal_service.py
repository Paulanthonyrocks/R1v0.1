# backend/app/services/traffic_signal_service.py
import httpx
import logging
from typing import Dict, Any, Optional, List # Ensure all are here
from uuid import uuid4
from datetime import datetime
import asyncio

from app.models.signals import (
    SignalState, SignalPhaseEnum, SignalControlStatusEnum,
    SignalOperationalStatusEnum, SignalControlCommandResponse
)
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum, SignalStateUpdate
from app.websocket.connection_manager import ConnectionManager # For broadcasting
from app.services.llm_signal_advisor import LLMSignalAdvisor # Added import

logger = logging.getLogger(__name__)

EXTERNAL_CONTROLLER_API_URL = "http://localhost:8082/mock/signals"

class TrafficSignalService:
    """
    Manages traffic signal states, interactions with external signal controllers (mocked),
    and WebSocket communication for signal updates. It can also leverage an
    LLMSignalAdvisor to get AI-based recommendations for signal adjustments.
    """
    def __init__(self, 
                 config: Dict[str, Any], 
                 connection_manager: ConnectionManager,
                 llm_signal_advisor: Optional[LLMSignalAdvisor] = None):
        """
        Initializes the TrafficSignalService.

        Args:
            config (Dict[str, Any]): Application configuration. Expected to contain
                                     "traffic_signal_controller" settings like "api_base_url"
                                     and "default_signals".
            connection_manager (ConnectionManager): Manager for WebSocket connections,
                                                    used for broadcasting signal state updates.
            llm_signal_advisor (Optional[LLMSignalAdvisor], optional): An instance of
                                                                    LLMSignalAdvisor for obtaining
                                                                    LLM-based signal timing advice.
                                                                    Defaults to None. If None,
                                                                    a warning is logged, and LLM-based
                                                                    advice features will be disabled.
        """
        self.config = config
        self.external_api_url = config.get("traffic_signal_controller", {}).get("api_base_url", EXTERNAL_CONTROLLER_API_URL)
        self._client = httpx.AsyncClient(base_url=self.external_api_url, timeout=10.0)
        self._connection_manager = connection_manager
        self.llm_signal_advisor = llm_signal_advisor # Store the LLM signal advisor instance
        
        # Log a warning if the LLM signal advisor is not provided, as LLM features will be unavailable.
        if not self.llm_signal_advisor:
            logger.warning("LLMSignalAdvisor not provided to TrafficSignalService. LLM-based advice will be unavailable.")
        
        self._signal_states: Dict[str, SignalState] = {} # In-memory store for signal states
        self._initialize_default_signals() # Initialize default/mock signal states

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

    async def process_llm_signal_advice(self, traffic_prediction_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Processes a request for signal adjustment advice from the LLM based on traffic prediction data.

        This method queries the configured `LLMSignalAdvisor` for advice. It passes the
        provided `traffic_prediction_data` and the current states of all signals
        to the advisor. The advisor is expected to interpret this data and return
        signal adjustment recommendations.

        Args:
            traffic_prediction_data (Dict[str, Any]): A dictionary containing traffic
                prediction information. This data is passed directly to the
                `LLMSignalAdvisor`'s `get_signal_adjustment_advice` method as the
                `traffic_prediction` keyword argument. The structure of this dictionary
                is dependent on what `TrafficPredictor` provides and what
                `LLMSignalAdvisor` expects (e.g., it might contain intersection ID,
                current conditions, predicted congestion, etc.).

        Returns:
            List[Dict[str, Any]]: A list containing a single dictionary of advice if
                successful. The structure of this advice dictionary is determined by
                `LLMSignalAdvisor`. If the `llm_signal_advisor` is not configured,
                if the advisor returns no actionable advice (e.g., an empty response or
                a response with an "error" key), or if an exception occurs during the
                process, an empty list `[]` is returned.

        Note:
            The `LLMSignalAdvisor.get_signal_adjustment_advice` method is assumed to have
            a signature that accepts `traffic_prediction` and `signal_states` keyword arguments,
            as per the implementation in Turn 20/40.
        """
        # Check if the LLM signal advisor is configured. If not, log a warning and return empty list.
        if not self.llm_signal_advisor:
            logger.warning("LLMSignalAdvisor not available. Cannot process signal advice request. Returning empty list.")
            return []

        # Extract intersection_id for logging purposes, if available in the prediction data.
        # Default to "unknown_intersection" if not found.
        intersection_id_log = traffic_prediction_data.get("intersection_id", "unknown_intersection")
        # If intersection_id is still unknown, try to use 'location' for logging.
        if intersection_id_log == "unknown_intersection" and "location" in traffic_prediction_data:
            intersection_id_log = str(traffic_prediction_data.get("location", "unknown_location_for_log"))


        try:
            logger.info(f"Requesting LLM signal advice for intersection ID/location: {intersection_id_log} using full traffic prediction data.")
            
            # Retrieve all current signal states to provide context to the advisor.
            all_signal_states = await self.get_all_signal_states()
            
            # Call the LLM signal advisor with the traffic prediction data and all signal states.
            # This specific call signature (using traffic_prediction and signal_states kwargs)
            # was established in a previous modification (around Turn 20/40).
            advice_result = await self.llm_signal_advisor.get_signal_adjustment_advice(
                traffic_prediction=traffic_prediction_data, 
                signal_states=all_signal_states 
            )

            # Process the advisor's response.
            if advice_result and not advice_result.get("error"):
                # Log successful reception of advice.
                # The advice_result itself might contain a more specific intersection_id.
                advice_intersection_id = advice_result.get("intersection_id", intersection_id_log)
                logger.info(f"LLM Signal Advice received for {advice_intersection_id}: {advice_result}")
                
                # TODO: Implement WebSocket broadcasting for the received advice.
                # This would involve defining a new WebSocketMessageTypeEnum for signal advice
                # and broadcasting the advice_result to relevant topics (e.g., a general admin topic
                # or a topic specific to the intersection).
                # Example:
                # ws_payload_dict = {**advice_result, "context_intersection_id": advice_intersection_id}
                # advice_message = WebSocketMessage(
                #     event_type=WebSocketMessageTypeEnum.SIGNAL_ADVICE_UPDATE, # Requires new enum member
                #     payload=ws_payload_dict 
                # )
                # await self._connection_manager.broadcast_message_model(advice_message, specific_topic=f"signal_advice:{advice_intersection_id}")
                
                return [advice_result]  # Return the advice dictionary wrapped in a list.
            else:
                # Log a warning if no advice was returned or if the advisor indicated an error.
                logger.warning(f"No advice or error received from LLMSignalAdvisor for {intersection_id_log}. Response: {advice_result}. Returning empty list.")
                return [] # Return empty list for no advice or error from advisor.

        except Exception as e:
            # Log any unexpected exceptions that occur during the process.
            logger.error(f"Error processing LLM signal advice for {intersection_id_log}: {e}", exc_info=True)
            return [] # Return empty list in case of exceptions.