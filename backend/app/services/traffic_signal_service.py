# backend/app/services/traffic_signal_service.py
import httpx
import logging
from typing import Dict, Any, Optional, List
from uuid import uuid4
from datetime import datetime
import math

import numpy as np # Import numpy for haversine distance
from app.models.signals import (
    SignalState,
    SignalPhaseEnum,
    SignalControlStatusEnum,
    SignalOperationalStatusEnum,
    SignalControlCommandResponse,
)
from app.models.websocket import (
    WebSocketMessage,
    WebSocketMessageTypeEnum,
    SignalStateUpdate,
)
from app.models.traffic import (
    IncidentSeverityEnum,  # Import IncidentSeverityEnum

)
from app.websocket.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)

EXTERNAL_CONTROLLER_API_URL = "http://localhost:8082/mock/signals"


class TrafficSignalControlError(Exception):
    """Custom exception for traffic signal control errors."""

    pass


class TrafficSignalService:
    def __init__(self, config: Dict[str, Any], connection_manager: ConnectionManager):
        self.config = config
        self.external_api_url = config.get("traffic_signal_controller", {}).get(
            "api_base_url", EXTERNAL_CONTROLLER_API_URL
        )
        self._client = httpx.AsyncClient(base_url=self.external_api_url, timeout=10.0)
        self._connection_manager = connection_manager
        self._signal_states: Dict[str, SignalState] = {}
        self._initialize_default_signals()

    def _initialize_default_signals(self):
        default_signals_config = self.config.get("traffic_signal_controller", {}).get(
            "default_signals", []
        )
        if not default_signals_config:
            default_signals_config = [
                {"signal_id": "sig_101", "location_description": "Main St & First Ave"},
                {
                    "signal_id": "sig_102",
                    "location_description": "Oak Rd & Second Blvd",
                },
            ]

        for sig_conf in default_signals_config:
            signal_id = sig_conf.get("signal_id", str(uuid4()))
            if signal_id not in self._signal_states:
                self._signal_states[signal_id] = SignalState(
                    signal_id=signal_id,
                    location_description=sig_conf.get(
                        "location_description", "Unknown Location"
                    ),
                    current_phase=SignalPhaseEnum.UNKNOWN,
                    operational_status=SignalOperationalStatusEnum.ONLINE,
                    last_updated=datetime.utcnow(),
                )
        # Add latitude and longitude to the default signals for distance calculation example
        logger.info(f"Initialized {len(self._signal_states)} mock signals.")

    async def _broadcast_signal_state_update(
        self, signal_id: str, signal_state: SignalState
    ):
        if not self._connection_manager:
            logger.warning(
                f"Cannot broadcast signal state for {signal_id}: ConnectionManager not available."
            )
            return

        ws_payload = SignalStateUpdate(signal_data=signal_state)
        message = WebSocketMessage(
            type=WebSocketMessageTypeEnum.SIGNAL_STATE_UPDATE, data=ws_payload
        )
        topic = f"signal:{signal_id}"
        await self._connection_manager.broadcast_to_topic(
            message, topic="signal_updates"
        )
        logger.debug(
            f"Broadcasted signal state update for {signal_id} to topic {topic}"
        )

    async def get_all_signal_states(self) -> List[SignalState]:
        return list(self._signal_states.values())

    async def get_signal_state(self, signal_id: str) -> Optional[SignalState]:
        return self._signal_states.get(signal_id)

    async def suggest_signal_adjustment(
        self, incident_location: Dict[str, Any], incident_severity: IncidentSeverityEnum
    ):
        """
        Suggests a traffic signal adjustment based on a detected or predicted incident.
        This is a placeholder for actual signal adjustment logic.
        """
        inc_lat = incident_location.get("latitude")
        inc_lon = incident_location.get("longitude")

        if inc_lat is None or inc_lon is None:
            logger.warning(
                "Incident location missing latitude or longitude. Cannot suggest signal adjustment."
            )
            return

        nearby_signal_threshold_km = self.config.get(
            "traffic_signal_controller", {}
        ).get("nearby_incident_distance_km", 0.5)  # Configurable threshold

        logger.info(
            f"Suggesting signal adjustment for incident at ({inc_lat:.4f}, {inc_lon:.4f}) "
            f"with severity {incident_severity.value}, checking for signals within {nearby_signal_threshold_km} km."
        )

        for signal_id, signal_state in self._signal_states.items():
            # Assuming SignalState has latitude and longitude fields based on the need for distance calculation
            # If not, this data would need to be retrieved from another source (e.g., configuration, database)
            sig_lat = signal_state.latitude
            sig_lon = signal_state.longitude

            if sig_lat is not None and sig_lon is not None:
                distance = self._haversine_distance(
                    inc_lat, inc_lon, sig_lat, sig_lon
                )
                logger.debug(f"Signal {signal_id} at ({sig_lat:.4f}, {sig_lon:.4f}) is {distance:.2f} km from incident.")
                if distance <= nearby_signal_threshold_km:
                    logger.info(
                        f"Signal {signal_id} is {distance:.2f} km away. "
                        f"Suggesting adjustment due to {incident_severity.value} incident."
                    )

                    suggested_phase = None
                    temporary_duration = None

                    # Simplified logic: based on severity, suggest a phase
                    if incident_severity == IncidentSeverityEnum.CRITICAL:
                        # Suggest setting to RED_ALL for a short period to manage intersection
                        suggested_phase = SignalPhaseEnum.RED_ALL
                        temporary_duration = 90 # Seconds
                        logger.warning(f"Incident severity CRITICAL: Suggesting {suggested_phase.value} for signal {signal_id} for {temporary_duration}s.")
                    elif incident_severity == IncidentSeverityEnum.HIGH:
                         # Suggest a change, e.g., extend green for major flow, or a specific emergency phase if available
                         # For simplicity, let's just suggest a generic phase change (e.g., to a less busy phase or a predefined plan)
                         # This needs to be more sophisticated based on traffic conditions and signal configuration
                         # As a placeholder, let's just cycle phases or pick a specific one.
                         # A real implementation would load and use predefined emergency plans or adaptive logic.
                         suggested_phase = SignalPhaseEnum.GREEN # Placeholder - actual phase depends on situation
                         temporary_duration = 60 # Seconds
                         logger.warning(f"Incident severity HIGH: Suggesting temporary phase change for signal {signal_id}.")

                    if suggested_phase:
                        try:
                            await self.set_signal_phase(signal_id, suggested_phase, duration_seconds=temporary_duration)
                        except Exception as e:
                            logger.error(f"Failed to send signal phase command for {signal_id}: {e}", exc_info=True)

    async def set_signal_phase(
        self,
        signal_id: str,
        phase: SignalPhaseEnum,
        duration_seconds: Optional[int] = None,
    ) -> SignalControlCommandResponse:
        logger.info(f"Attempting to set phase for signal {signal_id} to {phase.value}")
        command_payload = {"phase": phase.value}
        if duration_seconds is not None:
            command_payload["duration_seconds"] = duration_seconds

        try:
            response = await self._client.post(f"/{signal_id}/set_phase", json=command_payload)
            response.raise_for_status()  # Raise an exception for bad status codes
            api_response_data = response.json()
            # Mocking success for now
            api_response_data = {
                "status": "accepted",
                "message": "Phase change command accepted by controller.",
            }

            if api_response_data.get("status") == "accepted":
                current_signal_state = self._signal_states[signal_id]
                current_signal_state.current_phase = phase
                current_signal_state.last_updated = datetime.utcnow()
                current_signal_state.operational_status = (
                    SignalOperationalStatusEnum.ONLINE
                )

                await self._broadcast_signal_state_update(
                    signal_id, current_signal_state
                )

                return SignalControlCommandResponse(
                    signal_id=signal_id,
                    status=SignalControlStatusEnum.ACCEPTED,
                    message=api_response_data.get("message", "Command accepted."),
                    new_state=current_signal_state,
                    timestamp=datetime.utcnow(),
                )
            else:
                return SignalControlCommandResponse(
                    signal_id=signal_id,
                    status=SignalControlStatusEnum(
                        api_response_data.get("status", "error").lower()
                    ),
                    message=api_response_data.get(
                        "message", "Command failed at controller."
                    ),
                    timestamp=datetime.utcnow(),
                )

        except httpx.HTTPStatusError as e:
            logger.error(
                f"External API error setting phase for {signal_id}: {e.response.status_code} - {e.response.text}"
            )
            if signal_id in self._signal_states:
                self._signal_states[
                    signal_id
                ].operational_status = SignalOperationalStatusEnum.ERROR
                self._signal_states[signal_id].last_updated = datetime.utcnow()
                await self._broadcast_signal_state_update(
                    signal_id, self._signal_states[signal_id]
                )
            return SignalControlCommandResponse(
                signal_id=signal_id,
                status=SignalControlStatusEnum.ERROR,
                message=f"External API error: {e.response.status_code}",
                timestamp=datetime.utcnow(),
            )
        except httpx.RequestError as e:
            logger.error(f"Request error setting phase for {signal_id}: {e}")
            if signal_id in self._signal_states:
                self._signal_states[
                    signal_id
                ].operational_status = SignalOperationalStatusEnum.ERROR
                self._signal_states[signal_id].last_updated = datetime.utcnow()
                await self._broadcast_signal_state_update(
                    signal_id, self._signal_states[signal_id]
                )
            return SignalControlCommandResponse(
                signal_id=signal_id,
                status=SignalControlStatusEnum.ERROR,
                message="Request error",
                timestamp=datetime.utcnow(),
            )
        except Exception as e:
            logger.error(
                f"Unexpected error setting phase for {signal_id}: {e}", exc_info=True
            )
            return SignalControlCommandResponse(
                signal_id=signal_id,
                status=SignalControlStatusEnum.ERROR,
                message="Unexpected server error",
                timestamp=datetime.utcnow(),
            )

    async def close(self):
        await self._client.aclose()
        logger.info("TrafficSignalService HTTP client closed.")

    def _haversine_distance(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> float:
        """
        Calculate the distance between two points on the Earth (in km)
        using the Haversine formula.
        """
        R = 6371  # Earth's radius in kilometers

        lat1_rad = math.radians(lat1)
        lon1_rad = math.radians(lon1)
        lat2_rad = math.radians(lat2)
        lon2_rad = math.radians(lon2)

        dlat = lat2_rad - lat1_rad
        dlon = lon2_rad - lon1_rad

        a = np.sin(dlat / 2) ** 2 + np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(dlon / 2) ** 2
        c = 2 * np.arcsin(np.sqrt(a))

        return R * c
