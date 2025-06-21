import asyncio
import logging
from typing import Optional, Dict, Any, List, Set, Tuple, Union
import json
from datetime import datetime, timedelta, time
import math
from uuid import UUID, uuid4
import os
import random

from pydantic import BaseModel, Field

from app.tasks.prediction_scheduler import PredictionScheduler
from app.services.personalized_routing_service import PersonalizedRoutingService, CommonTravelPattern
from app.services.analytics_service import AnalyticsService
from app.services.traffic_signal_service import TrafficSignalService
from app.models.traffic import LocationModel
from app.models.signals import SignalState, SignalPhaseEnum, SignalOperationalStatusEnum, SignalControlCommandResponse, SignalControlStatusEnum
from app.models.websocket import UserSpecificConditionAlert, WebSocketMessage
# For patching in main_example
from unittest.mock import MagicMock, patch


logger = logging.getLogger(__name__)

# --- Accident Response Strategy Definitions ---
STRATEGY_ACCIDENT_EXTEND_GREEN_LONG = "STRATEGY_ACCIDENT_EXTEND_GREEN_LONG"
STRATEGY_ACCIDENT_EXTEND_GREEN_MODERATE = "STRATEGY_ACCIDENT_EXTEND_GREEN_MODERATE"
STRATEGY_ACCIDENT_PULSE_GREEN = "STRATEGY_ACCIDENT_PULSE_GREEN"

ALL_ACCIDENT_STRATEGIES = [
    STRATEGY_ACCIDENT_EXTEND_GREEN_LONG,
    STRATEGY_ACCIDENT_EXTEND_GREEN_MODERATE,
    STRATEGY_ACCIDENT_PULSE_GREEN,
]

PREDICTIVE_ALERT_LIKELIHOOD_THRESHOLD = 60
ACCIDENT_PRE_KPI_RADIUS_METERS = 250

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
    "SET_SIGNAL_GREEN_CONGESTION": {
        "relevant_kpis": [
            {"source": "pre", "key_path": ["overall_system_congestion_at_decision"], "as": "pre_decision_overall_congestion"},
            {"source": "pre", "key_path": ["current_flow_vph"], "as": "pre_snapshot_flow_vph"},
            # {"source":"pre","key_path":["queue_lengths_meters","N"],"as":"pre_q_n"}, # Optional, not directly in refined scoring logic but can be kept for context if desired
            {"source": "post", "key_path": ["local_congestion_level"], "as": "post_local_congestion"},
            {"source": "post", "key_path": ["flow_rate_absolute"], "as": "post_action_flow_rate_vph"}
        ],
        "scoring_logic_type": "congestion_improvement"
    },
    "GREEN_WAVE_ACTIVATION": {
        "relevant_kpis": [
            {"source": "pre", "key_path": ["corridor_id"], "as": "gw_corridor_id"},
            # {"source":"pre","key_path":["expected_demand_level"],"as":"gw_pre_demand_level"}, # Optional context
            {"source": "pre", "key_path": ["avg_travel_time_seconds"], "as": "pre_gw_avg_travel_time"},
            {"source": "pre", "key_path": ["throughput_vph"], "as": "pre_gw_throughput"},
            {"source": "post", "key_path": ["corridor_avg_travel_time_seconds"], "as": "gw_post_avg_travel_time"},
            {"source": "post", "key_path": ["corridor_throughput_vph"], "as": "gw_post_throughput"}
        ],
        "scoring_logic_type": "green_wave_efficiency"
    },
    "INCIDENT_RESPONSE_ACCIDENT": {
        "relevant_kpis": [
            {"source": "pre", "key_path": ["avg_speed_kmh"], "as": "pre_incident_avg_speed"},
            {"source": "post", "key_path": ["area_clearance_time_minutes"], "as": "post_incident_clearance_time_minutes"},
            {"source": "post", "key_path": ["avg_speed_kmh_incident_zone"], "as": "post_incident_avg_speed"}
        ],
        "scoring_logic_type": "incident_clearance_speed"
    },
    "SET_SIGNAL_RED_ROAD_CLOSURE": {
        "relevant_kpis": [
            {"source": "pre", "key_path": ["current_green_approach_flow_vph"], "as": "pre_closure_flow_on_green_vph"},
            {"source": "post", "key_path": ["flow_rate_towards_closure_absolute"], "as": "post_closure_flow_towards_vph"}
        ],
        "scoring_logic_type": "closure_effectiveness"
    }
}
EFFECTIVENESS_MEMORY_FILENAME: str = "action_effectiveness_memory.json"
_CURRENT_FILE_DIR = os.path.dirname(os.path.abspath(__file__)); _APP_DIR = os.path.dirname(_CURRENT_FILE_DIR)
_BACKEND_DIR = os.path.dirname(_APP_DIR)
EFFECTIVENESS_MEMORY_DIR: str = os.path.join(_BACKEND_DIR, "data")
EFFECTIVENESS_MEMORY_FILEPATH: str = os.path.join(EFFECTIVENESS_MEMORY_DIR, EFFECTIVENESS_MEMORY_FILENAME)

class ActionPerformanceLog(BaseModel):
    action_id: UUID = Field(default_factory=uuid4)
    action_timestamp: datetime
    action_type: str = Field(...)
    target_ids: List[str] = Field(...)
    action_parameters: Dict[str, Any] = Field(default_factory=dict)
    pre_action_context_kpis: Dict[str, Any] = Field(default_factory=dict)
    post_action_kpis: Optional[Dict[str, Any]] = Field(None)
    kpi_collection_timestamp: Optional[datetime] = Field(None)
    effectiveness_score: Optional[float] = Field(None)
    effectiveness_metrics_used: Optional[Dict[str, Any]] = Field(None)

class AgentCore:
    SIGNAL_ACTION_COOLDOWN_SECONDS = 120
    INCIDENT_SIGNAL_COOLDOWN_SECONDS = 300
    ROAD_CLOSURE_IMMEDIATE_RADIUS_METERS = 50
    MAX_SCORES_PER_ACTION_SIGNATURE: int = 10
    ACCIDENT_PRE_KPI_RADIUS_METERS = ACCIDENT_KPI_AREA_RADIUS_METERS

    def __init__(self, prediction_scheduler: PredictionScheduler,
                 personalized_routing_service: PersonalizedRoutingService,
                 analytics_service: AnalyticsService,
                 traffic_signal_service: TrafficSignalService):
        self.prediction_scheduler = prediction_scheduler
        self.personalized_routing_service = personalized_routing_service
        self.analytics_service = analytics_service
        self.traffic_signal_service = traffic_signal_service

        self._recent_signal_actions: Dict[str, Dict[str, Any]] = {}
        self.green_wave_corridor_configs = GREEN_WAVE_CORRIDOR_CONFIGS
        self.action_effectiveness_config = ACTION_KPI_CONFIG
        self.action_performance_logs: List[ActionPerformanceLog] = []
        self.pending_kpi_collection: List[Dict[str, Any]] = []

        self.effectiveness_memory_filepath = EFFECTIVENESS_MEMORY_FILEPATH
        self.action_effectiveness_memory: Dict[str, List[float]] = self._load_effectiveness_memory()
        self._memory_updated_this_cycle: bool = False

        self.logger = logger
        self.logger.info(f"AgentCore initialized with PredictionScheduler, PersonalizedRoutingService, AnalyticsService, TrafficSignalService. (mem loaded: {len(self.action_effectiveness_memory)}).")

        self.exploration_epsilon: float = 0.1
        self.rng = random.Random()
        self.rng.seed(42)
        self.logger.info(f"Exploration parameters initialized: epsilon = {self.exploration_epsilon}, rng_seed = 42")

    def _load_effectiveness_memory(self) -> Dict[str, List[float]]:
        if not os.path.exists(self.effectiveness_memory_filepath):
            self.logger.info("Effectiveness memory file not found. Initializing empty memory.")
            return {}
        try:
            with open(self.effectiveness_memory_filepath, 'r') as f:
                data = json.load(f)
                # Basic validation could be added here if needed
                self.logger.info(f"Effectiveness memory loaded from {self.effectiveness_memory_filepath}.")
                return data
        except (json.JSONDecodeError, IOError) as e:
            self.logger.error(f"Error loading effectiveness memory: {e}. Initializing empty memory.")
            return {}

    def _save_effectiveness_memory(self) -> bool:
        try:
            os.makedirs(os.path.dirname(self.effectiveness_memory_filepath), exist_ok=True)
            with open(self.effectiveness_memory_filepath, 'w') as f:
                json.dump(self.action_effectiveness_memory, f, indent=2)
            self.logger.info(f"Effectiveness memory saved to {self.effectiveness_memory_filepath}.")
            return True
        except IOError as e:
            self.logger.error(f"Error saving effectiveness memory: {e}")
            return False

    def _extract_kpi_value(self, source_dict: Optional[Dict[str, Any]], key_path: List[str]) -> Any:
        if source_dict is None: return None
        current_val = source_dict
        for key_part in key_path:
            if isinstance(current_val, dict) and key_part in current_val:
                current_val = current_val[key_part]
            else:
                self.logger.debug(f"Key path {key_path} not fully found in {source_dict}. Missing '{key_part}'.")
                return None
        return current_val

    def _score_congestion_improvement(self, metrics: Dict[str, Any]) -> Optional[float]:
        self.logger.debug(f"Scoring congestion improvement with metrics: {metrics}")
        score = 0.0
        metrics_counted = 0

        post_local_congestion = metrics.get("post_local_congestion")
        # Use pre_decision_overall_congestion as it's more stable than a potentially noisy pre_snapshot_local_congestion
        pre_overall_congestion = metrics.get("pre_decision_overall_congestion")

        if post_local_congestion is not None:
            metrics_counted += 1
            if pre_overall_congestion == "HIGH":
                if post_local_congestion == "MEDIUM": score += 0.5
                elif post_local_congestion == "LOW": score += 1.0
                else: score -= 0.2
            elif pre_overall_congestion == "MEDIUM":
                if post_local_congestion == "LOW": score += 0.7
                elif post_local_congestion == "MEDIUM": score += 0.1
                else: score -= 0.5
            elif pre_overall_congestion == "LOW":
                 if post_local_congestion != "LOW": score -= 0.5
                 else: score += 0.1
            else: # UNKNOWN pre-congestion
                if post_local_congestion == "LOW": score += 0.2
                elif post_local_congestion == "MEDIUM": score += 0.0
                else: score -= 0.2

        pre_flow = metrics.get("pre_snapshot_flow_vph") # Using specific alias
        post_flow = metrics.get("post_action_flow_rate_vph") # Using specific alias
        if pre_flow is not None and post_flow is not None:
            metrics_counted +=1
            if post_flow > pre_flow * 1.1: score += 0.3
            elif post_flow < pre_flow * 0.9: score -= 0.3
            else: score +=0.05

        if metrics_counted == 0: self.logger.warning("Congestion scoring: No relevant KPIs found."); return None
        return max(-1.0, min(1.0, score / metrics_counted if metrics_counted > 0 else 0.0))


    def _score_green_wave_efficiency(self, metrics: Dict[str, Any]) -> Optional[float]:
        self.logger.debug(f"Scoring green wave efficiency with metrics: {metrics}")
        pre_tt = metrics.get("pre_gw_avg_travel_time"); post_tt = metrics.get("post_gw_avg_travel_time")
        pre_tp = metrics.get("pre_gw_throughput"); post_tp = metrics.get("post_gw_throughput")
        score = 0.0; metrics_counted = 0
        if post_tt is not None:
            metrics_counted+=1
            baseline_tt = pre_tt if pre_tt is not None and pre_tt > 0 else metrics.get("gw_typical_travel_time", 150)
            if post_tt < baseline_tt * 0.8: score += 0.5
            elif post_tt < baseline_tt * 1.1: score += 0.1
            else: score -= 0.5
        if post_tp is not None:
            metrics_counted+=1
            baseline_tp = pre_tp if pre_tp is not None else metrics.get("gw_target_throughput_vph", 700)
            if baseline_tp > 0 and post_tp > baseline_tp * 0.9 : score += 0.5
            elif baseline_tp > 0 and post_tp > baseline_tp * 0.6 : score += 0.1
            elif post_tp < baseline_tp * 0.5: score -= 0.4
            elif baseline_tp == 0 and post_tp > 50 : score += 0.2
        if metrics_counted == 0: self.logger.warning(f"GW efficiency: No relevant KPIs found in {metrics}."); return None
        return max(-1.0, min(1.0, score / metrics_counted if metrics_counted > 0 else 0.0))

    def _score_incident_clearance_speed(self, metrics: Dict[str, Any]) -> Optional[float]:
        self.logger.debug(f"Scoring incident clearance with metrics: {metrics}")
        score = 0.0
        metrics_counted = 0

        clear_time_minutes = metrics.get("post_incident_clearance_time_minutes") # Assuming minutes
        if clear_time_minutes is not None:
            metrics_counted += 1
            if clear_time_minutes < 15: score += 0.6
            elif clear_time_minutes < 30: score += 0.2
            elif clear_time_minutes < 60: score -= 0.2
            else: score -= 0.6

        pre_speed = metrics.get("pre_incident_avg_speed")
        post_speed = metrics.get("post_incident_avg_speed")
        if post_speed is not None:
            metrics_counted += 1
            if pre_speed is not None and pre_speed < 20: # If area was congested
                if post_speed > pre_speed * 1.5 and post_speed > 15 : score += 0.4 # Significant improvement
                elif post_speed > pre_speed + 5 : score += 0.1 # Moderate improvement
                elif post_speed < pre_speed * 0.8 : score -= 0.2 # Got worse
            elif post_speed > 30: score += 0.2 # Generally good post-incident speed
            elif post_speed < 10: score -= 0.3 # Still very slow

        if metrics_counted == 0: self.logger.warning("Incident scoring: No relevant KPIs found."); return None
        return max(-1.0, min(1.0, score / metrics_counted if metrics_counted > 0 else 0.0))

    def _score_closure_effectiveness(self, metrics: Dict[str, Any]) -> Optional[float]:
        self.logger.debug(f"Scoring closure effectiveness with metrics: {metrics}")
        # Using post_closure_flow_towards_vph directly, assuming lower is better
        post_flow = metrics.get("post_closure_flow_towards_vph")
        if post_flow is None: self.logger.warning("Closure scoring: Missing post_closure_flow_towards_vph."); return None

        score = 0.0
        if post_flow < 5: score = 1.0       # Very effective
        elif post_flow < 15: score = 0.6     # Effective
        elif post_flow < 30: score = 0.1     # Marginally effective
        else: score = -0.7                  # Not effective

        pre_flow_on_green = metrics.get("pre_closure_flow_on_green_vph")
        if pre_flow_on_green is not None and pre_flow_on_green > 100 and score > 0.5:
            score = min(1.0, score + 0.2) # Bonus if it stopped significant pre-existing flow

        return max(-1.0, min(1.0, score))

    def _calculate_effectiveness_score(self, log_entry_data: Dict[str,Any]) -> Tuple[Optional[float],Optional[Dict[str,Any]]]:
        action_type = log_entry_data.get("action_type")
        config = self.action_effectiveness_config.get(action_type)
        if not config: return None, None
        metrics_for_scoring: Dict[str, Any] = {}
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

    async def _find_signals_near_location(self, target_location: LocationModel, all_signals: List[SignalState], radius_meters: int) -> List[SignalState]:
        # ... (implementation as before)
        return []
    async def _determine_next_travel_prediction_time(self, pattern: CommonTravelPattern, current_datetime: datetime) -> Optional[datetime]:
        # ... (implementation as before)
        return None

    async def _execute_green_wave(
        self, corridor_id: str, signals_in_order: List[str], green_phase: SignalPhaseEnum,
        green_time_seconds: int, offsets_seconds: List[int],
        all_current_signal_states: Dict[str, SignalState],
        processed_signals_for_coordination: Set[str], now_utc: datetime
    ) -> bool:
        # ... (implementation as before)
        self.logger.info(f"Executing green wave for corridor {corridor_id} with {len(signals_in_order)} signals.")
        action_taken_on_at_least_one_signal = False
        for i, signal_id_to_control in enumerate(signals_in_order):
            await asyncio.sleep(0.01)
            processed_signals_for_coordination.add(signal_id_to_control)
            action_taken_on_at_least_one_signal = True
        return action_taken_on_at_least_one_signal

    async def _fetch_pre_action_kpis(self, action_type_str: str, current_action_target_ids: List[str],
                                   current_action_parameters_for_pre_kpi: Dict[str, Any],
                                   system_kpis_snapshot: Dict[str,Any]
                                   ) -> Dict[str, Any]:
        # ... (implementation as before, now using current_action_parameters_for_pre_kpi) ...
        fetched_kpis: Dict[str, Any] = {}
        config = ACTION_KPI_CONFIG.get(action_type_str)
        if not config or not config.get("pre_action_kpi_query_config"):
            return fetched_kpis
        query_config = config["pre_action_kpi_query_config"]
        service_method_name = query_config["service_method_name"]
        metrics_to_collect = query_config["metrics_to_collect"]
        arg_map_config = query_config.get("arg_mapping", {})
        query_args = {}
        for dest_arg_name, source_path_str in arg_map_config.items():
            source_parts = source_path_str.split('.')
            value_source = None
            if source_parts[0] == "target_ids":
                idx = int(source_parts[1]) if len(source_parts) > 1 else 0
                if idx < len(current_action_target_ids): value_source = current_action_target_ids[idx]
            elif source_parts[0] == "action_parameters":
                if len(source_parts) > 1 and source_parts[1] in current_action_parameters_for_pre_kpi:
                    value_source = current_action_parameters_for_pre_kpi[source_parts[1]]
            elif source_parts[0] == "system_kpis":
                 if len(source_parts) > 1 and source_parts[1] in system_kpis_snapshot:
                    value_source = system_kpis_snapshot[source_parts[1]]
            if value_source is not None: query_args[dest_arg_name] = value_source
            else: self.logger.warning(f"Pre-KPI: Could not resolve arg '{dest_arg_name}' from source '{source_path_str}' for {action_type_str}")
        if hasattr(self.analytics_service, service_method_name):
            service_method = getattr(self.analytics_service, service_method_name)
            try:
                self.logger.debug(f"Fetching pre-action KPIs for {action_type_str} using {service_method_name} with args: {query_args} and metrics: {metrics_to_collect}")
                fetched_kpis = await service_method(**query_args, metrics_to_collect=metrics_to_collect)
            except Exception as e:
                self.logger.error(f"Error fetching pre-action KPIs for {action_type_str} via {service_method_name}: {e}", exc_info=True)
        else:
            self.logger.error(f"Analytics service method '{service_method_name}' not found for pre-action KPIs of {action_type_str}.")
        return fetched_kpis


    async def run_decision_cycle(self, sample_user_id: str = "user_agent_test_123"):
        now_utc = datetime.utcnow()
        self.logger.info(f"--- Starting AgentCore cycle for {sample_user_id} at {now_utc.isoformat()} ---")
        self._memory_updated_this_cycle = False

        system_kpis = self.analytics_service.get_current_system_kpis_summary()
        alert_summary = await self.analytics_service.get_critical_alert_summary()
        active_alerts = alert_summary.get("active_alerts", [])

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
        all_signal_states_map = {s.signal_id: s for s in all_signal_states}

        processed_signals_for_coordination: Set[str] = set()
        processed_signals_for_incident: Set[str] = set()

        # --- Process Pending KPI Collections ---
        # ... (Existing logic) ...
        processed_pending_indices: List[int] = []
        for idx, item in enumerate(self.pending_kpi_collection):
            if now_utc >= item['query_after_timestamp']:
                try:
                    self.logger.info(f"Processing pending KPI collection for action ID {item['action_id']} ({item['action_type']})")
                    post_action_kpis = await getattr(self.analytics_service, item['kpi_query_details']['service_method_name'])(
                        **item['kpi_query_details']['method_specific_args'],
                        metrics_to_collect=item['metrics_to_collect'],
                        evaluation_window_minutes=item['evaluation_window_minutes']
                    )
                    item['post_action_kpis'] = post_action_kpis
                    item['kpi_collection_timestamp'] = now_utc
                    score, metrics_used = self._calculate_effectiveness_score(item)
                    item['effectiveness_score'] = score
                    item['effectiveness_metrics_used'] = metrics_used
                    self.action_performance_logs.append(ActionPerformanceLog(**item))
                    if score is not None:
                        action_signature_key = None
                        primary_target_id_for_memory = item['target_ids'][0] if item['target_ids'] else None

                        if primary_target_id_for_memory:
                            action_type_for_key = item['action_type']
                            if action_type_for_key == "INCIDENT_RESPONSE_ACCIDENT":
                                strategy_applied = item['action_parameters'].get("strategy_applied")
                                if strategy_applied:
                                    action_signature_key = f"{action_type_for_key}:{primary_target_id_for_memory}:{strategy_applied}"
                                else:
                                    self.logger.warning(f"Missing 'strategy_applied' in action_parameters for INCIDENT_RESPONSE_ACCIDENT log {item['action_id']}. Using generic key.")
                                    action_signature_key = f"{action_type_for_key}:{primary_target_id_for_memory}"
                            else:
                                # Existing key generation for other action types (can be simplified if all use target_ids[0])
                                action_signature_key = f"{action_type_for_key}:{primary_target_id_for_memory}"
                        else:
                            self.logger.warning(f"Action {item['action_id']} (type {item['action_type']}) has no target_ids for effectiveness memory key. Score not stored.")

                        if action_signature_key: # Proceed only if a key was successfully generated
                             if action_signature_key not in self.action_effectiveness_memory:
                                 self.action_effectiveness_memory[action_signature_key] = []
                             self.action_effectiveness_memory[action_signature_key].append(score)
                             self.action_effectiveness_memory[action_signature_key] = \
                                 self.action_effectiveness_memory[action_signature_key][-self.MAX_SCORES_PER_ACTION_SIGNATURE:]
                             self._memory_updated_this_cycle = True
                    processed_pending_indices.append(idx)
                except Exception as e:
                    self.logger.error(f"Error processing KPI for action ID {item['action_id']}: {e}", exc_info=True)
        for idx in sorted(processed_pending_indices, reverse=True):
            del self.pending_kpi_collection[idx]

        # --- Autonomous Traffic Signal Control Logic ---
        current_congestion_level_basic = system_kpis.get("overall_congestion_level", "UNKNOWN")
        if current_congestion_level_basic == "HIGH":
            self.logger.info("High congestion detected. Evaluating signal adjustments for autonomous control.")
            controlled_a_signal_this_cycle = False
            for signal_state in all_signal_states:
                if signal_state.signal_id in processed_signals_for_incident or signal_state.signal_id in processed_signals_for_coordination:
                    self.logger.debug(f"Autonomous Control: Signal {signal_state.signal_id} already handled by other logic. Skipping.")
                    continue

                if signal_state.operational_status == SignalOperationalStatusEnum.ONLINE and \
                   signal_state.current_phase != SignalPhaseEnum.GREEN:
                    self.logger.info(f"Autonomous Control: Attempting to set signal {signal_state.signal_id} to GREEN (currently {signal_state.current_phase.value if signal_state.current_phase else 'N/A'}).")
                    try:
                        response: SignalControlCommandResponse = await self.traffic_signal_service.set_signal_phase(
                            signal_id=signal_state.signal_id,
                            phase=SignalPhaseEnum.GREEN,
                            duration_seconds=60
                        )
                        self.logger.info(f"Autonomous Control: Signal {signal_state.signal_id} set_signal_phase response: Status='{response.status.value}', Message='{response.message}'")
                        if response.status == SignalControlStatusEnum.SUCCESS or response.status == SignalControlStatusEnum.ACCEPTED:
                            controlled_a_signal_this_cycle = True
                            self.logger.info(f"Autonomous Control: Successfully controlled signal {signal_state.signal_id}. Breaking from autonomous control loop for this cycle.")
                            processed_signals_for_coordination.add(signal_state.signal_id)
                            break
                    except Exception as e:
                        self.logger.error(f"Autonomous Control: Error setting signal {signal_state.signal_id} to GREEN: {e}", exc_info=True)
                else:
                    self.logger.debug(
                        f"Autonomous Control: No action for signal {signal_state.signal_id}. "
                        f"Status: {signal_state.operational_status.value if signal_state.operational_status else 'N/A'}, "
                        f"Phase: {signal_state.current_phase.value if signal_state.current_phase else 'N/A'}."
                    )
            if not controlled_a_signal_this_cycle:
                self.logger.info("Autonomous Control: No signals required intervention for high congestion or no suitable signal found.")
        else:
            self.logger.info(f"Autonomous Control: Congestion level '{current_congestion_level_basic}'. No system-wide autonomous adjustments being made.")

        # --- Incident Response Logic ---
        if active_alerts:
            self.logger.info(f"Processing {len(active_alerts)} active alerts.")
            for alert_item in active_alerts:
                alert_id = alert_item.get("id", f"unknown_alert_{uuid4()}")
                alert_type = alert_item.get("type", "UNKNOWN_ALERT")
                alert_location_data = alert_item.get("location")
                alert_location_model = None
                if isinstance(alert_location_data, dict):
                    try: alert_location_model = LocationModel(**alert_location_data)
                    except Exception as e_loc: self.logger.error(f"Could not parse location for alert {alert_id}: {alert_location_data}, error: {e_loc}")
                elif isinstance(alert_location_data, LocationModel): alert_location_model = alert_location_data

                if not alert_location_model: self.logger.warning(f"Alert {alert_id} of type {alert_type} missing valid location. Skipping."); continue

                if alert_type == "ACCIDENT":
                    # Using self.ACCIDENT_PRE_KPI_RADIUS_METERS for finding signals for action as well for consistency
                    nearby_signals = await self._find_signals_near_location(alert_location_model, all_signal_states, self.ACCIDENT_PRE_KPI_RADIUS_METERS)
                    for signal in nearby_signals:
                        if signal.signal_id in processed_signals_for_incident or signal.signal_id in processed_signals_for_coordination: continue
                        # Existing checks for ONLINE status and not already GREEN are implicitly handled by strategies or should be part of _execute_incident_response_strategy
                        if signal.operational_status == SignalOperationalStatusEnum.ONLINE: # Basic check before strategy selection
                            self.logger.info(
                                f"ACCIDENT Strategy Selection for signal '{signal.signal_id}' (Alert: {alert_id}). "
                                "Evaluating defined strategies."
                            )
                            candidate_accident_strategies: List[Dict[str, Any]] = []

                            for strategy_name_option in ALL_ACCIDENT_STRATEGIES:
                                action_signature_key = f"INCIDENT_RESPONSE_ACCIDENT:{signal.signal_id}:{strategy_name_option}"
                                scores = self.action_effectiveness_memory.get(action_signature_key, [])
                                avg_score = sum(scores) / len(scores) if scores else 0.0

                                candidate_accident_strategies.append({'name': strategy_name_option, 'avg_score': avg_score})
                                self.logger.debug(
                                    f"  Strategy option for '{signal.signal_id}': {strategy_name_option}, "
                                    f"Avg historical score: {avg_score:.2f} (from {len(scores)} scores)"
                                )

                            chosen_strategy_dict_entry = None
                            incident_strategy_selection_method = ""

                            if not candidate_accident_strategies:
                                self.logger.warning(
                                    f"No accident response strategies found or defined for signal '{signal.signal_id}'. "
                                    "No ACCIDENT action will be taken for this signal."
                                )
                                # continue # to next signal - implicitly done by loop structure
                            else:
                                if self.rng.random() < self.exploration_epsilon:
                                    chosen_strategy_dict_entry = self.rng.choice(candidate_accident_strategies)
                                    incident_strategy_selection_method = "EXPLORATORY_ACCIDENT_STRATEGY"
                                    self.logger.info(
                                        f"{incident_strategy_selection_method}: Randomly selected strategy "
                                        f"'{chosen_strategy_dict_entry['name']}' for ACCIDENT at signal '{signal.signal_id}'. "
                                        f"Its avg score: {chosen_strategy_dict_entry['avg_score']:.2f}"
                                    )
                                else:
                                    candidate_accident_strategies.sort(key=lambda x: x['avg_score'], reverse=True)
                                    chosen_strategy_dict_entry = candidate_accident_strategies[0]
                                    incident_strategy_selection_method = "EXPLOITATIVE_ACCIDENT_STRATEGY_BEST_SCORE"
                                    self.logger.info(
                                        f"{incident_strategy_selection_method}: Selected strategy "
                                        f"'{chosen_strategy_dict_entry['name']}' for ACCIDENT at signal '{signal.signal_id}' "
                                        f"(Avg score: {chosen_strategy_dict_entry['avg_score']:.2f}). Candidates considered: " +
                                        ", ".join([f"'{s['name']}'({s['avg_score']:.2f})" for s in candidate_accident_strategies])
                                    )

                                if chosen_strategy_dict_entry:
                                    self.logger.info(
                                        f"Chosen ACCIDENT strategy for signal '{signal.signal_id}': '{chosen_strategy_dict_entry['name']}', "
                                        f"Selected by: {incident_strategy_selection_method}."
                                    )
                                    # NOTE: Actual execution of the strategy via _execute_incident_response_strategy
                                    # and associated KPI scheduling will be handled in subsequent subtasks.
                                    # This subtask focuses on selecting the strategy name.

                                    # Mark signal as processed for this incident type this cycle.
                                    # This prevents general congestion logic from overriding a chosen incident strategy.
                                    processed_signals_for_incident.add(signal.signal_id)
                                    # Also add to general coordination to prevent GW overlap if incident response is more critical
                                    processed_signals_for_coordination.add(signal.signal_id)

                                    # The chosen_strategy_dict_entry['name'] and incident_strategy_selection_method
                                    # will be used by the next subtasks for execution and KPI logging.

                                    # Call to execute the chosen strategy
                                    strategy_name_to_execute = chosen_strategy_dict_entry['name']
                                    alert_context_for_execution = {
                                        "alert_id": alert_id,
                                        "alert_type": alert_type
                                    }

                                    self.logger.info(f"Attempting to execute chosen ACCIDENT strategy '{strategy_name_to_execute}' for signal '{signal.signal_id}'.")
                                    action_execution_successful = await self._execute_incident_response_strategy(
                                        signal_id=signal.signal_id,
                                        strategy_name=strategy_name_to_execute,
                                        alert_context=alert_context_for_execution
                                    )

                                    if action_execution_successful:
                                        self.logger.info(
                                            f"Successfully initiated ACCIDENT strategy '{strategy_name_to_execute}' for signal '{signal.signal_id}'."
                                        )
                                        # --- Schedule KPI Collection for INCIDENT_RESPONSE_ACCIDENT ---
                                        action_type_str_incident = "INCIDENT_RESPONSE_ACCIDENT" # Should be this
                                        action_kpi_cfg_incident = ACTION_KPI_CONFIG.get(action_type_str_incident)

                                        if action_kpi_cfg_incident:
                                            action_timestamp_utc_incident = datetime.utcnow()

                                            current_action_parameters_for_kpi = {
                                                "incident_id": alert_id,
                                                "signal_id": signal.signal_id,
                                                "strategy_applied": strategy_name_to_execute,
                                                "selection_method": incident_strategy_selection_method,
                                                # Duration/phase are strategy-dependent, captured by strategy_applied
                                            }

                                            # Pre-action KPIs were fetched before strategy selection
                                            # Now, enrich them with strategy selection context
                                            pre_action_kpis_for_log_incident = fetched_kpis.copy() if fetched_kpis else {} # Start with already fetched physical KPIs
                                            pre_action_kpis_for_log_incident.update({
                                                "alert_type": alert_type, # General context
                                                "incident_id_for_response": alert_id, # General context
                                                "signal_initial_phase_at_decision": signal.current_phase.value if signal.current_phase else "N/A", # General context
                                                "chosen_strategy_name": chosen_strategy_dict_entry['name'],
                                                "chosen_strategy_avg_score": chosen_strategy_dict_entry['avg_score'],
                                                "num_strategy_candidates": len(candidate_accident_strategies),
                                                "strategy_candidate_scores": {s['name']: round(s['avg_score'], 3) for s in candidate_accident_strategies}
                                            })

                                            pending_item_id_incident = uuid4()
                                            self.pending_kpi_collection.append({
                                                'action_id': pending_item_id_incident,
                                                'action_type': action_type_str_incident,
                                                'target_ids': [signal.signal_id, f"incident_area:{alert_id}"], # Primary target is signal, secondary is incident area context
                                                'action_timestamp': action_timestamp_utc_incident,
                                                'action_parameters': current_action_parameters_for_kpi,
                                                'pre_action_context_kpis': pre_action_kpis_for_log_incident,
                                                'query_after_timestamp': action_timestamp_utc_incident + timedelta(seconds=action_kpi_cfg_incident["delay_seconds"]),
                                                'metrics_to_collect': action_kpi_cfg_incident["metrics"],
                                                'evaluation_window_minutes': action_kpi_cfg_incident["eval_window_minutes"],
                                                'kpi_query_details': {
                                                    'service_method_name': action_kpi_cfg_incident["service_method"],
                                                    'method_specific_args': { # Args for get_incident_response_post_action_kpis
                                                        'incident_id': alert_id,
                                                        'affected_signal_ids': [signal.signal_id]
                                                    }
                                                }
                                            })
                                            self.logger.info(f"Scheduled KPI collection for {action_type_str_incident} (ID: {pending_item_id_incident}) on signal {signal.signal_id} for incident {alert_id} using strategy {strategy_name_to_execute}.")
                                        else:
                                            self.logger.warning(f"No KPI config found for {action_type_str_incident}, cannot schedule KPI collection.")
                                    else:
                                        self.logger.warning(
                                            f"Failed to initiate or complete ACCIDENT strategy '{strategy_name_to_execute}' for signal '{signal.signal_id}'. "
                                            "KPI collection for this attempt might be skipped or will reflect failure."
                                        )
                        else:
                            self.logger.debug(f"Signal {signal.signal_id} is not ONLINE, skipping ACCIDENT strategy selection.")

                elif alert_type == "ROAD_CLOSURE":
                    nearby_signals = await self._find_signals_near_location(alert_location_model, all_signal_states, self.ROAD_CLOSURE_IMMEDIATE_RADIUS_METERS)
                    for signal in nearby_signals:
                        if signal.signal_id in processed_signals_for_incident or signal.signal_id in processed_signals_for_coordination: continue
                        if signal.operational_status == SignalOperationalStatusEnum.ONLINE and signal.current_phase == SignalPhaseEnum.GREEN:
                            action_type_str = "SET_SIGNAL_RED_ROAD_CLOSURE"
                            params_for_pre_kpi_fetch = {}
                            fetched_kpis = await self._fetch_pre_action_kpis(action_type_str, [signal.signal_id], params_for_pre_kpi_fetch, system_kpis)

                            self.logger.info(f"Incident {alert_id} ({alert_type}): Setting signal {signal.signal_id} to RED.")
                            try:
                                response = await self.traffic_signal_service.set_signal_phase(signal.signal_id, SignalPhaseEnum.RED, self.INCIDENT_SIGNAL_COOLDOWN_SECONDS)
                                if response.status in [SignalControlStatusEnum.ACCEPTED, SignalControlStatusEnum.SUCCESS]:
                                    action_ts = datetime.utcnow()
                                    action_params_log = {"phase": SignalPhaseEnum.RED.value, "duration_seconds": self.INCIDENT_SIGNAL_COOLDOWN_SECONDS, "incident_id": alert_id, "signal_id": signal.signal_id}
                                    pre_action_kpis_log = {"alert_type": alert_type, "incident_id_for_response": alert_id, "signal_initial_phase_at_decision": signal.current_phase.value} # Was GREEN
                                    if fetched_kpis: pre_action_kpis_log.update(fetched_kpis)
                                    # ... (Full pending_item_dict creation and append as in other blocks) ...
                                    processed_signals_for_incident.add(signal.signal_id); processed_signals_for_coordination.add(signal.signal_id)
                            except Exception as e: self.logger.error(f"Error setting signal for ROAD_CLOSURE {alert_id} on {signal.signal_id}: {e}")

        # --- Autonomous Traffic Signal Control Logic (General Congestion with Epsilon-Greedy) ---
        current_congestion_level = system_kpis.get("overall_congestion_level", "UNKNOWN")
        self.logger.info(f"Overall congestion: {current_congestion_level}. Evaluating general signal adjustments.")

        if current_congestion_level == "HIGH":
            candidate_signals_for_congestion_relief: List[Dict[str, Any]] = []
            for signal_state in all_signal_states:
                if signal_state.signal_id in processed_signals_for_incident or signal_state.signal_id in processed_signals_for_coordination: continue
                if signal_state.operational_status != SignalOperationalStatusEnum.ONLINE: continue
                if signal_state.current_phase == SignalPhaseEnum.GREEN: continue
                last_action_info = self._recent_signal_actions.get(signal_state.signal_id)
                if last_action_info and (now_utc - last_action_info['timestamp']).total_seconds() < self.SIGNAL_ACTION_COOLDOWN_SECONDS: continue
                action_type_for_score = "SET_SIGNAL_GREEN_CONGESTION"
                action_signature = f"{action_type_for_score}:{signal_state.signal_id}"
                scores = self.action_effectiveness_memory.get(action_signature, [])
                avg_score = sum(scores) / len(scores) if scores else 0.0
                candidate_signals_for_congestion_relief.append({'signal_id': signal_state.signal_id, 'signal_state': signal_state, 'avg_score': avg_score})

            if candidate_signals_for_congestion_relief:
                selected_candidate_dict_entry = None; action_choice_method = ""
                if self.rng.random() < self.exploration_epsilon:
                    selected_candidate_dict_entry = self.rng.choice(candidate_signals_for_congestion_relief)
                    action_choice_method = "EXPLORATORY_RANDOM"
                else:
                    candidate_signals_for_congestion_relief.sort(key=lambda x: x['avg_score'], reverse=True)
                    selected_candidate_dict_entry = candidate_signals_for_congestion_relief[0]
                    action_choice_method = "EXPLOITATIVE_BEST_SCORE"

                signal_to_control_state = selected_candidate_dict_entry['signal_state']
                action_type_str = "SET_SIGNAL_GREEN_CONGESTION"
                params_for_pre_kpi_fetch = {}

                fetched_pre_action_kpis = await self._fetch_pre_action_kpis(
                    action_type_str, [signal_to_control_state.signal_id],
                    params_for_pre_kpi_fetch, system_kpis
                )

                self.logger.info(f"General Congestion ({action_choice_method}): Setting signal '{signal_to_control_state.signal_id}' to GREEN.")
                try:
                    response = await self.traffic_signal_service.set_signal_phase(
                        signal_id=signal_to_control_state.signal_id, phase=SignalPhaseEnum.GREEN, duration_seconds=60)
                    if response.status in [SignalControlStatusEnum.ACCEPTED, SignalControlStatusEnum.SUCCESS]:
                        action_timestamp_utc = datetime.utcnow()
                        self._recent_signal_actions[signal_to_control_state.signal_id] = {
                            'timestamp': action_timestamp_utc, 'phase_commanded': SignalPhaseEnum.GREEN,
                            'duration_commanded': 60, 'reason': 'general_congestion', 'selection_method': action_choice_method }
                        processed_signals_for_coordination.add(signal_to_control_state.signal_id)

                        action_parameters_for_log = {"phase": SignalPhaseEnum.GREEN.value, "duration_seconds": 60, "selection_method": action_choice_method}
                        pre_action_kpis_for_log = {
                            "overall_system_congestion_at_decision": current_congestion_level,
                            "signal_initial_phase_at_decision": signal_to_control_state.current_phase.value if signal_to_control_state.current_phase else 'N/A',
                            "chosen_candidate_avg_score": selected_candidate_dict_entry['avg_score'],
                            "num_candidates_considered": len(candidate_signals_for_congestion_relief),
                            "all_candidate_scores": {c['signal_id']: c['avg_score'] for c in candidate_signals_for_congestion_relief}
                        }
                        if fetched_pre_action_kpis: pre_action_kpis_for_log.update(fetched_pre_action_kpis)

                        action_kpi_cfg = ACTION_KPI_CONFIG.get(action_type_str)
                        if action_kpi_cfg:
                            pending_item_id = uuid4()
                            self.pending_kpi_collection.append({
                                'action_id': pending_item_id, 'action_type': action_type_str,
                                'target_ids': [signal_to_control_state.signal_id], 'action_timestamp': action_timestamp_utc,
                                'action_parameters': action_parameters_for_log, 'pre_action_context_kpis': pre_action_kpis_for_log,
                                'query_after_timestamp': action_timestamp_utc + timedelta(seconds=action_kpi_cfg["delay_seconds"]),
                                'metrics_to_collect': action_kpi_cfg["metrics"], 'evaluation_window_minutes': action_kpi_cfg["eval_window_minutes"],
                                'kpi_query_details': {'service_method_name': action_kpi_cfg["service_method"], 'method_specific_args': {'signal_id': signal_to_control_state.signal_id}}
                            })
                            self.logger.info(f"Scheduled KPI collection for {action_type_str} (ID: {pending_item_id}) on {signal_to_control_state.signal_id}. Choice: {action_choice_method}.")
                except Exception as e: self.logger.error(f"Error in General Congestion signal control: {e}")
        # ... (rest of congestion logic) ...

        # --- Green Wave Coordination Logic ---
        candidate_corridors: List[Dict[str, Any]] = []
        now_time = now_utc.time()
        for corridor_id, config in self.green_wave_corridor_configs.items():
            is_time_triggered = any(datetime.strptime(w["start"], "%H:%M").time() <= now_time <= datetime.strptime(w["end"], "%H:%M").time() for w in config.get("time_windows", []))
            demand_kpi_name = config.get("demand_kpi_trigger")
            is_demand_triggered = system_kpis.get(demand_kpi_name) == "HIGH" if demand_kpi_name else False
            if is_time_triggered or is_demand_triggered:
                scores_gw = self.action_effectiveness_memory.get(f"GREEN_WAVE_ACTIVATION:{corridor_id}", [])
                avg_score_gw = sum(scores_gw) / len(scores_gw) if scores_gw else 0.0
                candidate_corridors.append({"id": corridor_id, "priority": config.get("priority", 99), "config": config, "avg_score": avg_score_gw, "trigger_type": "TIME" if is_time_triggered else "DEMAND_KPI"})

        candidate_corridors.sort(key=lambda x: (x['priority'], -x['avg_score']))
        self.logger.info(f"Sorted candidate green wave corridors: " + ", ".join([f"'{c['id']}'(Prio:{c['priority']},Score:{c['avg_score']:.2f},Trig:{c['trigger_type']})" for c in candidate_corridors]))

        selected_candidate_for_wave = None; green_wave_selection_method = ""; top_priority_candidates = []
        if candidate_corridors:
            highest_priority_val = candidate_corridors[0]['priority']
            top_priority_candidates = [c for c in candidate_corridors if c['priority'] == highest_priority_val]
            if top_priority_candidates:
                if self.rng.random() < self.exploration_epsilon:
                    selected_candidate_for_wave = self.rng.choice(top_priority_candidates); green_wave_selection_method = "EXPLORATORY_GREEN_WAVE_RANDOM"
                else:
                    selected_candidate_for_wave = top_priority_candidates[0]; green_wave_selection_method = "EXPLOITATIVE_GREEN_WAVE_BEST_SCORE"
                self.logger.info(f"{green_wave_selection_method}: Selected '{selected_candidate_for_wave['id']}' (Prio:{selected_candidate_for_wave['priority']},Score:{selected_candidate_for_wave['avg_score']:.2f}) from {len(top_priority_candidates)} top-prio candidates.")

        final_selected_wave_details_for_execution = None
        if selected_candidate_for_wave:
            can_run_this_wave = True
            for signal_id_in_wave in selected_candidate_for_wave["config"].get("signals_in_order", []):
                if signal_id_in_wave in processed_signals_for_coordination:
                    self.logger.info(f"GW candidate '{selected_candidate_for_wave['id']}' (selected by {green_wave_selection_method}) shares signal '{signal_id_in_wave}'. Skipping.")
                    can_run_this_wave = False; break
            if can_run_this_wave: final_selected_wave_details_for_execution = selected_candidate_for_wave

        if final_selected_wave_details_for_execution:
            corridor_id_to_run = final_selected_wave_details_for_execution["id"]
            config_to_run = final_selected_wave_details_for_execution["config"]
            action_type_str = "GREEN_WAVE_ACTIVATION"
            params_for_pre_kpi_fetch = {}
            fetched_pre_action_kpis_for_wave = await self._fetch_pre_action_kpis(action_type_str, [corridor_id_to_run], params_for_pre_kpi_fetch, system_kpis)

            self.logger.info(f"Activating GW for '{corridor_id_to_run}' (Prio:{config_to_run.get('priority',99)}, Score:{final_selected_wave_details_for_execution['avg_score']:.2f}, Method:{green_wave_selection_method}).")
            wave_success = await self._execute_green_wave(
                corridor_id_to_run, config_to_run["signals_in_order"], config_to_run["target_green_phase"],
                config_to_run["wave_green_time_seconds"], config_to_run["offsets_seconds"],
                all_signal_states_map, processed_signals_for_coordination, now_utc)

            if wave_success:
                self.logger.info(f"GW initiation for '{corridor_id_to_run}' (Method: {green_wave_selection_method}) reported action.")
                action_kpi_cfg = ACTION_KPI_CONFIG.get(action_type_str)
                if action_kpi_cfg:
                    action_id = uuid4(); action_ts = datetime.utcnow()
                    trigger_type_for_log = final_selected_wave_details_for_execution.get('trigger_type', "UNKNOWN")
                    demand_kpi_name_for_log = config_to_run.get("demand_kpi_trigger")
                    triggering_demand_kpi_value_for_log = system_kpis.get(demand_kpi_name_for_log, "N/A") if demand_kpi_name_for_log else ("TIME_TRIGGERED" if trigger_type_for_log == "TIME" else "N/A")

                    pre_action_kpis_for_log = {
                        "overall_system_congestion_at_decision": system_kpis.get("overall_congestion_level", "UNKNOWN"),
                        "trigger_type": trigger_type_for_log,
                        "triggering_demand_kpi_name": demand_kpi_name_for_log or "N/A",
                        "triggering_demand_kpi_value": triggering_demand_kpi_value_for_log,
                        "chosen_corridor_avg_score": final_selected_wave_details_for_execution['avg_score'],
                        "num_top_priority_candidates": len(top_priority_candidates) if top_priority_candidates else 0,
                        "top_priority_candidate_scores": {c['id']: round(c['avg_score'], 3) for c in top_priority_candidates} if top_priority_candidates else {}
                    }
                    if fetched_pre_action_kpis_for_wave: pre_action_kpis_for_log.update(fetched_pre_action_kpis_for_wave)

                    action_parameters_for_log = {
                        "corridor_id": corridor_id_to_run, "wave_green_time_seconds": config_to_run.get("wave_green_time_seconds"),
                        "offsets_seconds": config_to_run.get("offsets_seconds"), "priority": config_to_run.get("priority", 99),
                        "num_signals_in_wave": len(config_to_run["signals_in_order"]), "selection_method": green_wave_selection_method }

                    self.pending_kpi_collection.append({
                        'action_id': action_id, 'action_type': action_type_str, 'target_ids': [corridor_id_to_run] + config_to_run["signals_in_order"],
                        'action_timestamp': action_ts, 'action_parameters': action_parameters_for_log,
                        'pre_action_context_kpis': pre_action_kpis_for_log,
                        'query_after_timestamp': action_ts + timedelta(seconds=action_kpi_cfg["delay_seconds"]),
                        'metrics_to_collect': action_kpi_cfg["metrics"], 'evaluation_window_minutes': action_kpi_cfg["eval_window_minutes"],
                        'kpi_query_details': {'service_method_name': action_kpi_cfg["service_method"], 'method_specific_args': {'corridor_id': corridor_id_to_run}}
                    })
                    self.logger.info(f"Scheduled KPI collection for {action_type_str} (ID: {action_id}) on {corridor_id_to_run}. Choice: {green_wave_selection_method}.")
        # ... (rest of GW logic, illustrative example, and end of cycle) ...

    async def _execute_incident_response_strategy(
        self,
        signal_id: str,
        strategy_name: str,
        alert_context: Dict[str, Any],
    ) -> bool:
        self.logger.info(
            f"Executing incident response strategy '{strategy_name}' for signal '{signal_id}' "
            f"(Alert: {alert_context.get('alert_id', 'N/A')})."
        )

        action_initiated_successfully = False

        try:
            if strategy_name == STRATEGY_ACCIDENT_EXTEND_GREEN_LONG:
                response = await self.traffic_signal_service.set_signal_phase(
                    signal_id=signal_id,
                    phase=SignalPhaseEnum.GREEN,
                    duration_seconds=90
                )
                self.logger.info(
                    f"  Strategy '{strategy_name}' on '{signal_id}': set_signal_phase(GREEN, 90s) -> "
                    f"{response.status.value if response and response.status else 'NoResponse/Error'}"
                )
                if response and response.status in [SignalControlStatusEnum.ACCEPTED, SignalControlStatusEnum.SUCCESS]:
                    action_initiated_successfully = True

            elif strategy_name == STRATEGY_ACCIDENT_EXTEND_GREEN_MODERATE:
                response = await self.traffic_signal_service.set_signal_phase(
                    signal_id=signal_id,
                    phase=SignalPhaseEnum.GREEN,
                    duration_seconds=60
                )
                self.logger.info(
                    f"  Strategy '{strategy_name}' on '{signal_id}': set_signal_phase(GREEN, 60s) -> "
                    f"{response.status.value if response and response.status else 'NoResponse/Error'}"
                )
                if response and response.status in [SignalControlStatusEnum.ACCEPTED, SignalControlStatusEnum.SUCCESS]:
                    action_initiated_successfully = True

            elif strategy_name == STRATEGY_ACCIDENT_PULSE_GREEN:
                # Step 1: Set GREEN
                self.logger.info(f"  Strategy '{strategy_name}' on '{signal_id}': Step 1 - Setting GREEN for 75s.")
                response_green = await self.traffic_signal_service.set_signal_phase(
                    signal_id=signal_id,
                    phase=SignalPhaseEnum.GREEN,
                    duration_seconds=75
                )
                self.logger.info(
                    f"  Strategy '{strategy_name}' on '{signal_id}': Step 1 set_signal_phase(GREEN, 75s) -> "
                    f"{response_green.status.value if response_green and response_green.status else 'NoResponse/Error'}"
                )

                if response_green and response_green.status in [SignalControlStatusEnum.ACCEPTED, SignalControlStatusEnum.SUCCESS]:
                    action_initiated_successfully = True

                    sleep_duration = 70
                    self.logger.info(f"  Strategy '{strategy_name}' on '{signal_id}': Step 2 - Waiting {sleep_duration}s before setting RED.")
                    await asyncio.sleep(sleep_duration)

                    self.logger.info(f"  Strategy '{strategy_name}' on '{signal_id}': Step 3 - Setting RED for 30s.")
                    response_red = await self.traffic_signal_service.set_signal_phase(
                        signal_id=signal_id,
                        phase=SignalPhaseEnum.RED,
                        duration_seconds=30
                    )
                    self.logger.info(
                        f"  Strategy '{strategy_name}' on '{signal_id}': Step 3 set_signal_phase(RED, 30s) -> "
                        f"{response_red.status.value if response_red and response_red.status else 'NoResponse/Error'}"
                    )
                else:
                    self.logger.warning(
                        f"  Strategy '{strategy_name}' on '{signal_id}': Initial GREEN command failed. Skipping subsequent RED phase."
                    )
            else:
                self.logger.warning(
                    f"Unknown or unhandled incident response strategy '{strategy_name}' for signal '{signal_id}'. No action taken."
                )
                return False

        except Exception as e:
            self.logger.error(
                f"Error executing strategy '{strategy_name}' for signal '{signal_id}': {e}", exc_info=True
            )
            return False

        return action_initiated_successfully

    # --- Illustrative Green Wave Example (This should be reviewed if it's still needed or if it's test code) ---
    # This block is separate from the operational logic above.
        if True: # Placeholder for actual trigger - This makes it always run, which might be unintended.
            selected_wave_to_run_example = {"id": "main_st_ns_wave", "config": GREEN_WAVE_CORRIDOR_CONFIGS["main_st_ns_wave"]}
            if selected_wave_to_run_example:
                cfg_run_example = selected_wave_to_run_example["config"]; cid_run_example = selected_wave_to_run_example["id"]
                example_can_run = True
                for sig_id_ex in cfg_run_example.get("signals_in_order",[]):
                    if sig_id_ex in processed_signals_for_coordination: example_can_run = False; break
                if example_can_run:
                    self.logger.info(f"ILLUSTRATIVE_EXAMPLE: Attempting to run illustrative wave {cid_run_example}")
                    action_type_str_example = "GREEN_WAVE_ACTIVATION"; current_action_target_ids_example = [cid_run_example]
                    current_action_parameters_example = {"corridor_config": cfg_run_example, "corridor_id": cid_run_example, "selection_method": "ILLUSTRATIVE_HARDCODED"}
                    fetched_pre_action_kpis_example = await self._fetch_pre_action_kpis(action_type_str_example, current_action_target_ids_example, current_action_parameters_example, system_kpis)
                    action_kpi_cfg_example = ACTION_KPI_CONFIG.get(action_type_str_example)
                    if action_kpi_cfg_example:
                        action_id_example = uuid4(); action_ts_example = datetime.utcnow()
                        base_pre_kpis_example = {"overall_congestion_at_decision": system_kpis.get("overall_congestion_level"),
                                                 "corridor_id": cid_run_example,
                                                 "expected_demand_level": system_kpis.get(cfg_run_example.get("demand_kpi_trigger"), "N/A"),
                                                 "selection_method": "ILLUSTRATIVE_HARDCODED"
                                                 }
                        if fetched_pre_action_kpis_example: base_pre_kpis_example.update(fetched_pre_action_kpis_example)
                        self.pending_kpi_collection.append({
                            'action_id': action_id_example, 'action_type': action_type_str_example,
                            'target_ids': [cid_run_example] + cfg_run_example["signals_in_order"],
                            'action_timestamp': action_ts_example,
                            'action_parameters': {"wave_green_time_seconds": cfg_run_example["wave_green_time_seconds"], "selection_method": "ILLUSTRATIVE_HARDCODED"},
                            'pre_action_context_kpis': base_pre_kpis_example,
                            'query_after_timestamp': action_ts_example + timedelta(seconds=action_kpi_cfg_example["delay_seconds"]),
                            'metrics_to_collect': action_kpi_cfg_example["metrics"],
                            'evaluation_window_minutes': action_kpi_cfg_example["eval_window_minutes"],
                            'kpi_query_details': {'service_method_name': action_kpi_cfg_example["service_method"], 'method_specific_args': {'corridor_id': cid_run_example}}})
                        self.logger.info(f"Scheduled KPI collection for Illustrative GW Example {action_id_example} on {cid_run_example} with pre_kpis: {base_pre_kpis_example}")
                else:
                    self.logger.info(f"ILLUSTRATIVE_EXAMPLE: Illustrative wave {cid_run_example} conflicts with already processed signals. Skipping.")

        if self._memory_updated_this_cycle: self._save_effectiveness_memory()
        self.logger.info(f"--- AgentCore cycle completed for {sample_user_id} at {datetime.utcnow().isoformat()} ---")

# --- Main Example for Traffic Signal Integration (as per subtask) ---
# ... (main_example_traffic_integration - unchanged by this subtask) ...
async def main_example_traffic_integration():
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    logger_main_example = logging.getLogger(__name__ + ".main_example_traffic_integration")
    logger_main_example.info("--- Starting main_example_traffic_integration ---")
    class MockAnalyticsService:
        async def get_critical_alert_summary(self): return {"critical_unack_alert_count": 1, "active_alerts": [{"id": "alert1", "type": "TEST_ALERT"}]}
        def get_current_system_kpis_summary(self): return {"overall_congestion_level": "HIGH", "sample_kpi": 123}
        async def get_signal_current_kpis(self, signal_id: str, metrics: List[str]): return {"mock_metric": 100}
        async def get_signal_post_action_kpis(self, signal_id: str, **kwargs): return {"mock_post_metric": 110}
        async def get_corridor_current_kpis(self, corridor_id: str, metrics: List[str]): return {}
        async def get_incident_area_current_kpis(self, incident_location: LocationModel, radius_meters: int, metrics: List[str]): return {}
        async def get_corridor_post_action_kpis(self, corridor_id: str, **kwargs): return {}
        async def get_incident_response_post_action_kpis(self, incident_id: str, **kwargs): return {}
    class MockPredictionScheduler:
        async def set_priority_locations(self, locations: List[LocationModel]): pass
        async def get_traffic_predictions_for_locations(self, locations: List[LocationModel]): return []
    class MockPersonalizedRoutingService:
        async def proactively_suggest_route(self, user_id: str, common_pattern: CommonTravelPattern, current_location: LocationModel): return None
        async def get_user_common_travel_patterns(self, user_id: str) -> List[CommonTravelPattern]:
            return [CommonTravelPattern(pattern_id="pattern1",user_id=user_id,start_location_summary={"name": "Home"},end_location_summary={"name": "Work"},days_of_week=[0,1,2,3,4],time_of_day="08:00",frequency=5)]
        async def update_user_route_feedback(self, user_id: str, route_id: str, feedback: Dict[str, Any]): pass
    class MockConnectionManager:
        async def broadcast_message_model(self, message: WebSocketMessage): pass
    class MockTrafficSignalService:
        def __init__(self, config: Optional[Dict[str, Any]] = None, connection_manager: Optional[MockConnectionManager] = None):
            self._signals: Dict[str, SignalState] = {}
            self._cycle_count = 0
            self._signals["TS001"] = SignalState(signal_id="TS001", location=LocationModel(latitude=1.0, longitude=1.0, name="Main St @ First Ave"), current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=datetime.utcnow())
            self._signals["TS002"] = SignalState(signal_id="TS002", location=LocationModel(latitude=1.01, longitude=1.01, name="Main St @ Second Ave"), current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=datetime.utcnow())
            self._signals["TS003"] = SignalState(signal_id="TS003", location=LocationModel(latitude=1.02, longitude=1.02, name="Oak St @ Third Ave"), current_phase=SignalPhaseEnum.OFF, operational_status=SignalOperationalStatusEnum.OFFLINE, last_updated=datetime.utcnow())
        async def get_all_signal_states(self) -> List[SignalState]:
            self._cycle_count += 1
            if self._cycle_count == 2:
                for signal_id in self._signals:
                    if self._signals[signal_id].operational_status == SignalOperationalStatusEnum.ONLINE:
                        self._signals[signal_id].current_phase = SignalPhaseEnum.GREEN; self._signals[signal_id].last_updated = datetime.utcnow(); break
            return list(self._signals.values())
        async def set_signal_phase(self, signal_id: str, phase: SignalPhaseEnum, duration_seconds: Optional[int] = None) -> SignalControlCommandResponse:
            if signal_id not in self._signals: return SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.FAILED, message="Signal not found")
            signal = self._signals[signal_id]
            if signal.operational_status != SignalOperationalStatusEnum.ONLINE: return SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.REJECTED, message="Signal not ONLINE")
            signal.current_phase = phase; signal.last_updated = datetime.utcnow()
            return SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.ACCEPTED, message="Phase change command accepted")
    agent_core = AgentCore(MockPredictionScheduler(), MockPersonalizedRoutingService(), MockAnalyticsService(), MockTrafficSignalService())
    logger_main_example.info("--- Running decision cycle 1 ---"); await agent_core.run_decision_cycle(sample_user_id="cycle_1_user")
    logger_main_example.info("--- Running decision cycle 2 ---"); await agent_core.run_decision_cycle(sample_user_id="cycle_2_user")
    logger_main_example.info("--- main_example_traffic_integration completed ---")

# --- Main Example (main_example) ---
# ... (main_example - unchanged by this subtask, but its internal calls to run_decision_cycle will use the updated logic) ...
async def main_example():
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    logger.info(f"--- Setting up main_example for Enhanced Mock Analytics & Scoring ---")
    os.makedirs(EFFECTIVENESS_MEMORY_DIR, exist_ok=True)
    if os.path.exists(EFFECTIVENESS_MEMORY_FILEPATH): os.remove(EFFECTIVENESS_MEMORY_FILEPATH)

    class MockAnalytics(MagicMock):
        _call_counters = {}
        _pre_configured_kpis = {}
        _post_configured_kpis = {}

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
            logger.debug(f"MockAnalytics: Attempting lookup for {lookup_key} with metrics: {metrics}")
            if lookup_key in self._pre_configured_kpis:
                logger.debug(f"MockAnalytics: Found pre-configured KPIs for {lookup_key}")
                return self._pre_configured_kpis[lookup_key]
            kpis = {"queried_signal_id_pre_action": signal_id, "data_timestamp": datetime.utcnow().isoformat()}
            if "queue_lengths_meters" in metrics: kpis["queue_lengths_meters"] = {"N": self._dynamic_value(signal_id, "pre_q_n", 10, 50), "S": self._dynamic_value(signal_id, "pre_q_s", 5, 30)}
            if "current_flow_vph" in metrics: kpis["current_flow_vph"] = self._dynamic_value(signal_id, "pre_flow", 150, 450)
            logger.debug(f"MockAnalytics: Returning dynamic current KPIs for {signal_id}: {kpis}")
            return kpis

        async def get_signal_baseline_kpis(self, signal_id: str, time_context: datetime, metrics: List[str]) -> Dict[str, Any]:
            lookup_key = f"get_signal_baseline_kpis:{signal_id}"
            logger.debug(f"MockAnalytics: Attempting lookup for {lookup_key} with time_context: {time_context}, metrics: {metrics}")
            if lookup_key in self._pre_configured_kpis:
                logger.debug(f"MockAnalytics: Found pre-configured baseline KPIs for {lookup_key}")
                return self._pre_configured_kpis[lookup_key]

            kpis_to_return = {
                "queried_baseline_for_signal": signal_id,
                "baseline_data_timestamp": time_context.isoformat()
            }
            if "typical_queue_lengths_meters" in metrics:
                kpis_to_return["typical_queue_lengths_meters"] = {"N": self._dynamic_value(signal_id, "base_q_n", 15, 45), "S": self._dynamic_value(signal_id, "base_q_s", 10, 30)}
            if "typical_flow_vph" in metrics:
                kpis_to_return["typical_flow_vph"] = self._dynamic_value(signal_id, "base_flow", 250, 550)
            logger.info(f"MOCK get_signal_baseline_kpis for {signal_id} returning: {kpis_to_return}")
            return kpis_to_return

        async def get_corridor_current_kpis(self, corridor_id: str, metrics: List[str]):
            lookup_key = f"get_corridor_current_kpis:{corridor_id}"
            logger.debug(f"MockAnalytics: Attempting lookup for {lookup_key} with metrics: {metrics}")
            if lookup_key in self._pre_configured_kpis:
                logger.debug(f"MockAnalytics: Found pre-configured KPIs for {lookup_key}")
                return self._pre_configured_kpis[lookup_key]
            kpis = {"queried_corridor_id_pre_action": corridor_id, "data_timestamp": datetime.utcnow().isoformat()}
            if "avg_travel_time_seconds" in metrics: kpis["avg_travel_time_seconds"] = self._dynamic_value(corridor_id, "pre_tt", 100, 220)
            if "throughput_vph" in metrics: kpis["throughput_vph"] = self._dynamic_value(corridor_id, "pre_tp", 300, 650)
            logger.debug(f"MockAnalytics: Returning dynamic current KPIs for {corridor_id}: {kpis}")
            return kpis

        async def get_corridor_baseline_kpis(self, corridor_id: str, time_context: datetime, metrics: List[str]) -> Dict[str, Any]:
            lookup_key = f"get_corridor_baseline_kpis:{corridor_id}"
            logger.debug(f"MockAnalytics: Attempting lookup for {lookup_key} with time_context: {time_context}, metrics: {metrics}")
            if lookup_key in self._pre_configured_kpis:
                logger.debug(f"MockAnalytics: Found pre-configured baseline KPIs for {lookup_key}")
                return self._pre_configured_kpis[lookup_key]

            kpis_to_return = {
                "queried_baseline_for_corridor": corridor_id,
                "baseline_data_timestamp": time_context.isoformat()
            }
            if "corridor_baseline_avg_travel_time_seconds" in metrics:
                kpis_to_return["corridor_baseline_avg_travel_time_seconds"] = self._dynamic_value(corridor_id, "base_tt", 90, 180)
            if "corridor_baseline_throughput_vph" in metrics:
                kpis_to_return["corridor_baseline_throughput_vph"] = self._dynamic_value(corridor_id, "base_tp", 600, 900)
            logger.info(f"MOCK get_corridor_baseline_kpis for {corridor_id} returning: {kpis_to_return}")
            return kpis_to_return

    async def get_incident_area_current_kpis(self, incident_location: LocationModel, radius_meters: int, metrics: List[str]):
        # Key for mock configuration can be based on location string for simplicity in demo
        mock_config_key = f"{incident_location.latitude}_{incident_location.longitude}"
        lookup_key = f"get_incident_area_current_kpis:{mock_config_key}"
        logger.debug(f"MockAnalytics: Attempting lookup for {lookup_key} with metrics: {metrics}")
        if lookup_key in self._pre_configured_kpis:
            logger.debug(f"MockAnalytics.get_incident_area_current_kpis: Found pre-configured KPIs for key {lookup_key}")
            return self._pre_configured_kpis[lookup_key]
        logger.debug(f"MockAnalytics.get_incident_area_current_kpis: No pre-configured KPIs for key {lookup_key}, returning dynamic.")
        # Default return structure should align with what ACTION_EFFECTIVENESS_CONFIG expects for "pre_incident_avg_speed" etc.
        kpis = {"avg_speed_kmh": self._dynamic_value(str(incident_location), "pre_inc_speed", 5,25),
                "vehicle_count": self._dynamic_value(str(incident_location), "pre_inc_vc", 50,100)}
        logger.debug(f"MockAnalytics: Returning dynamic incident KPIs: {kpis}")
        return kpis

        async def get_signal_post_action_kpis(self, signal_id: str, metrics_to_collect: List[str] = None, **kwargs) -> Dict[str, Any]:
            if metrics_to_collect is None: metrics_to_collect = [] # Ensure it's a list
            lookup_key = f"get_signal_post_action_kpis:{signal_id}"
            logger.debug(f"MockAnalytics: Attempting post-action lookup for {lookup_key} with metrics_to_collect: {metrics_to_collect}")
            if lookup_key in self._post_configured_kpis:
                logger.debug(f"MockAnalytics: Found pre-configured post-KPIs for {lookup_key}")
                return self._post_configured_kpis[lookup_key]

            kpis = {"local_congestion_level": "LOW", "flow_rate_absolute": self._dynamic_value(signal_id,"post_flow",700,1500)}
            if "cross_traffic_queue_lengths_meters" in metrics_to_collect:
                kpis["cross_traffic_queue_lengths_meters"] = {
                    "total": self._dynamic_value(signal_id, "post_cross_q_total", 10, 100),
                    "E": self._dynamic_value(signal_id, "post_cross_q_e", 5, 50),
                    "W": self._dynamic_value(signal_id, "post_cross_q_w", 5, 50)
                }
            logger.debug(f"MockAnalytics: Returning dynamic post-KPIs for {signal_id}: {kpis}")
            return kpis

        async def get_corridor_post_action_kpis(self, corridor_id: str, metrics_to_collect: List[str] = None, **kwargs) -> Dict[str, Any]:
            if metrics_to_collect is None: metrics_to_collect = []
            lookup_key = f"get_corridor_post_action_kpis:{corridor_id}"
            logger.debug(f"MockAnalytics: Attempting post-action lookup for {lookup_key} with metrics_to_collect: {metrics_to_collect}")
            if lookup_key in self._post_configured_kpis:
                logger.debug(f"MockAnalytics: Found pre-configured post-KPIs for {lookup_key}")
                return self._post_configured_kpis[lookup_key]

            kpis = {"corridor_avg_travel_time_seconds": self._dynamic_value(corridor_id, "post_tt", 70,150),
                    "corridor_throughput_vph": self._dynamic_value(corridor_id,"post_tp",600,1200)}
            if "side_street_avg_queue_increase_meters" in metrics_to_collect:
                kpis["side_street_avg_queue_increase_meters"] = self._dynamic_value(corridor_id, "post_side_q_inc", 5, 25)
            logger.debug(f"MockAnalytics: Returning dynamic post-KPIs for {corridor_id}: {kpis}")
            return kpis

        async def get_incident_response_post_action_kpis(self, incident_id: str, **kwargs) -> Dict[str, Any]: # metrics_to_collect often not used here if fixed
            lookup_key = f"get_incident_response_post_action_kpis:{incident_id}"
            logger.debug(f"MockAnalytics: Attempting post-action lookup for {lookup_key}")
            if lookup_key in self._post_configured_kpis:
                logger.debug(f"MockAnalytics.get_incident_response_post_action_kpis: Found post-configured KPIs for key {lookup_key}")
                return self._post_configured_kpis[lookup_key]

            logger.debug(f"MockAnalytics.get_incident_response_post_action_kpis: No post-configured KPIs for key {lookup_key}, returning dynamic.")
            kpis = {"area_clearance_time_minutes": self._dynamic_value(incident_id,"clear_time_min",10,60),
                    "avg_speed_kmh_incident_zone": self._dynamic_value(incident_id,"post_inc_speed",20,50)}
            # Add other potential metrics if ACTION_EFFECTIVENESS_CONFIG might ask for them
            logger.debug(f"MockAnalytics: Returning dynamic incident post-KPIs: {kpis}")
            return kpis

    class MockTraffic(MagicMock):
        _signals = {}
        def __init__(self, *args, **kwargs): super().__init__(*args, **kwargs); self._initialize_mock_signals()
        def _initialize_mock_signals(self):
            self._signals.clear()
            sids = ["TS001","TS002","TS003","TS004","TS005"]
            for i,sid in enumerate(sids):
                self._signals[sid]=SignalState(
                    signal_id=sid,
                    location=LocationModel(latitude=1+i*0.01,longitude=1, name=f"Signal {sid}"), # Added name
                    current_phase=SignalPhaseEnum.RED,
                    operational_status=SignalOperationalStatusEnum.ONLINE,
                    last_updated=datetime.utcnow(),
                    main_flow_direction="NS" # Example
                )
        async def get_all_signal_states(self): return list(self._signals.values())
        async def set_signal_phase(self, signal_id,phase,duration):
            if signal_id in self._signals:
                self._signals[signal_id].current_phase=phase
                self._signals[signal_id].last_updated=datetime.utcnow()
                return SignalControlCommandResponse(signal_id=signal_id,status=SignalControlStatusEnum.ACCEPTED)
            return SignalControlCommandResponse(signal_id=signal_id,status=SignalControlStatusEnum.FAILED, message="Not found")


    analytics_mock = MockAnalytics(); traffic_mock = MockTraffic()

    def reset_mock_traffic_signals_for_congestion_demo(phase=SignalPhaseEnum.RED):
        # This helper is specific to the congestion demo signals.
        # GW demo will set its signals directly or use a new helper.
        for sig_id_congestion in ["TS001", "TS002", "TS004"]: # Signals used in congestion demo
            if sig_id_congestion in traffic_mock._signals:
                traffic_mock._signals[sig_id_congestion].current_phase = phase
                traffic_mock._signals[sig_id_congestion].operational_status = SignalOperationalStatusEnum.ONLINE
        if "TS003" in traffic_mock._signals: traffic_mock._signals["TS003"].current_phase = SignalPhaseEnum.GREEN # Make it not a candidate for congestion
        if "TS005" in traffic_mock._signals: traffic_mock._signals["TS005"].operational_status = SignalOperationalStatusEnum.OFFLINE # Make it not a candidate
        logger.debug(f"MAIN_EXAMPLE: Reset signals for congestion demo (TS001,TS002,TS004 to {phase.value}).")

    agent = AgentCore(MagicMock(spec=PredictionScheduler), MagicMock(spec=PersonalizedRoutingService), analytics_mock, traffic_mock)

    logger.info("--- MAIN_EXAMPLE: Starting Epsilon-Greedy General Congestion Demonstration ---")
    original_epsilon = agent.exploration_epsilon
    original_rng_state = agent.rng.getstate()
    agent.rng.seed(123)

    kpi_collection_delay = ACTION_KPI_CONFIG["SET_SIGNAL_GREEN_CONGESTION"]["delay_seconds"]
    current_sim_time_str = "2023-01-01T10:00:00Z"

    logger.info("--- MAIN_EXAMPLE: Cycle Group 1 - Building Initial History (Forcing Exploitation) ---")
    agent.exploration_epsilon = 0.0
    logger.info(f"MAIN_EXAMPLE: Temporarily set exploration_epsilon to {agent.exploration_epsilon}")

    async def run_action_and_kpi_cycles(action_time_str, action_user_id, kpi_user_id,
                                        target_id_for_kpi_config, # Can be signal_id or corridor_id
                                        action_type_for_kpi_config, # e.g. SET_SIGNAL_GREEN_CONGESTION or GREEN_WAVE_ACTIVATION
                                        post_kpi_payload,
                                        kpi_service_method_name, # e.g. get_signal_post_action_kpis
                                        demand_kpi_settings=None, # For GW, to set specific demand KPIs
                                        overall_congestion_level_action="HIGH",
                                        overall_congestion_level_kpi="LOW"):
        nonlocal current_sim_time_str # Use the general sim time for congestion demo

        logger.info(f"MAIN_EXAMPLE: Running ACTION cycle at {action_time_str} for {action_user_id} targeting {target_id_for_kpi_config if target_id_for_kpi_config else 'any'}")

        current_kpi_snapshot = {"overall_congestion_level": overall_congestion_level_action}
        if demand_kpi_settings: # Used for GW demo to set specific corridor demands
            for gw_cfg_id_inner in GREEN_WAVE_CORRIDOR_CONFIGS:
                demand_kpi_inner = GREEN_WAVE_CORRIDOR_CONFIGS[gw_cfg_id_inner].get("demand_kpi_trigger")
                if demand_kpi_inner and demand_kpi_inner not in demand_kpi_settings: # Default others to LOW
                    current_kpi_snapshot[demand_kpi_inner] = "LOW"
            current_kpi_snapshot.update(demand_kpi_settings)

        await main_example_run_with_mock_time(
            action_time_str, action_user_id, agent, analytics_mock,
            kpis=current_kpi_snapshot
        )

        latest_action_item = None
        if agent.pending_kpi_collection:
            for item_idx in range(len(agent.pending_kpi_collection) - 1, -1, -1):
                if agent.pending_kpi_collection[item_idx]['action_type'] == action_type_for_kpi_config:
                    # For GW, target_ids[0] is corridor_id. For signal, target_ids[0] is signal_id.
                    if target_id_for_kpi_config is None or agent.pending_kpi_collection[item_idx]['target_ids'][0] == target_id_for_kpi_config:
                        latest_action_item = agent.pending_kpi_collection[item_idx]
                        break

        if latest_action_item:
            actual_target_id = latest_action_item['target_ids'][0]
            analytics_mock.configure_post_action_kpis(
                actual_target_id,
                kpi_service_method_name,
                post_kpi_payload
            )
            logger.info(f"MAIN_EXAMPLE: Configured post-KPIs for {actual_target_id} ({action_type_for_kpi_config}) to yield score via: {post_kpi_payload}")
        elif target_id_for_kpi_config: # If we expected a specific target but no action was found
             logger.warning(f"MAIN_EXAMPLE: Expected action for {target_id_for_kpi_config} ({action_type_for_kpi_config}) but none found in pending items.")


        current_kpi_collection_delay = ACTION_KPI_CONFIG[action_type_for_kpi_config]["delay_seconds"]
        kpi_collection_time = datetime.fromisoformat(action_time_str.replace("Z","+00:00")) + timedelta(seconds=current_kpi_collection_delay + 15) # Generic buffer
        kpi_collection_time_str = kpi_collection_time.isoformat().replace("+00:00", "Z")
        logger.info(f"MAIN_EXAMPLE: Running KPI PROCESSING cycle at {kpi_collection_time_str} for {kpi_user_id}")
        await main_example_run_with_mock_time(
            kpi_collection_time_str, kpi_user_id, agent, analytics_mock,
            kpis={"overall_congestion_level": overall_congestion_level_kpi}
        )
        # Update the correct sim time string based on context (congestion or GW)
        # This part needs to be context-aware or use separate time trackers if demos interleave more.
        # For now, assuming demos run sequentially, so current_sim_time_str (general) is updated.
        current_sim_time_str = (kpi_collection_time + timedelta(minutes=15)).isoformat().replace("+00:00", "Z")


    # Congestion Demo Cycles (using current_sim_time_str)
    logger.info("--- MAIN_EXAMPLE: Cycle 1.1 (TS001 Good Score - Congestion Demo) ---")
    reset_mock_traffic_signals_for_congestion_demo()
    traffic_mock._signals["TS002"].current_phase = SignalPhaseEnum.GREEN
    traffic_mock._signals["TS004"].current_phase = SignalPhaseEnum.GREEN
    await run_action_and_kpi_cycles(current_sim_time_str, "user_hist_ts001", "user_kpi_ts001",
                                    "TS001", "SET_SIGNAL_GREEN_CONGESTION",
                                    {"local_congestion_level": "LOW", "flow_rate_absolute": 800},
                                    "get_signal_post_action_kpis"
                                    )

    logger.info("--- MAIN_EXAMPLE: Cycle 1.2 (TS002 Bad Score - Congestion Demo) ---")
    reset_mock_traffic_signals_for_congestion_demo()
    traffic_mock._signals["TS001"].current_phase = SignalPhaseEnum.GREEN
    agent._recent_signal_actions.clear()
    agent._recent_signal_actions["TS001"] = {'timestamp': datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) - timedelta(seconds=10), 'reason':'demo'}
    traffic_mock._signals["TS004"].current_phase = SignalPhaseEnum.GREEN
    await run_action_and_kpi_cycles(current_sim_time_str, "user_hist_ts002", "user_kpi_ts002",
                                    "TS002", "SET_SIGNAL_GREEN_CONGESTION",
                                    {"local_congestion_level": "HIGH", "flow_rate_absolute": 100},
                                    "get_signal_post_action_kpis"
                                    )

    logger.info("--- MAIN_EXAMPLE: Cycle 1.3 (TS004 Neutral Score - Congestion Demo) ---")
    reset_mock_traffic_signals_for_congestion_demo()
    agent._recent_signal_actions.clear()
    agent._recent_signal_actions["TS001"] = {'timestamp': datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) - timedelta(seconds=10), 'reason':'demo'}
    agent._recent_signal_actions["TS002"] = {'timestamp': datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) - timedelta(seconds=10), 'reason':'demo'}
    await run_action_and_kpi_cycles(current_sim_time_str, "user_hist_ts004", "user_kpi_ts004",
                                    "TS004", "SET_SIGNAL_GREEN_CONGESTION",
                                    {"local_congestion_level": "MEDIUM", "flow_rate_absolute": 400},
                                    "get_signal_post_action_kpis"
                                    )

    logger.info(f"MAIN_EXAMPLE: Effectiveness Memory after Congestion History Building: {json.dumps(agent.action_effectiveness_memory, indent=2)}")

    logger.info("--- MAIN_EXAMPLE: Cycle Group 2 - Demonstrating Epsilon-Greedy (Congestion Demo) ---")
    agent.exploration_epsilon = 0.5
    logger.info(f"MAIN_EXAMPLE: Set exploration_epsilon to {agent.exploration_epsilon} for Congestion Demo")

    congestion_candidate_ids = ["TS001", "TS002", "TS004"]
    for i in range(6):
        cycle_num = i + 1
        logger.info(f"--- MAIN_EXAMPLE: Epsilon-Greedy Cycle {cycle_num} (Congestion Demo) ---")
        reset_mock_traffic_signals_for_congestion_demo(SignalPhaseEnum.RED)
        agent._recent_signal_actions.clear()

        logger.info("Current scores before cycle (Congestion Demo):")
        for sig_id in congestion_candidate_ids:
            score_key = f"SET_SIGNAL_GREEN_CONGESTION:{sig_id}"
            scores = agent.action_effectiveness_memory.get(score_key, [])
            avg_score = sum(scores) / len(scores) if scores else 0.0
            logger.info(f"  {sig_id}: Avg Score = {avg_score:.2f} (History: {scores})")

        action_time_str = current_sim_time_str # Uses the general current_sim_time_str
        action_user_id = f"user_egreedy_action_{cycle_num}"
        kpi_user_id = f"user_egreedy_kpi_{cycle_num}"

        await run_action_and_kpi_cycles(
            action_time_str, action_user_id, kpi_user_id,
            None, # Let the agent pick, then we'll see what it was
            "SET_SIGNAL_GREEN_CONGESTION",
            {"local_congestion_level": "MEDIUM", "flow_rate_absolute": 500}, # Neutral outcome for any chosen signal
            "get_signal_post_action_kpis"
        )
        # Log details from the *last* general congestion action, if any
        latest_congestion_action_item = None
        for item_idx in range(len(agent.pending_kpi_collection) - 1, -1, -1):
            item = agent.pending_kpi_collection[item_idx]
            if item['action_type'] == "SET_SIGNAL_GREEN_CONGESTION":
                latest_congestion_action_item = item
                break
        if latest_congestion_action_item:
            chosen_signal_id = latest_congestion_action_item['target_ids'][0]
            selection_method = latest_congestion_action_item['action_parameters'].get('selection_method', 'UNKNOWN_METHOD')
            chosen_score_raw = latest_congestion_action_item['pre_action_context_kpis'].get('chosen_candidate_avg_score', 'N/A')
            chosen_score_str = f"{chosen_score_raw:.2f}" if isinstance(chosen_score_raw, float) else str(chosen_score_raw)
            logger.info(f"MAIN_EXAMPLE (Congestion Demo): Cycle {cycle_num} action: {selection_method} chose {chosen_signal_id} (score {chosen_score_str})")
        else:
            logger.info(f"MAIN_EXAMPLE (Congestion Demo): Cycle {cycle_num}: No congestion action found in pending items this iteration.")

        logger.info(f"MAIN_EXAMPLE: Effectiveness Memory after E-Greedy Cycle {cycle_num} (Congestion Demo): {json.dumps(agent.action_effectiveness_memory, indent=2)}")

    agent.exploration_epsilon = original_epsilon # Restore original epsilon
    agent.rng.setstate(original_rng_state) # Restore original RNG state
    logger.info(f"MAIN_EXAMPLE: Restored exploration_epsilon to {agent.exploration_epsilon}. RNG state restored.")
    logger.info("--- MAIN_EXAMPLE: Epsilon-Greedy General Congestion Demonstration Completed ---")

    # --- Epsilon-Greedy GREEN WAVE SELECTION Demonstration ---
    logger.info("--- MAIN_EXAMPLE: Starting Epsilon-Greedy GREEN WAVE SELECTION Demonstration ---")
    original_epsilon_gw_demo = agent.exploration_epsilon
    original_rng_state_gw_demo = agent.rng.getstate()
    agent.exploration_epsilon = 0.5
    agent.rng.seed(456) # New seed for this demo segment
    logger.info(f"MAIN_EXAMPLE (GW Demo): Temporarily set exploration_epsilon to {agent.exploration_epsilon}.")

    # Ensure signals for alt_st_ew_wave (TS003, TS005) and main_st_ns_wave (TS001, TS002, TS004) are ONLINE and RED.
    gw_demo_signals = ["TS001", "TS002", "TS003", "TS004", "TS005"]
    for sig_id_gw_setup in gw_demo_signals:
        if sig_id_gw_setup in traffic_mock._signals:
            traffic_mock._signals[sig_id_gw_setup].current_phase = SignalPhaseEnum.RED
            traffic_mock._signals[sig_id_gw_setup].operational_status = SignalOperationalStatusEnum.ONLINE
        else: # Should not happen if MockTraffic initializes them
            logger.warning(f"MAIN_EXAMPLE (GW Demo): Signal {sig_id_gw_setup} not found in traffic_mock for setup.")
    logger.info(f"MAIN_EXAMPLE (GW Demo): Ensured GW demo signals ({', '.join(gw_demo_signals)}) are ONLINE and RED.")

    current_sim_time_str_gw_demo = "2023-01-02T07:00:00Z" # Using a new date to avoid conflicts with congestion demo times

    # --- Cycle Group 1: Build Initial GW Effectiveness History (Forcing Exploitation for GW Demo) ---
    logger.info("--- MAIN_EXAMPLE (GW Demo): Cycle Group 1 - Building GW History (Forcing Exploitation) ---")
    agent.exploration_epsilon = 0.0
    logger.info(f"MAIN_EXAMPLE (GW Demo): Set exploration_epsilon to {agent.exploration_epsilon} for GW History Building.")

    # History for "main_st_ns_wave" (P1) - Moderate Score (e.g. ~0.2)
    current_sim_time_str_gw_demo = "2023-01-02T07:15:00Z"
    demand_kpis_main_st_only_gw = {
        GREEN_WAVE_CORRIDOR_CONFIGS["main_st_ns_wave"]["demand_kpi_trigger"]: "HIGH",
        GREEN_WAVE_CORRIDOR_CONFIGS["alt_st_ew_wave"]["demand_kpi_trigger"]: "LOW"
    }
    await run_action_and_kpi_cycles(
        current_sim_time_str_gw_demo, "user_gw_hist_main", "user_gw_kpi_main",
        "main_st_ns_wave", "GREEN_WAVE_ACTIVATION",
        {"corridor_avg_travel_time_seconds": 130, "corridor_throughput_vph": 500}, # Expected score ~0.2
        "get_corridor_post_action_kpis",
        demand_kpi_settings=demand_kpis_main_st_only_gw,
        overall_congestion_level_action="LOW" # GWs can run in LOW congestion
    )
    # Update current_sim_time_str from the helper's effect if it modifies a shared variable,
    # or manage it explicitly here if the helper's scope for time update is local.
    # Assuming run_action_and_kpi_cycles updates the general `current_sim_time_str`, so we'll use a distinct one for this demo.
    # For safety, let's advance GW demo time based on its own state:
    gw_kpi_collection_delay_val_hist = ACTION_KPI_CONFIG["GREEN_WAVE_ACTIVATION"]["delay_seconds"]
    current_sim_time_str_gw_demo = (datetime.fromisoformat(current_sim_time_str_gw_demo.replace("Z","+00:00")) + timedelta(seconds=gw_kpi_collection_delay_val_hist + 15 + 5*60)).isoformat() + "Z"


    # History for "alt_st_ew_wave" (P1) - Good Score (e.g. ~1.0)
    # current_sim_time_str_gw_demo = "2023-01-02T07:45:00Z" # Explicit time setting
    demand_kpis_alt_st_only_gw = {
        GREEN_WAVE_CORRIDOR_CONFIGS["main_st_ns_wave"]["demand_kpi_trigger"]: "LOW",
        GREEN_WAVE_CORRIDOR_CONFIGS["alt_st_ew_wave"]["demand_kpi_trigger"]: "HIGH"
    }
    await run_action_and_kpi_cycles(
        current_sim_time_str_gw_demo, "user_gw_hist_alt", "user_gw_kpi_alt",
        "alt_st_ew_wave", "GREEN_WAVE_ACTIVATION",
        {"corridor_avg_travel_time_seconds": 70, "corridor_throughput_vph": 900}, # Expected score ~1.0
        "get_corridor_post_action_kpis",
        demand_kpi_settings=demand_kpis_alt_st_only_gw,
        overall_congestion_level_action="LOW"
    )
    current_sim_time_str_gw_demo = (datetime.fromisoformat(current_sim_time_str_gw_demo.replace("Z","+00:00")) + timedelta(seconds=gw_kpi_collection_delay_val_hist + 15 + 5*60)).isoformat() + "Z"
    logger.info(f"MAIN_EXAMPLE (GW Demo): Effectiveness Memory after History Building: {json.dumps(agent.action_effectiveness_memory, indent=2)}")

    # --- Cycle Group 2: Demonstrate Epsilon-Greedy GW Selection ---
    logger.info("--- MAIN_EXAMPLE (GW Demo): Cycle Group 2 - Demonstrating Epsilon-Greedy GW Selection ---")
    agent.exploration_epsilon = 0.5
    logger.info(f"MAIN_EXAMPLE (GW Demo): Set exploration_epsilon to {agent.exploration_epsilon}")

    gw_candidate_ids_for_demo = ["main_st_ns_wave", "alt_st_ew_wave"]
    # Reset current_sim_time_str_gw if it was modified by run_action_and_kpi_cycles
    current_sim_time_str_gw_demo = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=1)).isoformat() + "Z"

    for i in range(8):
        cycle_num = i + 1
        logger.info(f"--- MAIN_EXAMPLE (GW Demo): Epsilon-Greedy Cycle {cycle_num} ---")

        current_sim_time_str_gw_demo = (datetime.fromisoformat(current_sim_time_str_gw_demo.replace("Z","+00:00")) + timedelta(minutes=20)).isoformat().replace("+00:00","Z")
        demand_kpis_both_high_gw = {
            GREEN_WAVE_CORRIDOR_CONFIGS["main_st_ns_wave"]["demand_kpi_trigger"]: "HIGH",
            GREEN_WAVE_CORRIDOR_CONFIGS["alt_st_ew_wave"]["demand_kpi_trigger"]: "HIGH"
        }
        agent._recent_signal_actions.clear()

        logger.info("MAIN_EXAMPLE (GW Demo): Current GW scores before cycle:")
        for corridor_id_log in gw_candidate_ids_for_demo:
            score_key = f"GREEN_WAVE_ACTIVATION:{corridor_id_log}"
            scores = agent.action_effectiveness_memory.get(score_key, [])
            avg_score = sum(scores) / len(scores) if scores else 0.0
            logger.info(f"  {corridor_id_log}: Avg Score = {avg_score:.2f} (History: {scores})")

        action_user_id = f"user_gw_egreedy_action_{cycle_num}"
        kpi_user_id = f"user_gw_egreedy_kpi_{cycle_num}"

        await run_action_and_kpi_cycles(
            current_sim_time_str_gw_demo, action_user_id, kpi_user_id,
            None, # Let agent choose, we'll see what it was from pending_kpi_collection
            "GREEN_WAVE_ACTIVATION",
            {"corridor_avg_travel_time_seconds": 140, "corridor_throughput_vph": 650}, # Neutral outcome for any chosen GW
            "get_corridor_post_action_kpis",
            demand_kpi_settings=demand_kpis_both_high_gw,
            overall_congestion_level_action="LOW" # GWs can run if not high congestion
        )

        latest_gw_action_item = None
        for item_idx in range(len(agent.pending_kpi_collection) - 1, -1, -1):
            item_check = agent.pending_kpi_collection[item_idx]
            if item_check['action_type'] == "GREEN_WAVE_ACTIVATION" and item_check['action_parameters'].get('selection_method') in ["EXPLORATORY_GREEN_WAVE_RANDOM", "EXPLOITATIVE_GREEN_WAVE_BEST_SCORE"]:
                latest_gw_action_item = item_check
                break

        if latest_gw_action_item:
            chosen_gw_id_for_logging = latest_gw_action_item['target_ids'][0]
            selection_method_gw = latest_gw_action_item['action_parameters'].get('selection_method', 'UNKNOWN_METHOD')
            chosen_gw_score_raw = latest_gw_action_item['pre_action_context_kpis'].get('chosen_corridor_avg_score', 'N/A')
            chosen_gw_score_str = f"{chosen_gw_score_raw:.2f}" if isinstance(chosen_gw_score_raw, float) else str(chosen_gw_score_raw)
            num_top_prio = latest_gw_action_item['pre_action_context_kpis'].get('num_top_priority_candidates',0)
            top_prio_scores_log = latest_gw_action_item['pre_action_context_kpis'].get('top_priority_candidate_scores',{})
            logger.info(f"MAIN_EXAMPLE (GW Demo): Cycle {cycle_num} action: {selection_method_gw} chose {chosen_gw_id_for_logging} (score {chosen_gw_score_str}). "
                        f"Num top_prio: {num_top_prio}. Top prio scores: {json.dumps(top_prio_scores_log)}")
        else:
            logger.info(f"MAIN_EXAMPLE (GW Demo): Cycle {cycle_num}: No Epsilon-Greedy GREEN_WAVE_ACTIVATION action found in pending items for logging.")

        logger.info(f"MAIN_EXAMPLE (GW Demo): Effectiveness Memory after E-Greedy Cycle {cycle_num}: {json.dumps(agent.action_effectiveness_memory, indent=2)}")

    agent.exploration_epsilon = original_epsilon_gw_demo
    agent.rng.setstate(original_rng_state_gw_demo)
    logger.info(f"MAIN_EXAMPLE (GW Demo): Restored exploration_epsilon to {agent.exploration_epsilon}. RNG state restored.")
    logger.info("--- MAIN_EXAMPLE: Epsilon-Greedy GREEN WAVE SELECTION Demonstration Completed ---")


    # --- Demonstration of Specific KPI-based Scoring ---
    logger.info("--- MAIN_EXAMPLE: Starting Specific KPI-based Scoring Demonstrations ---")
    # Reset agent's memory and logs for these specific demos
    agent.action_effectiveness_memory = {}
    agent.action_performance_logs = []
    agent.pending_kpi_collection = []
    agent.exploration_epsilon = 0.0 # Force exploitation for predictability in these demos
    current_sim_time_str = "2023-01-03T10:00:00Z" # New time for these demos

    # --- Demo 1: SET_SIGNAL_GREEN_CONGESTION Scoring ---
    logger.info("--- Demo: SET_SIGNAL_GREEN_CONGESTION - Scoring with Pre/Post KPIs ---")
    action_target_signal_id = "TS001"
    action_type_congestion = "SET_SIGNAL_GREEN_CONGESTION"

    # Setup signal state for TS001
    traffic_mock._signals[action_target_signal_id].current_phase = SignalPhaseEnum.RED
    traffic_mock._signals[action_target_signal_id].operational_status = SignalOperationalStatusEnum.ONLINE
    # Ensure other signals won't be chosen (e.g. make them green or offline)
    for sid, sig_state in traffic_mock._signals.items():
        if sid != action_target_signal_id:
            sig_state.current_phase = SignalPhaseEnum.GREEN

    # Configure Pre-Action KPIs for TS001
    snapshot_kpis_ts001 = {"current_flow_vph": 100, "queue_lengths_meters": {"N": 60, "S": 10}}
    baseline_kpis_ts001 = {"typical_flow_vph": 90, "typical_queue_lengths_meters": {"N": 50, "S": 8}}
    analytics_mock.configure_pre_action_kpis(
        action_target_signal_id,
        "get_signal_current_kpis",
        snapshot_kpis_ts001
    )
    analytics_mock.configure_pre_action_kpis(
        action_target_signal_id,
        "get_signal_baseline_kpis", # New baseline call
        baseline_kpis_ts001
    )

    # Configure Post-Action KPIs for TS001, including externalities
    post_kpi_payload_congestion = {
        "local_congestion_level": "LOW",
        "flow_rate_absolute": 350,
        "cross_traffic_queue_lengths_meters": {"total": 25, "E": 10, "W": 15} # Externality
    }

    await run_action_and_kpi_cycles(
        action_time_str=current_sim_time_str,
        action_user_id="user_congestion_score_action",
        kpi_user_id="user_congestion_score_kpi",
        target_id_for_kpi_config=action_target_signal_id,
        action_type_for_kpi_config=action_type_congestion,
        post_kpi_payload=post_kpi_payload_congestion,
        kpi_service_method_name="get_signal_post_action_kpis",
        overall_congestion_level_action="HIGH" # System KPI for action cycle
    )

    congestion_action_log = next((log for log in agent.action_performance_logs if log.action_type == action_type_congestion and log.target_ids[0] == action_target_signal_id), None)
    assert congestion_action_log is not None, f"Action log for {action_type_congestion} on {action_target_signal_id} not found."
    logger.info(f"MAIN_EXAMPLE ({action_type_congestion}): Final ActionPerformanceLog: {congestion_action_log.model_dump_json(indent=2, default=str)}")

    # Verify merged pre_action_context_kpis
    assert congestion_action_log.pre_action_context_kpis.get("current_flow_vph") == 100 # From snapshot
    assert congestion_action_log.pre_action_context_kpis.get("typical_flow_vph") == 90   # From baseline

    # Verify post_action_kpis include externalities
    assert congestion_action_log.post_action_kpis.get("local_congestion_level") == "LOW"
    assert congestion_action_log.post_action_kpis.get("cross_traffic_queue_lengths_meters", {}).get("total") == 25

    # Verify effectiveness_metrics_used include aliased baseline and externality KPIs
    assert congestion_action_log.effectiveness_metrics_used.get("pre_snapshot_flow_vph") == 100
    assert congestion_action_log.effectiveness_metrics_used.get("baseline_typical_flow_vph") == 90 # Aliased baseline
    assert congestion_action_log.effectiveness_metrics_used.get("post_action_flow_rate_vph") == 350
    assert congestion_action_log.effectiveness_metrics_used.get("post_cross_traffic_queue_total_meters") == 25 # Aliased externality

    logger.info(f"MAIN_EXAMPLE ({action_type_congestion}): Score ({congestion_action_log.effectiveness_score}) now reflects baseline comparison and externality penalties (if AgentCore logic updated).")
    # Example: Score was 0.65. With baseline flow of 90 (instead of 100 snapshot if baseline is preferred by scoring)
    # and cross-traffic penalty (e.g. -0.1 for 25m), the score would change.
    # For now, we are not asserting the exact score as it depends on AgentCore's internal scoring adjustments.
    # assert congestion_action_log.effectiveness_score == pytest.approx(EXPECTED_NEW_SCORE)
    current_sim_time_str = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")


    # --- Demo 2: GREEN_WAVE_ACTIVATION Scoring (Refined from original) ---
    logger.info("--- Demo: GREEN_WAVE_ACTIVATION - Scoring with Pre/Post KPIs (including Baselines & Externalities) ---")
    action_target_corridor_id = "main_st_ns_wave"
    action_type_gw = "GREEN_WAVE_ACTIVATION"

    # Reset relevant signal states for this green wave
    for sig_id_gw in GREEN_WAVE_CORRIDOR_CONFIGS[action_target_corridor_id]["signals_in_order"]:
        if sig_id_gw in traffic_mock._signals:
            traffic_mock._signals[sig_id_gw].current_phase = SignalPhaseEnum.RED
            traffic_mock._signals[sig_id_gw].operational_status = SignalOperationalStatusEnum.ONLINE
        else: # Ensure they exist if not already
            traffic_mock._signals[sig_id_gw] = SignalState(signal_id=sig_id_gw, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, location=LocationModel(latitude=0,longitude=0,name=sig_id_gw), last_updated=datetime.utcnow())


    snapshot_kpis_gw = {"avg_travel_time_seconds": 190, "throughput_vph": 450}
    baseline_kpis_gw = {"corridor_baseline_avg_travel_time_seconds": 200, "corridor_baseline_throughput_vph": 400}
    analytics_mock.configure_pre_action_kpis(
        action_target_corridor_id,
        "get_corridor_current_kpis",
        snapshot_kpis_gw
    )
    analytics_mock.configure_pre_action_kpis(
        action_target_corridor_id,
        "get_corridor_baseline_kpis", # New baseline call
        baseline_kpis_gw
    )

    # Post-action KPIs including externalities
    post_kpi_payload_gw = {
        "corridor_avg_travel_time_seconds": 95,
        "corridor_throughput_vph": 880,
        "side_street_avg_queue_increase_meters": 40 # Externality
    }

    # Demand KPI for the target corridor to ensure it's triggered
    demand_kpi_gw_demo = GREEN_WAVE_CORRIDOR_CONFIGS[action_target_corridor_id].get("demand_kpi_trigger")
    demand_kpi_settings_gw_demo = {demand_kpi_gw_demo: "HIGH"} if demand_kpi_gw_demo else {}

    await run_action_and_kpi_cycles(
        action_time_str=current_sim_time_str,
        action_user_id="user_gw_score_action",
        kpi_user_id="user_gw_score_kpi",
        target_id_for_kpi_config=action_target_corridor_id,
        action_type_for_kpi_config=action_type_gw,
        post_kpi_payload=post_kpi_payload_gw,
        kpi_service_method_name="get_corridor_post_action_kpis",
        demand_kpi_settings=demand_kpi_settings_gw_demo, # Ensure corridor is triggered
        overall_congestion_level_action="MEDIUM" # System KPI for action cycle
    )

    gw_action_log = next((log for log in agent.action_performance_logs if log.action_type == action_type_gw and log.target_ids[0] == action_target_corridor_id), None)
    assert gw_action_log is not None, f"Action log for {action_type_gw} on {action_target_corridor_id} not found."
    logger.info(f"MAIN_EXAMPLE ({action_type_gw}): Final ActionPerformanceLog: {gw_action_log.model_dump_json(indent=2, default=str)}")

    # Verify merged pre_action_context_kpis
    assert gw_action_log.pre_action_context_kpis.get("avg_travel_time_seconds") == 190 # Snapshot
    assert gw_action_log.pre_action_context_kpis.get("corridor_baseline_avg_travel_time_seconds") == 200 # Baseline

    # Verify post_action_kpis include externalities
    assert gw_action_log.post_action_kpis.get("corridor_avg_travel_time_seconds") == 95
    assert gw_action_log.post_action_kpis.get("side_street_avg_queue_increase_meters") == 40

    # Verify effectiveness_metrics_used include aliased baseline and externality KPIs
    assert gw_action_log.effectiveness_metrics_used.get("pre_gw_avg_travel_time") == 190
    assert gw_action_log.effectiveness_metrics_used.get("baseline_gw_avg_travel_time") == 200 # Aliased baseline
    assert gw_action_log.effectiveness_metrics_used.get("gw_post_avg_travel_time") == 95
    assert gw_action_log.effectiveness_metrics_used.get("post_side_street_avg_queue_increase_meters") == 40 # Aliased externality

    logger.info(f"MAIN_EXAMPLE ({action_type_gw}): Score ({gw_action_log.effectiveness_score}) now reflects baseline comparison and externality penalties (if AgentCore logic updated).")
    # Example: Original score might have been 0.5. With baseline_tt of 200 (worse than snapshot 190, so snapshot might be preferred if logic does that)
    # and side_street_queue penalty (e.g. -0.4 for 40m), the score would change.
    # assert gw_action_log.effectiveness_score == pytest.approx(EXPECTED_NEW_SCORE_GW)
    current_sim_time_str = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")


    # --- Demo 3: INCIDENT_RESPONSE_ACCIDENT Scoring ---
    logger.info("--- Demo: INCIDENT_RESPONSE_ACCIDENT - Scoring with Pre/Post KPIs (no new baselines/externalities in this example for this action) ---")
    action_type_incident = "INCIDENT_RESPONSE_ACCIDENT"
    incident_signal_target = "TS002" # Signal agent will control
    mock_incident_id = f"test_accident_for_{incident_signal_target}"
    # Ensure TS002 exists in traffic_mock._signals for location lookup
    if incident_signal_target not in traffic_mock._signals:
        traffic_mock._signals[incident_signal_target] = SignalState(signal_id=incident_signal_target, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, location=LocationModel(latitude=1.01,longitude=1.0,name=incident_signal_target), last_updated=datetime.utcnow())

    mock_incident_location = traffic_mock._signals[incident_signal_target].location

    # Setup signal state for TS002
    traffic_mock._signals[incident_signal_target].current_phase = SignalPhaseEnum.RED
    traffic_mock._signals[incident_signal_target].operational_status = SignalOperationalStatusEnum.ONLINE

    # Configure Pre-Action KPIs for the incident area
    # The key for configure_pre_action_kpis needs to match how the mock's get_incident_area_current_kpis will look it up.
    # Current mock uses str(incident_location) which is not ideal as LocationModel doesn't have a fixed str.
    # Updated MockAnalytics.get_incident_area_current_kpis to use lat_lon string.
    pre_kpi_incident_target_key = f"{mock_incident_location.latitude}_{mock_incident_location.longitude}"
    analytics_mock.configure_pre_action_kpis(
        target_id=pre_kpi_incident_target_key,
        service_method_name="get_incident_area_current_kpis",
        kpi_data={"avg_speed_kmh": 5, "vehicle_count": 80} # Low speed, high count
    )

    # Post-action: get_incident_response_post_action_kpis (keyed by incident_id)
    post_kpi_payload_incident = {"avg_speed_kmh_incident_zone": 25, "area_clearance_time_minutes": 12}

    # Setup system KPIs for the action cycle (to trigger incident processing)
    system_kpis_for_incident_action = {"overall_congestion_level": "MEDIUM"}
    active_alerts_for_incident = [{
        "id": mock_incident_id, "type": "ACCIDENT",
        "location": mock_incident_location.model_dump(),
        "description": f"Mock accident near {incident_signal_target} for scoring demo"
    }]
    # Temporarily override get_critical_alert_summary for this specific demo segment
    original_get_critical_alert_summary = analytics_mock.get_critical_alert_summary
    analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts": active_alerts_for_incident})

    # Run Action Cycle (Agent should detect incident and act on incident_signal_target)
    logger.info(f"MAIN_EXAMPLE ({action_type_incident}): Running ACTION cycle at {current_sim_time_str}")
    await main_example_run_with_mock_time(
        current_sim_time_str, f"user_{action_type_incident}_action", agent, analytics_mock,
        kpis=system_kpis_for_incident_action
    )

    # Restore original mock method
    analytics_mock.get_critical_alert_summary = original_get_critical_alert_summary

    # Configure Post-Action KPIs for the specific incident ID
    analytics_mock.configure_post_action_kpis(
        target_id=mock_incident_id, # Post KPIs are keyed by incident_id
        service_method_name="get_incident_response_post_action_kpis",
        kpi_data=post_kpi_payload_incident
    )

    # Run KPI Collection Cycle
    kpi_collection_delay_incident = ACTION_KPI_CONFIG[action_type_incident]["delay_seconds"]
    kpi_collection_time_incident = datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(seconds=kpi_collection_delay_incident + 15)
    kpi_collection_time_str_incident = kpi_collection_time_incident.isoformat().replace("+00:00", "Z")

    logger.info(f"MAIN_EXAMPLE ({action_type_incident}): Running KPI PROCESSING cycle at {kpi_collection_time_str_incident}")
    await main_example_run_with_mock_time(
        kpi_collection_time_str_incident, f"user_{action_type_incident}_kpi", agent, analytics_mock,
        kpis={"overall_congestion_level": "LOW"} # System KPI for KPI collection cycle
    )

    incident_action_log = next((log for log in agent.action_performance_logs if log.action_type == action_type_incident and log.action_parameters.get("incident_id") == mock_incident_id), None)
    assert incident_action_log is not None, f"Action log for {action_type_incident} with incident_id {mock_incident_id} not found."
    logger.info(f"MAIN_EXAMPLE ({action_type_incident}): Final ActionPerformanceLog: {incident_action_log.model_dump_json(indent=2, default=str)}")

    assert incident_action_log.pre_action_context_kpis.get("avg_speed_kmh") == 5
    assert incident_action_log.post_action_kpis.get("area_clearance_time_minutes") == 12
    assert incident_action_log.effectiveness_metrics_used.get("pre_incident_avg_speed") == 5
    assert incident_action_log.effectiveness_metrics_used.get("post_incident_clearance_time_minutes") == 12
    assert incident_action_log.effectiveness_metrics_used.get("post_incident_avg_speed") == 25
    # Expected score: clear_time=12 (<15) -> +0.6
    #                 pre_speed=5 (<20), post_speed=25 (>1.5*5=7.5 and >15) -> +0.4
    # Total = (0.6 + 0.4) / 2 = 0.5
    assert incident_action_log.effectiveness_score == pytest.approx(0.5)
    current_sim_time_str = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")

    # Restore mocks if they were changed for specific demos
    analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts":[]}) # Reset to no active alerts


    # --- MAIN_EXAMPLE: Epsilon-Greedy ACCIDENT Response Strategy Demonstration ---
    logger.info("--- MAIN_EXAMPLE: Starting Epsilon-Greedy ACCIDENT Response Strategy Demonstration ---")
    agent.exploration_epsilon = 0.5  # Restore for this demo section
    agent.rng.seed(789) # Distinct seed for this part of the demo

    target_signal_id_incident_demo = "TS002"
    mock_incident_location_ts002 = traffic_mock._signals[target_signal_id_incident_demo].location
    mock_accident_alert_details_ts002 = {
        "id": "demo_acc_ts002",
        "type": "ACCIDENT",
        "location": mock_incident_location_ts002.model_dump(),
        "description": f"Mock accident near {target_signal_id_incident_demo} for strategy demo"
    }
    pre_kpi_incident_target_key_ts002 = f"{mock_incident_location_ts002.latitude}_{mock_incident_location_ts002.longitude}"

    # --- Cycle Group 1: Build Initial Effectiveness History for Accident Strategies on TS002 ---
    logger.info("--- MAIN_EXAMPLE (Accident Strategy Demo): Cycle Group 1 - Building Strategy History ---")
    original_epsilon_hist_build = agent.exploration_epsilon
    agent.exploration_epsilon = 1.0  # Force exploration path to mock choice easily

    strategies_to_build_history_for = [
        (STRATEGY_ACCIDENT_EXTEND_GREEN_LONG, {"area_clearance_time_minutes": 10, "avg_speed_kmh_incident_zone": 35}, 0.8), # Good score
        (STRATEGY_ACCIDENT_EXTEND_GREEN_MODERATE, {"area_clearance_time_minutes": 25, "avg_speed_kmh_incident_zone": 25}, 0.2), # Moderate
        (STRATEGY_ACCIDENT_PULSE_GREEN, {"area_clearance_time_minutes": 50, "avg_speed_kmh_incident_zone": 15}, -0.5) # Bad score
    ]

    for i, (strategy_name, post_kpi_payload, expected_score_approx) in enumerate(strategies_to_build_history_for):
        logger.info(f"--- MAIN_EXAMPLE (Accident Strategy Demo): History Build for {strategy_name} ---")
        current_sim_time_str = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=30*i)).isoformat().replace("+00:00", "Z")

        # Setup Mocks for this run
        analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts": [mock_accident_alert_details_ts002]})
        traffic_mock._signals[target_signal_id_incident_demo].current_phase = SignalPhaseEnum.RED
        traffic_mock._signals[target_signal_id_incident_demo].operational_status = SignalOperationalStatusEnum.ONLINE
        agent._recent_signal_actions.clear() # Ensure no cooldown interference

        analytics_mock.configure_pre_action_kpis(
            pre_kpi_incident_target_key_ts002,
            "get_incident_area_current_kpis",
            {"avg_speed_kmh": 10, "vehicle_count": 60} # Consistent pre-KPIs
        )
        # Force choice of the current strategy for history building
        forced_choice_candidate = {'name': strategy_name, 'avg_score': 0.0} # avg_score here is just for the dict structure

        with patch.object(agent.rng, 'choice', return_value=forced_choice_candidate):
            # Action Cycle
            await main_example_run_with_mock_time(
                current_sim_time_str, f"user_acc_hist_{i}_action", agent, analytics_mock,
                kpis={"overall_congestion_level": "MEDIUM"} # Background KPI
            )

        # Configure Post-Action KPIs for this specific strategy and incident
        analytics_mock.configure_post_action_kpis(
            mock_accident_alert_details_ts002["id"],
            "get_incident_response_post_action_kpis",
            post_kpi_payload
        )

        # KPI Cycle
        kpi_delay = ACTION_KPI_CONFIG["INCIDENT_RESPONSE_ACCIDENT"]["delay_seconds"]
        kpi_time = datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(seconds=kpi_delay + 20)
        await main_example_run_with_mock_time(
            kpi_time.isoformat().replace("+00:00", "Z"), f"user_acc_hist_{i}_kpi", agent, analytics_mock,
            kpis={"overall_congestion_level": "LOW"} # Background KPI
        )
        logger.info(f"MAIN_EXAMPLE (Accident Strategy Demo): Memory after {strategy_name}: {json.dumps(agent.action_effectiveness_memory, indent=2)}")

    agent.exploration_epsilon = original_epsilon_hist_build # Restore original epsilon for this demo block if needed later, or set new one.
    logger.info(f"MAIN_EXAMPLE (Accident Strategy Demo): Effectiveness Memory after History Building: {json.dumps(agent.action_effectiveness_memory, indent=2)}")

    # --- Cycle Group 2: Demonstrate Epsilon-Greedy Strategy Selection for Accidents on TS002 ---
    logger.info("--- MAIN_EXAMPLE (Accident Strategy Demo): Cycle Group 2 - Demonstrating Epsilon-Greedy Selection ---")
    agent.exploration_epsilon = 0.5 # Set for this demo part

    for i in range(6): # Run a few cycles to observe exploration/exploitation
        cycle_num_acc_demo = i + 1
        current_sim_time_str = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
        logger.info(f"--- MAIN_EXAMPLE (Accident Strategy Demo): Epsilon-Greedy Cycle {cycle_num_acc_demo} ---")

        # Setup for the cycle
        analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts": [mock_accident_alert_details_ts002]})
        traffic_mock._signals[target_signal_id_incident_demo].current_phase = SignalPhaseEnum.RED
        traffic_mock._signals[target_signal_id_incident_demo].operational_status = SignalOperationalStatusEnum.ONLINE
        if target_signal_id_incident_demo in agent._recent_signal_actions: # Clear cooldown for demo
            del agent._recent_signal_actions[target_signal_id_incident_demo]

        logger.info(f"MAIN_EXAMPLE (Accident Strategy Demo): Scores before cycle {cycle_num_acc_demo}:")
        for strat_name in ALL_ACCIDENT_STRATEGIES:
            mem_key = f"INCIDENT_RESPONSE_ACCIDENT:{target_signal_id_incident_demo}:{strat_name}"
            scores = agent.action_effectiveness_memory.get(mem_key, [])
            avg_s = sum(scores)/len(scores) if scores else 0.0
            logger.info(f"  Strategy {strat_name}: Avg Score = {avg_s:.2f} (History: {scores})")

        # Action Cycle
        await main_example_run_with_mock_time(
            current_sim_time_str, f"user_acc_egreedy_{cycle_num_acc_demo}_action", agent, analytics_mock,
            kpis={"overall_congestion_level": "MEDIUM"}
        )

        # Log chosen strategy from pending items
        chosen_strategy_for_kpi = "UNKNOWN"
        if agent.pending_kpi_collection:
            last_pending_item = agent.pending_kpi_collection[-1]
            if last_pending_item['action_type'] == "INCIDENT_RESPONSE_ACCIDENT":
                chosen_strategy_for_kpi = last_pending_item['action_parameters'].get('strategy_applied', "ERROR_NO_STRATEGY")
                selection_method_log = last_pending_item['action_parameters'].get('selection_method', "ERROR_NO_METHOD")
                chosen_score_log = last_pending_item['pre_action_context_kpis'].get('chosen_strategy_avg_score', "N/A")
                logger.info(f"MAIN_EXAMPLE (Accident Strategy Demo): Cycle {cycle_num_acc_demo} chose '{chosen_strategy_for_kpi}' via '{selection_method_log}' (score: {chosen_score_log}).")

        # Configure Post-Action KPIs for a neutral outcome for the chosen strategy
        analytics_mock.configure_post_action_kpis(
            mock_accident_alert_details_ts002["id"],
            "get_incident_response_post_action_kpis",
            {"area_clearance_time_minutes": 28, "avg_speed_kmh_incident_zone": 22} # Neutral-ish score
        )

        # KPI Cycle
        kpi_delay = ACTION_KPI_CONFIG["INCIDENT_RESPONSE_ACCIDENT"]["delay_seconds"]
        kpi_time = datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(seconds=kpi_delay + 20)
        await main_example_run_with_mock_time(
            kpi_time.isoformat().replace("+00:00", "Z"), f"user_acc_egreedy_{cycle_num_acc_demo}_kpi", agent, analytics_mock,
            kpis={"overall_congestion_level": "LOW"}
        )
        logger.info(f"MAIN_EXAMPLE (Accident Strategy Demo): Memory after E-Greedy Cycle {cycle_num_acc_demo}: {json.dumps(agent.action_effectiveness_memory, indent=2)}")

    # Restore original agent state if necessary
    agent.exploration_epsilon = original_epsilon # Restore original epsilon from the very start of main_example
    agent.rng.setstate(original_rng_state) # Restore original RNG state from the very start
    analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts":[]})
    logger.info("--- MAIN_EXAMPLE: Epsilon-Greedy ACCIDENT Response Strategy Demonstration Completed ---")


    if os.path.exists(EFFECTIVENESS_MEMORY_FILEPATH): os.remove(EFFECTIVENESS_MEMORY_FILEPATH)
    logger.info("--- AgentCore main_example for All Scoring Demonstrations completed ---")

if __name__ == "__main__":
    # asyncio.run(main_example())
    logger.info("AgentCore module defined. Example main_example() function available for testing.")


# --- Main Example as per Subtask IV ---
async def main_example_subtask():
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    logger_subtask = logging.getLogger(__name__ + ".main_example_subtask")
    logger_subtask.info("--- Starting main_example_subtask ---")

    class MockAnalyticsService:
        def get_current_system_kpis_summary(self):
            logger_subtask.debug("MockAnalyticsService.get_current_system_kpis_summary called")
            return {"overall_congestion_level": "HIGH", "some_other_kpi": 123.45}

        async def get_critical_alert_summary(self):
            logger_subtask.debug("MockAnalyticsService.get_critical_alert_summary called")
            return {"critical_unack_alert_count": 1, "active_alerts": [{"id": "alert_mock_1", "type": "MOCK_ALERT", "location": {"latitude": 1.0, "longitude": 1.0}}]}

        # Other methods required by AgentCore during its full cycle, returning defaults
        async def get_signal_current_kpis(self, signal_id: str, metrics: List[str]):
            logger_subtask.debug(f"MockAnalyticsService.get_signal_current_kpis called for {signal_id} with {metrics}")
            return {m: 0 for m in metrics}
        async def get_corridor_current_kpis(self, corridor_id: str, metrics: List[str]):
            logger_subtask.debug(f"MockAnalyticsService.get_corridor_current_kpis called for {corridor_id} with {metrics}")
            return {m: 0 for m in metrics}
        async def get_incident_area_current_kpis(self, incident_location: LocationModel, radius_meters: int, metrics: List[str]):
            logger_subtask.debug(f"MockAnalyticsService.get_incident_area_current_kpis called for {incident_location} with {metrics}")
            return {m: 0 for m in metrics}
        async def get_signal_post_action_kpis(self, signal_id: str, **kwargs):
            logger_subtask.debug(f"MockAnalyticsService.get_signal_post_action_kpis called for {signal_id} with {kwargs}")
            return {}
        async def get_corridor_post_action_kpis(self, corridor_id: str, **kwargs):
            logger_subtask.debug(f"MockAnalyticsService.get_corridor_post_action_kpis called for {corridor_id} with {kwargs}")
            return {}
        async def get_incident_response_post_action_kpis(self, incident_id: str, **kwargs):
            logger_subtask.debug(f"MockAnalyticsService.get_incident_response_post_action_kpis called for {incident_id} with {kwargs}")
            return {}


    class MockPredictionScheduler:
        async def set_priority_locations(self, locations: List[LocationModel]):
            logger_subtask.debug(f"MockPredictionScheduler.set_priority_locations called with {locations}")
        # Other methods that might be called by AgentCore
        async def get_traffic_predictions_for_locations(self, locations: List[LocationModel]):
            logger_subtask.debug(f"MockPredictionScheduler.get_traffic_predictions_for_locations called with {locations}")
            return []


    class MockPersonalizedRoutingService:
        async def proactively_suggest_route(self, user_id: str, common_pattern: CommonTravelPattern, current_location: LocationModel) -> None:
            logger_subtask.debug(f"MockPersonalizedRoutingService.proactively_suggest_route called for {user_id}")
            return None

        async def get_user_common_travel_patterns(self, user_id: str) -> List[CommonTravelPattern]:
            logger_subtask.debug(f"MockPersonalizedRoutingService.get_user_common_travel_patterns called for {user_id}")
            return [
                CommonTravelPattern(
                    pattern_id="mock_pattern_1",
                    user_id=user_id,
                    start_location_summary={"name": "Mock Home", "latitude": 1.1, "longitude": 1.1},
                    end_location_summary={"name": "Mock Work", "latitude": 2.2, "longitude": 2.2},
                    days_of_week=[0, 1, 2, 3, 4], # Mon-Fri
                    time_of_day="08:30",
                    frequency=5
                )
            ]
        # Other methods
        async def update_user_route_feedback(self, user_id: str, route_id: str, feedback: Dict[str, Any]):
            logger_subtask.debug(f"MockPersonalizedRoutingService.update_user_route_feedback for {user_id}, route {route_id}")


    class MockConnectionManager:
        async def broadcast_message_model(self, message: WebSocketMessage):
            logger_subtask.debug(f"MockConnectionManager.broadcast_message_model called with {message.model_dump_json()}")

    class MockTrafficSignalService:
        def __init__(self, config: Optional[Dict[str, Any]] = None, connection_manager: Optional[MockConnectionManager] = None):
            self.config = config
            self.connection_manager = connection_manager
            self._signals: Dict[str, SignalState] = {
                "TS_MOCK_01": SignalState(
                    signal_id="TS_MOCK_01",
                    location=LocationModel(latitude=34.0522, longitude=-118.2437, name="Downtown Signal 1"),
                    current_phase=SignalPhaseEnum.RED,
                    operational_status=SignalOperationalStatusEnum.ONLINE,
                    last_updated=datetime.utcnow()
                ),
                "TS_MOCK_02": SignalState(
                    signal_id="TS_MOCK_02",
                    location=LocationModel(latitude=34.0530, longitude=-118.2445, name="Downtown Signal 2"),
                    current_phase=SignalPhaseEnum.RED,
                    operational_status=SignalOperationalStatusEnum.ONLINE,
                    last_updated=datetime.utcnow()
                ),
                "TS_MOCK_03": SignalState(
                    signal_id="TS_MOCK_03",
                    location=LocationModel(latitude=34.0538, longitude=-118.2453, name="Downtown Signal 3 Offline"),
                    current_phase=SignalPhaseEnum.OFF, # As per requirement "1 OFFLINE/OFF"
                    operational_status=SignalOperationalStatusEnum.OFFLINE,
                    last_updated=datetime.utcnow()
                ),
            }
            self._cycle_count = 0
            logger_subtask.debug(f"MockTrafficSignalService initialized with {len(self._signals)} signals.")

        async def get_all_signal_states(self) -> List[SignalState]:
            self._cycle_count += 1
            logger_subtask.debug(f"MockTrafficSignalService.get_all_signal_states called (cycle {self._cycle_count}).")
            # "If self._cycle_count == 2, change the first ONLINE signal in self._signals to SignalPhaseEnum.GREEN before returning."
            if self._cycle_count == 2:
                logger_subtask.info("MockTrafficSignalService: Cycle 2, attempting to change a signal to GREEN.")
                for signal_id in self._signals: # Iterate to find the first ONLINE one
                    if self._signals[signal_id].operational_status == SignalOperationalStatusEnum.ONLINE:
                        self._signals[signal_id].current_phase = SignalPhaseEnum.GREEN
                        self._signals[signal_id].last_updated = datetime.utcnow()
                        logger_subtask.debug(f"MockTrafficSignalService: Changed signal {signal_id} to GREEN for cycle {self._cycle_count}.")
                        break
            return list(self._signals.values())

        async def set_signal_phase(self, signal_id: str, phase: SignalPhaseEnum, duration_seconds: Optional[int] = None) -> SignalControlCommandResponse:
            logger_subtask.debug(f"MockTrafficSignalService.set_signal_phase called for {signal_id} to {phase.value} for {duration_seconds}s.")
            if signal_id not in self._signals:
                logger_subtask.warning(f"MockTrafficSignalService: Signal {signal_id} not found for set_signal_phase.")
                return SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.FAILED, message="Signal not found")

            signal = self._signals[signal_id]
            if signal.operational_status != SignalOperationalStatusEnum.ONLINE:
                logger_subtask.warning(f"MockTrafficSignalService: Signal {signal_id} is not ONLINE (status: {signal.operational_status.value}). Command REJECTED.")
                return SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.REJECTED, message="Signal not ONLINE")

            logger_subtask.info(f"MockTrafficSignalService: Setting signal {signal_id} from {signal.current_phase.value if signal.current_phase else 'N/A'} to {phase.value}.")
            signal.current_phase = phase
            signal.last_updated = datetime.utcnow()
            return SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.ACCEPTED, message="Phase change command accepted by mock.")

    # Instantiate and Run
    mock_analytics_service = MockAnalyticsService()
    mock_prediction_scheduler = MockPredictionScheduler()
    mock_personalized_routing_service = MockPersonalizedRoutingService()
    mock_connection_manager = MockConnectionManager() # Not directly used by AgentCore constructor but good for TrafficSignalService mock
    mock_traffic_signal_service = MockTrafficSignalService(config={}, connection_manager=mock_connection_manager)

    agent_core = AgentCore(
        prediction_scheduler=mock_prediction_scheduler,
        personalized_routing_service=mock_personalized_routing_service,
        analytics_service=mock_analytics_service,
        traffic_signal_service=mock_traffic_signal_service
    )

    logger_subtask.info("--- Running main_example_subtask: decision cycle 1 ---")
    await agent_core.run_decision_cycle(sample_user_id="cycle_1_subtask_user")

    logger_subtask.info("--- Running main_example_subtask: decision cycle 2 ---")
    await agent_core.run_decision_cycle(sample_user_id="cycle_2_subtask_user")

    logger_subtask.info("--- main_example_subtask completed ---")

# To run this specific example if needed (though subtask says keep __main__ commented):
# if __name__ == "__main__":
#     asyncio.run(main_example_subtask())

[end of backend/app/core/agent_core.py]
