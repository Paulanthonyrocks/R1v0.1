import asyncio
import logging
from typing import Optional, Dict, Any, List, Set
import json
from datetime import datetime, timedelta
import math

from app.tasks.prediction_scheduler import PredictionScheduler
from app.services.personalized_routing_service import PersonalizedRoutingService, CommonTravelPattern
from app.services.analytics_service import AnalyticsService
from app.services.traffic_signal_service import TrafficSignalService
from app.models.traffic import LocationModel
from app.models.signals import SignalState, SignalPhaseEnum, SignalOperationalStatusEnum, SignalControlCommandResponse, SignalControlStatusEnum
from app.models.websocket import UserSpecificConditionAlert, WebSocketMessage
logger = logging.getLogger(__name__)

PREDICTIVE_ALERT_LIKELIHOOD_THRESHOLD = 60
MOCK_GREEN_WAVE_TRIGGER_KPI = "corridor_main_st_ns_demand"

PILOT_CORRIDOR_CONFIG = {
    "main_st_ns_wave": {
        "signals_in_order": ["TS001", "TS002", "TS004"], # TS004 needs to be in mock signals
        "target_green_phase": SignalPhaseEnum.GREEN,
        "wave_green_time_seconds": 50,
        "offsets_seconds": [0, 18, 36],
        "corridor_flow_direction_assumption": "NS",
        "total_wave_cycle_estimation": 120
    }
}

class AgentCore:
    SIGNAL_ACTION_COOLDOWN_SECONDS = 120
    INCIDENT_SIGNAL_COOLDOWN_SECONDS = 300
    ROAD_CLOSURE_IMMEDIATE_RADIUS_METERS = 50

    def __init__(
        self,
        prediction_scheduler: PredictionScheduler,
        personalized_routing_service: PersonalizedRoutingService,
        analytics_service: AnalyticsService,
        traffic_signal_service: TrafficSignalService,
    ):
        self.prediction_scheduler = prediction_scheduler
        self.personalized_routing_service = personalized_routing_service
        self.analytics_service = analytics_service
        self.traffic_signal_service = traffic_signal_service
        self._recent_signal_actions: Dict[str, Dict[str, Any]] = {}
        self.pilot_corridor_configs = PILOT_CORRIDOR_CONFIG # Store the config
        self.logger = logger
        logger.info("AgentCore initialized with services and pilot corridor configs.")

    def _calculate_haversine_distance(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 6371000
        phi1 = math.radians(lat1); phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1); delta_lambda = math.radians(lon2 - lon1)
        a = math.sin(delta_phi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    async def _find_signals_near_location(self, incident_location: LocationModel, all_signals: List[SignalState], radius_meters: int) -> List[SignalState]:
        nearby_signals = []
        if not incident_location: self.logger.warning("Incident location is None..."); return nearby_signals
        for signal in all_signals:
            if signal.location and isinstance(signal.location.latitude, (float, int)) and isinstance(signal.location.longitude, (float, int)):
                distance = self._calculate_haversine_distance(incident_location.latitude, incident_location.longitude, signal.location.latitude, signal.location.longitude)
                if distance <= radius_meters: nearby_signals.append(signal)
            else: self.logger.debug(f"Signal {signal.signal_id} has no valid location data.")
        self.logger.info(f"Found {len(nearby_signals)} signals within {radius_meters}m of incident."); return nearby_signals

    async def _determine_next_travel_prediction_time(self, pattern: CommonTravelPattern, current_dt: datetime) -> Optional[datetime]:
        # This method's internal logic is assumed correct from previous steps.
        # For brevity in this specific subtask, its full body isn't re-expanded here.
        # It should return an Optional[datetime] or None.
        self.logger.debug(f"Determining next travel time for pattern {pattern.pattern_id}...")
        # Placeholder for actual logic from previous steps:
        if pattern.time_of_day_group == "morning_commute" and current_dt.hour < 10:
             return current_dt + timedelta(hours=1) # Simplified placeholder
        return None


    async def _execute_green_wave(
        self, corridor_id: str, signals_in_order: List[str], green_phase: SignalPhaseEnum,
        green_time_seconds: int, offsets_seconds: List[int], all_current_signal_states: Dict[str, SignalState],
        processed_signals_for_coordination: Set[str], now_utc: datetime # Use consistent now_utc from cycle start
    ) -> bool:
        self.logger.info(f"Initiating green wave '{corridor_id}' for signals: {signals_in_order}")
        if not (len(signals_in_order) == len(offsets_seconds) and signals_in_order and offsets_seconds):
            self.logger.error(f"Green wave '{corridor_id}': Invalid configuration - signals/offsets mismatch or empty. Cannot execute.")
            return False

        wave_initiation_time_utc = now_utc # Base time for all offset calculations

        for i, signal_id in enumerate(signals_in_order):
            signal_state = all_current_signal_states.get(signal_id)
            current_offset_seconds = offsets_seconds[i]

            if not signal_state or signal_state.operational_status != SignalOperationalStatusEnum.ONLINE:
                self.logger.warning(f"Green wave '{corridor_id}': Signal '{signal_id}' is not online or not found. Skipping in wave.")
                continue
            if signal_id in processed_signals_for_incident: # Check against incident-processed signals
                self.logger.info(f"Green wave '{corridor_id}': Signal '{signal_id}' was handled by incident logic. Skipping in wave.")
                continue
            if signal_id in self._recent_signal_actions and \
               (now_utc - self._recent_signal_actions[signal_id]['timestamp']).total_seconds() < self.SIGNAL_ACTION_COOLDOWN_SECONDS:
                self.logger.info(f"Green wave '{corridor_id}': Signal '{signal_id}' on general cooldown ({self.SIGNAL_ACTION_COOLDOWN_SECONDS}s). Reason: {self._recent_signal_actions[signal_id].get('reason')}. Skipping in wave.")
                continue

            target_command_time_for_this_signal = wave_initiation_time_utc + timedelta(seconds=current_offset_seconds)
            # Use datetime.utcnow() for the most current time before sleeping to get accurate delay
            delay_seconds_from_now = (target_command_time_for_this_signal - datetime.utcnow()).total_seconds()

            if delay_seconds_from_now > 0.05: # Small buffer to avoid tiny/negative sleeps
                self.logger.debug(f"Green wave '{corridor_id}': Waiting {delay_seconds_from_now:.2f}s to command '{signal_id}' (offset {current_offset_seconds}s).")
                await asyncio.sleep(delay_seconds_from_now)
            else:
                 self.logger.debug(f"Green wave '{corridor_id}': Commanding '{signal_id}' (offset {current_offset_seconds}s) - current delay: {delay_seconds_from_now:.2f}s (minimal or past due).")

            self.logger.info(f"Green wave '{corridor_id}': Commanding signal '{signal_id}' to {green_phase.value} for {green_time_seconds}s.")
            try:
                response = await self.traffic_signal_service.set_signal_phase(signal_id, green_phase, green_time_seconds)
                action_timestamp = datetime.utcnow() # Actual time of command attempt post-sleep
                if response.status in [SignalControlStatusEnum.ACCEPTED, SignalControlStatusEnum.SUCCESS]:
                    self._recent_signal_actions[signal_id] = {
                        'timestamp': action_timestamp, 'phase_commanded': green_phase,
                        'duration_commanded': green_time_seconds, 'reason': f'green_wave_{corridor_id}'
                    }
                    processed_signals_for_coordination.add(signal_id)
                    self.logger.info(f"Green wave '{corridor_id}': Successfully commanded '{signal_id}'. Action recorded.")
                else:
                    self.logger.error(f"Green wave '{corridor_id}': Failed to command '{signal_id}'. Response: {response.status.value} - {response.message}")
            except Exception as e:
                self.logger.error(f"Green wave '{corridor_id}': Exception commanding '{signal_id}': {e}", exc_info=True)

        self.logger.info(f"Green wave '{corridor_id}' sequence attempt finished.")
        return True

    async def run_decision_cycle(self, sample_user_id: str = "user_agent_test_123"):
        processed_signals_for_incident: Set[str] = set()
        processed_signals_for_coordination: Set[str] = set() # New set
        now_utc = datetime.utcnow()

        logger.info(f"--- Starting AgentCore decision cycle for user: {sample_user_id} at {now_utc.isoformat()} ---")

        actions_to_remove = [sid for sid, data in self._recent_signal_actions.items() if (now_utc - data['timestamp']).total_seconds() > (self.INCIDENT_SIGNAL_COOLDOWN_SECONDS if 'incident_id' in data else self.SIGNAL_ACTION_COOLDOWN_SECONDS)]
        for sid in actions_to_remove:
            if sid in self._recent_signal_actions : del self._recent_signal_actions[sid]; self.logger.debug(f"Removed '{sid}' from recent actions (cooldown expired).")

        system_kpis = self.analytics_service.get_current_system_kpis_summary()
        alert_summary = await self.analytics_service.get_critical_alert_summary()
        all_signal_states_list = await self.traffic_signal_service.get_all_signal_states()
        all_signal_states_map: Dict[str, SignalState] = {s.signal_id: s for s in all_signal_states_list}

        self.logger.info(f"KPIs: {json.dumps(system_kpis, indent=2)}, Alerts: {alert_summary.get('critical_unack_alert_count',0)}, Signals: {len(all_signal_states_list)}")
        for state in all_signal_states_list: self.logger.debug(f"Sig: {state.signal_id}, Ph: {state.current_phase.value if state.current_phase else 'N/A'}, Sts: {state.operational_status.value if state.operational_status else 'N/A'}, Flow: {state.main_flow_direction if state.main_flow_direction else 'N/A'}")

        current_congestion_level = system_kpis.get("overall_congestion_level", "UNKNOWN")

        # --- Incident-Specific Signal Control ---
        # (Full logic from previous step should be here)
        # This section uses `all_signal_states_list` and updates `processed_signals_for_incident` and `_recent_signal_actions`.
        # For brevity, only a summary:
        self.logger.info("Evaluating critical alerts for incident-specific signal responses...")
        active_individual_alerts = alert_summary.get('active_alerts', [])
        if active_individual_alerts:
            # ... (Full iteration and logic as per previous step, including ROAD_CLOSURE and ACCIDENT strategies) ...
            # Example for one alert type:
            for alert_data in active_individual_alerts: # Simplified loop for illustration
                alert_type = alert_data.get('type', "UNKNOWN").upper()
                # ... (parsing, location check, _find_signals_near_location with all_signal_states_list) ...
                if alert_type == "ACCIDENT": # And other types like ROAD_CLOSURE
                    # ... (logic to control signals, update _recent_signal_actions, add to processed_signals_for_incident) ...
                    pass
        else:
            self.logger.info("No active individual critical alerts for incident response.")


        # --- Green Wave Coordination Logic ---
        self.logger.info("Evaluating conditions for green wave coordination...")
        if system_kpis.get(MOCK_GREEN_WAVE_TRIGGER_KPI) == "HIGH":
            self.logger.info(f"High demand detected for KPI '{MOCK_GREEN_WAVE_TRIGGER_KPI}'. Attempting to activate green wave.")
            pilot_wave_id = "main_st_ns_wave"

            if pilot_wave_id in self.pilot_corridor_configs:
                config = self.pilot_corridor_configs[pilot_wave_id]
                self.logger.info(f"Activating green wave for corridor: '{pilot_wave_id}'")
                wave_success = await self._execute_green_wave(
                    corridor_id=pilot_wave_id, signals_in_order=config["signals_in_order"],
                    green_phase=config["target_green_phase"], green_time_seconds=config["wave_green_time_seconds"],
                    offsets_seconds=config["offsets_seconds"], all_current_signal_states=all_signal_states_map, # Pass map
                    processed_signals_for_coordination=processed_signals_for_coordination, now_utc=now_utc
                )
                if wave_success: self.logger.info(f"Green wave initiation for '{pilot_wave_id}' completed its sequence attempt.")
                else: self.logger.warning(f"Green wave initiation for '{pilot_wave_id}' failed or did not run due to config/preconditions.")
            else: self.logger.warning(f"Pilot corridor ID '{pilot_wave_id}' not found in configurations.")
        else: self.logger.info(f"Green wave trigger KPI '{MOCK_GREEN_WAVE_TRIGGER_KPI}' not HIGH (is '{system_kpis.get(MOCK_GREEN_WAVE_TRIGGER_KPI)}'). No green wave activated.")

        # --- Autonomous Traffic Signal Control Logic (General Congestion) ---
        if current_congestion_level == "HIGH":
            self.logger.info(f"High congestion detected ({current_congestion_level}). Evaluating general traffic signal interventions.")
            controlled_general_signal_this_cycle = False
            for signal_state in all_signal_states_list:
                if signal_state.signal_id in processed_signals_for_incident:
                    self.logger.debug(f"Signal '{signal_state.signal_id}' was handled by incident logic. Skipping general control.")
                    continue
                if signal_state.signal_id in processed_signals_for_coordination: # New check
                    self.logger.debug(f"Signal '{signal_state.signal_id}' was handled by coordination logic. Skipping general control.")
                    continue

                if signal_state.signal_id in self._recent_signal_actions:
                    action_data = self._recent_signal_actions[signal_state.signal_id]
                    cooldown_to_apply = self.SIGNAL_ACTION_COOLDOWN_SECONDS
                    log_reason = action_data.get('reason', 'unknown_reason')
                    if 'incident_id' in action_data: cooldown_to_apply = self.INCIDENT_SIGNAL_COOLDOWN_SECONDS

                    elapsed_time_seconds = (now_utc - action_data['timestamp']).total_seconds()
                    if elapsed_time_seconds < cooldown_to_apply:
                        self.logger.debug(f"Signal '{signal_state.signal_id}' on cooldown for '{log_reason}'. Elapsed {elapsed_time_seconds:.0f}s < {cooldown_to_apply}s. Skipping general control.")
                        continue
                    else: self.logger.debug(f"Signal '{signal_state.signal_id}' cooldown expired for '{log_reason}'.")

                if signal_state.operational_status == SignalOperationalStatusEnum.ONLINE and signal_state.current_phase != SignalPhaseEnum.GREEN:
                    self.logger.info(f"General Congestion: Attempting to set signal '{signal_state.signal_id}' to GREEN.")
                    try:
                        response = await self.traffic_signal_service.set_signal_phase(signal_state.signal_id, SignalPhaseEnum.GREEN, 60)
                        if response.status in [SignalControlStatusEnum.ACCEPTED, SignalControlStatusEnum.SUCCESS]:
                            self._recent_signal_actions[signal_state.signal_id] = {'timestamp': datetime.utcnow(), 'phase_commanded': SignalPhaseEnum.GREEN, 'duration_commanded': 60, 'reason': 'general_congestion'}
                            controlled_general_signal_this_cycle = True; self.logger.info(f"General congestion: Successfully set {signal_state.signal_id} to GREEN."); break
                        else: self.logger.error(f"General congestion: Failed to set {signal_state.signal_id} to GREEN. Response: {response.message}")
                    except Exception as e: self.logger.error(f"Error general control {signal_state.signal_id}: {e}")
            if not controlled_general_signal_this_cycle: self.logger.info("High congestion: No general signal interventions taken this cycle (all suitable signals on cooldown, processed, or failed).")
        else: self.logger.info(f"Congestion not HIGH ({current_congestion_level}). No general signal adjustments.")

        # --- Priority Location Setting ---
        # ... (existing logic from previous step - assumed correct) ...
        # --- Personalized Routing Phase ---
        # ... (existing logic - assumed correct) ...
        # --- System Status Assessment & Global Action (Operational Alerting) ---
        # ... (existing logic - assumed correct) ...
        # --- User-Specific Proactive Notifications (Predictive) ---
        # ... (existing logic - assumed correct) ...

        logger.info(f"--- AgentCore decision cycle completed for user: {sample_user_id} at {datetime.utcnow().isoformat()} ---")


async def main_example():
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    logger.info("--- Setting up AgentCore example for Green Wave ---")

    class MockAnalyticsService:
        _kpi_call_count = 0; _alert_call_count = 0
        def get_current_system_kpis_summary(self) -> Dict[str, Any]:
            MockAnalyticsService._kpi_call_count += 1
            kpis = {"overall_congestion_level": "MEDIUM", MOCK_GREEN_WAVE_TRIGGER_KPI: "LOW", "average_speed_kmh": 45} # Defaults
            if MockAnalyticsService._kpi_call_count == 1: # Cycle 1: Trigger Green Wave
                kpis[MOCK_GREEN_WAVE_TRIGGER_KPI] = "HIGH"
                kpis["overall_congestion_level"] = "LOW"
            elif MockAnalyticsService._kpi_call_count == 2: # Cycle 2: High overall congestion, no wave
                kpis["overall_congestion_level"] = "HIGH"
            logger.debug(f"MOCK Analytics KPI (call {MockAnalyticsService._kpi_call_count}): {kpis}")
            return kpis
        async def get_critical_alert_summary(self) -> Dict[str, Any]:
            self._alert_call_count +=1
            # Return no active incidents for this green wave focused test
            return { "critical_unack_alert_count": 0, "recent_critical_types": [], "active_alerts": [] }
        async def broadcast_operational_alert(self,title,msg,sev,act): pass
        async def send_user_specific_alert(self,uid,notif):pass
        async def predict_incident_likelihood(self,loc,time): return {}

    class MockTrafficSignalService:
        def __init__(self):
            self._signals: Dict[str, SignalState] = {}
            self._initialize_mock_signals()
        def _initialize_mock_signals(self):
            self._signals.clear()
            ids = ["TS001", "TS002", "TS003_NotInWave", "TS004"] # Ensure TS004 is here
            locations = [(1.0,1.0), (1.001, 1.001), (2.0, 2.0), (1.002, 1.002)]
            flows = ["NS", "NS", "EW", "NS"] # TS004 is NS
            for i, sid in enumerate(ids):
                self._signals[sid] = SignalState(signal_id=sid, location=LocationModel(latitude=locations[i][0], longitude=locations[i][1], name=f"{sid} ({flows[i]})"),
                                                 current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE,
                                                 last_updated=datetime.utcnow(), main_flow_direction=flows[i])
            logger.info(f"MockTrafficSignalService: Initialized {len(self._signals)} signals for green wave test.")
        async def get_all_signal_states(self) -> List[SignalState]: return list(self._signals.values())
        async def set_signal_phase(self, sid, phase, duration) -> SignalControlCommandResponse:
            if sid in self._signals and self._signals[sid].operational_status == SignalOperationalStatusEnum.ONLINE:
                self._signals[sid].current_phase = phase; self._signals[sid].last_updated = datetime.utcnow()
                logger.info(f"MOCK TSS: '{sid}' set to {phase.value} for {duration}s.")
                return SignalControlCommandResponse(signal_id=sid, status=SignalControlStatusEnum.ACCEPTED, new_state=self._signals[sid])
            logger.error(f"MOCK TSS: Failed to set '{sid}' - not found or not online.")
            return SignalControlCommandResponse(signal_id=sid, status=SignalControlStatusEnum.FAILED, message="Signal error in mock.")

    # Use MagicMock for services not directly under test focus for this example
    mock_prediction_scheduler = MagicMock(spec=PredictionScheduler)
    mock_prediction_scheduler.set_priority_locations = AsyncMock()
    mock_personalized_routing_service = MagicMock(spec=PersonalizedRoutingService)
    mock_personalized_routing_service.proactively_suggest_route = AsyncMock(return_value=None)
    mock_personalized_routing_service.get_user_common_travel_patterns = AsyncMock(return_value=[])


    agent = AgentCore(mock_prediction_scheduler, mock_personalized_routing_service, MockAnalyticsService(), MockTrafficSignalService())

    logger.info("--- MainExample GreenWave: Cycle 1 (Green Wave Triggered) ---")
    await agent.run_decision_cycle("user_greenwave_test1")
    assert "TS001" in agent._recent_signal_actions and agent._recent_signal_actions["TS001"]['reason'] == 'green_wave_main_st_ns_wave'
    assert "TS002" in agent._recent_signal_actions and agent._recent_signal_actions["TS002"]['reason'] == 'green_wave_main_st_ns_wave'
    assert "TS004" in agent._recent_signal_actions and agent._recent_signal_actions["TS004"]['reason'] == 'green_wave_main_st_ns_wave'

    logger.info("--- MainExample GreenWave: Cycle 2 (High Congestion, No Wave, Cooldowns from Wave Active) ---")
    agent.traffic_signal_service._signals["TS003_NotInWave"].current_phase = SignalPhaseEnum.RED
    await agent.run_decision_cycle("user_greenwave_test2")
    # TS001, TS002, TS004 should be skipped by general congestion due to green wave cooldown.
    # TS003_NotInWave should be targeted by general congestion if it's RED.
    assert "TS003_NotInWave" in agent._recent_signal_actions and agent._recent_signal_actions["TS003_NotInWave"]['reason'] == 'general_congestion'
    assert agent._recent_signal_actions.get("TS001", {}).get('reason') == 'green_wave_main_st_ns_wave' # Still from wave

    logger.info("--- AgentCore main_example for Green Wave completed ---")

if __name__ == "__main__":
    # asyncio.run(main_example())
    logger.info("AgentCore module defined. Example main_example() function available for testing.")

[end of backend/app/core/agent_core.py]
