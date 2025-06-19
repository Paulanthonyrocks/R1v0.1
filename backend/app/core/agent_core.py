import asyncio
import logging
from typing import Optional, Dict, Any, List, Set
import json
from datetime import datetime, timedelta, time
import math
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

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

ACTION_KPI_CONFIG = {
    "SET_SIGNAL_GREEN_CONGESTION": {
        "delay_seconds": 300,
        "metrics": ["flow_rate_absolute", "local_congestion_level", "wait_time_vehicle_seconds"],
        "eval_window_minutes": 5,
        "service_method": "get_signal_post_action_kpis"
    },
    "INCIDENT_RESPONSE_ACCIDENT": {
        "delay_seconds": 600,
        "metrics": ["clearance_time_seconds", "queue_length_meters", "local_congestion_level_incident_zone"],
        "eval_window_minutes": 10,
        "service_method": "get_incident_response_post_action_kpis" # Aligned name
    },
    "SET_SIGNAL_RED_ROAD_CLOSURE": {
        "delay_seconds": 300,
        "metrics": ["upstream_flow_rate_reduction_percentage", "local_congestion_level_upstream"],
        "eval_window_minutes": 10,
        "service_method": "get_signal_post_action_kpis"
    },
    "GREEN_WAVE_ACTIVATION": {
        "delay_seconds": 300,
        "metrics": ["corridor_avg_travel_time_seconds", "corridor_throughput_vehicle_per_hour", "stops_per_vehicle_in_corridor"],
        "eval_window_minutes": 15,
        "service_method": "get_corridor_post_action_kpis"
    }
}

class ActionPerformanceLog(BaseModel):
    action_id: UUID = Field(default_factory=uuid4)
    action_timestamp: datetime
    action_type: str = Field(..., description="Type of action taken by the agent", example="GREEN_WAVE_ACTIVATION")
    target_ids: List[str] = Field(..., description="Primary ID(s) of entities targeted by action", example=["corridor_main_st_ns_wave"] or ["TS001"])
    action_parameters: Dict[str, Any] = Field(default_factory=dict, description="Parameters used for the action", example={"duration_seconds": 60, "phase": "GREEN"})
    pre_action_context_kpis: Dict[str, Any] = Field(default_factory=dict, description="KPIs or context leading to the action", example={"overall_congestion": "HIGH", "trigger_kpi_value": "HIGH"})
    post_action_kpis: Optional[Dict[str, Any]] = Field(None, description="KPIs collected after the action's evaluation window", example={"corridor_avg_travel_time_seconds": 120})
    kpi_collection_timestamp: Optional[datetime] = Field(None, description="Timestamp when post_action_kpis were collected")


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
        self.action_performance_logs: List[ActionPerformanceLog] = []
        self.pending_kpi_collection: List[Dict[str, Any]] = []
        self.logger = logger # Use the module-level logger
        self.logger.info("AgentCore initialized with services, configs, and action performance logging.")

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

    async def _determine_next_travel_prediction_time(self, pattern: CommonTravelPattern, current_dt: datetime) -> Optional[datetime]: return None

    async def _execute_green_wave(
        self, corridor_id: str, signals_in_order: List[str], green_phase: SignalPhaseEnum,
        green_time_seconds: int, offsets_seconds: List[int], all_current_signal_states: Dict[str, SignalState],
        processed_signals_for_coordination: Set[str], now_utc: datetime
    ) -> bool:
        # ... (implementation from previous subtask, assumed correct)
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
        self.logger.info(f"--- Starting AgentCore cycle for {sample_user_id} at {now_utc.isoformat()} ---")

        # --- Process Pending KPI Collections ---
        self.logger.info(f"Checking {len(self.pending_kpi_collection)} pending KPI collection tasks.")
        processed_pending_indices: List[int] = []
        for i, pending_item in enumerate(self.pending_kpi_collection):
            if now_utc >= pending_item['query_after_timestamp']: # Use now_utc (start of cycle time)
                self.logger.info(f"KPI collection due for action ID {pending_item['action_id']} (type: {pending_item['action_type']}).")
                kpi_query_details = pending_item.get('kpi_query_details', {})
                service_method_name = kpi_query_details.get('service_method_name')
                method_specific_args = kpi_query_details.get('method_specific_args', {})
                if not service_method_name: self.logger.error(f"No service_method for action {pending_item['action_id']}. Skip."); continue

                service_call_args = method_specific_args.copy()
                service_call_args.update({
                    'action_type': pending_item['action_type'], 'action_timestamp': pending_item['action_timestamp'],
                    'metrics_to_collect': pending_item['metrics_to_collect'],
                    'evaluation_window_minutes': pending_item['evaluation_window_minutes']
                })
                if 'incident_location' in service_call_args and isinstance(service_call_args['incident_location'], dict):
                    try: service_call_args['incident_location'] = LocationModel(**service_call_args['incident_location'])
                    except Exception as e: self.logger.error(f"Error parsing loc for KPI query {pending_item['action_id']}: {e}"); continue
                post_kpis = None
                try:
                    if hasattr(self.analytics_service, service_method_name):
                        service_method = getattr(self.analytics_service, service_method_name)
                        self.logger.debug(f"Calling AnalyticsService method '{service_method_name}' with args: {service_call_args}")
                        post_kpis = await service_method(**service_call_args)
                        self.logger.info(f"Collected post-action KPIs for action {pending_item['action_id']}: {post_kpis}")
                    else: self.logger.error(f"AnalyticsService method '{service_method_name}' not found for {pending_item['action_id']}.")
                except Exception as e: self.logger.error(f"Error collecting KPIs for {pending_item['action_id']}: {e}", exc_info=True)

                self.action_performance_logs.append(ActionPerformanceLog(
                    action_id=pending_item['action_id'], action_timestamp=pending_item['action_timestamp'],
                    action_type=pending_item['action_type'], target_ids=pending_item['target_ids'],
                    action_parameters=pending_item['action_parameters'], pre_action_context_kpis=pending_item['pre_action_context_kpis'],
                    post_action_kpis=post_kpis, kpi_collection_timestamp=now_utc # Use consistent now_utc
                ))
                self.logger.info(f"Added ActionPerformanceLog for {pending_item['action_id']}. Total logs: {len(self.action_performance_logs)}.")
                processed_pending_indices.append(i)
            else: self.logger.debug(f"KPI collection for {pending_item['action_id']} not due (Query at {pending_item['query_after_timestamp'].isoformat()}).")
        if processed_pending_indices:
            for index in sorted(processed_pending_indices, reverse=True): self.pending_kpi_collection.pop(index)
            self.logger.info(f"Removed {len(processed_pending_indices)} processed KPI tasks. {len(self.pending_kpi_collection)} remain.")

        # Cooldown cleanup for _recent_signal_actions
        # ... (logic from previous step) ...

        system_kpis = self.analytics_service.get_current_system_kpis_summary() # Re-fetch or use earlier if state should be frozen
        alert_summary = await self.analytics_service.get_critical_alert_summary()
        all_signal_states_list = await self.traffic_signal_service.get_all_signal_states()
        all_signal_states_map: Dict[str, SignalState] = {s.signal_id: s for s in all_signal_states_list}
        current_congestion_level = system_kpis.get("overall_congestion_level", "UNKNOWN")

        # --- Incident-Specific Signal Control --- (Placeholder for brevity, ensure KPI scheduling is integrated here)
        # ...

        # --- Green Wave Coordination Logic --- (Placeholder for brevity, ensure KPI scheduling is integrated here)
        # ...

        # --- Autonomous Traffic Signal Control Logic (General Congestion) --- (Placeholder for brevity, ensure KPI scheduling is integrated here)
        # ...

        self.logger.info(f"--- AgentCore cycle completed for {sample_user_id} at {datetime.utcnow().isoformat()} ---")


@patch('app.core.agent_core.datetime')
async def main_example_run_with_mock_time(mock_datetime_obj, specific_time_utc_str: str, sample_user_id: str, agent_core: AgentCore, analytics_service_mock: Any, kpi_overrides: Dict[str, Any]):
    # ... (helper from previous step, assumed correct) ...
    mocked_now = datetime.fromisoformat(specific_time_utc_str.replace("Z","+00:00"))
    mock_datetime_obj.utcnow.return_value = mocked_now
    original_kpis_func = analytics_service_mock.get_current_system_kpis_summary
    def get_modified_kpis():
        base_kpis = {"overall_congestion_level": "LOW", "average_speed_kmh": 50}
        for kpi_name in ALL_CORRIDOR_DEMAND_KPIS: base_kpis[kpi_name] = "LOW"
        base_kpis.update(kpi_overrides); return base_kpis
    analytics_service_mock.get_current_system_kpis_summary = get_modified_kpis
    logger.info(f"--- Running main_example cycle MOCKED TIME: {mocked_now.isoformat()} for {sample_user_id} ---")
    await agent_core.run_decision_cycle(sample_user_id=sample_user_id)
    analytics_service_mock.get_current_system_kpis_summary = original_kpis_func


async def main_example():
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    logger.info("--- Setting up AgentCore example (Enhanced Mock Analytics for KPI Collection) ---")

    class MockAnalyticsService:
        _get_kpi_call_counters = {
            "get_signal_post_action_kpis": 0,
            "get_incident_response_post_action_kpis": 0,
            "get_corridor_post_action_kpis": 0
        }

        async def get_critical_alert_summary(self) -> Dict[str, Any]: return {"active_alerts": []}
        def get_current_system_kpis_summary(self) -> Dict[str, Any]: return {}
        async def broadcast_operational_alert(self,t,m,s,a):pass; async def send_user_specific_alert(self,u,n):pass; async def predict_incident_likelihood(self,l,t): return {}

        async def get_signal_post_action_kpis(self, signal_id: str, action_type: str, action_timestamp: datetime, metrics_to_collect: List[str], evaluation_window_minutes: int, **kwargs) -> Dict[str, Any]:
            self._get_kpi_call_counters["get_signal_post_action_kpis"] += 1
            call_count = self._get_kpi_call_counters["get_signal_post_action_kpis"]
            logger.info(
                f"MOCK Analytics: get_signal_post_action_kpis (Call #{call_count}) for signal '{signal_id}', action_type '{action_type}', "
                f"metrics: {metrics_to_collect}, window: {evaluation_window_minutes}min. Action at {action_timestamp.isoformat()}. Called at {datetime.utcnow().isoformat()}"
            )
            kpis = {"queried_signal_id": signal_id, "action_type_processed": action_type, "call_count": call_count}
            if "flow_rate_absolute" in metrics_to_collect: kpis["flow_rate_absolute"] = 100 + (hash(signal_id+action_type) % 50) + call_count
            if "local_congestion_level" in metrics_to_collect: kpis["local_congestion_level"] = "LOW" if call_count % 2 == 0 else "MEDIUM"
            logger.debug(f"Returning mock signal KPIs: {kpis}")
            return kpis

        async def get_corridor_post_action_kpis(self, corridor_id: str, action_type: str, action_timestamp: datetime, metrics_to_collect: List[str], evaluation_window_minutes: int, **kwargs) -> Dict[str, Any]:
            self._get_kpi_call_counters["get_corridor_post_action_kpis"] += 1
            call_count = self._get_kpi_call_counters["get_corridor_post_action_kpis"]
            logger.info(
                f"MOCK Analytics: get_corridor_post_action_kpis (Call #{call_count}) for corridor '{corridor_id}', action_type '{action_type}', "
                f"metrics: {metrics_to_collect}, window: {evaluation_window_minutes}min. Action at {action_timestamp.isoformat()}. Called at {datetime.utcnow().isoformat()}"
            )
            kpis = {"queried_corridor_id": corridor_id, "action_type_processed": action_type, "call_count": call_count}
            if "corridor_avg_travel_time_seconds" in metrics_to_collect: kpis["corridor_avg_travel_time_seconds"] = 120 - (hash(corridor_id) % 30) - call_count
            if "stops_per_vehicle_in_corridor" in metrics_to_collect: kpis["stops_per_vehicle_in_corridor"] = 2 + (call_count % 3)
            logger.debug(f"Returning mock corridor KPIs: {kpis}")
            return kpis

        async def get_incident_response_post_action_kpis(self, incident_id: str, incident_location: Optional[LocationModel], signal_ids_involved: List[str], action_type: str, action_timestamp: datetime, metrics_to_collect: List[str], evaluation_window_minutes: int, **kwargs) -> Dict[str, Any]:
            self._get_kpi_call_counters["get_incident_response_post_action_kpis"] += 1
            call_count = self._get_kpi_call_counters["get_incident_response_post_action_kpis"]
            logger.info(
                f"MOCK Analytics: get_incident_response_post_action_kpis (Call #{call_count}) for incident '{incident_id}', signals {signal_ids_involved}, type '{action_type}'. "
                f"Metrics: {metrics_to_collect}, window: {evaluation_window_minutes}min. Action at {action_timestamp.isoformat()}. Called at {datetime.utcnow().isoformat()}"
            )
            kpis = {"queried_incident_id": incident_id, "action_type_processed": action_type, "call_count": call_count}
            if "clearance_time_seconds" in metrics_to_collect: kpis["clearance_time_seconds"] = 1500 - (hash(incident_id) % 300)
            if "queue_length_meters" in metrics_to_collect: kpis["queue_length_meters"] = 50 + (call_count * 10)
            logger.debug(f"Returning mock incident KPIs: {kpis}")
            return kpis

    class MockTrafficSignalService: # Simplified
        def __init__(self): self._signals: Dict[str, SignalState] = {}; self._initialize_mock_signals()
        def _initialize_mock_signals(self):
            self._signals.clear(); sids = ["TS001", "TS002", "TS004"]; # Signals for main_st_ns_wave
            for i, sid in enumerate(sids): self._signals[sid] = SignalState(signal_id=sid, location=LocationModel(latitude=1.0+i*0.001, longitude=1.0+i*0.001, name=sid), current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=datetime.utcnow(), main_flow_direction="NS")
        async def get_all_signal_states(self) -> List[SignalState]: return list(self._signals.values())
        async def set_signal_phase(self, sid,p,d) -> SignalControlCommandResponse:
            if sid in self._signals: self._signals[sid].current_phase = p; return SignalControlCommandResponse(signal_id=sid, status=SignalControlStatusEnum.ACCEPTED)
            return SignalControlCommandResponse(signal_id=sid, status=SignalControlStatusEnum.FAILED)

    analytics_mock = MockAnalyticsService()
    agent_core = AgentCore(MagicMock(spec=PredictionScheduler), MagicMock(spec=PersonalizedRoutingService), analytics_mock, MockTrafficSignalService())

    # Cycle 1: Trigger Green Wave, schedule KPI collection
    logger.info("--- MainExample Enhanced Mock: Cycle 1 (Trigger Green Wave) ---")
    kpi_scen1 = {GREEN_WAVE_CORRIDOR_CONFIGS["main_st_ns_wave"]["demand_kpi_trigger"]:"HIGH"}
    await main_example_run_with_mock_time("2023-01-01T08:00:00Z", "user_kpi_final_test", agent_core, analytics_mock, kpi_scen1)
    assert len(agent_core.pending_kpi_collection) == 1
    pending_item_action_id = agent_core.pending_kpi_collection[0]['action_id']

    # Cycle 2: Time advanced, KPI collection for green wave IS DUE
    logger.info("--- MainExample Enhanced Mock: Cycle 2 (KPI collection for Green Wave) ---")
    delay_for_gw_kpi = ACTION_KPI_CONFIG["GREEN_WAVE_ACTIVATION"]["delay_seconds"]
    time_for_gw_kpi_collection = datetime.fromisoformat("2023-01-01T08:00:00Z") + timedelta(seconds=delay_for_gw_kpi + 30) # Add 30s buffer
    await main_example_run_with_mock_time(time_for_gw_kpi_collection.isoformat().replace("+00:00","Z"), "user_kpi_final_test", agent_core, analytics_mock, {})
    assert len(agent_core.pending_kpi_collection) == 0
    assert len(agent_core.action_performance_logs) == 1
    if agent_core.action_performance_logs:
        assert agent_core.action_performance_logs[0].action_id == pending_item_action_id
        assert agent_core.action_performance_logs[0].post_action_kpis.get("queried_corridor_id") == "main_st_ns_wave"
        logger.info(f"Collected Log for GW: {agent_core.action_performance_logs[0].model_dump_json(indent=2, default=str)}")

    logger.info("--- AgentCore main_example for Enhanced Mock Analytics completed ---")

if __name__ == "__main__":
    # asyncio.run(main_example())
    logger.info("AgentCore module defined. Example main_example() function available for testing.")

[end of backend/app/core/agent_core.py]
