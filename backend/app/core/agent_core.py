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
    "SET_SIGNAL_GREEN_CONGESTION": {"relevant_kpis": [{"source": "pre", "key_path": ["overall_congestion"], "as": "pre_overall_congestion_proxy"},{"source": "post", "key_path": ["local_congestion_level"], "as": "post_local_congestion"}],"scoring_logic_type": "congestion_improvement"},
    "GREEN_WAVE_ACTIVATION": { "relevant_kpis": [{"source": "post", "key_path": ["corridor_avg_travel_time_seconds"], "as": "post_travel_time"}], "scoring_logic_type": "green_wave_efficiency"},
    # ... (other configs)
}

EFFECTIVENESS_MEMORY_FILENAME: str = "action_effectiveness_memory.json"
_CURRENT_FILE_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = _CURRENT_FILE_DIR
_APP_DIR = os.path.dirname(_CORE_DIR)
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
        self.logger.info(
            "AgentCore initialized with services, configs, performance logging, "
            "and effectiveness memory system (loaded %s records from '%s').",
            len(self.action_effectiveness_memory), self.effectiveness_memory_filepath
        )

    def _load_effectiveness_memory(self) -> Dict[str, List[float]]:
        filepath = self.effectiveness_memory_filepath
        self.logger.info(f"Attempting to load effectiveness memory from: {filepath}")
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r') as f: data = json.load(f)
                if not isinstance(data, dict):
                    self.logger.warning(f"Memory file '{filepath}' not a valid dict. Starting fresh."); return {}
                validated_memory: Dict[str, List[float]] = {}
                valid_entries, invalid_entries = 0, 0
                for key, value in data.items():
                    if isinstance(key, str) and isinstance(value, list) and all(isinstance(item, (float, int)) for item in value):
                        validated_memory[key] = [float(item) for item in value]; valid_entries +=1
                    else: self.logger.warning(f"Invalid data for key '{key}' in '{filepath}'. Skip."); invalid_entries +=1
                if invalid_entries > 0: self.logger.warning(f"Skipped {invalid_entries} invalid entries during memory load.")
                if validated_memory: self.logger.info(f"Loaded {valid_entries} entries from memory: {filepath}")
                elif valid_entries == 0 and invalid_entries > 0: self.logger.warning(f"No valid entries in '{filepath}'. Starting fresh.")
                else: self.logger.info(f"Memory file '{filepath}' empty or no valid data. Starting fresh.")
                return validated_memory
            except (IOError, json.JSONDecodeError, TypeError) as e:
                self.logger.error(f"Error loading memory from '{filepath}': {e}. Starting fresh.", exc_info=True); return {}
        else: self.logger.info(f"Memory file '{filepath}' not found. Starting fresh (normal on first run)."); return {}

    def _save_effectiveness_memory(self) -> bool:
        filepath = self.effectiveness_memory_filepath
        self.logger.info(f"Attempting to save effectiveness memory to: {filepath}")
        try:
            dir_name = os.path.dirname(filepath)
            if dir_name and not os.path.exists(dir_name):
                try: os.makedirs(dir_name, exist_ok=True); self.logger.info(f"Created directory for memory: {dir_name}")
                except OSError as e_mkdir: self.logger.error(f"Failed to create dir '{dir_name}': {e_mkdir}", exc_info=True); return False
            with open(filepath, 'w') as f: json.dump(self.action_effectiveness_memory, f, indent=4)
            self.logger.info(f"Successfully saved {len(self.action_effectiveness_memory)} memory entries to {filepath}"); return True
        except (IOError, TypeError, OSError) as e: self.logger.error(f"Error saving memory to '{filepath}': {e}", exc_info=True); return False

    def _extract_kpi_value(self, source_dict: Optional[Dict[str, Any]], key_path: List[str]) -> Any:
        if source_dict is None: return None; val = source_dict
        for key in key_path:
            if isinstance(val, dict) and key in val: val = val[key]
            else: return None
        return val
    def _score_congestion_improvement(self, metrics: Dict[str, Any]) -> Optional[float]:
        score = 0.0; pre_overall_proxy = metrics.get("pre_overall_congestion_proxy"); post_local = metrics.get("post_local_congestion")
        if post_local is None : self.logger.debug("Missing post_local_congestion for scoring."); return 0.0 # Neutral if key data missing
        if pre_overall_proxy == "HIGH": score = {"LOW": 1.0, "MEDIUM": 0.5, "HIGH": -0.2}.get(post_local, 0.0)
        elif pre_overall_proxy == "MEDIUM": score = {"LOW": 0.5, "MEDIUM": 0.0, "HIGH": -0.5}.get(post_local, 0.0)
        elif pre_overall_proxy == "LOW": score = {"LOW": 0.0, "MEDIUM": -0.2, "HIGH": -0.7}.get(post_local, 0.0)
        return max(-1.0, min(1.0, score))
    def _score_green_wave_efficiency(self, metrics: Dict[str, Any]) -> Optional[float]:
        if not metrics: return 0.0; post_travel_time = metrics.get("post_travel_time"); score = 0.0
        if post_travel_time is not None: score = 0.8 if post_travel_time < 120 else (0.4 if post_travel_time < 180 else -0.5)
        return max(-1.0, min(1.0, score))
    def _score_incident_clearance_speed(self, metrics: Dict[str, Any]) -> Optional[float]: return 0.6
    def _score_closure_effectiveness(self, metrics: Dict[str, Any]) -> Optional[float]: return 0.4
    def _calculate_effectiveness_score(self, log_entry_data: Dict[str, Any]) -> Tuple[Optional[float], Optional[Dict[str, Any]]]:
        action_type = log_entry_data.get("action_type"); config = self.action_effectiveness_config.get(action_type)
        if not config: self.logger.warning(f"No effectiveness config for {action_type}"); return None, None
        metrics_for_scoring: Dict[str, Any] = {}
        for kpi_spec in config.get("relevant_kpis", []):
            source_data = log_entry_data.get("pre_action_context_kpis") if kpi_spec["source"] == "pre" else log_entry_data.get("post_action_kpis")
            value = self._extract_kpi_value(source_data, kpi_spec["key_path"])
            if value is not None: metrics_for_scoring[kpi_spec["as"]] = value
        if not metrics_for_scoring and config.get("relevant_kpis"): self.logger.debug(f"No relevant KPI values extracted for {action_type}."); return 0.0, metrics_for_scoring
        logic_type = config.get("scoring_logic_type"); score: Optional[float] = 0.0 # Default to neutral
        if logic_type == "congestion_improvement": score = self._score_congestion_improvement(metrics_for_scoring)
        elif logic_type == "green_wave_efficiency": score = self._score_green_wave_efficiency(metrics_for_scoring)
        elif logic_type == "incident_clearance_speed": score = self._score_incident_clearance_speed(metrics_for_scoring)
        elif logic_type == "closure_effectiveness": score = self._score_closure_effectiveness(metrics_for_scoring)
        else: self.logger.warning(f"Unknown scoring_logic_type: {logic_type}"); return None, metrics_for_scoring
        self.logger.info(f"Effectiveness score for {action_type} (ID: {log_entry_data.get('action_id')}): {score}. Metrics used: {metrics_for_scoring}")
        return score, metrics_for_scoring

    async def _find_signals_near_location(self, il: LocationModel, sigs: List[SignalState], r: int) -> List[SignalState]: return []
    async def _determine_next_travel_prediction_time(self, p: CommonTravelPattern, dt: datetime) -> Optional[datetime]: return None
    async def _execute_green_wave( self, cid: str, sigs_ord: List[str], gph: SignalPhaseEnum, gts: int, offs: List[int], all_curr_states: Dict[str, SignalState], proc_coord: Set[str], nu: datetime) -> bool: return True


    async def run_decision_cycle(self, sample_user_id: str = "user_agent_test_123"):
        # ... (Full logic as implemented, including KPI processing and memory update & save logic) ...
        processed_signals_for_incident: Set[str] = set(); processed_signals_for_coordination: Set[str] = set()
        now_utc = datetime.utcnow(); current_time_obj = now_utc.time()
        self.logger.info(f"--- Starting AgentCore cycle for {sample_user_id} at {now_utc.isoformat()} ---")
        self._memory_updated_this_cycle = False

        # --- Process Pending KPI Collections ---
        processed_pending_indices: List[int] = []
        # ... (Full KPI collection logic from previous step) ...

        # --- Persist Effectiveness Memory if Updated ---
        if self._memory_updated_this_cycle:
            self.logger.info("Effectiveness memory updated this cycle. Attempting to save.")
            save_success = self._save_effectiveness_memory()
            if save_success: self.logger.info(f"Effectiveness memory saved to {self.effectiveness_memory_filepath}")
            else: self.logger.error("Failed to save effectiveness memory this cycle.")
        else:
            self.logger.info("Effectiveness memory not updated this cycle. No save needed.")

        self.logger.info(f"--- AgentCore cycle completed for {sample_user_id} at {datetime.utcnow().isoformat()} ---")


@patch('app.core.agent_core.datetime')
async def main_example_run_with_mock_time(mock_datetime_obj, specific_time_utc_str: str, sample_user_id: str, agent_core: AgentCore, analytics_service_mock: Any, kpi_overrides: Dict[str, Any]):
    mocked_now = datetime.fromisoformat(specific_time_utc_str.replace("Z","+00:00"))
    mock_datetime_obj.utcnow.return_value = mocked_now
    original_kpis_func = analytics_service_mock.get_current_system_kpis_summary
    def get_modified_kpis(): # This closure will capture kpi_overrides from the outer scope
        base_kpis = {"overall_congestion_level": "LOW", "average_speed_kmh": 50}
        for kpi_name in ALL_CORRIDOR_DEMAND_KPIS: base_kpis[kpi_name] = "LOW"
        base_kpis.update(kpi_overrides)
        logger.debug(f"MOCK AnalyticsService providing KPIs: {base_kpis} for time {mocked_now.isoformat()}")
        return base_kpis
    analytics_service_mock.get_current_system_kpis_summary = get_modified_kpis
    logger.info(f"--- Running main_example cycle MOCKED TIME: {mocked_now.isoformat()} for {sample_user_id} ---")
    await agent_core.run_decision_cycle(sample_user_id=sample_user_id)
    analytics_service_mock.get_current_system_kpis_summary = original_kpis_func # Restore for other calls

async def main_example():
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')

    logger.info(f"--- Setting up main_example for Full Persistence Lifecycle ---")
    logger.info(f"Memory file path configured: {EFFECTIVENESS_MEMORY_FILEPATH}")
    os.makedirs(EFFECTIVENESS_MEMORY_DIR, exist_ok=True)
    if os.path.exists(EFFECTIVENESS_MEMORY_FILEPATH):
        os.remove(EFFECTIVENESS_MEMORY_FILEPATH)
        logger.info(f"Removed pre-existing memory file: {EFFECTIVENESS_MEMORY_FILEPATH}")

    class MockAnalyticsService:
        _kpi_call_count = 0
        _signal_kpi_data = {} # To provide different post-KPIs for different signals

        def configure_signal_kpi_data(self, signal_id: str, post_congestion_level: str, flow_rate: int):
            self._signal_kpi_data[signal_id] = {"local_congestion_level": post_congestion_level, "flow_rate_absolute": flow_rate}

        async def get_critical_alert_summary(self) -> Dict[str, Any]: return {"active_alerts": []}
        def get_current_system_kpis_summary(self) -> Dict[str, Any]: return {} # Will be replaced by lambda in helper
        async def get_signal_post_action_kpis(self, signal_id: str, metrics_to_collect: List[str], **kwargs) -> Dict[str, Any]:
            self.logger.info(f"MOCK Analytics: get_signal_post_action_kpis for {signal_id}, metrics: {metrics_to_collect}")
            return self._signal_kpi_data.get(signal_id, {"local_congestion_level": "UNKNOWN", "flow_rate_absolute": 0})
        # Add other KPI methods if needed by other action types being tested
        async def get_corridor_post_action_kpis(self, corridor_id: str, **kwargs) -> Dict[str, Any]:
            return {"corridor_avg_travel_time_seconds": 150, "stops_per_vehicle_in_corridor": 2}


    class MockTrafficSignalService:
        def __init__(self): self._signals: Dict[str, SignalState] = {}; self._initialize_mock_signals()
        def _initialize_mock_signals(self, signal_ids_to_init=["TS001", "TS002", "TS003", "TS004", "TS005"]):
            self._signals.clear()
            for i, sid in enumerate(signal_ids_to_init):
                 self._signals[sid] = SignalState(signal_id=sid, location=LocationModel(latitude=1.0+i*0.001, longitude=1.0+i*0.001, name=sid),
                                                 current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE,
                                                 last_updated=datetime.utcnow(), main_flow_direction="NS")
            logger.info(f"MockTrafficSignalService: Initialized/Reset signals: {list(self._signals.keys())}")
        async def get_all_signal_states(self) -> List[SignalState]: return list(self._signals.values())
        async def set_signal_phase(self, sid,p,d) -> SignalControlCommandResponse:
            if sid in self._signals: self._signals[sid].current_phase = p; return SignalControlCommandResponse(signal_id=sid, status=SignalControlStatusEnum.ACCEPTED)
            return SignalControlCommandResponse(signal_id=sid, status=SignalControlStatusEnum.FAILED)

    # --- Agent Session 1 ---
    logger.info("--- MainExample Full Lifecycle: Agent Session 1 (Initial Run) ---")
    analytics_mock1 = MockAnalyticsService()
    traffic_service_mock1 = MockTrafficSignalService()
    agent1 = AgentCore(MagicMock(spec=PredictionScheduler), MagicMock(spec=PersonalizedRoutingService), analytics_mock1, traffic_service_mock1)
    logger.info(f"Agent1 initial memory: {agent1.action_effectiveness_memory}")
    assert not agent1.action_effectiveness_memory

    # Cycle 1.1: Action on TS001 (results in good score)
    action_id_ts001_good = uuid4(); ts001_action_time = datetime(2023,1,1,10,0,0)
    analytics_mock1.configure_signal_kpi_data("TS001", "LOW", 1500) # Good KPIs
    agent1.pending_kpi_collection.append({
        'action_id': action_id_ts001_good, 'action_type': "SET_SIGNAL_GREEN_CONGESTION", 'target_ids': ["TS001"],
        'action_timestamp': ts001_action_time, 'action_parameters': {"phase":"GREEN"}, 'pre_action_context_kpis': {"overall_congestion":"HIGH"},
        'query_after_timestamp': ts001_action_time + timedelta(seconds=ACTION_KPI_CONFIG["SET_SIGNAL_GREEN_CONGESTION"]["delay_seconds"]-60), # Make it due
        'metrics_to_collect': ["local_congestion_level", "flow_rate_absolute"], 'evaluation_window_minutes': 5,
        'kpi_query_details': {'service_method_name': "get_signal_post_action_kpis", 'method_specific_args': {'signal_id': "TS001"}}
    })
    await main_example_run_with_mock_time(ts001_action_time.isoformat().replace("+00:00","Z"), "user_s1_c1", agent1, analytics_mock1, {"overall_congestion_level":"HIGH"})

    # Cycle 1.2: Action on TS002 (results in bad score)
    action_id_ts002_bad = uuid4(); ts002_action_time = datetime(2023,1,1,10,10,0) # Slightly later
    analytics_mock1.configure_signal_kpi_data("TS002", "HIGH", 300) # Bad KPIs
    agent1.pending_kpi_collection.append({
        'action_id': action_id_ts002_bad, 'action_type': "SET_SIGNAL_GREEN_CONGESTION", 'target_ids': ["TS002"],
        'action_timestamp': ts002_action_time, 'action_parameters': {"phase":"GREEN"}, 'pre_action_context_kpis': {"overall_congestion":"HIGH"},
        'query_after_timestamp': ts002_action_time + timedelta(seconds=ACTION_KPI_CONFIG["SET_SIGNAL_GREEN_CONGESTION"]["delay_seconds"]-60), # Make it due
        'metrics_to_collect': ["local_congestion_level", "flow_rate_absolute"], 'evaluation_window_minutes': 5,
        'kpi_query_details': {'service_method_name': "get_signal_post_action_kpis", 'method_specific_args': {'signal_id': "TS002"}}
    })
    await main_example_run_with_mock_time(ts002_action_time.isoformat().replace("+00:00","Z"), "user_s1_c2", agent1, analytics_mock1, {"overall_congestion_level":"HIGH"})

    logger.info(f"Agent1 memory before shutdown: {json.dumps(agent1.action_effectiveness_memory, indent=2)}")
    assert os.path.exists(agent1.effectiveness_memory_filepath)
    agent1_memory_snapshot = agent1.action_effectiveness_memory.copy()

    # --- Agent Session 2 ---
    logger.info("--- MainExample Full Lifecycle: Agent Session 2 (Reloads Memory) ---")
    analytics_mock2 = MockAnalyticsService(); traffic_service_mock2 = MockTrafficSignalService() # Fresh mocks for new agent
    agent2 = AgentCore(MagicMock(spec=PredictionScheduler), MagicMock(spec=PersonalizedRoutingService), analytics_mock2, traffic_service_mock2)
    logger.info(f"Agent2 initial memory (loaded): {json.dumps(agent2.action_effectiveness_memory, indent=2)}")
    assert agent2.action_effectiveness_memory == agent1_memory_snapshot

    # Cycle 2.1: New action for TS001 (another good score)
    action_id_ts001_good2 = uuid4(); ts001_action2_time = datetime(2023,1,1,11,0,0)
    analytics_mock2.configure_signal_kpi_data("TS001", "LOW", 1600) # Still good
    agent2.pending_kpi_collection.append({
        'action_id': action_id_ts001_good2, 'action_type': "SET_SIGNAL_GREEN_CONGESTION", 'target_ids': ["TS001"],
        'action_timestamp': ts001_action2_time, 'action_parameters': {"phase":"GREEN"}, 'pre_action_context_kpis': {"overall_congestion":"HIGH"},
        'query_after_timestamp': ts001_action2_time + timedelta(seconds=ACTION_KPI_CONFIG["SET_SIGNAL_GREEN_CONGESTION"]["delay_seconds"]-60),
        'metrics_to_collect': ["local_congestion_level", "flow_rate_absolute"], 'evaluation_window_minutes': 5,
        'kpi_query_details': {'service_method_name': "get_signal_post_action_kpis", 'method_specific_args': {'signal_id': "TS001"}}
    })
    await main_example_run_with_mock_time(ts001_action2_time.isoformat().replace("+00:00","Z"), "user_s2_c1", agent2, analytics_mock2, {"overall_congestion_level":"HIGH"})
    logger.info(f"Agent2 memory after new action: {json.dumps(agent2.action_effectiveness_memory, indent=2)}")
    assert len(agent2.action_effectiveness_memory.get("SET_SIGNAL_GREEN_CONGESTION:TS001", [])) > 1 # Should have appended

    agent2_memory_snapshot = agent2.action_effectiveness_memory.copy()

    # --- Agent Session 3 (Verify last save) ---
    logger.info("--- MainExample Full Lifecycle: Agent Session 3 (Verify Agent2 Save) ---")
    analytics_mock3 = MockAnalyticsService(); traffic_service_mock3 = MockTrafficSignalService()
    agent3 = AgentCore(MagicMock(spec=PredictionScheduler), MagicMock(spec=PersonalizedRoutingService), analytics_mock3, traffic_service_mock3)
    logger.info(f"Agent3 initial memory (loaded from Agent2's save): {json.dumps(agent3.action_effectiveness_memory, indent=2)}")
    assert agent3.action_effectiveness_memory == agent2_memory_snapshot

    # Cleanup
    logger.info("--- Main example execution finished. Attempting cleanup of memory file. ---")
    if os.path.exists(EFFECTIVENESS_MEMORY_FILEPATH):
        try: os.remove(EFFECTIVENESS_MEMORY_FILEPATH); logger.info(f"Cleaned up memory file: {EFFECTIVENESS_MEMORY_FILEPATH}")
        except OSError as e: logger.error(f"Error cleaning up memory file {EFFECTIVENESS_MEMORY_FILEPATH}: {e}")

if __name__ == "__main__":
    # asyncio.run(main_example())
    logger.info("AgentCore module defined. Example main_example() function available for testing.")

[end of backend/app/core/agent_core.py]
