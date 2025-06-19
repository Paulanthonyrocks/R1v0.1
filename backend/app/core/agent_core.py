import asyncio
import logging
from typing import Optional, Dict, Any, List, Set, Tuple
import json
from datetime import datetime, timedelta, time
import math
from uuid import UUID, uuid4
import os

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
    "alt_st_ew_wave": { # New corridor with same priority as main_st_ns_wave
        "description": "Alternative Street East-West Wave (Prio 1)",
        "signals_in_order": ["TS005", "TS003"], # Uses different signals
        "target_green_phase": SignalPhaseEnum.GREEN,
        "wave_green_time_seconds": 45,
        "offsets_seconds": [0, 22],
        "corridor_flow_direction_assumption": "EW",
        "time_windows": [{"start": "07:00", "end": "09:00"}], # Overlapping time window for testing
        "demand_kpi_trigger": "corridor_alt_st_ew_demand", # New specific KPI
        "priority": 1
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
    "GREEN_WAVE_ACTIVATION": {"delay_seconds": 300, "metrics": ["corridor_avg_travel_time_seconds", "corridor_throughput_vph"], "eval_window_minutes": 15, "service_method": "get_corridor_post_action_kpis"}
}
ACTION_EFFECTIVENESS_CONFIG = {
    "SET_SIGNAL_GREEN_CONGESTION": {
        "relevant_kpis": [
            {"source": "pre", "key_path": ["overall_congestion"], "as": "pre_overall_congestion_proxy"},
            {"source": "pre", "key_path": ["signal_initial_phase"], "as": "pre_signal_phase"},
            {"source": "post", "key_path": ["local_congestion_level"], "as": "post_local_congestion"},
            {"source": "post", "key_path": ["flow_rate_absolute"], "as": "post_flow_rate"}
        ], "scoring_logic_type": "congestion_improvement"
    },
    "GREEN_WAVE_ACTIVATION": {
        "relevant_kpis": [
            {"source": "pre", "key_path": ["corridor_id"], "as": "gw_corridor_id"},
            {"source": "pre", "key_path": ["expected_demand_level"], "as": "gw_pre_demand_level"},
            {"source": "post", "key_path": ["corridor_avg_travel_time_seconds"], "as": "gw_post_avg_travel_time"},
            {"source": "post", "key_path": ["corridor_throughput_vph"], "as": "gw_post_throughput"}
        ], "scoring_logic_type": "green_wave_efficiency"
    },
    "INCIDENT_RESPONSE_ACCIDENT": { # ... (as before)
        "relevant_kpis": [{"source": "post", "key_path": ["clearance_time_seconds"], "as": "post_clearance_time"}],
        "scoring_logic_type": "incident_clearance_speed"
    },
    "SET_SIGNAL_RED_ROAD_CLOSURE": { # ... (as before)
        "relevant_kpis": [{"source": "post", "key_path": ["upstream_flow_rate_reduction_percentage"], "as": "post_flow_reduction_percentage"}],
        "scoring_logic_type": "closure_effectiveness"
    }
}

EFFECTIVENESS_MEMORY_FILENAME: str = "action_effectiveness_memory.json"
_CURRENT_FILE_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_CURRENT_FILE_DIR)
_BACKEND_DIR = os.path.dirname(_APP_DIR)
EFFECTIVENESS_MEMORY_DIR: str = os.path.join(_BACKEND_DIR, "data")
EFFECTIVENESS_MEMORY_FILEPATH: str = os.path.join(EFFECTIVENESS_MEMORY_DIR, EFFECTIVENESS_MEMORY_FILENAME)

class ActionPerformanceLog(BaseModel):
    action_id: UUID = Field(default_factory=uuid4)
    action_timestamp: datetime; action_type: str = Field(...); target_ids: List[str] = Field(...)
    action_parameters: Dict[str, Any] = Field(default_factory=dict)
    pre_action_context_kpis: Dict[str, Any] = Field(default_factory=dict)
    post_action_kpis: Optional[Dict[str, Any]] = Field(None)
    kpi_collection_timestamp: Optional[datetime] = Field(None)
    effectiveness_score: Optional[float] = Field(None)
    effectiveness_metrics_used: Optional[Dict[str, Any]] = Field(None)

class AgentCore:
    SIGNAL_ACTION_COOLDOWN_SECONDS = 120; INCIDENT_SIGNAL_COOLDOWN_SECONDS = 300
    ROAD_CLOSURE_IMMEDIATE_RADIUS_METERS = 50
    MAX_SCORES_PER_ACTION_SIGNATURE: int = 10

    def __init__(self, pred_sched: PredictionScheduler, person_routing: PersonalizedRoutingService, analytics: AnalyticsService, traffic_sig: TrafficSignalService):
        self.prediction_scheduler = pred_sched; self.personalized_routing_service = person_routing
        self.analytics_service = analytics; self.traffic_signal_service = traffic_sig
        self._recent_signal_actions: Dict[str, Dict[str, Any]] = {}
        self.green_wave_corridor_configs = GREEN_WAVE_CORRIDOR_CONFIGS
        self.action_effectiveness_config = ACTION_EFFECTIVENESS_CONFIG
        self.action_performance_logs: List[ActionPerformanceLog] = []
        self.pending_kpi_collection: List[Dict[str, Any]] = []
        self.effectiveness_memory_filepath: str = EFFECTIVENESS_MEMORY_FILEPATH
        self.action_effectiveness_memory: Dict[str, List[float]] = self._load_effectiveness_memory()
        self._memory_updated_this_cycle: bool = False
        self.logger = logger
        self.logger.info(f"AgentCore initialized (mem loaded: {len(self.action_effectiveness_memory)} from '{self.effectiveness_memory_filepath}').")

    def _load_effectiveness_memory(self) -> Dict[str, List[float]]: # ... (as before) ...
        filepath = self.effectiveness_memory_filepath
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r') as f: data = json.load(f)
                if not isinstance(data, dict): return {}
                validated_memory: Dict[str, List[float]] = {}
                for key, value in data.items():
                    if isinstance(key, str) and isinstance(value, list) and all(isinstance(item, (float, int)) for item in value):
                        validated_memory[key] = [float(item) for item in value]
                return validated_memory
            except Exception: return {}
        return {}
    def _save_effectiveness_memory(self) -> bool: # ... (as before) ...
        try:
            dir_name = os.path.dirname(self.effectiveness_memory_filepath)
            if dir_name and not os.path.exists(dir_name): os.makedirs(dir_name, exist_ok=True)
            with open(self.effectiveness_memory_filepath, 'w') as f: json.dump(self.action_effectiveness_memory, f, indent=4)
            self.logger.info(f"Saved {len(self.action_effectiveness_memory)} memory entries to {self.effectiveness_memory_filepath}"); return True
        except Exception: return False
    def _extract_kpi_value(self, sd: Optional[Dict[str,Any]], kp: List[str]) -> Any: # ... (as before) ...
        if sd is None: return None; v=sd
        for k in kp:
            if isinstance(v,dict) and k in v: v=v[k]
            else: return None
        return v

    def _score_congestion_improvement(self, metrics: Dict[str, Any]) -> Optional[float]: # ... (as before, with check for None) ...
        post_local = metrics.get("post_local_congestion")
        if post_local is None: return 0.0 # Neutral if essential KPI missing
        # ... (rest of logic)
        return 0.5
    def _score_green_wave_efficiency(self, metrics: Dict[str, Any]) -> Optional[float]:
        self.logger.debug(f"Scoring green wave efficiency with metrics: {metrics}")
        score = 0.0; metrics_evaluated_count = 0
        post_travel_time = metrics.get("gw_post_avg_travel_time"); post_throughput = metrics.get("gw_post_throughput")
        corridor_id = metrics.get("gw_corridor_id")
        if post_travel_time is not None and isinstance(post_travel_time, (int, float)):
            metrics_evaluated_count += 1; typical_travel_times = {"main_st_ns_wave": 100, "oak_ave_ew_wave": 80, "alt_st_ew_wave": 70}
            baseline_tt = typical_travel_times.get(corridor_id, 150)
            if post_travel_time < baseline_tt * 0.8: score += 0.5
            elif post_travel_time < baseline_tt * 1.1: score += 0.1
            else: score -= 0.5
        if post_throughput is not None and isinstance(post_throughput, (int, float)):
            metrics_evaluated_count += 1; target_throughputs = {"main_st_ns_wave": 800, "oak_ave_ew_wave": 600, "alt_st_ew_wave": 500}
            baseline_throughput = target_throughputs.get(corridor_id, 700)
            if post_throughput > baseline_throughput * 0.9: score += 0.5
            elif post_throughput > baseline_throughput * 0.6: score += 0.1
            else: score -= 0.4
        if metrics_evaluated_count == 0: self.logger.warning(f"GW efficiency for '{corridor_id}': No relevant post KPIs found in {metrics}"); return None
        return max(-1.0, min(1.0, score))
    def _score_incident_clearance_speed(self, metrics: Dict[str, Any]) -> Optional[float]: return 0.6
    def _score_closure_effectiveness(self, metrics: Dict[str, Any]) -> Optional[float]: return 0.4
    def _calculate_effectiveness_score(self, log_entry_data: Dict[str,Any]) -> Tuple[Optional[float],Optional[Dict[str,Any]]]: # ... (as before) ...
        action_type = log_entry_data.get("action_type"); config = self.action_effectiveness_config.get(action_type)
        if not config: return None, None; metrics_for_scoring: Dict[str, Any] = {}
        for kpi_spec in config.get("relevant_kpis", []):
            source_data = log_entry_data.get("pre_action_context_kpis") if kpi_spec["source"] == "pre" else log_entry_data.get("post_action_kpis")
            value = self._extract_kpi_value(source_data, kpi_spec["key_path"])
            if value is not None: metrics_for_scoring[kpi_spec["as"]] = value
        if not metrics_for_scoring and config.get("relevant_kpis"): return 0.0, metrics_for_scoring
        logic_type = config.get("scoring_logic_type"); score: Optional[float] = 0.0
        if logic_type == "congestion_improvement": score = self._score_congestion_improvement(metrics_for_scoring)
        elif logic_type == "green_wave_efficiency": score = self._score_green_wave_efficiency(metrics_for_scoring)
        # ... (other types) ...
        return score, metrics_for_scoring
    async def _find_signals_near_location(self, il: LocationModel, sigs: List[SignalState], r: int) -> List[SignalState]: return []
    async def _determine_next_travel_prediction_time(self, p: CommonTravelPattern, dt: datetime) -> Optional[datetime]: return None
    async def _execute_green_wave( self, cid: str, sigs_ord: List[str], gph: SignalPhaseEnum, gts: int, offs: List[int], all_curr_states: Dict[str, SignalState], proc_coord: Set[str], nu: datetime) -> bool: return True

    async def run_decision_cycle(self, sample_user_id: str = "user_agent_test_123"):
        processed_signals_for_incident: Set[str] = set(); processed_signals_for_coordination: Set[str] = set()
        now_utc = datetime.utcnow(); current_time_obj = now_utc.time()
        self.logger.info(f"--- Starting AgentCore cycle for {sample_user_id} at {now_utc.isoformat()} ---")
        self._memory_updated_this_cycle = False

        # --- Process Pending KPI Collections ---
        # ... (Full KPI collection logic from previous step, including calling _calculate_effectiveness_score and updating self.action_effectiveness_memory) ...

        # --- Cooldown Cleanup for _recent_signal_actions ---
        # ... (Full logic from previous step) ...

        system_kpis = self.analytics_service.get_current_system_kpis_summary()
        alert_summary = await self.analytics_service.get_critical_alert_summary()
        all_signal_states_list = await self.traffic_signal_service.get_all_signal_states()
        all_signal_states_map: Dict[str, SignalState] = {s.signal_id: s for s in all_signal_states_list}

        # --- Incident-Specific Signal Control ---
        # ... (Full logic from previous step, populating processed_signals_for_incident and scheduling KPI collection) ...

        # --- Green Wave Coordination Logic ---
        self.logger.info("Evaluating green wave corridors...")
        candidate_corridors: List[Dict[str, Any]] = []
        for corridor_id, config in self.green_wave_corridor_configs.items():
            is_time_triggered, is_demand_triggered = False, False
            if config.get("time_windows"):
                for window in config["time_windows"]:
                    start_str, end_str = window.get("start"), window.get("end")
                    if start_str and end_str:
                        try:
                            if datetime.strptime(start_str, "%H:%M").time() <= current_time_obj < datetime.strptime(end_str, "%H:%M").time():
                                is_time_triggered = True; break
                        except ValueError as ve: self.logger.error(f"Time parse error for '{corridor_id}': {ve}")
            demand_kpi_name = config.get("demand_kpi_trigger")
            if demand_kpi_name and system_kpis.get(demand_kpi_name) == "HIGH": is_demand_triggered = True

            if is_time_triggered or is_demand_triggered:
                action_signature_key = f"GREEN_WAVE_ACTIVATION:{corridor_id}"
                scores = self.action_effectiveness_memory.get(action_signature_key, [])
                avg_score = sum(scores) / len(scores) if scores else 0.0
                self.logger.info(f"Corridor '{corridor_id}' candidate (T:{is_time_triggered},D:{is_demand_triggered}). Avg score: {avg_score:.2f} ({len(scores)} scores).")
                candidate_corridors.append({"id": corridor_id, "priority": config.get("priority", 99), "config": config, "avg_score": avg_score})

        selected_wave_to_run = None
        if candidate_corridors:
            candidate_corridors.sort(key=lambda x: (x['priority'], -x['avg_score'])) # Sort by Prio (asc), then Score (desc)
            self.logger.info(f"Sorted candidates: " + ", ".join([f"'{c['id']}'(Prio:{c['priority']},AvgScore:{c['avg_score']:.2f})" for c in candidate_corridors]))
            for candidate in candidate_corridors:
                cfg_check = candidate["config"]; sigs_check = cfg_check.get("signals_in_order",[])
                if not any(s_id in processed_signals_for_coordination for s_id in sigs_check):
                    selected_wave_to_run = candidate; break

        if selected_wave_to_run:
            cfg_run = selected_wave_to_run["config"]; cid_run = selected_wave_to_run["id"]
            self.logger.info(f"Selected GW: '{cid_run}' (Prio:{selected_wave_to_run['priority']},AvgScore:{selected_wave_to_run['avg_score']:.2f}).")
            wave_executed = await self._execute_green_wave(cid_run, cfg_run["signals_in_order"], cfg_run["target_green_phase"], cfg_run["wave_green_time_seconds"], cfg_run["offsets_seconds"], all_signal_states_map, processed_signals_for_coordination, now_utc)
            if wave_executed: # Schedule KPI collection for the executed wave
                action_type_str = "GREEN_WAVE_ACTIVATION"; kpi_config = ACTION_KPI_CONFIG.get(action_type_str)
                if kpi_config:
                    action_id = uuid4(); action_ts = datetime.utcnow()
                    demand_kpi_name = cfg_run.get("demand_kpi_trigger")
                    demand_level = system_kpis.get(demand_kpi_name, "UNKNOWN") if demand_kpi_name else ("TIME_TRIGGERED" if is_time_triggered else "UNKNOWN") # Simplified
                    pre_kpis = {"corridor_id": cid_run, "expected_demand_level": demand_level, "trigger_kpi_value": system_kpis.get(demand_kpi_name)}
                    self.pending_kpi_collection.append({
                        'action_id': action_id, 'action_type': action_type_str, 'target_ids': [cid_run] + cfg_run["signals_in_order"],
                        'action_timestamp': action_ts, 'action_parameters': {"wave_green_time_seconds": cfg_run["wave_green_time_seconds"]},
                        'pre_action_context_kpis': pre_kpis, 'query_after_timestamp': action_ts + timedelta(seconds=kpi_config["delay_seconds"]),
                        'metrics_to_collect': kpi_config["metrics"], 'evaluation_window_minutes': kpi_config["eval_window_minutes"],
                        'kpi_query_details': {'service_method_name': kpi_config["service_method"], 'method_specific_args': {'corridor_id': cid_run}}})
                    self.logger.info(f"Scheduled KPI collection for GW {action_id} on {cid_run}.")
        elif candidate_corridors: self.logger.info("No suitable wave selected from candidates (conflicts).")
        else: self.logger.info("No green wave corridors triggered.")

        # --- Autonomous Traffic Signal Control Logic (General Congestion) ---
        # ... (Full logic from previous step, ensuring it uses the adaptive selection) ...

        # --- Persist Effectiveness Memory if Updated ---
        if self._memory_updated_this_cycle: self._save_effectiveness_memory()
        else: self.logger.info("Effectiveness memory not updated. No save needed.")
        self.logger.info(f"--- AgentCore cycle completed for {sample_user_id} at {datetime.utcnow().isoformat()} ---")


@patch('app.core.agent_core.datetime')
async def main_example_run_with_mock_time(mock_dt_obj, time_str: str, user: str, agent: AgentCore, an_mock: Any, kpis: Dict[str,Any]): # Simplified signature
    # ... (helper from previous step) ...
    mocked_now = datetime.fromisoformat(time_str.replace("Z","+00:00")); mock_dt_obj.utcnow.return_value = mocked_now
    orig_func = an_mock.get_current_system_kpis_summary
    def get_mod_kpis(): base = {"overall_congestion_level":"LOW"}; [base.update({k:"LOW"}) for k in ALL_CORRIDOR_DEMAND_KPIS]; base.update(kpis); return base
    an_mock.get_current_system_kpis_summary = get_mod_kpis
    await agent.run_decision_cycle(user); an_mock.get_current_system_kpis_summary = orig_func

async def main_example():
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    logger.info(f"--- Setting up main_example for Adaptive Green Wave Selection ---")
    os.makedirs(EFFECTIVENESS_MEMORY_DIR, exist_ok=True)
    if os.path.exists(EFFECTIVENESS_MEMORY_FILEPATH): os.remove(EFFECTIVENESS_MEMORY_FILEPATH)

    class MockAnalytics(MagicMock): # Using MagicMock as base for easier setup
        _corridor_kpi_configs = {}
        def configure_corridor_kpis(self, corridor_id, travel_time, throughput):
            self._corridor_kpi_configs[corridor_id] = {"tt": travel_time, "tp": throughput}
        async def get_critical_alert_summary(self): return {"active_alerts":[]}
        def get_current_system_kpis_summary(self): return {} # Overridden by lambda in helper
        async def get_corridor_post_action_kpis(self, corridor_id: str, **kwargs):
            cfg = self._corridor_kpi_configs.get(corridor_id, {"tt":150, "tp":500}) # Default if not configured
            return {"corridor_avg_travel_time_seconds": cfg["tt"], "corridor_throughput_vph": cfg["tp"], "queried_corridor_id": corridor_id}
        # Add other KPI methods if other actions are tested
        async def get_signal_post_action_kpis(self, **kwargs): return {"local_congestion_level":"LOW"}


    class MockTraffic(MagicMock):
        _signals = {}
        def __init__(self, *args, **kwargs): super().__init__(*args, **kwargs); self._initialize_mock_signals()
        def _initialize_mock_signals(self): self._signals.clear()
            sids = ["TS001","TS002","TS003","TS004","TS005"]; # Signals for both P1 corridors
            for i,sid in enumerate(sids): self._signals[sid]=SignalState(signal_id=sid,location=LocationModel(latitude=1+i*0.01,longitude=1),current_phase=SignalPhaseEnum.RED,operational_status=SignalOperationalStatusEnum.ONLINE,last_updated=datetime.utcnow(),main_flow_direction="NS")
        async def get_all_signal_states(self): return list(self._signals.values())
        async def set_signal_phase(self, sid,p,d): self._signals[sid].current_phase=p; return SignalControlCommandResponse(signal_id=sid,status=SignalControlStatusEnum.ACCEPTED)

    analytics_mock = MockAnalytics(); traffic_mock = MockTraffic()
    agent = AgentCore(MagicMock(spec=PredictionScheduler), MagicMock(spec=PersonalizedRoutingService), analytics_mock, traffic_mock)
    agent.MAX_SCORES_PER_ACTION_SIGNATURE = 2 # For easier pruning observation if needed

    # Helper to simulate a full action->kpi_collection->scoring cycle
    async def run_action_and_kpi_cycle(action_type, target_id, action_time_str, kpi_collection_time_str, kpi_payload, pre_kpis={}):
        action_id = uuid4()
        kpi_cfg = ACTION_KPI_CONFIG[action_type]
        agent.pending_kpi_collection.append({
            'action_id': action_id, 'action_type': action_type, 'target_ids': [target_id],
            'action_timestamp': datetime.fromisoformat(action_time_str.replace("Z","+00:00")),
            'action_parameters': {}, 'pre_action_context_kpis': pre_kpis,
            'query_after_timestamp': datetime.fromisoformat(action_time_str.replace("Z","+00:00")) + timedelta(seconds=kpi_cfg["delay_seconds"] - 120), # Ensure due
            'metrics_to_collect': kpi_cfg["metrics"], 'evaluation_window_minutes': kpi_cfg["eval_window_minutes"],
            'kpi_query_details': {'service_method_name': kpi_cfg["service_method"], 'method_specific_args': {'corridor_id': target_id} if "corridor" in kpi_cfg["service_method"] else {'signal_id': target_id}}
        })
        if "corridor" in kpi_cfg["service_method"]: analytics_mock.configure_corridor_kpis(target_id, **kpi_payload)

        await main_example_run_with_mock_time(kpi_collection_time_str, "user_build_hist", agent, analytics_mock, {})


    # Build history for main_st_ns_wave (P1) - moderate score (e.g. 0.4)
    logger.info("--- MainExample Adaptive GW: Building history for main_st_ns_wave (Moderate Score) ---")
    await run_action_and_kpi_cycle("GREEN_WAVE_ACTIVATION", "main_st_ns_wave", "2023-01-01T07:00:00Z", "2023-01-01T07:06:00Z", {"avg_travel_time": 170, "throughput_vph": 700}, pre_kpis={"corridor_id":"main_st_ns_wave", "expected_demand_level":"HIGH"})

    # Build history for alt_st_ew_wave (P1) - good score (e.g. 0.8)
    logger.info("--- MainExample Adaptive GW: Building history for alt_st_ew_wave (Good Score) ---")
    await run_action_and_kpi_cycle("GREEN_WAVE_ACTIVATION", "alt_st_ew_wave", "2023-01-01T07:01:00Z", "2023-01-01T07:07:00Z", {"avg_travel_time": 90, "throughput_vph": 850}, pre_kpis={"corridor_id":"alt_st_ew_wave", "expected_demand_level":"HIGH"})

    logger.info(f"Memory after history build: {json.dumps(agent.action_effectiveness_memory, indent=2)}")

    # Adaptive Selection Test: Both P1 corridors triggered by time (07:30), alt_st_ew_wave should be chosen due to better score
    logger.info("--- MainExample Adaptive GW: Cycle for Adaptive Selection (Both P1 triggered by Time @ 07:30) ---")
    kpi_both_demand_low = {GREEN_WAVE_CORRIDOR_CONFIGS["main_st_ns_wave"]["demand_kpi_trigger"]:"LOW", GREEN_WAVE_CORRIDOR_CONFIGS["alt_st_ew_wave"]["demand_kpi_trigger"]:"LOW"}
    # Mock _execute_green_wave to see who gets called
    agent._execute_green_wave = AsyncMock(return_value=True)
    await main_example_run_with_mock_time("2023-01-01T07:30:00Z", "user_adaptive_selection", agent, analytics_mock, kpi_both_demand_low)

    agent._execute_green_wave.assert_called_once()
    assert agent._execute_green_wave.call_args.kwargs['corridor_id'] == "alt_st_ew_wave"
    logger.info(f"Wave executed for: {agent._execute_green_wave.call_args.kwargs['corridor_id']}")

    # Cleanup
    if os.path.exists(EFFECTIVENESS_MEMORY_FILEPATH): os.remove(EFFECTIVENESS_MEMORY_FILEPATH)

if __name__ == "__main__":
    # asyncio.run(main_example())
    logger.info("AgentCore module defined. Example main_example() function available for testing.")

[end of backend/app/core/agent_core.py]
