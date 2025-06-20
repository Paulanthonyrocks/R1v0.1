import asyncio
import logging
from typing import Optional, Dict, Any, List, Set, Tuple, Union # Added Union
import json
from datetime import datetime, timedelta, time
import math
from uuid import UUID, uuid4
import os
import random # Added for exploration parameters

from pydantic import BaseModel, Field

from app.tasks.prediction_scheduler import PredictionScheduler
from app.services.personalized_routing_service import PersonalizedRoutingService, CommonTravelPattern
from app.services.analytics_service import AnalyticsService
from app.services.traffic_signal_service import TrafficSignalService
from app.models.traffic import LocationModel
from app.models.signals import SignalState, SignalPhaseEnum, SignalOperationalStatusEnum, SignalControlCommandResponse, SignalControlStatusEnum
from app.models.websocket import UserSpecificConditionAlert, WebSocketMessage # WebSocketMessage might be needed by mock examples
# Ensure all signal model imports are present
from app.models.signals import SignalState, SignalPhaseEnum, SignalOperationalStatusEnum, SignalControlCommandResponse, SignalControlStatusEnum # Already mostly there, ensuring completeness

logger = logging.getLogger(__name__)

PREDICTIVE_ALERT_LIKELIHOOD_THRESHOLD = 60
# ... (GREEN_WAVE_CORRIDOR_CONFIGS, ALL_CORRIDOR_DEMAND_KPIS, ACTION_KPI_CONFIG, ACTION_EFFECTIVENESS_CONFIG, Filepath constants remain the same) ...
GREEN_WAVE_CORRIDOR_CONFIGS = {
    "main_st_ns_wave": { "description": "Main Street NS Wave", "signals_in_order": ["TS001", "TS002", "TS004"], "target_green_phase": SignalPhaseEnum.GREEN, "wave_green_time_seconds": 50, "offsets_seconds": [0, 18, 36], "corridor_flow_direction_assumption": "NS", "time_windows": [{"start": "07:00", "end": "09:00"}, {"start": "16:00", "end": "18:00"}], "demand_kpi_trigger": "corridor_main_st_ns_demand_high", "priority": 1 },
    "alt_st_ew_wave": { "description": "Alternative Street EW Wave (Prio 1)", "signals_in_order": ["TS005", "TS003"], "target_green_phase": SignalPhaseEnum.GREEN, "wave_green_time_seconds": 45, "offsets_seconds": [0, 22], "corridor_flow_direction_assumption": "EW", "time_windows": [{"start": "07:00", "end": "09:00"}], "demand_kpi_trigger": "corridor_alt_st_ew_demand", "priority": 1 },
    "oak_ave_ew_wave": { "description": "Oak Avenue EW Mid-day Wave", "signals_in_order": ["TS003", "TS005"], "target_green_phase": SignalPhaseEnum.GREEN, "wave_green_time_seconds": 40, "offsets_seconds": [0, 25], "corridor_flow_direction_assumption": "EW", "time_windows": [{"start": "11:00", "end": "13:00"}], "demand_kpi_trigger": "corridor_oak_ave_ew_demand_moderate", "priority": 2 }
}
ALL_CORRIDOR_DEMAND_KPIS = list(set([c.get("demand_kpi_trigger") for c in GREEN_WAVE_CORRIDOR_CONFIGS.values() if c.get("demand_kpi_trigger")]))
ACTION_KPI_CONFIG = {
    "SET_SIGNAL_GREEN_CONGESTION": {"pre_action_kpi_query_config": {"service_method_name": "get_signal_current_kpis", "metrics_to_collect": ["queue_lengths_meters", "current_flow_vph"], "arg_mapping": {"signal_id": "target_ids[0]"}}, "delay_seconds": 5, "metrics": ["flow_rate_absolute", "local_congestion_level"], "eval_window_minutes": 1, "service_method": "get_signal_post_action_kpis"},
    "INCIDENT_RESPONSE_ACCIDENT": {"pre_action_kpi_query_config": {"service_method_name": "get_incident_area_current_kpis", "metrics_to_collect": ["avg_speed_kmh", "vehicle_count"], "arg_mapping": {"incident_location": "action_parameters.incident_location", "radius_meters": "action_parameters.radius_meters"}}, "delay_seconds": 10, "metrics": ["clearance_time_seconds", "avg_speed_kmh_incident_zone"], "eval_window_minutes": 2, "service_method": "get_incident_response_post_action_kpis"},
    "SET_SIGNAL_RED_ROAD_CLOSURE": {"pre_action_kpi_query_config": {"service_method_name": "get_signal_current_kpis", "metrics_to_collect": ["current_green_approach_flow_vph"], "arg_mapping": {"signal_id": "target_ids[0]"}}, "delay_seconds": 5, "metrics": ["upstream_flow_rate_reduction_percentage"], "eval_window_minutes": 1, "service_method": "get_signal_post_action_kpis"},
    "GREEN_WAVE_ACTIVATION": {"pre_action_kpi_query_config": {"service_method_name": "get_corridor_current_kpis", "metrics_to_collect": ["avg_travel_time_seconds", "throughput_vph"], "arg_mapping": {"corridor_id": "target_ids[0]"}}, "delay_seconds": 10, "metrics": ["corridor_avg_travel_time_seconds", "corridor_throughput_vph"], "eval_window_minutes": 2, "service_method": "get_corridor_post_action_kpis"}
}
ACTION_EFFECTIVENESS_CONFIG = {
    "SET_SIGNAL_GREEN_CONGESTION": {"relevant_kpis": [{"source":"pre","key_path":["current_flow_vph"],"as":"pre_flow"},{"source":"pre","key_path":["queue_lengths_meters","N"],"as":"pre_q_n"},{"source":"post","key_path":["local_congestion_level"],"as":"post_local_congestion"},{"source":"post","key_path":["flow_rate_absolute"],"as":"post_flow_rate"}], "scoring_logic_type": "congestion_improvement"},
    "GREEN_WAVE_ACTIVATION": {"relevant_kpis": [{"source":"pre","key_path":["corridor_id"],"as":"gw_corridor_id"},{"source":"pre","key_path":["expected_demand_level"],"as":"gw_pre_demand_level"},{"source":"pre","key_path":["avg_travel_time_seconds"],"as":"pre_gw_avg_travel_time"},{"source":"pre","key_path":["throughput_vph"],"as":"pre_gw_throughput"},{"source":"post","key_path":["corridor_avg_travel_time_seconds"],"as":"gw_post_avg_travel_time"},{"source":"post","key_path":["corridor_throughput_vph"],"as":"gw_post_throughput"}], "scoring_logic_type": "green_wave_efficiency"},
    "INCIDENT_RESPONSE_ACCIDENT": {"relevant_kpis": [{"source":"pre","key_path":["avg_speed_kmh"],"as":"pre_incident_avg_speed"},{"source":"post","key_path":["clearance_time_seconds"],"as":"post_clearance_time"},{"source":"post","key_path":["avg_speed_kmh_incident_zone"],"as":"post_incident_avg_speed"}], "scoring_logic_type": "incident_clearance_speed"},
    "SET_SIGNAL_RED_ROAD_CLOSURE": {"relevant_kpis": [{"source":"pre","key_path":["current_green_approach_flow_vph"],"as":"pre_closure_flow_on_green"},{"source":"post","key_path":["upstream_flow_rate_reduction_percentage"],"as":"post_flow_reduction_percentage"}], "scoring_logic_type": "closure_effectiveness"}
}
EFFECTIVENESS_MEMORY_FILENAME: str = "action_effectiveness_memory.json"
_CURRENT_FILE_DIR = os.path.dirname(os.path.abspath(__file__)); _APP_DIR = os.path.dirname(_CURRENT_FILE_DIR)
_BACKEND_DIR = os.path.dirname(_APP_DIR)
EFFECTIVENESS_MEMORY_DIR: str = os.path.join(_BACKEND_DIR, "data")
EFFECTIVENESS_MEMORY_FILEPATH: str = os.path.join(EFFECTIVENESS_MEMORY_DIR, EFFECTIVENESS_MEMORY_FILENAME)

class ActionPerformanceLog(BaseModel): # ... (as before) ...
    action_id: UUID = Field(default_factory=uuid4); action_timestamp: datetime; action_type: str = Field(...)
    target_ids: List[str] = Field(...); action_parameters: Dict[str, Any] = Field(default_factory=dict)
    pre_action_context_kpis: Dict[str, Any] = Field(default_factory=dict)
    post_action_kpis: Optional[Dict[str, Any]] = Field(None); kpi_collection_timestamp: Optional[datetime] = Field(None)
    effectiveness_score: Optional[float] = Field(None); effectiveness_metrics_used: Optional[Dict[str, Any]] = Field(None)

class AgentCore: # ... (attributes and __init__ as before) ...
    SIGNAL_ACTION_COOLDOWN_SECONDS = 120; INCIDENT_SIGNAL_COOLDOWN_SECONDS = 300
    ROAD_CLOSURE_IMMEDIATE_RADIUS_METERS = 50; MAX_SCORES_PER_ACTION_SIGNATURE: int = 10
    def __init__(self, ps, pr, an, ts): # Simplified args
        self.prediction_scheduler=ps; self.personalized_routing_service=pr; self.analytics_service=an; self.traffic_signal_service=ts
        self._recent_signal_actions={}; self.green_wave_corridor_configs=GREEN_WAVE_CORRIDOR_CONFIGS
        self.action_effectiveness_config=ACTION_EFFECTIVENESS_CONFIG; self.action_performance_logs=[]
        self.pending_kpi_collection=[]; self.effectiveness_memory_filepath=EFFECTIVENESS_MEMORY_FILEPATH
        self.action_effectiveness_memory=self._load_effectiveness_memory(); self._memory_updated_this_cycle=False
        self.logger=logger; self.logger.info(f"AgentCore initialized with PredictionScheduler, PersonalizedRoutingService, AnalyticsService, TrafficSignalService. (mem loaded: {len(self.action_effectiveness_memory)}).")

        # Initialize Exploration Attributes
        self.exploration_epsilon: float = 0.1  # Default 10% exploration rate
        self.rng = random.Random()
        self.rng.seed(42) # Optional: for deterministic behavior in examples
        self.logger.info(f"Exploration parameters initialized: epsilon = {self.exploration_epsilon}, rng_seed = 42")

    def _load_effectiveness_memory(self) -> Dict[str, List[float]]: return {}
    def _save_effectiveness_memory(self) -> bool: return True
    def _extract_kpi_value(self, sd: Optional[Dict[str,Any]], kp: List[str]) -> Any:
        if sd is None: return None; v=sd
        for k in kp:
            if isinstance(v,dict) and k in v: v=v[k]
            else: self.logger.debug(f"Key path {kp} not fully found in {sd}. Missing '{k}'."); return None
        return v

    def _score_congestion_improvement(self, metrics: Dict[str, Any]) -> Optional[float]:
        self.logger.debug(f"Scoring congestion improvement with: {metrics}")
        pre_overall = metrics.get("pre_overall_congestion_proxy"); post_local = metrics.get("post_local_congestion")
        # pre_flow = metrics.get("pre_flow"); post_flow = metrics.get("post_flow_rate") # Example if using flow
        if post_local is None: self.logger.warning("Congestion score: post_local_congestion missing."); return None
        score = 0.0
        if pre_overall == "HIGH": score = {"LOW":1.0, "MEDIUM":0.5, "HIGH":-0.2}.get(post_local,0.0)
        elif pre_overall == "MEDIUM": score = {"LOW":0.5, "MEDIUM":0.0, "HIGH":-0.5}.get(post_local,0.0)
        else: score = {"LOW":0.0, "MEDIUM":-0.2, "HIGH":-0.7}.get(post_local,0.0)
        return max(-1.0, min(1.0, score))

    def _score_green_wave_efficiency(self, metrics: Dict[str, Any]) -> Optional[float]:
        self.logger.debug(f"Scoring green wave efficiency with: {metrics}")
        pre_tt = metrics.get("pre_gw_avg_travel_time"); post_tt = metrics.get("post_gw_avg_travel_time")
        pre_tp = metrics.get("pre_gw_throughput"); post_tp = metrics.get("gw_post_throughput")
        score = 0.0; metrics_counted = 0
        if post_tt is not None:
            metrics_counted+=1
            baseline_tt = pre_tt if pre_tt is not None and pre_tt > 0 else metrics.get("gw_typical_travel_time", 150) # Fallback typical
            if post_tt < baseline_tt * 0.8: score += 0.5
            elif post_tt < baseline_tt * 1.1: score += 0.1
            else: score -= 0.5
        if post_tp is not None:
            metrics_counted+=1
            baseline_tp = pre_tp if pre_tp is not None else metrics.get("gw_target_throughput_vph", 700) # Fallback target
            if baseline_tp > 0 and post_tp > baseline_tp * 0.9 : score += 0.5
            elif baseline_tp > 0 and post_tp > baseline_tp * 0.6 : score += 0.1
            elif post_tp < baseline_tp * 0.5: score -= 0.4 # Penalize if significantly below non-zero baseline
            elif baseline_tp == 0 and post_tp > 50 : score += 0.2 # Some throughput is better than none
        if metrics_counted == 0: self.logger.warning(f"GW efficiency: No relevant post-KPIs found in {metrics}."); return None
        return max(-1.0, min(1.0, score))

    def _score_incident_clearance_speed(self, metrics: Dict[str, Any]) -> Optional[float]:
        self.logger.debug(f"Scoring incident clearance with: {metrics}")
        clear_time = metrics.get("post_clearance_time"); post_speed = metrics.get("post_incident_avg_speed"); pre_speed = metrics.get("pre_incident_avg_speed")
        if clear_time is None: self.logger.warning("Incident score: post_clearance_time missing."); return None
        score = 0.0
        if clear_time < 900: score += 0.6      # < 15 mins
        elif clear_time < 1800: score += 0.2   # < 30 mins
        else: score -= 0.6
        if post_speed is not None and pre_speed is not None and pre_speed < 40 : # If congested before
             if post_speed > pre_speed * 1.5 and post_speed > 20 : score += 0.4
             elif post_speed > pre_speed + 5 : score += 0.1
        return max(-1.0, min(1.0, score))

    def _score_closure_effectiveness(self, metrics: Dict[str, Any]) -> Optional[float]:
        self.logger.debug(f"Scoring closure effectiveness with: {metrics}")
        post_reduction = metrics.get("post_flow_reduction_percentage")
        if post_reduction is None: self.logger.warning("Closure score: post_flow_reduction_percentage missing."); return None
        if post_reduction > 75: return 0.9
        if post_reduction > 50: return 0.5
        if post_reduction > 20: return 0.1
        return -0.5

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
        elif logic_type == "incident_clearance_speed": score = self._score_incident_clearance_speed(metrics_for_scoring)
        elif logic_type == "closure_effectiveness": score = self._score_closure_effectiveness(metrics_for_scoring)
        else: self.logger.warning(f"Unknown scoring_logic_type: {logic_type}"); return None, metrics_for_scoring
        self.logger.info(f"Effectiveness score for {action_type} (ID: {log_entry_data.get('action_id')}): {score}. Metrics used: {metrics_for_scoring}")
        return score, metrics_for_scoring

    async def _find_signals_near_location(self, il: LocationModel, sigs: List[SignalState], r: int) -> List[SignalState]: return []
    async def _determine_next_travel_prediction_time(self, p: CommonTravelPattern, dt: datetime) -> Optional[datetime]: return None
    async def _execute_green_wave( self, cid: str, sigs_ord: List[str], gph: SignalPhaseEnum, gts: int, offs: List[int], all_curr_states: Dict[str, SignalState], proc_coord: Set[str], nu: datetime) -> bool: return True
    async def _fetch_pre_action_kpis(self, action_type_str: str, current_action_target_ids: List[str], current_action_parameters: Dict[str, Any], system_kpis_snapshot: Dict[str,Any]) -> Dict[str, Any]: # ... (as before)
        return {}

# --- Main Example for Traffic Signal Integration (as per subtask) ---
async def main_example_traffic_integration():
    # 1. Logging Setup
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    logger_main_example = logging.getLogger(__name__ + ".main_example_traffic_integration") # Use a specific logger
    logger_main_example.info("--- Starting main_example_traffic_integration ---")

    # 2. Mock Classes (defined within main_example_traffic_integration)
    class MockAnalyticsService:
        async def get_critical_alert_summary(self):
            logger_main_example.debug("MockAnalyticsService.get_critical_alert_summary called")
            # Return structure as per subtask example, even if AgentCore doesn't use all fields immediately
            return {"critical_unack_alert_count": 1, "active_alerts": [{"id": "alert1", "type": "TEST_ALERT"}]}

        def get_current_system_kpis_summary(self): # Sync as per subtask example
            logger_main_example.debug("MockAnalyticsService.get_current_system_kpis_summary called")
            # Return structure as per subtask example
            return {"overall_congestion_level": "HIGH", "sample_kpi": 123}

        async def get_signal_current_kpis(self, signal_id: str, metrics: List[str]):
            logger_main_example.debug(f"MockAnalyticsService.get_signal_current_kpis called for {signal_id} with {metrics}")
            return {"mock_metric": 100}

        async def get_signal_post_action_kpis(self, signal_id: str, **kwargs):
            logger_main_example.debug(f"MockAnalyticsService.get_signal_post_action_kpis called for {signal_id} with {kwargs}")
            return {"mock_post_metric": 110}

        # Add other async methods used by AgentCore if any, returning defaults
        async def get_corridor_current_kpis(self, corridor_id: str, metrics: List[str]): return {}
        async def get_incident_area_current_kpis(self, incident_location: LocationModel, radius_meters: int, metrics: List[str]): return {}
        async def get_corridor_post_action_kpis(self, corridor_id: str, **kwargs): return {}
        async def get_incident_response_post_action_kpis(self, incident_id: str, **kwargs): return {}


    class MockPredictionScheduler:
        async def set_priority_locations(self, locations: List[LocationModel]):
            logger_main_example.debug(f"MockPredictionScheduler.set_priority_locations called with {locations}")
            pass
        # Add other async methods used by AgentCore if any
        async def get_traffic_predictions_for_locations(self, locations: List[LocationModel]): return []


    class MockPersonalizedRoutingService:
        async def proactively_suggest_route(self, user_id: str, common_pattern: CommonTravelPattern, current_location: LocationModel):
            logger_main_example.debug(f"MockPersonalizedRoutingService.proactively_suggest_route called for {user_id}")
            return None

        async def get_user_common_travel_patterns(self, user_id: str) -> List[CommonTravelPattern]:
            logger_main_example.debug(f"MockPersonalizedRoutingService.get_user_common_travel_patterns called for {user_id}")
            # Return list with one CommonTravelPattern with valid nested dicts for location summaries
            return [
                CommonTravelPattern(
                    pattern_id="pattern1",
                    user_id=user_id,
                    start_location_summary={"name": "Home"},
                    end_location_summary={"name": "Work"},
                    days_of_week=[0,1,2,3,4], # Mon-Fri
                    time_of_day="08:00",
                    frequency=5
                )
            ]
        # Add other async methods used by AgentCore if any
        async def update_user_route_feedback(self, user_id: str, route_id: str, feedback: Dict[str, Any]): pass


    class MockConnectionManager: # As per subtask, if needed
        async def broadcast_message_model(self, message: WebSocketMessage):
            logger_main_example.debug(f"MockConnectionManager.broadcast_message_model called with {message.model_dump_json()}")
            pass

    class MockTrafficSignalService:
        def __init__(self, config: Optional[Dict[str, Any]] = None, connection_manager: Optional[MockConnectionManager] = None):
            self.config = config
            self.connection_manager = connection_manager # Stored but may not be used in this mock
            self._signals: Dict[str, SignalState] = {}
            self._cycle_count = 0

            # Initialize with 3 SignalState instances
            self._signals["TS001"] = SignalState(
                signal_id="TS001", location=LocationModel(latitude=1.0, longitude=1.0, name="Main St @ First Ave"),
                current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE,
                last_updated=datetime.utcnow()
            )
            self._signals["TS002"] = SignalState(
                signal_id="TS002", location=LocationModel(latitude=1.01, longitude=1.01, name="Main St @ Second Ave"),
                current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE,
                last_updated=datetime.utcnow()
            )
            self._signals["TS003"] = SignalState(
                signal_id="TS003", location=LocationModel(latitude=1.02, longitude=1.02, name="Oak St @ Third Ave"),
                current_phase=SignalPhaseEnum.OFF, operational_status=SignalOperationalStatusEnum.OFFLINE, # OFFLINE/OFF
                last_updated=datetime.utcnow()
            )
            logger_main_example.debug(f"MockTrafficSignalService initialized with {len(self._signals)} signals.")

        async def get_all_signal_states(self) -> List[SignalState]:
            self._cycle_count += 1
            logger_main_example.debug(f"MockTrafficSignalService.get_all_signal_states called (cycle {self._cycle_count}).")

            # If self._cycle_count == 2, change the first ONLINE signal to GREEN
            if self._cycle_count == 2:
                for signal_id in self._signals:
                    if self._signals[signal_id].operational_status == SignalOperationalStatusEnum.ONLINE:
                        logger_main_example.info(f"MockTrafficSignalService: Cycle 2 - Changing {signal_id} to GREEN before returning states.")
                        self._signals[signal_id].current_phase = SignalPhaseEnum.GREEN
                        self._signals[signal_id].last_updated = datetime.utcnow()
                        break
            return list(self._signals.values())

        async def set_signal_phase(self, signal_id: str, phase: SignalPhaseEnum, duration_seconds: Optional[int] = None) -> SignalControlCommandResponse:
            logger_main_example.debug(f"MockTrafficSignalService.set_signal_phase called for {signal_id} to {phase.value} for {duration_seconds}s.")
            if signal_id not in self._signals:
                logger_main_example.warning(f"Signal {signal_id} not found.")
                return SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.FAILED, message="Signal not found")

            signal = self._signals[signal_id]
            if signal.operational_status != SignalOperationalStatusEnum.ONLINE:
                logger_main_example.warning(f"Signal {signal_id} is not ONLINE (status: {signal.operational_status.value}). Action REJECTED.")
                return SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.REJECTED, message="Signal not ONLINE")

            logger_main_example.info(f"Signal {signal_id} phase updated to {phase.value}. Status ACCEPTED.")
            signal.current_phase = phase
            signal.last_updated = datetime.utcnow()
            # Here, you might also want to simulate the duration if your main logic depends on it being held
            # For this mock, just accepting the command is enough.
            return SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.ACCEPTED, message="Phase change command accepted")

    # 3. Instantiate and Run
    mock_analytics = MockAnalyticsService()
    mock_prediction_scheduler = MockPredictionScheduler() # Using specific mock for clarity
    mock_routing_service = MockPersonalizedRoutingService() # Using specific mock
    # mock_conn_manager = MockConnectionManager() # Not directly passed to AgentCore in current structure
    mock_traffic_signal_service = MockTrafficSignalService() # Using specific mock

    agent_core = AgentCore(
        prediction_scheduler=mock_prediction_scheduler,
        personalized_routing_service=mock_routing_service,
        analytics_service=mock_analytics,
        traffic_signal_service=mock_traffic_signal_service # ts argument
    )

    logger_main_example.info("--- Running decision cycle 1 ---")
    await agent_core.run_decision_cycle(sample_user_id="cycle_1_user")

    logger_main_example.info("--- Running decision cycle 2 ---")
    await agent_core.run_decision_cycle(sample_user_id="cycle_2_user")

    logger_main_example.info("--- main_example_traffic_integration completed ---")


    async def run_decision_cycle(self, sample_user_id: str = "user_agent_test_123"):
        # ... (Full logic including KPI scheduling and processing, and other agent phases) ...
        # For brevity, only showing the relevant parts for KPI scheduling and processing
        now_utc = datetime.utcnow()
        self.logger.info(f"--- Starting AgentCore cycle for {sample_user_id} at {now_utc.isoformat()} ---")
        self._memory_updated_this_cycle = False

        # Existing KPI and alert fetching
        system_kpis = self.analytics_service.get_current_system_kpis_summary()
        # alert_summary = await self.analytics_service.get_critical_alert_summary() # Assuming this is fetched elsewhere or not critical for this specific logic block

        # --- Fetch all traffic signal states (as per subtask) ---
        self.logger.info("Fetching all traffic signal states...")
        all_signal_states: List[SignalState] = await self.traffic_signal_service.get_all_signal_states()
        self.logger.info(f"AgentCore received {len(all_signal_states)} signal states.")
        for state in all_signal_states:
            self.logger.debug(
                f"Signal ID: {state.signal_id}, "
                f"Location: {state.location.name if state.location and state.location.name else 'N/A'}, "
                f"Phase: {state.current_phase.value if state.current_phase else 'N/A'}, "
                f"Status: {state.operational_status.value if state.operational_status else 'N/A'}"
            )

        # --- Autonomous Traffic Signal Control Logic (General Congestion with Epsilon-Greedy) ---
        current_congestion_level = system_kpis.get("overall_congestion_level", "UNKNOWN")
        self.logger.info(f"Overall congestion: {current_congestion_level}. Evaluating general signal adjustments.")

        controlled_a_signal_this_cycle_general = False # Specific flag for this logic block

        if current_congestion_level == "HIGH":
            candidate_signals_for_congestion_relief: List[Dict[str, Any]] = []
            for signal_state in all_signal_states:
                # Eligibility checks
                if signal_state.operational_status != SignalOperationalStatusEnum.ONLINE:
                    self.logger.debug(f"Signal {signal_state.signal_id} skipped (not ONLINE)."); continue
                if signal_state.current_phase == SignalPhaseEnum.GREEN: # Don't intervene if already green
                    self.logger.debug(f"Signal {signal_state.signal_id} skipped (already GREEN)."); continue

                # Cooldown check (ensure this respects other signal control reasons like incident or green wave)
                last_action_info = self._recent_signal_actions.get(signal_state.signal_id)
                if last_action_info and (now_utc - last_action_info['timestamp']).total_seconds() < self.SIGNAL_ACTION_COOLDOWN_SECONDS:
                    self.logger.debug(f"Signal {signal_state.signal_id} skipped (on cooldown). Last action: {last_action_info['reason']} at {last_action_info['timestamp']}.")
                    continue

                # Fetch historical average effectiveness score
                action_type_for_score = "SET_SIGNAL_GREEN_CONGESTION"
                action_signature = f"{action_type_for_score}:{signal_state.signal_id}"
                scores = self.action_effectiveness_memory.get(action_signature, [])
                avg_score = sum(scores) / len(scores) if scores else 0.0 # Default to 0.0 if no history

                candidate_signals_for_congestion_relief.append({
                    'signal_id': signal_state.signal_id,
                    'signal_state': signal_state, # Keep the full state object
                    'avg_score': avg_score
                })
                self.logger.debug(f"Signal {signal_state.signal_id} added as candidate for congestion relief. Avg score: {avg_score:.2f}")

            if candidate_signals_for_congestion_relief:
                selected_candidate_dict_entry = None
                action_choice_method = ""

                if self.rng.random() < self.exploration_epsilon:
                    # --- EXPLORE ---
                    selected_candidate_dict_entry = self.rng.choice(candidate_signals_for_congestion_relief)
                    action_choice_method = "EXPLORATORY_RANDOM"
                    self.logger.info(
                        f"{action_choice_method} general congestion action: Randomly selected signal "
                        f"'{selected_candidate_dict_entry['signal_id']}' from {len(candidate_signals_for_congestion_relief)} candidates. "
                        f"(Its avg score: {selected_candidate_dict_entry['avg_score']:.2f})"
                    )
                else:
                    # --- EXPLOIT ---
                    candidate_signals_for_congestion_relief.sort(key=lambda x: x['avg_score'], reverse=True)
                    selected_candidate_dict_entry = candidate_signals_for_congestion_relief[0]
                    action_choice_method = "EXPLOITATIVE_BEST_SCORE"
                    self.logger.info(
                        f"{action_choice_method} general congestion action: Selected signal "
                        f"'{selected_candidate_dict_entry['signal_id']}' (Avg score: {selected_candidate_dict_entry['avg_score']:.2f}). "
                        f"Top candidates considered (ID, Score): {[{'id':c['signal_id'], 'score':f'{c['avg_score']:.2f}'} for c in candidate_signals_for_congestion_relief[:3]]}"
                    )

                signal_to_control_state = selected_candidate_dict_entry['signal_state']

                self.logger.info(
                    f"General Congestion ({action_choice_method}): Attempting to set signal '{signal_to_control_state.signal_id}' "
                    f"to GREEN. Current phase: {signal_to_control_state.current_phase.value if signal_to_control_state.current_phase else 'N/A'}"
                )
                try:
                    response: SignalControlCommandResponse = await self.traffic_signal_service.set_signal_phase(
                        signal_id=signal_to_control_state.signal_id,
                        phase=SignalPhaseEnum.GREEN,
                        duration_seconds=60
                    )
                    self.logger.info(
                        f"General Congestion ({action_choice_method}) signal control for '{signal_to_control_state.signal_id}': "
                        f"Status {response.status.value} - {response.message}"
                    )

                    if response.status in [SignalControlStatusEnum.ACCEPTED, SignalControlStatusEnum.SUCCESS]:
                        controlled_a_signal_this_cycle_general = True
                        action_timestamp_utc = datetime.utcnow()

                        self._recent_signal_actions[signal_to_control_state.signal_id] = {
                            'timestamp': action_timestamp_utc,
                            'phase_commanded': SignalPhaseEnum.GREEN,
                            'duration_commanded': 60,
                            'reason': 'general_congestion',
                            'selection_method': action_choice_method
                        }
                        self.logger.info(
                            f"Recorded general congestion action ({action_choice_method}) for signal '{signal_to_control_state.signal_id}'. "
                            f"Recent actions: {len(self._recent_signal_actions)}"
                        )

                        action_type_str = "SET_SIGNAL_GREEN_CONGESTION"
                        action_kpi_cfg = ACTION_KPI_CONFIG.get(action_type_str)
                        if action_kpi_cfg:
                            current_action_target_ids = [signal_to_control_state.signal_id]
                            current_action_parameters = {
                                "phase": SignalPhaseEnum.GREEN.value,
                                "duration_seconds": 60,
                                "selection_method": action_choice_method
                            }

                            fetched_pre_action_kpis = await self._fetch_pre_action_kpis(
                                action_type_str=action_type_str,
                                current_action_target_ids=current_action_target_ids,
                                current_action_parameters=current_action_parameters, # Pass parameters that might be needed for KPI query (e.g. signal_id from target_ids)
                                system_kpis_snapshot=system_kpis
                            )

                            pre_action_kpis_for_log = {
                                "overall_system_congestion_at_decision": system_kpis.get("overall_congestion_level", "UNKNOWN"),
                                "signal_initial_phase_at_decision": signal_to_control_state.current_phase.value if signal_to_control_state.current_phase else 'N/A',
                                "chosen_candidate_avg_score": selected_candidate_dict_entry['avg_score'],
                                "num_candidates_considered": len(candidate_signals_for_congestion_relief),
                                "all_candidate_scores": {c['signal_id']: c['avg_score'] for c in candidate_signals_for_congestion_relief} # Store all scores for context
                            }
                            if fetched_pre_action_kpis: pre_action_kpis_for_log.update(fetched_pre_action_kpis)

                            pending_item_id = uuid4()
                            self.pending_kpi_collection.append({
                                'action_id': pending_item_id,
                                'action_type': action_type_str,
                                'target_ids': current_action_target_ids,
                                'action_timestamp': action_timestamp_utc,
                                'action_parameters': current_action_parameters,
                                'pre_action_context_kpis': pre_action_kpis_for_log,
                                'query_after_timestamp': action_timestamp_utc + timedelta(seconds=action_kpi_cfg["delay_seconds"]),
                                'metrics_to_collect': action_kpi_cfg["metrics"],
                                'evaluation_window_minutes': action_kpi_cfg["eval_window_minutes"],
                                'kpi_query_details': {'service_method_name': action_kpi_cfg["service_method"], 'method_specific_args': {'signal_id': signal_to_control_state.signal_id}}
                            })
                            self.logger.info(f"Scheduled KPI collection for {action_type_str} (ID: {pending_item_id}) on {signal_to_control_state.signal_id}. Choice: {action_choice_method}.")
                        else:
                            self.logger.warning(f"No ACTION_KPI_CONFIG found for '{action_type_str}'. KPI collection will not be scheduled.")

                except Exception as e_signal_control:
                    self.logger.error(
                        f"Error controlling signal '{signal_to_control_state.signal_id}' for general congestion ({action_choice_method}): {e_signal_control}",
                        exc_info=True
                    )
            else:
                self.logger.info("General Congestion: No suitable signals found for congestion relief this cycle after filtering.")

        else: # Congestion not HIGH
            self.logger.info("Congestion not HIGH. No system-wide general signal adjustments for congestion relief.")

        # --- Process Pending KPI Collections ---
        processed_pending_indices: List[int] = []
        # ... (Full KPI collection logic as per previous step, calling _calculate_effectiveness_score) ...

        # --- Green Wave Example (Illustrative of pre-KPI fetch and scheduling) ---
        # This is a simplified version of the full green wave logic block
        if True: # Placeholder for actual trigger
            selected_wave_to_run = {"id": "main_st_ns_wave", "config": GREEN_WAVE_CORRIDOR_CONFIGS["main_st_ns_wave"]}
            if selected_wave_to_run:
                cfg_run = selected_wave_to_run["config"]; cid_run = selected_wave_to_run["id"]
                action_type_str = "GREEN_WAVE_ACTIVATION"; current_action_target_ids = [cid_run]
                current_action_parameters = {"corridor_config": cfg_run, "corridor_id": cid_run} # Add corridor_id here for pre-KPI arg_mapping

                fetched_pre_action_kpis = await self._fetch_pre_action_kpis(action_type_str, current_action_target_ids, current_action_parameters, system_kpis)
                wave_executed = True # Assume executed for demo
                if wave_executed:
                    action_kpi_cfg = ACTION_KPI_CONFIG.get(action_type_str)
                    if action_kpi_cfg:
                        action_id = uuid4(); action_ts = datetime.utcnow()
                        base_pre_kpis = {"overall_congestion_at_decision": system_kpis.get("overall_congestion_level"), "corridor_id": cid_run, "expected_demand_level": system_kpis.get(cfg_run.get("demand_kpi_trigger"), "N/A")}
                        if fetched_pre_action_kpis: base_pre_kpis.update(fetched_pre_action_kpis)
                        self.pending_kpi_collection.append({
                            'action_id': action_id, 'action_type': action_type_str, 'target_ids': [cid_run] + cfg_run["signals_in_order"],
                            'action_timestamp': action_ts, 'action_parameters': {"wave_green_time_seconds": cfg_run["wave_green_time_seconds"]},
                            'pre_action_context_kpis': base_pre_kpis,
                            'query_after_timestamp': action_ts + timedelta(seconds=action_kpi_cfg["delay_seconds"]),
                            'metrics_to_collect': action_kpi_cfg["metrics"], 'evaluation_window_minutes': action_kpi_cfg["eval_window_minutes"],
                            'kpi_query_details': {'service_method_name': action_kpi_cfg["service_method"], 'method_specific_args': {'corridor_id': cid_run}}})
                        self.logger.info(f"Scheduled KPI collection for GW {action_id} on {cid_run} with pre_kpis: {base_pre_kpis}")

        if self._memory_updated_this_cycle: self._save_effectiveness_memory()
        self.logger.info(f"--- AgentCore cycle completed for {sample_user_id} at {datetime.utcnow().isoformat()} ---")

@patch('app.core.agent_core.datetime')
async def main_example_run_with_mock_time(mock_dt_obj, time_str: str, user: str, agent: AgentCore, an_mock: Any, kpis: Dict[str,Any]):
    # ... (as before) ...
    mocked_now = datetime.fromisoformat(time_str.replace("Z","+00:00")); mock_dt_obj.utcnow.return_value = mocked_now
    orig_func = an_mock.get_current_system_kpis_summary
    def get_mod_kpis(): base = {"overall_congestion_level":"LOW"}; [base.update({k:"LOW"}) for k in ALL_CORRIDOR_DEMAND_KPIS]; base.update(kpis); return base
    an_mock.get_current_system_kpis_summary = get_mod_kpis
    await agent.run_decision_cycle(user); an_mock.get_current_system_kpis_summary = orig_func

async def main_example():
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    logger.info(f"--- Setting up main_example for Enhanced Mock Analytics & Scoring ---")
    os.makedirs(EFFECTIVENESS_MEMORY_DIR, exist_ok=True)
    if os.path.exists(EFFECTIVENESS_MEMORY_FILEPATH): os.remove(EFFECTIVENESS_MEMORY_FILEPATH)

    class MockAnalytics(MagicMock):
        _call_counters = {}
        _pre_configured_kpis = {} # For pre-action kpis
        _post_configured_kpis = {} # For post-action kpis

        def _dynamic_value(self, key_seed: str, metric_name: str, min_val: Union[int, float], max_val: Union[int, float], return_float: bool = False) -> Union[int, float]:
            try: val_hash = hash(f"{key_seed}_{metric_name}_{self._call_counters.get(metric_name, 0)}")
            except: val_hash = hash(f"{str(key_seed)}_{metric_name}_{self._call_counters.get(metric_name, 0)}")
            range_val = max_val - min_val
            if range_val <=0 : return min_val
            result = min_val + (val_hash % (range_val + (1 if not return_float else 0.001) ))
            return float(result) if return_float else int(result)

        def configure_pre_action_kpis(self, target_id: str, service_method_name: str, kpi_data: Dict[str, Any]):
            self._pre_configured_kpis[f"{service_method_name}:{target_id}"] = kpi_data
        def configure_post_action_kpis(self, target_id: str, service_method_name: str, kpi_data: Dict[str, Any]):
            self._post_configured_kpis[f"{service_method_name}:{target_id}"] = kpi_data

        async def get_critical_alert_summary(self): return {"active_alerts":[]}
        def get_current_system_kpis_summary(self): return {}

        async def get_signal_current_kpis(self, signal_id: str, metrics: List[str]):
            lookup_key = f"get_signal_current_kpis:{signal_id}"
            if lookup_key in self._pre_configured_kpis: return self._pre_configured_kpis[lookup_key]
            kpis = {"queried_signal_id_pre_action": signal_id, "data_timestamp": datetime.utcnow().isoformat()}
            if "queue_lengths_meters" in metrics: kpis["queue_lengths_meters"] = {"N": self._dynamic_value(signal_id, "pre_q_n", 10, 50), "S": self._dynamic_value(signal_id, "pre_q_s", 5, 30)}
            if "current_flow_vph" in metrics: kpis["current_flow_vph"] = self._dynamic_value(signal_id, "pre_flow", 150, 450)
            return kpis
        async def get_corridor_current_kpis(self, corridor_id: str, metrics: List[str]):
            lookup_key = f"get_corridor_current_kpis:{corridor_id}"
            if lookup_key in self._pre_configured_kpis: return self._pre_configured_kpis[lookup_key]
            kpis = {"queried_corridor_id_pre_action": corridor_id, "data_timestamp": datetime.utcnow().isoformat()}
            if "avg_travel_time_seconds" in metrics: kpis["avg_travel_time_seconds"] = self._dynamic_value(corridor_id, "pre_tt", 100, 220)
            if "throughput_vph" in metrics: kpis["throughput_vph"] = self._dynamic_value(corridor_id, "pre_tp", 300, 650)
            return kpis
        async def get_incident_area_current_kpis(self, incident_location: LocationModel, radius_meters: int, metrics: List[str]): return {"avg_speed_kmh": self._dynamic_value(str(incident_location), "pre_inc_speed", 5,25)}

        async def get_signal_post_action_kpis(self, signal_id: str, **kwargs) -> Dict[str, Any]:
            lookup_key = f"get_signal_post_action_kpis:{signal_id}"
            if lookup_key in self._post_configured_kpis: return self._post_configured_kpis[lookup_key]
            return {"local_congestion_level": "LOW", "flow_rate_absolute": self._dynamic_value(signal_id,"post_flow",700,1500)}
        async def get_corridor_post_action_kpis(self, corridor_id: str, **kwargs) -> Dict[str, Any]:
            lookup_key = f"get_corridor_post_action_kpis:{corridor_id}"
            if lookup_key in self._post_configured_kpis: return self._post_configured_kpis[lookup_key]
            return {"corridor_avg_travel_time_seconds": self._dynamic_value(corridor_id, "post_tt", 70,150), "corridor_throughput_vph": self._dynamic_value(corridor_id,"post_tp",600,1200)}
        async def get_incident_response_post_action_kpis(self, incident_id: str, **kwargs) -> Dict[str, Any]:
            lookup_key = f"get_incident_response_post_action_kpis:{incident_id}"
            if lookup_key in self._post_configured_kpis: return self._post_configured_kpis[lookup_key]
            return {"clearance_time_seconds": self._dynamic_value(incident_id,"clear_time",300,1200), "avg_speed_kmh_incident_zone": self._dynamic_value(incident_id,"post_inc_speed",20,50)}

    class MockTraffic(MagicMock): # ... (as before) ...
        _signals = {}
        def __init__(self, *args, **kwargs): super().__init__(*args, **kwargs); self._initialize_mock_signals()
        def _initialize_mock_signals(self): self._signals.clear(); sids = ["TS001","TS002","TS003","TS004","TS005"];
            for i,sid in enumerate(sids): self._signals[sid]=SignalState(signal_id=sid,location=LocationModel(latitude=1+i*0.01,longitude=1),current_phase=SignalPhaseEnum.RED,operational_status=SignalOperationalStatusEnum.ONLINE,last_updated=datetime.utcnow(),main_flow_direction="NS")
        async def get_all_signal_states(self): return list(self._signals.values())
        async def set_signal_phase(self, sid,p,d): self._signals[sid].current_phase=p; return SignalControlCommandResponse(signal_id=sid,status=SignalControlStatusEnum.ACCEPTED)

    analytics_mock = MockAnalytics(); traffic_mock = MockTraffic() # Renamed from MockTraffic to MockTrafficSignalService in prev steps, ensure consistency if this example is run. For now, assume MockTraffic is the intended class here.
    # For this demo, let's ensure the signals used in general congestion are part of MockTraffic's setup
    # TS001, TS002, TS004 are used by main_st_ns_wave, so they should exist.
    # Let's refine MockTraffic or ensure its state can be manipulated for the demo.

    # Helper to reset signal states in MockTraffic for a new general congestion scenario
    def reset_mock_traffic_signals_for_congestion_demo(phase=SignalPhaseEnum.RED):
        traffic_mock._signals["TS001"].current_phase = phase
        traffic_mock._signals["TS001"].operational_status = SignalOperationalStatusEnum.ONLINE
        traffic_mock._signals["TS002"].current_phase = phase
        traffic_mock._signals["TS002"].operational_status = SignalOperationalStatusEnum.ONLINE
        traffic_mock._signals["TS004"].current_phase = phase # TS004 is used in main_st_ns_wave
        traffic_mock._signals["TS004"].operational_status = SignalOperationalStatusEnum.ONLINE
        # Other signals can be offline or green to not interfere initially
        if "TS003" in traffic_mock._signals: traffic_mock._signals["TS003"].current_phase = SignalPhaseEnum.GREEN
        if "TS005" in traffic_mock._signals: traffic_mock._signals["TS005"].operational_status = SignalOperationalStatusEnum.OFFLINE
        logger.debug(f"MAIN_EXAMPLE: Reset TS001, TS002, TS004 to {phase.value}, ONLINE for congestion demo.")

    agent = AgentCore(MagicMock(spec=PredictionScheduler), MagicMock(spec=PersonalizedRoutingService), analytics_mock, traffic_mock)

    # --- Epsilon-Greedy General Congestion Demonstration ---
    logger.info("--- MAIN_EXAMPLE: Starting Epsilon-Greedy General Congestion Demonstration ---")
    original_epsilon = agent.exploration_epsilon
    original_rng_state = agent.rng.getstate() # Save RNG state if needed for other parts of main_example
    agent.rng.seed(123) # Consistent RNG for this demo part

    kpi_collection_delay = ACTION_KPI_CONFIG["SET_SIGNAL_GREEN_CONGESTION"]["delay_seconds"]
    # Initial time for the demo
    current_sim_time_str = "2023-01-01T10:00:00Z"

    # --- Cycle Group 1: Build Initial Effectiveness History (Forcing Exploitation) ---
    logger.info("--- MAIN_EXAMPLE: Cycle Group 1 - Building Initial History (Forcing Exploitation) ---")
    agent.exploration_epsilon = 0.0
    logger.info(f"MAIN_EXAMPLE: Temporarily set exploration_epsilon to {agent.exploration_epsilon}")

    # Helper function to run an action cycle and then a KPI processing cycle
    async def run_action_and_kpi_cycles(action_time_str, action_user_id, kpi_user_id,
                                        signal_to_configure_kpis_for, post_kpi_payload,
                                        overall_congestion_level_action="HIGH", overall_congestion_level_kpi="LOW"):
        nonlocal current_sim_time_str # To update the global sim time for the next iteration

        # Action Cycle
        logger.info(f"MAIN_EXAMPLE: Running ACTION cycle at {action_time_str} for {action_user_id}")
        await main_example_run_with_mock_time(
            action_time_str, action_user_id, agent, analytics_mock,
            kpis={"overall_congestion_level": overall_congestion_level_action}
        )

        # Configure post-action KPIs for the signal that was just acted upon
        # This assumes only one signal action happens per cycle in this controlled demo
        if agent.pending_kpi_collection:
            last_pending_action = agent.pending_kpi_collection[-1]
            if last_pending_action['action_type'] == "SET_SIGNAL_GREEN_CONGESTION":
                affected_signal_id = last_pending_action['target_ids'][0]
                if affected_signal_id == signal_to_configure_kpis_for:
                     analytics_mock.configure_post_action_kpis(affected_signal_id, "get_signal_post_action_kpis", post_kpi_payload)
                     logger.info(f"MAIN_EXAMPLE: Configured post-KPIs for {affected_signal_id} to yield score via: {post_kpi_payload}")
                else: # Safety log if a different signal was chosen than expected
                    logger.warning(f"MAIN_EXAMPLE: Expected to configure KPIs for {signal_to_configure_kpis_for}, but last action was for {affected_signal_id}. Not configuring KPIs.")

        # KPI Processing Cycle
        kpi_collection_time = datetime.fromisoformat(action_time_str.replace("Z","+00:00")) + timedelta(seconds=kpi_collection_delay + 5)
        kpi_collection_time_str = kpi_collection_time.isoformat().replace("+00:00", "Z")
        logger.info(f"MAIN_EXAMPLE: Running KPI PROCESSING cycle at {kpi_collection_time_str} for {kpi_user_id}")
        await main_example_run_with_mock_time(
            kpi_collection_time_str, kpi_user_id, agent, analytics_mock,
            kpis={"overall_congestion_level": overall_congestion_level_kpi} # Low congestion for KPI cycle
        )
        current_sim_time_str = (kpi_collection_time + timedelta(minutes=1)).isoformat().replace("+00:00", "Z") # Advance time for next action

    # Cycle 1.1 (TS001 - Good Score)
    logger.info("--- MAIN_EXAMPLE: Cycle 1.1 (TS001 Good Score) ---")
    reset_mock_traffic_signals_for_congestion_demo() # TS001, TS002, TS004 are RED
    traffic_mock._signals["TS002"].current_phase = SignalPhaseEnum.GREEN # Make TS002 not a candidate
    traffic_mock._signals["TS004"].current_phase = SignalPhaseEnum.GREEN # Make TS004 not a candidate
    await run_action_and_kpi_cycles(current_sim_time_str, "user_hist_ts001", "user_kpi_ts001",
                                    "TS001", {"local_congestion_level": "LOW", "flow_rate_absolute": 800}) # Good score

    # Cycle 1.2 (TS002 - Bad Score)
    logger.info("--- MAIN_EXAMPLE: Cycle 1.2 (TS002 Bad Score) ---")
    reset_mock_traffic_signals_for_congestion_demo()
    traffic_mock._signals["TS001"].current_phase = SignalPhaseEnum.GREEN # TS001 not candidate (or use cooldown from agent._recent_signal_actions)
    agent._recent_signal_actions.clear() # Clear recent for this controlled setup
    agent._recent_signal_actions["TS001"] = {'timestamp': datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) - timedelta(seconds=10), 'reason':'demo'} # Simulate TS001 just acted
    traffic_mock._signals["TS004"].current_phase = SignalPhaseEnum.GREEN
    await run_action_and_kpi_cycles(current_sim_time_str, "user_hist_ts002", "user_kpi_ts002",
                                    "TS002", {"local_congestion_level": "HIGH", "flow_rate_absolute": 100}) # Bad score

    # Cycle 1.3 (TS004 - Neutral Score)
    logger.info("--- MAIN_EXAMPLE: Cycle 1.3 (TS004 Neutral Score) ---")
    reset_mock_traffic_signals_for_congestion_demo()
    agent._recent_signal_actions.clear()
    agent._recent_signal_actions["TS001"] = {'timestamp': datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) - timedelta(seconds=10), 'reason':'demo'}
    agent._recent_signal_actions["TS002"] = {'timestamp': datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) - timedelta(seconds=10), 'reason':'demo'}
    await run_action_and_kpi_cycles(current_sim_time_str, "user_hist_ts004", "user_kpi_ts004",
                                    "TS004", {"local_congestion_level": "MEDIUM", "flow_rate_absolute": 400}) # Neutral score

    logger.info(f"MAIN_EXAMPLE: Effectiveness Memory after History Building: {json.dumps(agent.action_effectiveness_memory, indent=2)}")

    # --- Cycle Group 2: Demonstrate Epsilon-Greedy (Exploration & Exploitation) ---
    logger.info("--- MAIN_EXAMPLE: Cycle Group 2 - Demonstrating Epsilon-Greedy ---")
    agent.exploration_epsilon = 0.5
    logger.info(f"MAIN_EXAMPLE: Set exploration_epsilon to {agent.exploration_epsilon}")

    # Signals that might be chosen by congestion logic
    congestion_candidate_ids = ["TS001", "TS002", "TS004"]

    for i in range(6): # Run 6 cycles for demonstration
        cycle_num = i + 1
        logger.info(f"--- MAIN_EXAMPLE: Epsilon-Greedy Cycle {cycle_num} ---")
        # Ensure all 3 are viable candidates for general congestion logic
        reset_mock_traffic_signals_for_congestion_demo(SignalPhaseEnum.RED)
        agent._recent_signal_actions.clear() # Clear cooldowns for this demo to ensure all are candidates

        logger.info("Current scores before cycle:")
        for sig_id in congestion_candidate_ids:
            score_key = f"SET_SIGNAL_GREEN_CONGESTION:{sig_id}"
            avg_score = sum(agent.action_effectiveness_memory.get(score_key, [0])) / len(agent.action_effectiveness_memory.get(score_key, [1]))
            logger.info(f"  {sig_id}: Avg Score = {avg_score:.2f} (History: {agent.action_effectiveness_memory.get(score_key, [])})")

        # Determine which signal is likely to be chosen to set its post-KPIs
        # This is a simplification; in reality, the agent makes the choice.
        # For demo, we'll just give all chosen signals a 'MEDIUM' outcome.
        # The key is to observe the 'selection_method'.

        action_time_str = current_sim_time_str
        action_user_id = f"user_egreedy_action_{cycle_num}"
        kpi_user_id = f"user_egreedy_kpi_{cycle_num}"

        # Run action cycle (HIGH congestion)
        logger.info(f"MAIN_EXAMPLE: Running ACTION cycle for E-Greedy {cycle_num} at {action_time_str}")
        await main_example_run_with_mock_time(
            action_time_str, action_user_id, agent, analytics_mock,
            kpis={"overall_congestion_level": "HIGH"}
        )

        chosen_signal_for_kpi_config = None
        if agent.pending_kpi_collection:
            last_pending_item = agent.pending_kpi_collection[-1]
            if last_pending_item['action_type'] == "SET_SIGNAL_GREEN_CONGESTION":
                chosen_signal_for_kpi_config = last_pending_item['target_ids'][0]
                selection_method = last_pending_item['action_parameters'].get('selection_method', 'UNKNOWN_METHOD')
                chosen_signal_score = last_pending_item['pre_action_context_kpis'].get('chosen_candidate_avg_score', 'N/A')
                logger.info(f"MAIN_EXAMPLE: Cycle {cycle_num} action: {selection_method} chose {chosen_signal_for_kpi_config} (score {chosen_signal_score:.2f})")

                # Configure this chosen signal to produce a 'MEDIUM' outcome for simplicity
                analytics_mock.configure_post_action_kpis(chosen_signal_for_kpi_config, "get_signal_post_action_kpis",
                                                          {"local_congestion_level": "MEDIUM", "flow_rate_absolute": 500})
                logger.info(f"MAIN_EXAMPLE: Configured post-KPIs for chosen signal {chosen_signal_for_kpi_config} to yield MEDIUM score.")
        else:
            logger.info(f"MAIN_EXAMPLE: Cycle {cycle_num}: No general congestion action taken.")


        # Run KPI processing cycle
        kpi_collection_time = datetime.fromisoformat(action_time_str.replace("Z","+00:00")) + timedelta(seconds=kpi_collection_delay + 10) # Ensure enough delay
        kpi_collection_time_str = kpi_collection_time.isoformat().replace("+00:00", "Z")
        logger.info(f"MAIN_EXAMPLE: Running KPI PROCESSING cycle for E-Greedy {cycle_num} at {kpi_collection_time_str}")
        await main_example_run_with_mock_time(
            kpi_collection_time_str, kpi_user_id, agent, analytics_mock,
            kpis={"overall_congestion_level": "LOW"} # Low congestion for KPI cycle
        )
        current_sim_time_str = (kpi_collection_time + timedelta(minutes=2)).isoformat().replace("+00:00", "Z") # Advance time for next action

        logger.info(f"MAIN_EXAMPLE: Effectiveness Memory after E-Greedy Cycle {cycle_num}: {json.dumps(agent.action_effectiveness_memory, indent=2)}")
        if not agent.pending_kpi_collection and not chosen_signal_for_kpi_config : # if no action was taken, break early or log
             logger.info(f"MAIN_EXAMPLE: E-Greedy Cycle {cycle_num} - No action was taken, check setup if this is unexpected.")


    # Restore original epsilon and RNG state
    agent.exploration_epsilon = original_epsilon
    agent.rng.setstate(original_rng_state)
    logger.info(f"MAIN_EXAMPLE: Restored exploration_epsilon to {agent.exploration_epsilon}. RNG state restored.")
    logger.info("--- MAIN_EXAMPLE: Epsilon-Greedy Demonstration Completed ---")

    # Original Scenario: Green Wave, then KPI collection, check score
    logger.info("--- MainExample Refined Scoring: Cycle 1 (Trigger Green Wave 'main_st_ns_wave') ---")
    corridor_to_test = "main_st_ns_wave"
    action_time = "2023-01-01T08:00:00Z"
    # Configure specific pre-action KPIs for this corridor
    analytics_mock.configure_pre_action_kpis(corridor_to_test, "get_corridor_current_kpis", {"avg_travel_time_seconds": 190, "throughput_vph": 450})

    kpi_settings = {GREEN_WAVE_CORRIDOR_CONFIGS[corridor_to_test]["demand_kpi_trigger"]:"HIGH"}
    await main_example_run_with_mock_time(action_time, "user_score_test_gw_action", agent, analytics_mock, kpi_settings)

    assert len(agent.pending_kpi_collection) == 1
    pending_item = agent.pending_kpi_collection[0]
    logger.info(f"Pending item pre_action_kpis: {json.dumps(pending_item['pre_action_context_kpis'], default=str, indent=2)}")
    assert pending_item['pre_action_context_kpis'].get("avg_travel_time_seconds") == 190 # From pre-config

    # Configure specific post-action KPIs for this corridor
    analytics_mock.configure_post_action_kpis(corridor_to_test, "get_corridor_post_action_kpis", {"corridor_avg_travel_time_seconds": 95, "corridor_throughput_vph": 880})

    kpi_collection_time = (datetime.fromisoformat(action_time.replace("Z","+00:00")) +
                           timedelta(seconds=ACTION_KPI_CONFIG["GREEN_WAVE_ACTIVATION"]["delay_seconds"] + 30)).isoformat().replace("+00:00","Z")

    logger.info(f"--- MainExample Refined Scoring: Cycle 2 (KPI Collection for '{corridor_to_test}') ---")
    await main_example_run_with_mock_time(kpi_collection_time, "user_score_test_gw_collect", agent, analytics_mock, {})

    assert len(agent.action_performance_logs) == 1
    final_log = agent.action_performance_logs[0]
    logger.info(f"Final ActionPerformanceLog: {final_log.model_dump_json(indent=2, default=str)}")
    assert final_log.effectiveness_score is not None
    assert final_log.effectiveness_metrics_used.get("pre_gw_avg_travel_time") == 190
    assert final_log.effectiveness_metrics_used.get("gw_post_avg_travel_time") == 95
    # Example: score should be high for good improvement (0.5 for TT + 0.5 for TP = 1.0 based on current _score_green_wave_efficiency)
    assert final_log.effectiveness_score > 0.5

    if os.path.exists(EFFECTIVENESS_MEMORY_FILEPATH): os.remove(EFFECTIVENESS_MEMORY_FILEPATH)
    logger.info("--- AgentCore main_example for Refined Scoring completed ---")

if __name__ == "__main__":
    # asyncio.run(main_example()) # Original main_example, commented out.
    # To run the new example:
    # asyncio.run(main_example_traffic_integration())
    logger.info("AgentCore module defined. Original main_example() commented out. New main_example_traffic_integration() is available.")

[end of backend/app/core/agent_core.py]
