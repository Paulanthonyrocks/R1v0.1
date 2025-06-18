import asyncio
import logging
from typing import Optional, Dict, Any, List, Set
import json
from datetime import datetime, timedelta, time
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

GREEN_WAVE_CORRIDOR_CONFIGS = {
    "main_st_ns_wave": {
        "description": "Main Street North-South Peak Hour Green Wave",
        "signals_in_order": ["TS001", "TS002", "TS004"],
        "target_green_phase": SignalPhaseEnum.GREEN,
        "wave_green_time_seconds": 50,
        "offsets_seconds": [0, 18, 36],
        "corridor_flow_direction_assumption": "NS",
        "time_windows": [{"start": "07:00", "end": "09:00"}, {"start": "16:00", "end": "18:00"}],
        "demand_kpi_trigger": "corridor_main_st_ns_demand_high",
        "priority": 1
    },
    "oak_ave_ew_wave": {
        "description": "Oak Avenue East-West Mid-day Green Wave",
        "signals_in_order": ["TS003", "TS005"],
        "target_green_phase": SignalPhaseEnum.GREEN,
        "wave_green_time_seconds": 40,
        "offsets_seconds": [0, 25],
        "corridor_flow_direction_assumption": "EW",
        "time_windows": [{"start": "11:00", "end": "13:00"}],
        "demand_kpi_trigger": "corridor_oak_ave_ew_demand_moderate",
        "priority": 2
    }
}

ALL_CORRIDOR_DEMAND_KPIS = list(set([
    config.get("demand_kpi_trigger")
    for config in GREEN_WAVE_CORRIDOR_CONFIGS.values()
    if config.get("demand_kpi_trigger")
]))


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
        self.green_wave_corridor_configs = GREEN_WAVE_CORRIDOR_CONFIGS
        self.logger = logger
        logger.info("AgentCore initialized with services and new green wave corridor configs.")

    def _calculate_haversine_distance(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 6371000; phi1 = math.radians(lat1); phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1); delta_lambda = math.radians(lon2 - lon1)
        a = math.sin(delta_phi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)); return R * c

    async def _find_signals_near_location(self, incident_location: LocationModel, all_signals: List[SignalState], radius_meters: int) -> List[SignalState]:
        nearby_signals = []
        if not incident_location: self.logger.warning("Incident location is None..."); return nearby_signals
        for signal in all_signals:
            if signal.location and isinstance(signal.location.latitude, (float, int)) and isinstance(signal.location.longitude, (float, int)):
                distance = self._calculate_haversine_distance(incident_location.latitude, incident_location.longitude, signal.location.latitude, signal.location.longitude)
                if distance <= radius_meters: nearby_signals.append(signal)
        return nearby_signals

    async def _determine_next_travel_prediction_time(self, pattern: CommonTravelPattern, current_dt: datetime) -> Optional[datetime]:
        return None # Simplified for this context

    async def _execute_green_wave(
        self, corridor_id: str, signals_in_order: List[str], green_phase: SignalPhaseEnum,
        green_time_seconds: int, offsets_seconds: List[int], all_current_signal_states: Dict[str, SignalState],
        processed_signals_for_coordination: Set[str], now_utc: datetime
    ) -> bool:
        self.logger.info(f"Attempting to execute green wave '{corridor_id}' for signals: {signals_in_order}")
        if not (len(signals_in_order) == len(offsets_seconds) and signals_in_order):
            self.logger.error(f"GW '{corridor_id}': Invalid config. Cannot execute."); return False
        wave_initiation_time_utc = now_utc; actual_commands_sent = 0
        for i, signal_id in enumerate(signals_in_order):
            signal_state = all_current_signal_states.get(signal_id); current_offset_seconds = offsets_seconds[i]
            if not signal_state or signal_state.operational_status != SignalOperationalStatusEnum.ONLINE:
                self.logger.warning(f"GW '{corridor_id}': Sig '{signal_id}' not online/found. Skipping."); continue
            if signal_id in processed_signals_for_incident:
                self.logger.info(f"GW '{corridor_id}': Sig '{signal_id}' incident-processed. Skipping."); continue
            if signal_id in processed_signals_for_coordination:
                self.logger.info(f"GW '{corridor_id}': Sig '{signal_id}' already in a coordination plan this cycle. Skipping."); continue
            if signal_id in self._recent_signal_actions and \
               (now_utc - self._recent_signal_actions[signal_id]['timestamp']).total_seconds() < self.SIGNAL_ACTION_COOLDOWN_SECONDS:
                self.logger.info(f"GW '{corridor_id}': Sig '{signal_id}' on general cooldown. Skipping."); continue
            target_command_time = wave_initiation_time_utc + timedelta(seconds=current_offset_seconds)
            delay_seconds = (target_command_time - datetime.utcnow()).total_seconds()
            if delay_seconds > 0.05: self.logger.debug(f"GW '{corridor_id}': Wait {delay_seconds:.2f}s for '{signal_id}'."); await asyncio.sleep(delay_seconds)
            else: self.logger.debug(f"GW '{corridor_id}': Cmd '{signal_id}' (offset {current_offset_seconds}s) delay {delay_seconds:.2f}s.")
            self.logger.info(f"GW '{corridor_id}': Commanding '{signal_id}' to {green_phase.value} for {green_time_seconds}s.")
            try:
                response = await self.traffic_signal_service.set_signal_phase(signal_id, green_phase, green_time_seconds)
                action_ts = datetime.utcnow()
                if response.status in [SignalControlStatusEnum.ACCEPTED, SignalControlStatusEnum.SUCCESS]:
                    self._recent_signal_actions[signal_id] = {'timestamp': action_ts, 'phase_commanded': green_phase, 'duration_commanded': green_time_seconds, 'reason': f'green_wave_{corridor_id}'}
                    processed_signals_for_coordination.add(signal_id); actual_commands_sent +=1
                    self.logger.info(f"GW '{corridor_id}': OK cmd '{signal_id}'. Recorded.")
                else: self.logger.error(f"GW '{corridor_id}': FAIL cmd '{signal_id}'. Resp: {response.status.value}")
            except Exception as e: self.logger.error(f"GW '{corridor_id}': EXC cmd '{signal_id}': {e}", exc_info=True)
        self.logger.info(f"GW '{corridor_id}' sequence attempt finished. {actual_commands_sent}/{len(signals_in_order)} signals commanded.");
        return actual_commands_sent > 0

    async def run_decision_cycle(self, sample_user_id: str = "user_agent_test_123"):
        processed_signals_for_incident: Set[str] = set()
        processed_signals_for_coordination: Set[str] = set()
        now_utc = datetime.utcnow(); current_time_obj = now_utc.time()
        logger.info(f"--- Starting AgentCore cycle for {sample_user_id} at {now_utc.isoformat()} ---")

        actions_to_remove = [sid for sid, data in self._recent_signal_actions.items() if (now_utc - data['timestamp']).total_seconds() > (self.INCIDENT_SIGNAL_COOLDOWN_SECONDS if 'incident_id' in data else self.SIGNAL_ACTION_COOLDOWN_SECONDS)]
        for sid in actions_to_remove:
            if sid in self._recent_signal_actions : del self._recent_signal_actions[sid]; self.logger.debug(f"Removed '{sid}' from recent actions.")

        system_kpis = self.analytics_service.get_current_system_kpis_summary()
        alert_summary = await self.analytics_service.get_critical_alert_summary()
        all_signal_states_list = await self.traffic_signal_service.get_all_signal_states()
        all_signal_states_map: Dict[str, SignalState] = {s.signal_id: s for s in all_signal_states_list}

        self.logger.info(f"KPIs: {json.dumps(system_kpis, indent=0)}, Alerts: {alert_summary.get('critical_unack_alert_count',0)}, Signals: {len(all_signal_states_list)}")

        # --- Incident-Specific Signal Control --- (Placeholder for brevity)
        self.logger.debug("Incident logic would run here.")

        # --- Green Wave Coordination Logic ---
        self.logger.info("Evaluating green wave corridors...")
        candidate_corridors: List[Dict[str, Any]] = []
        for corridor_id, config in self.green_wave_corridor_configs.items():
            is_time_triggered, is_demand_triggered = False, False
            if config.get("time_windows"): # Time check
                for window in config["time_windows"]:
                    start_str, end_str = window.get("start"), window.get("end")
                    if start_str and end_str:
                        try:
                            if datetime.strptime(start_str, "%H:%M").time() <= current_time_obj < datetime.strptime(end_str, "%H:%M").time():
                                is_time_triggered = True; self.logger.debug(f"Corridor '{corridor_id}' time-triggered."); break
                        except ValueError as ve: self.logger.error(f"Time parse error for '{corridor_id}': {ve}")
            demand_kpi_name = config.get("demand_kpi_trigger") # Demand check
            if demand_kpi_name and system_kpis.get(demand_kpi_name) == "HIGH":
                is_demand_triggered = True; self.logger.debug(f"Corridor '{corridor_id}' demand-triggered.")
            if is_time_triggered or is_demand_triggered:
                self.logger.info(f"Corridor '{corridor_id}' candidate (T:{is_time_triggered},D:{is_demand_triggered}).")
                candidate_corridors.append({"id": corridor_id, "priority": config.get("priority", 99), "config": config})

        selected_wave_to_run = None
        if candidate_corridors:
            candidate_corridors.sort(key=lambda x: x["priority"])
            self.logger.info(f"Sorted candidates: {[c['id'] for c in candidate_corridors]}.")
            for candidate in candidate_corridors:
                config_to_check = candidate["config"]; signals_for_this_wave = config_to_check.get("signals_in_order", [])
                can_run_this_wave = not any(s_id in processed_signals_for_coordination for s_id in signals_for_this_wave)
                if can_run_this_wave: selected_wave_to_run = candidate; self.logger.info(f"Selected GW: '{candidate['id']}'."); break
                else: self.logger.info(f"Cand. wave '{candidate['id']}' skipped: signal conflict with higher-prio wave.")

        if selected_wave_to_run:
            cfg = selected_wave_to_run["config"]
            await self._execute_green_wave(selected_wave_to_run["id"], cfg["signals_in_order"], cfg["target_green_phase"],
                                           cfg["wave_green_time_seconds"], cfg["offsets_seconds"], all_signal_states_map,
                                           processed_signals_for_coordination, now_utc)
        elif candidate_corridors: self.logger.info("No suitable wave selected from candidates (conflicts).")
        else: self.logger.info("No green wave corridors triggered.")

        # --- Autonomous Traffic Signal Control Logic (General Congestion) --- (Placeholder for brevity)
        self.logger.debug("General congestion logic would run here, respecting processed sets.")

        # ... (rest of run_decision_cycle sections: priority locations, personalized routing, etc.) ...
        logger.info(f"--- AgentCore cycle completed for {sample_user_id} at {datetime.utcnow().isoformat()} ---")

@patch('app.core.agent_core.datetime')
async def main_example_run_with_mock_time(mock_datetime_obj, specific_time_utc_str: str, sample_user_id: str, agent_core: AgentCore, analytics_service_mock: Any, kpi_overrides: Dict[str, Any]):
    mocked_now = datetime.fromisoformat(specific_time_utc_str.replace("Z","+00:00"))
    mock_datetime_obj.utcnow.return_value = mocked_now
    original_kpis_func = analytics_service_mock.get_current_system_kpis_summary
    def get_modified_kpis():
        base_kpis = {"overall_congestion_level": "LOW", "average_speed_kmh": 50}
        for kpi_name in ALL_CORRIDOR_DEMAND_KPIS: base_kpis[kpi_name] = "LOW"
        base_kpis.update(kpi_overrides);
        logger.debug(f"MOCK AnalyticsService providing KPIs: {base_kpis} for time {mocked_now.isoformat()}")
        return base_kpis
    analytics_service_mock.get_current_system_kpis_summary = get_modified_kpis
    logger.info(f"--- Running main_example cycle MOCKED TIME: {mocked_now.isoformat()} for {sample_user_id} ---")
    await agent_core.run_decision_cycle(sample_user_id=sample_user_id)
    analytics_service_mock.get_current_system_kpis_summary = original_kpis_func

async def main_example():
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    logger.info("--- Setting up AgentCore example (Generalized Green Wave Trigger & Selection) ---")

    class MockAnalyticsService:
        async def get_critical_alert_summary(self) -> Dict[str, Any]: return {"active_alerts": []}
        async def broadcast_operational_alert(self,t,m,s,a):pass; async def send_user_specific_alert(self,u,n):pass; async def predict_incident_likelihood(self,l,t): return {}
        def get_current_system_kpis_summary(self) -> Dict[str, Any]: return {}

    class MockTrafficSignalService:
        def __init__(self): self._signals: Dict[str, SignalState] = {}; self._initialize_mock_signals()
        def _initialize_mock_signals(self):
            self._signals.clear()
            sids = ["TS001", "TS002", "TS003", "TS004", "TS005", "TS006_NotInWave"]
            for i, sid in enumerate(sids):
                 self._signals[sid] = SignalState(signal_id=sid, location=LocationModel(latitude=1.0+i*0.001, longitude=1.0+i*0.001, name=sid),
                                                 current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE,
                                                 last_updated=datetime.utcnow(), main_flow_direction="NS" if i%2==0 else "EW")
            logger.info(f"MockTrafficSignalService: Initialized/Reset {len(self._signals)} signals.")
        async def get_all_signal_states(self) -> List[SignalState]: return list(self._signals.values())
        async def set_signal_phase(self, sid,p,d) -> SignalControlCommandResponse:
            if sid in self._signals: self._signals[sid].current_phase = p; self._signals[sid].last_updated = datetime.utcnow(); logger.info(f"MOCK TSS: '{sid}' to {p.value}"); return SignalControlCommandResponse(signal_id=sid, status=SignalControlStatusEnum.ACCEPTED, new_state=self._signals[sid])
            return SignalControlCommandResponse(signal_id=sid, status=SignalControlStatusEnum.FAILED)

    analytics_mock = MockAnalyticsService()
    traffic_service_mock = MockTrafficSignalService()
    agent_core = AgentCore(MagicMock(spec=PredictionScheduler), MagicMock(spec=PersonalizedRoutingService), analytics_mock, traffic_service_mock)

    # Scenario 1: "main_st_ns_wave" (P1) by Time Only (08:00 UTC)
    logger.info("--- MainExample GW: Cycle 1 (main_st_ns_wave P1 by Time @ 08:00) ---")
    traffic_service_mock._initialize_mock_signals(); agent_core._recent_signal_actions.clear()
    kpi_scen1 = {cfg["demand_kpi_trigger"]:"LOW" for cfg_id, cfg in GREEN_WAVE_CORRIDOR_CONFIGS.items()}
    await main_example_run_with_mock_time("2023-01-01T08:00:00Z", "user_s1_main_time", agent_core, analytics_mock, kpi_scen1)
    assert "green_wave_main_st_ns_wave" == agent_core._recent_signal_actions.get("TS001",{}).get('reason')

    # Scenario 2: "oak_ave_ew_wave" (P2) by Time Only (11:30 UTC)
    logger.info("--- MainExample GW: Cycle 2 (oak_ave_ew_wave P2 by Time @ 11:30) ---")
    traffic_service_mock._initialize_mock_signals(); agent_core._recent_signal_actions.clear()
    kpi_scen2 = {cfg["demand_kpi_trigger"]:"LOW" for cfg_id, cfg in GREEN_WAVE_CORRIDOR_CONFIGS.items()}
    await main_example_run_with_mock_time("2023-01-01T11:30:00Z", "user_s2_oak_time", agent_core, analytics_mock, kpi_scen2)
    assert "green_wave_oak_ave_ew_wave" == agent_core._recent_signal_actions.get("TS003",{}).get('reason')

    # Scenario 3: No wave by Time (03:00 UTC), No wave by Demand
    logger.info("--- MainExample GW: Cycle 3 (No Time/Demand Triggers @ 03:00) ---")
    traffic_service_mock._initialize_mock_signals(); agent_core._recent_signal_actions.clear()
    kpi_scen3 = {cfg["demand_kpi_trigger"]:"LOW" for cfg_id, cfg in GREEN_WAVE_CORRIDOR_CONFIGS.items()}
    await main_example_run_with_mock_time("2023-01-01T03:00:00Z", "user_s3_no_wave", agent_core, analytics_mock, kpi_scen3)
    assert not any("green_wave" in r.get('reason','') for r in agent_core._recent_signal_actions.values() if r)

    # Scenario 4: main_st_ns_wave (P1) by Time (07:30), oak_ave_ew_wave (P2) by Demand. Expect P1.
    logger.info("--- MainExample GW: Cycle 4 (P1 Time vs P2 Demand @ 07:30 - P1 should run) ---")
    traffic_service_mock._initialize_mock_signals(); agent_core._recent_signal_actions.clear()
    kpi_scen4 = {GREEN_WAVE_CORRIDOR_CONFIGS["main_st_ns_wave"]["demand_kpi_trigger"]:"LOW", GREEN_WAVE_CORRIDOR_CONFIGS["oak_ave_ew_wave"]["demand_kpi_trigger"]:"HIGH"}
    await main_example_run_with_mock_time("2023-01-01T07:30:00Z", "user_s4_p1_time_vs_p2_demand", agent_core, analytics_mock, kpi_scen4)
    assert "green_wave_main_st_ns_wave" == agent_core._recent_signal_actions.get("TS001",{}).get('reason')
    assert "green_wave_oak_ave_ew_wave" not in [r.get('reason') for r in agent_core._recent_signal_actions.values() if r]

    logger.info("--- AgentCore main_example for Green Wave Trigger & Selection Logic completed ---")

if __name__ == "__main__":
    # asyncio.run(main_example())
    logger.info("AgentCore module defined. Example main_example() function available for testing.")

[end of backend/app/core/agent_core.py]
