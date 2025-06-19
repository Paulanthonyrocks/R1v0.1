import asyncio
import logging
from typing import Optional, Dict, Any, List, Set, Tuple
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
        "signals_in_order": ["TS001", "TS002", "TS004"], "target_green_phase": SignalPhaseEnum.GREEN,
        "wave_green_time_seconds": 50, "offsets_seconds": [0, 18, 36], "corridor_flow_direction_assumption": "NS",
        "time_windows": [{"start": "07:00", "end": "09:00"}, {"start": "16:00", "end": "18:00"}],
        "demand_kpi_trigger": "corridor_main_st_ns_demand_high", "priority": 1
    },
    "oak_ave_ew_wave": {
        "description": "Oak Avenue East-West Mid-day Green Wave",
        "signals_in_order": ["TS003", "TS005"], "target_green_phase": SignalPhaseEnum.GREEN,
        "wave_green_time_seconds": 40, "offsets_seconds": [0, 25], "corridor_flow_direction_assumption": "EW",
        "time_windows": [{"start": "11:00", "end": "13:00"}],
        "demand_kpi_trigger": "corridor_oak_ave_ew_demand_moderate", "priority": 2
    }
}

ALL_CORRIDOR_DEMAND_KPIS = list(set([ c.get("demand_kpi_trigger") for c in GREEN_WAVE_CORRIDOR_CONFIGS.values() if c.get("demand_kpi_trigger")]))

ACTION_KPI_CONFIG = {
    "SET_SIGNAL_GREEN_CONGESTION": {"delay_seconds": 300, "metrics": ["flow_rate_absolute", "local_congestion_level"], "eval_window_minutes": 5, "service_method": "get_signal_post_action_kpis"},
    "INCIDENT_RESPONSE_ACCIDENT": {"delay_seconds": 600, "metrics": ["clearance_time_seconds", "local_congestion_level_incident_zone"], "eval_window_minutes": 10, "service_method": "get_incident_response_post_action_kpis"},
    "SET_SIGNAL_RED_ROAD_CLOSURE": {"delay_seconds": 300, "metrics": ["upstream_flow_rate_reduction_percentage"], "eval_window_minutes": 10, "service_method": "get_signal_post_action_kpis"},
    "GREEN_WAVE_ACTIVATION": {"delay_seconds": 300, "metrics": ["corridor_avg_travel_time_seconds", "stops_per_vehicle_in_corridor"], "eval_window_minutes": 15, "service_method": "get_corridor_post_action_kpis"}
}

ACTION_EFFECTIVENESS_CONFIG = {
    "SET_SIGNAL_GREEN_CONGESTION": {
        "relevant_kpis": [
            {"source": "pre", "key_path": ["signal_initial_phase"], "as": "pre_signal_phase"},
            {"source": "pre", "key_path": ["overall_congestion"], "as": "pre_overall_congestion_proxy"},
            {"source": "post", "key_path": ["local_congestion_level"], "as": "post_local_congestion"},
            {"source": "post", "key_path": ["flow_rate_absolute"], "as": "post_flow_rate"}
        ], "scoring_logic_type": "congestion_improvement"
    },
    "GREEN_WAVE_ACTIVATION": {
        "relevant_kpis": [
            {"source": "pre", "key_path": ["trigger_kpi_value"], "as": "pre_trigger_kpi"},
            {"source": "post", "key_path": ["corridor_avg_travel_time_seconds"], "as": "post_travel_time"},
            {"source": "post", "key_path": ["stops_per_vehicle_in_corridor"], "as": "post_stops_per_vehicle"}
        ], "scoring_logic_type": "green_wave_efficiency"
    },
    "INCIDENT_RESPONSE_ACCIDENT": {
        "relevant_kpis": [
            {"source": "post", "key_path": ["clearance_time_seconds"], "as": "post_clearance_time"},
            {"source": "post", "key_path": ["local_congestion_level_incident_zone"], "as": "post_incident_zone_congestion"}
        ], "scoring_logic_type": "incident_clearance_speed"
    },
    "SET_SIGNAL_RED_ROAD_CLOSURE": {
        "relevant_kpis": [
            {"source": "post", "key_path": ["upstream_flow_rate_reduction_percentage"], "as": "post_flow_reduction_percentage"}
        ], "scoring_logic_type": "closure_effectiveness"
    }
}

class ActionPerformanceLog(BaseModel):
    action_id: UUID = Field(default_factory=uuid4)
    action_timestamp: datetime
    action_type: str = Field(...)
    target_ids: List[str] = Field(...)
    action_parameters: Dict[str, Any] = Field(default_factory=dict)
    pre_action_context_kpis: Dict[str, Any] = Field(default_factory=dict)
    post_action_kpis: Optional[Dict[str, Any]] = Field(None)
    kpi_collection_timestamp: Optional[datetime] = Field(None)
    effectiveness_score: Optional[float] = Field(None, description="Calculated effectiveness score, e.g., -1.0 to +1.0")
    effectiveness_metrics_used: Optional[Dict[str, Any]] = Field(None, description="Specific pre/post KPI values used for score")

class AgentCore:
    SIGNAL_ACTION_COOLDOWN_SECONDS = 120; INCIDENT_SIGNAL_COOLDOWN_SECONDS = 300
    ROAD_CLOSURE_IMMEDIATE_RADIUS_METERS = 50

    def __init__(self, pred_sched: PredictionScheduler, person_routing: PersonalizedRoutingService, analytics: AnalyticsService, traffic_sig: TrafficSignalService):
        self.prediction_scheduler = pred_sched; self.personalized_routing_service = person_routing
        self.analytics_service = analytics; self.traffic_signal_service = traffic_sig
        self._recent_signal_actions: Dict[str, Dict[str, Any]] = {}
        self.green_wave_corridor_configs = GREEN_WAVE_CORRIDOR_CONFIGS
        self.action_effectiveness_config = ACTION_EFFECTIVENESS_CONFIG
        self.action_performance_logs: List[ActionPerformanceLog] = []
        self.pending_kpi_collection: List[Dict[str, Any]] = []
        self.action_effectiveness_memory: Dict[str, List[float]] = {} # New
        self.MAX_SCORES_PER_ACTION_SIGNATURE: int = 10 # New
        self.logger = logger; self.logger.info("AgentCore initialized with effectiveness scoring and memory.")

    def _extract_kpi_value(self, source_dict: Optional[Dict[str, Any]], key_path: List[str]) -> Any:
        # ... (implementation from previous step) ...
        if source_dict is None: return None
        val = source_dict
        for key in key_path:
            if isinstance(val, dict) and key in val: val = val[key]
            else: self.logger.debug(f"Key path {key_path} not fully found. Missing '{key}'."); return None
        return val

    def _score_congestion_improvement(self, metrics: Dict[str, Any]) -> Optional[float]:
        # ... (implementation from previous step) ...
        score = 0.0; pre_overall_proxy = metrics.get("pre_overall_congestion_proxy"); post_local = metrics.get("post_local_congestion")
        if not metrics or post_local is None: self.logger.debug("Missing metrics for congestion_improvement score."); return None
        if pre_overall_proxy == "HIGH":
            if post_local == "MEDIUM": score += 0.5
            elif post_local == "LOW": score += 1.0
        # ... (rest of scoring logic)
        return max(-1.0, min(1.0, score))


    def _score_green_wave_efficiency(self, metrics: Dict[str, Any]) -> Optional[float]:
        # ... (implementation from previous step) ...
        if not metrics: return None; post_travel_time = metrics.get("post_travel_time"); score = 0.0
        if post_travel_time is not None: score = 0.8 if post_travel_time < 120 else (0.4 if post_travel_time < 180 else -0.5)
        return max(-1.0, min(1.0, score))


    def _score_incident_clearance_speed(self, metrics: Dict[str, Any]) -> Optional[float]:
        # ... (implementation from previous step) ...
        if not metrics: return None; clearance_time_sec = metrics.get("post_clearance_time"); score = 0.0
        if clearance_time_sec is not None: score += 0.5 if clearance_time_sec < (15*60) else (0.2 if clearance_time_sec < (30*60) else -0.5)
        return max(-1.0, min(1.0, score))


    def _score_closure_effectiveness(self, metrics: Dict[str, Any]) -> Optional[float]:
        # ... (implementation from previous step) ...
        if not metrics: return None; flow_reduction = metrics.get("post_flow_reduction_percentage");
        if flow_reduction is not None: return 0.9 if flow_reduction > 75 else (0.5 if flow_reduction > 50 else -0.5)
        return 0.0

    def _calculate_effectiveness_score(self, log_entry_data: Dict[str, Any]) -> Tuple[Optional[float], Optional[Dict[str, Any]]]:
        # ... (implementation from previous step, assumed correct) ...
        action_type = log_entry_data.get("action_type"); config = self.action_effectiveness_config.get(action_type)
        if not config: self.logger.warning(f"No effectiveness config for {action_type}"); return None, None
        metrics_for_scoring: Dict[str, Any] = {}
        for kpi_spec in config.get("relevant_kpis", []):
            source = log_entry_data.get("pre_action_context_kpis") if kpi_spec["source"] == "pre" else log_entry_data.get("post_action_kpis")
            value = self._extract_kpi_value(source, kpi_spec["key_path"])
            if value is not None: metrics_for_scoring[kpi_spec["as"]] = value
        if not metrics_for_scoring: return 0.0, metrics_for_scoring # Neutral if no relevant KPIs found
        logic_type = config.get("scoring_logic_type"); score: Optional[float] = None
        if logic_type == "congestion_improvement": score = self._score_congestion_improvement(metrics_for_scoring)
        elif logic_type == "green_wave_efficiency": score = self._score_green_wave_efficiency(metrics_for_scoring)
        # ... (other scoring logic types) ...
        else: self.logger.warning(f"Unknown scoring_logic_type: {logic_type}"); return None, metrics_for_scoring
        return score, metrics_for_scoring


    async def run_decision_cycle(self, sample_user_id: str = "user_agent_test_123"):
        processed_signals_for_incident: Set[str] = set(); processed_signals_for_coordination: Set[str] = set()
        now_utc = datetime.utcnow(); current_time_obj = now_utc.time()
        self.logger.info(f"--- Starting AgentCore cycle for {sample_user_id} at {now_utc.isoformat()} ---")

        # --- Process Pending KPI Collections ---
        processed_pending_indices: List[int] = []
        if self.pending_kpi_collection: self.logger.info(f"Checking {len(self.pending_kpi_collection)} pending KPI tasks.")
        for i, pending_item in enumerate(self.pending_kpi_collection):
            if now_utc >= pending_item['query_after_timestamp']:
                # ... (KPI collection logic from previous step) ...
                post_kpis = None # Placeholder for actual KPI fetching
                try: # Simplified KPI fetching call for brevity
                    service_method_name = pending_item['kpi_query_details']['service_method_name']
                    service_call_args = {**pending_item['kpi_query_details']['method_specific_args'], 'metrics_to_collect': pending_item['metrics_to_collect'], 'evaluation_window_minutes': pending_item['evaluation_window_minutes'], 'action_type': pending_item['action_type'], 'action_timestamp': pending_item['action_timestamp']}
                    if hasattr(self.analytics_service, service_method_name):
                        post_kpis = await getattr(self.analytics_service, service_method_name)(**service_call_args)
                except Exception as e: self.logger.error(f"Error collecting KPIs for {pending_item['action_id']}: {e}")

                log_data_for_scoring = {**pending_item, "post_action_kpis": post_kpis}
                score, metrics_used = self._calculate_effectiveness_score(log_data_for_scoring)

                performance_log_entry = ActionPerformanceLog(
                    action_id=pending_item['action_id'], action_timestamp=pending_item['action_timestamp'],
                    action_type=pending_item['action_type'], target_ids=pending_item['target_ids'],
                    action_parameters=pending_item['action_parameters'], pre_action_context_kpis=pending_item['pre_action_context_kpis'],
                    post_action_kpis=post_kpis, kpi_collection_timestamp=now_utc,
                    effectiveness_score=score, effectiveness_metrics_used=metrics_used
                )
                self.action_performance_logs.append(performance_log_entry)
                self.logger.info(f"Added ActionPerformanceLog for {pending_item['action_id']}. Score: {score:.2f} if score is not None else 'N/A'}.")

                # Update effectiveness memory
                if score is not None and performance_log_entry.target_ids:
                    primary_target_id = performance_log_entry.target_ids[0]
                    action_signature_key = f"{performance_log_entry.action_type}:{primary_target_id}"
                    if action_signature_key not in self.action_effectiveness_memory:
                        self.action_effectiveness_memory[action_signature_key] = []
                    score_list = self.action_effectiveness_memory[action_signature_key]
                    score_list.append(score)
                    if len(score_list) > self.MAX_SCORES_PER_ACTION_SIGNATURE:
                        self.action_effectiveness_memory[action_signature_key] = score_list[-self.MAX_SCORES_PER_ACTION_SIGNATURE:]
                    self.logger.info(f"Updated effectiveness memory for '{action_signature_key}'. Score: {score:.2f}. Recent: [{', '.join(f'{s:.2f}' for s in self.action_effectiveness_memory[action_signature_key])}]")

                processed_pending_indices.append(i)
        if processed_pending_indices:
            for index in sorted(processed_pending_indices, reverse=True): self.pending_kpi_collection.pop(index)
            self.logger.info(f"Removed {len(processed_pending_indices)} processed KPI tasks.")

        # ... (rest of run_decision_cycle logic: cooldowns, KPI fetching, incident, green wave, general congestion, etc.) ...
        # Placeholder for actual action-taking logic that would populate pending_kpi_collection
        # For example, if a green wave was executed:
        # if wave_executed_successfully:
        #    # ... (logic to add to self.pending_kpi_collection as in previous subtask)

        self.logger.info(f"--- AgentCore cycle completed for {sample_user_id} at {datetime.utcnow().isoformat()} ---")


@patch('app.core.agent_core.datetime')
async def main_example_run_with_mock_time(mock_datetime_obj, specific_time_utc_str: str, sample_user_id: str, agent_core: AgentCore, analytics_service_mock: Any, kpi_overrides: Dict[str, Any]):
    # ... (helper from previous step) ...
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
    logger.info("--- Setting up AgentCore example (Effectiveness Memory) ---")

    class MockAnalyticsService: # ... (Mock service from previous step, with KPI methods) ...
        _get_kpi_call_counters = {}
        async def get_critical_alert_summary(self) -> Dict[str, Any]: return {"active_alerts": []}
        def get_current_system_kpis_summary(self) -> Dict[str, Any]: return {}
        async def broadcast_operational_alert(self,t,m,s,a):pass; async def send_user_specific_alert(self,u,n):pass; async def predict_incident_likelihood(self,l,t): return {}
        async def _mock_kpi_method(self, method_name: str, specific_args: Dict[str, Any], metrics_to_collect: List[str]) -> Dict[str, Any]:
            self._get_kpi_call_counters[method_name] = self._get_kpi_call_counters.get(method_name, 0) + 1; call_count = self._get_kpi_call_counters[method_name]
            kpis = {"call_count": call_count, **specific_args}
            if "corridor_avg_travel_time_seconds" in metrics_to_collect: kpis["corridor_avg_travel_time_seconds"] = 160 - (call_count * 10) # Make it vary
            if "stops_per_vehicle_in_corridor" in metrics_to_collect: kpis["stops_per_vehicle_in_corridor"] = 3 - call_count if call_count < 3 else 1
            return kpis
        async def get_signal_post_action_kpis(self, **kwargs) -> Dict[str, Any]: return await self._mock_kpi_method("get_signal_post_action_kpis", kwargs, kwargs.get("metrics_to_collect",[]))
        async def get_corridor_post_action_kpis(self, **kwargs) -> Dict[str, Any]: return await self._mock_kpi_method("get_corridor_post_action_kpis", kwargs, kwargs.get("metrics_to_collect",[]))
        async def get_incident_response_post_action_kpis(self, **kwargs) -> Dict[str, Any]: return await self._mock_kpi_method("get_incident_response_post_action_kpis", kwargs, kwargs.get("metrics_to_collect",[]))


    class MockTrafficSignalService: # ... (Mock service from previous step) ...
        def __init__(self): self._signals: Dict[str, SignalState] = {}; self._initialize_mock_signals()
        def _initialize_mock_signals(self): self._signals.clear(); sids = ["TS001","TS002","TS004"];
            for i, sid in enumerate(sids): self._signals[sid] = SignalState(signal_id=sid, location=LocationModel(latitude=1.0+i*0.001, longitude=1.0+i*0.001, name=sid), current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=datetime.utcnow(), main_flow_direction="NS")
        async def get_all_signal_states(self) -> List[SignalState]: return list(self._signals.values())
        async def set_signal_phase(self, sid,p,d) -> SignalControlCommandResponse:
            if sid in self._signals: self._signals[sid].current_phase = p; return SignalControlCommandResponse(signal_id=sid, status=SignalControlStatusEnum.ACCEPTED)
            return SignalControlCommandResponse(signal_id=sid, status=SignalControlStatusEnum.FAILED)

    analytics_mock = MockAnalyticsService()
    agent_core = AgentCore(MagicMock(spec=PredictionScheduler), MagicMock(spec=PersonalizedRoutingService), analytics_mock, MockTrafficSignalService())

    # Simulate multiple Green Wave actions for the same corridor to test memory appending and pruning
    corridor_id_to_test = "main_st_ns_wave"
    kpi_config_for_wave = ACTION_KPI_CONFIG["GREEN_WAVE_ACTIVATION"]
    action_delay_seconds = kpi_config_for_wave["delay_seconds"]

    # Set MAX_SCORES_PER_ACTION_SIGNATURE to a small number for easier testing of pruning
    agent_core.MAX_SCORES_PER_ACTION_SIGNATURE = 3
    logger.info(f"Set MAX_SCORES_PER_ACTION_SIGNATURE to {agent_core.MAX_SCORES_PER_ACTION_SIGNATURE} for this test run.")

    num_test_actions = 5 # More than MAX_SCORES_PER_ACTION_SIGNATURE

    for i in range(num_test_actions):
        action_time_str = f"2023-01-{i+1:02d}T08:00:00Z" # Each action on a different day for unique action_timestamp
        kpi_collection_time_str = (datetime.fromisoformat(action_time_str.replace("Z","+00:00")) + timedelta(seconds=action_delay_seconds + 30)).isoformat().replace("+00:00","Z")

        logger.info(f"--- MainExample Memory Test: Cycle {i+1}A (Trigger Action) ---")
        # Manually add a pending item as if a green wave was triggered
        action_id = uuid4()
        agent_core.pending_kpi_collection.append({
            'action_id': action_id, 'action_type': "GREEN_WAVE_ACTIVATION", 'target_ids': [corridor_id_to_test] + GREEN_WAVE_CORRIDOR_CONFIGS[corridor_id_to_test]["signals_in_order"],
            'action_timestamp': datetime.fromisoformat(action_time_str.replace("Z","+00:00")),
            'action_parameters': {"wave_green_time_seconds": GREEN_WAVE_CORRIDOR_CONFIGS[corridor_id_to_test]["wave_green_time_seconds"]},
            'pre_action_context_kpis': {"trigger_kpi_value": "HIGH"},
            'query_after_timestamp': datetime.fromisoformat(action_time_str.replace("Z","+00:00")) + timedelta(seconds=action_delay_seconds),
            'metrics_to_collect': kpi_config_for_wave["metrics"],
            'evaluation_window_minutes': kpi_config_for_wave["eval_window_minutes"],
            'kpi_query_details': {'service_method_name': kpi_config_for_wave["service_method"], 'method_specific_args': {'corridor_id': corridor_id_to_test}}
        })
        logger.info(f"Manually added pending KPI for action {action_id} for corridor {corridor_id_to_test}")

        logger.info(f"--- MainExample Memory Test: Cycle {i+1}B (Process KPI & Update Memory) ---")
        await main_example_run_with_mock_time(kpi_collection_time_str, f"user_mem_test_collect_{i+1}", agent_core, analytics_mock, {})

    logger.info("--- Final Action Effectiveness Memory ---")
    if agent_core.action_effectiveness_memory:
        logger.info(json.dumps(agent_core.action_effectiveness_memory, indent=2, default=str))
        expected_key = f"GREEN_WAVE_ACTIVATION:{corridor_id_to_test}"
        assert expected_key in agent_core.action_effectiveness_memory
        assert len(agent_core.action_effectiveness_memory[expected_key]) == agent_core.MAX_SCORES_PER_ACTION_SIGNATURE
    else: logger.info("Action effectiveness memory is empty.")
    logger.info(f"Total performance logs created: {len(agent_core.action_performance_logs)}")
    assert len(agent_core.action_performance_logs) == num_test_actions

    logger.info("--- AgentCore main_example for Effectiveness Memory completed ---")

if __name__ == "__main__":
    # asyncio.run(main_example())
    logger.info("AgentCore module defined. Example main_example() function available for testing.")

[end of backend/app/core/agent_core.py]
