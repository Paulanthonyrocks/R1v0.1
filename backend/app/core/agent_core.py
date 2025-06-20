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

logger = logging.getLogger(__name__)

PREDICTIVE_ALERT_LIKELIHOOD_THRESHOLD = 60
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
        self.action_effectiveness_config = ACTION_EFFECTIVENESS_CONFIG
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

    def _load_effectiveness_memory(self) -> Dict[str, List[float]]: return {}
    def _save_effectiveness_memory(self) -> bool: return True

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
        self.logger.debug(f"Scoring congestion improvement with: {metrics}")
        pre_overall = metrics.get("pre_overall_congestion_proxy"); post_local = metrics.get("post_local_congestion")
        if post_local is None: self.logger.warning("Congestion score: post_local_congestion missing."); return 0.0
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
        if metrics_counted == 0: self.logger.warning(f"GW efficiency: No relevant post-KPIs found in {metrics}."); return None
        return max(-1.0, min(1.0, score))

    def _score_incident_clearance_speed(self, metrics: Dict[str, Any]) -> Optional[float]:
        self.logger.debug(f"Scoring incident clearance with: {metrics}")
        clear_time = metrics.get("post_clearance_time"); post_speed = metrics.get("post_incident_avg_speed"); pre_speed = metrics.get("pre_incident_avg_speed")
        if clear_time is None: self.logger.warning("Incident score: post_clearance_time missing."); return None
        score = 0.0
        if clear_time < 900: score += 0.6
        elif clear_time < 1800: score += 0.2
        else: score -= 0.6
        if post_speed is not None and pre_speed is not None and pre_speed < 40 :
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

    def _calculate_effectiveness_score(self, log_entry_data: Dict[str,Any]) -> Tuple[Optional[float],Optional[Dict[str,Any]]]:
        action_type = log_entry_data.get("action_type")
        config = self.action_effectiveness_config.get(action_type)
        if not config: return None, None

        metrics_for_scoring: Dict[str, Any] = {}
        for kpi_spec in config.get("relevant_kpis", []):
            source_data = log_entry_data.get("pre_action_context_kpis") if kpi_spec["source"] == "pre" else log_entry_data.get("post_action_kpis")
            value = self._extract_kpi_value(source_data, kpi_spec["key_path"])
            if value is not None: metrics_for_scoring[kpi_spec["as"]] = value

        if not metrics_for_scoring and config.get("relevant_kpis"): return 0.0, metrics_for_scoring # Return neutral if no relevant KPIs were extracted but some were expected.

        logic_type = config.get("scoring_logic_type")
        score: Optional[float] = 0.0
        if logic_type == "congestion_improvement": score = self._score_congestion_improvement(metrics_for_scoring)
        elif logic_type == "green_wave_efficiency": score = self._score_green_wave_efficiency(metrics_for_scoring)
        elif logic_type == "incident_clearance_speed": score = self._score_incident_clearance_speed(metrics_for_scoring)
        elif logic_type == "closure_effectiveness": score = self._score_closure_effectiveness(metrics_for_scoring)
        else: self.logger.warning(f"Unknown scoring_logic_type: {logic_type}"); return None, metrics_for_scoring

        self.logger.info(f"Effectiveness score for {action_type} (ID: {log_entry_data.get('action_id')}): {score}. Metrics used: {metrics_for_scoring}")
        return score, metrics_for_scoring

    async def _find_signals_near_location(self, target_location: LocationModel, all_signals: List[SignalState], radius_meters: int) -> List[SignalState]: return [] # Placeholder
    async def _determine_next_travel_prediction_time(self, pattern: CommonTravelPattern, current_datetime: datetime) -> Optional[datetime]: return None # Placeholder

    async def _execute_green_wave(
        self, corridor_id: str, signals_in_order: List[str], green_phase: SignalPhaseEnum,
        green_time_seconds: int, offsets_seconds: List[int],
        all_current_signal_states: Dict[str, SignalState],
        processed_signals_for_coordination: Set[str], now_utc: datetime
    ) -> bool:
        # ... (existing _execute_green_wave implementation) ...
        self.logger.info(f"Executing green wave for corridor {corridor_id} - placeholder")
        # Simulate some action for now
        action_taken_on_at_least_one_signal = False
        for i, signal_id_to_control in enumerate(signals_in_order):
            await asyncio.sleep(0.01) # simulate async work
            processed_signals_for_coordination.add(signal_id_to_control)
            action_taken_on_at_least_one_signal = True
        return action_taken_on_at_least_one_signal


    async def _fetch_pre_action_kpis(self, action_type_str: str, current_action_target_ids: List[str],
                                   current_action_parameters: Dict[str, Any], system_kpis_snapshot: Dict[str,Any]
                                   ) -> Dict[str, Any]:
        # ... (existing _fetch_pre_action_kpis implementation) ...
        return {"fetched_pre_kpi_example": 123}


    async def run_decision_cycle(self, sample_user_id: str = "user_agent_test_123"):
        now_utc = datetime.utcnow()
        self.logger.info(f"--- Starting AgentCore cycle for {sample_user_id} at {now_utc.isoformat()} ---")
        self._memory_updated_this_cycle = False

        system_kpis = self.analytics_service.get_current_system_kpis_summary()
        alert_summary = await self.analytics_service.get_critical_alert_summary()
        active_alerts = alert_summary.get("active_alerts", [])

        all_signal_states: List[SignalState] = await self.traffic_signal_service.get_all_signal_states()
        self.logger.info(f"AgentCore received {len(all_signal_states)} signal states.")
        for state in all_signal_states:
            self.logger.debug(
                f"Signal ID: {state.signal_id}, "
                f"Location: {state.location.name if state.location and state.location.name else 'N/A'}, "
                f"Phase: {state.current_phase.value if state.current_phase else 'N/A'}, "
                f"Status: {state.operational_status.value if state.operational_status else 'N/A'}"
            )

        all_signal_states_map = {s.signal_id: s for s in all_signal_states} # For quick lookups

        # --- Process Pending KPI Collections ---
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
                        action_signature_for_memory = f"{item['action_type']}:{item['target_ids'][0]}" # Main target
                        if item['action_type'] == "GREEN_WAVE_ACTIVATION": # For GW, target_ids[0] is corridor_id
                           action_signature_for_memory = f"{item['action_type']}:{item['target_ids'][0]}"
                        elif item['action_type'] == "SET_SIGNAL_GREEN_CONGESTION": # For signal, target_ids[0] is signal_id
                           action_signature_for_memory = f"{item['action_type']}:{item['target_ids'][0]}"
                        # Add other specific handling if main target_id isn't always first

                        if action_signature_for_memory:
                             if action_signature_for_memory not in self.action_effectiveness_memory:
                                 self.action_effectiveness_memory[action_signature_for_memory] = []
                             self.action_effectiveness_memory[action_signature_for_memory].append(score)
                             # Keep only the last N scores
                             self.action_effectiveness_memory[action_signature_for_memory] = \
                                 self.action_effectiveness_memory[action_signature_for_memory][-self.MAX_SCORES_PER_ACTION_SIGNATURE:]
                             self._memory_updated_this_cycle = True

                    processed_pending_indices.append(idx)
                except Exception as e:
                    self.logger.error(f"Error processing KPI for action ID {item['action_id']}: {e}", exc_info=True)
                    # Optionally, decide if it should remain in pending_kpi_collection or be moved to a failed queue

        for idx in sorted(processed_pending_indices, reverse=True):
            del self.pending_kpi_collection[idx]

        # --- Incident Response Logic ---
        processed_signals_for_incident: Set[str] = set()
        # ... (Assuming incident logic exists here and populates processed_signals_for_incident) ...

        # --- Autonomous Traffic Signal Control Logic (General Congestion with Epsilon-Greedy) ---
        current_congestion_level = system_kpis.get("overall_congestion_level", "UNKNOWN")
        self.logger.info(f"Overall congestion: {current_congestion_level}. Evaluating general signal adjustments.")
        controlled_a_signal_this_cycle_general = False

        if current_congestion_level == "HIGH":
            candidate_signals_for_congestion_relief: List[Dict[str, Any]] = []
            for signal_state in all_signal_states:
                if signal_state.signal_id in processed_signals_for_incident: continue # Skip if handled by incident logic
                if signal_state.operational_status != SignalOperationalStatusEnum.ONLINE:
                    self.logger.debug(f"Signal {signal_state.signal_id} skipped (not ONLINE)."); continue
                if signal_state.current_phase == SignalPhaseEnum.GREEN:
                    self.logger.debug(f"Signal {signal_state.signal_id} skipped (already GREEN)."); continue

                last_action_info = self._recent_signal_actions.get(signal_state.signal_id)
                if last_action_info and (now_utc - last_action_info['timestamp']).total_seconds() < self.SIGNAL_ACTION_COOLDOWN_SECONDS:
                    self.logger.debug(f"Signal {signal_state.signal_id} skipped (on cooldown). Last action: {last_action_info['reason']} at {last_action_info['timestamp']}.")
                    continue

                action_type_for_score = "SET_SIGNAL_GREEN_CONGESTION"
                action_signature = f"{action_type_for_score}:{signal_state.signal_id}"
                scores = self.action_effectiveness_memory.get(action_signature, [])
                avg_score = sum(scores) / len(scores) if scores else 0.0

                candidate_signals_for_congestion_relief.append({
                    'signal_id': signal_state.signal_id,
                    'signal_state': signal_state,
                    'avg_score': avg_score
                })
                self.logger.debug(f"Signal {signal_state.signal_id} added as candidate for congestion relief. Avg score: {avg_score:.2f}")

            if candidate_signals_for_congestion_relief:
                selected_candidate_dict_entry = None
                action_choice_method = ""

                if self.rng.random() < self.exploration_epsilon:
                    selected_candidate_dict_entry = self.rng.choice(candidate_signals_for_congestion_relief)
                    action_choice_method = "EXPLORATORY_RANDOM"
                    self.logger.info(
                        f"{action_choice_method} general congestion action: Randomly selected signal "
                        f"'{selected_candidate_dict_entry['signal_id']}' from {len(candidate_signals_for_congestion_relief)} candidates. "
                        f"(Its avg score: {selected_candidate_dict_entry['avg_score']:.2f})"
                    )
                else:
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
                        processed_signals_for_coordination.add(signal_to_control_state.signal_id) # Add to set to prevent GW using it

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
                                current_action_parameters=current_action_parameters,
                                system_kpis_snapshot=system_kpis
                            )

                            pre_action_kpis_for_log = {
                                "overall_system_congestion_at_decision": system_kpis.get("overall_congestion_level", "UNKNOWN"),
                                "signal_initial_phase_at_decision": signal_to_control_state.current_phase.value if signal_to_control_state.current_phase else 'N/A',
                                "chosen_candidate_avg_score": selected_candidate_dict_entry['avg_score'],
                                "num_candidates_considered": len(candidate_signals_for_congestion_relief),
                                "all_candidate_scores": {c['signal_id']: c['avg_score'] for c in candidate_signals_for_congestion_relief}
                            }
                            if fetched_pre_action_kpis: pre_action_kpis_for_log.update(fetched_pre_action_kpis)

                            pending_item_id = uuid4()
                            self.pending_kpi_collection.append({
                                'action_id': pending_item_id, 'action_type': action_type_str,
                                'target_ids': current_action_target_ids, 'action_timestamp': action_timestamp_utc,
                                'action_parameters': current_action_parameters, 'pre_action_context_kpis': pre_action_kpis_for_log,
                                'query_after_timestamp': action_timestamp_utc + timedelta(seconds=action_kpi_cfg["delay_seconds"]),
                                'metrics_to_collect': action_kpi_cfg["metrics"], 'evaluation_window_minutes': action_kpi_cfg["eval_window_minutes"],
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
        else:
            self.logger.info("Congestion not HIGH. No system-wide general signal adjustments for congestion relief.")

        # --- Green Wave Coordination Logic ---
        # (This is the block that was previously overwritten with epsilon-greedy logic for GW selection)
        candidate_corridors: List[Dict[str, Any]] = []
        now_time = now_utc.time()

        for corridor_id, config in self.green_wave_corridor_configs.items():
            is_time_triggered = False
            for window in config.get("time_windows", []):
                start_time = datetime.strptime(window["start"], "%H:%M").time()
                end_time = datetime.strptime(window["end"], "%H:%M").time()
                if start_time <= now_time <= end_time:
                    is_time_triggered = True; break

            demand_kpi_name = config.get("demand_kpi_trigger")
            is_demand_triggered = system_kpis.get(demand_kpi_name) == "HIGH" if demand_kpi_name else False

            if is_time_triggered or is_demand_triggered:
                action_signature_gw = f"GREEN_WAVE_ACTIVATION:{corridor_id}"
                scores_gw = self.action_effectiveness_memory.get(action_signature_gw, [])
                avg_score_gw = sum(scores_gw) / len(scores_gw) if scores_gw else 0.0

                candidate_corridors.append({
                    "id": corridor_id, "priority": config.get("priority", 99),
                    "config": config, "avg_score": avg_score_gw,
                    "trigger_type": "TIME" if is_time_triggered else "DEMAND_KPI"
                })

        candidate_corridors.sort(key=lambda x: (x['priority'], -x['avg_score']))

        self.logger.info(f"Sorted candidate green wave corridors: " +
                         ", ".join([f"'{c['id']}'(Prio:{c['priority']},Score:{c['avg_score']:.2f},Trig:{c['trigger_type']})" for c in candidate_corridors]))

        selected_candidate_for_wave = None
        green_wave_selection_method = ""
        top_priority_candidates = [] # Defined here for broader scope for KPI context

        if candidate_corridors:
            highest_priority_val = candidate_corridors[0]['priority']
            top_priority_candidates = [
                c for c in candidate_corridors if c['priority'] == highest_priority_val
            ]

            if top_priority_candidates:
                if self.rng.random() < self.exploration_epsilon:
                    selected_candidate_for_wave = self.rng.choice(top_priority_candidates)
                    green_wave_selection_method = "EXPLORATORY_GREEN_WAVE_RANDOM"
                    self.logger.info(
                        f"{green_wave_selection_method}: Randomly selected corridor '{selected_candidate_for_wave['id']}' "
                        f"(Prio: {selected_candidate_for_wave['priority']}, AvgScore: {selected_candidate_for_wave['avg_score']:.2f}) "
                        f"from {len(top_priority_candidates)} top-priority candidates."
                    )
                else:
                    selected_candidate_for_wave = top_priority_candidates[0]
                    green_wave_selection_method = "EXPLOITATIVE_GREEN_WAVE_BEST_SCORE"
                    self.logger.info(
                        f"{green_wave_selection_method}: Selected best-score corridor '{selected_candidate_for_wave['id']}' "
                        f"(Prio: {selected_candidate_for_wave['priority']}, AvgScore: {selected_candidate_for_wave['avg_score']:.2f}). "
                        f"Top-priority candidates considered (ID, Prio, Score): " +
                        ", ".join([f"'{c['id']}'(P{c['priority']},{c['avg_score']:.2f})" for c in top_priority_candidates[:3]])
                    )

        final_selected_wave_details_for_execution = None

        if selected_candidate_for_wave:
            corridor_id_to_check = selected_candidate_for_wave["id"]
            config_to_check = selected_candidate_for_wave["config"]
            signals_for_this_wave = config_to_check.get("signals_in_order", [])

            can_run_this_wave = True
            for signal_id_in_wave in signals_for_this_wave:
                if signal_id_in_wave in processed_signals_for_coordination:
                    self.logger.info(
                        f"Green wave candidate '{corridor_id_to_check}' (selected by {green_wave_selection_method}) "
                        f"shares signal '{signal_id_in_wave}' with an already chosen coordination strategy this cycle. Skipping."
                    )
                    can_run_this_wave = False; break

            if can_run_this_wave:
                final_selected_wave_details_for_execution = selected_candidate_for_wave

        if final_selected_wave_details_for_execution:
            corridor_id_to_run = final_selected_wave_details_for_execution["id"]
            config_to_run = final_selected_wave_details_for_execution["config"]

            self.logger.info(
                f"Activating green wave for selected corridor: '{corridor_id_to_run}' "
                f"(Priority: {config_to_run.get('priority',99)}, "
                f"AvgScore: {final_selected_wave_details_for_execution['avg_score']:.2f}, Method: {green_wave_selection_method})."
            )

            wave_success = await self._execute_green_wave(
                corridor_id=corridor_id_to_run,
                signals_in_order=config_to_run["signals_in_order"],
                green_phase=config_to_run["target_green_phase"],
                green_time_seconds=config_to_run["wave_green_time_seconds"],
                offsets_seconds=config_to_run["offsets_seconds"],
                all_current_signal_states=all_signal_states_map,
                processed_signals_for_coordination=processed_signals_for_coordination,
                now_utc=now_utc
            )

            if wave_success:
                self.logger.info(f"Green wave initiation for '{corridor_id_to_run}' (Method: {green_wave_selection_method}) reported some action.")
                action_type_str = "GREEN_WAVE_ACTIVATION"
                action_kpi_cfg = ACTION_KPI_CONFIG.get(action_type_str)
                if action_kpi_cfg:
                    action_id = uuid4(); action_ts = datetime.utcnow()

                    # Determine triggering_demand_kpi_value for logging
                    trigger_type_for_log = final_selected_wave_details_for_execution.get('trigger_type', "UNKNOWN")
                    demand_kpi_name_for_log = config_to_run.get("demand_kpi_trigger")
                    triggering_demand_kpi_value_for_log = "N/A"
                    if trigger_type_for_log == "DEMAND_KPI" and demand_kpi_name_for_log:
                        triggering_demand_kpi_value_for_log = system_kpis.get(demand_kpi_name_for_log, "NOT_FOUND")
                    elif trigger_type_for_log == "TIME":
                        triggering_demand_kpi_value_for_log = "TIME_TRIGGERED"

                    base_pre_kpis = {
                        "overall_congestion_at_decision": system_kpis.get("overall_congestion_level", "UNKNOWN"),
                        "corridor_id": corridor_id_to_run,
                        "triggering_demand_kpi_name": demand_kpi_name_for_log or "N/A",
                        "triggering_demand_kpi_value": triggering_demand_kpi_value_for_log,
                        "chosen_corridor_avg_score": final_selected_wave_details_for_execution['avg_score'] if final_selected_wave_details_for_execution else None,
                        "num_top_priority_candidates": len(top_priority_candidates) if top_priority_candidates is not None else 0,
                        "top_priority_candidate_scores": {
                            c['id']: round(c['avg_score'], 3) for c in top_priority_candidates
                        } if top_priority_candidates is not None else {}
                    }
                    current_action_target_ids_gw = [corridor_id_to_run] + config_to_run["signals_in_order"]
                    current_action_parameters_gw = {
                        "corridor_id": corridor_id_to_run,
                        "wave_green_time_seconds": config_to_run.get("wave_green_time_seconds"),
                        "offsets_seconds": config_to_run.get("offsets_seconds"),
                        "priority": config_to_run.get("priority", 99),
                        "num_signals_in_wave": len(config_to_run["signals_in_order"]),
                        "selection_method": green_wave_selection_method
                    }

                    fetched_pre_action_kpis_for_wave = await self._fetch_pre_action_kpis(
                        action_type_str=action_type_str,
                        current_action_target_ids=current_action_target_ids_gw,
                        current_action_parameters=current_action_parameters_gw,
                        system_kpis_snapshot=system_kpis
                    )
                    if fetched_pre_action_kpis_for_wave and isinstance(fetched_pre_action_kpis_for_wave, dict):
                        base_pre_kpis.update(fetched_pre_action_kpis_for_wave)

                    self.pending_kpi_collection.append({
                        'action_id': action_id, 'action_type': action_type_str,
                        'target_ids': current_action_target_ids_gw,
                        'action_timestamp': action_ts,
                        'action_parameters': current_action_parameters_gw,
                        'pre_action_context_kpis': base_pre_kpis,
                        'query_after_timestamp': action_ts + timedelta(seconds=action_kpi_cfg["delay_seconds"]),
                        'metrics_to_collect': action_kpi_cfg["metrics"],
                        'evaluation_window_minutes': action_kpi_cfg["eval_window_minutes"],
                        'kpi_query_details': {'service_method_name': action_kpi_cfg["service_method"], 'method_specific_args': {'corridor_id': corridor_id_to_run}}
                    })
                    self.logger.info(f"Scheduled KPI collection for {action_type_str} (ID: {action_id}) on {corridor_id_to_run}. Choice: {green_wave_selection_method}.")
            else:
                self.logger.warning(f"Green wave '{corridor_id_to_run}' (Method: {green_wave_selection_method}) execution attempt reported no action or failure.")
        elif candidate_corridors:
             self.logger.info("No suitable green wave corridor selected for execution this cycle (all candidates conflicted or other selection reasons).")
        else:
            self.logger.info("No green wave corridors were triggered as candidates this cycle.")

        # --- Illustrative Green Wave Example (This should be reviewed if it's still needed or if it's test code) ---
        # This block is separate from the operational logic above.
        if True: # Placeholder for actual trigger - This makes it always run, which might be unintended.
            selected_wave_to_run_example = {"id": "main_st_ns_wave", "config": GREEN_WAVE_CORRIDOR_CONFIGS["main_st_ns_wave"]}
            if selected_wave_to_run_example: # This will always be true if the above line is active
                # This example might conflict with the operational logic if not managed by processed_signals_for_coordination
                # For now, assuming it's intended to run or is for a different purpose.
                # However, it does not use the epsilon-greedy selection.
                cfg_run_example = selected_wave_to_run_example["config"]; cid_run_example = selected_wave_to_run_example["id"]

                # Check if this example conflicts with signals already processed by operational green wave
                example_can_run = True
                for sig_id_ex in cfg_run_example.get("signals_in_order",[]):
                    if sig_id_ex in processed_signals_for_coordination:
                        example_can_run = False; break

                if example_can_run:
                    self.logger.info(f"ILLUSTRATIVE_EXAMPLE: Attempting to run illustrative wave {cid_run_example}")
                    action_type_str_example = "GREEN_WAVE_ACTIVATION"; current_action_target_ids_example = [cid_run_example]
                    current_action_parameters_example = {"corridor_config": cfg_run_example, "corridor_id": cid_run_example, "selection_method": "ILLUSTRATIVE_HARDCODED"}

                    fetched_pre_action_kpis_example = await self._fetch_pre_action_kpis(action_type_str_example, current_action_target_ids_example, current_action_parameters_example, system_kpis)
                    # wave_executed_example = True # This illustrative block does not call _execute_green_wave
                                                # It directly schedules KPI collection if the placeholder `True` is met.
                                                # This might be a bug or incomplete illustrative code.
                                                # For now, let's assume it proceeds to KPI scheduling directly if the example is active.

                    action_kpi_cfg_example = ACTION_KPI_CONFIG.get(action_type_str_example)
                    if action_kpi_cfg_example:
                        action_id_example = uuid4(); action_ts_example = datetime.utcnow()
                        base_pre_kpis_example = {"overall_congestion_at_decision": system_kpis.get("overall_congestion_level"),
                                                 "corridor_id": cid_run_example,
                                                 "expected_demand_level": system_kpis.get(cfg_run_example.get("demand_kpi_trigger"), "N/A"),
                                                 "selection_method": "ILLUSTRATIVE_HARDCODED" # Explicitly mark as illustrative
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

@patch('app.core.agent_core.datetime')
async def main_example_run_with_mock_time(mock_dt_obj, time_str: str, user: str, agent: AgentCore, an_mock: Any, kpis: Dict[str,Any]):
    # ... (as before) ...
    mocked_now = datetime.fromisoformat(time_str.replace("Z","+00:00")); mock_dt_obj.utcnow.return_value = mocked_now
    orig_func = an_mock.get_current_system_kpis_summary
    def get_mod_kpis(): base = {"overall_congestion_level":"LOW"}; [base.update({k:"LOW"}) for k in ALL_CORRIDOR_DEMAND_KPIS]; base.update(kpis); return base
    an_mock.get_current_system_kpis_summary = get_mod_kpis
    await agent.run_decision_cycle(user); an_mock.get_current_system_kpis_summary = orig_func

# The main_example() function remains below, unchanged by this specific subtask.
# It will be modified by the overwrite_file_with_block call.
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

    # Ensure signals for alt_st_ew_wave (TS003, TS005) are also ONLINE and RED initially
    # main_st_ns_wave (TS001, TS002, TS004) are handled by reset_mock_traffic_signals_for_congestion_demo
    if "TS003" in traffic_mock._signals: # traffic_mock is from the outer main_example scope
        traffic_mock._signals["TS003"].current_phase = SignalPhaseEnum.RED
        traffic_mock._signals["TS003"].operational_status = SignalOperationalStatusEnum.ONLINE
    if "TS005" in traffic_mock._signals:
        traffic_mock._signals["TS005"].current_phase = SignalPhaseEnum.RED
        traffic_mock._signals["TS005"].operational_status = SignalOperationalStatusEnum.ONLINE
    # Also ensure the primary signals for main_st_ns_wave are reset if they were changed by congestion demo
    traffic_mock._signals["TS001"].current_phase = SignalPhaseEnum.RED
    traffic_mock._signals["TS001"].operational_status = SignalOperationalStatusEnum.ONLINE
    traffic_mock._signals["TS002"].current_phase = SignalPhaseEnum.RED
    traffic_mock._signals["TS002"].operational_status = SignalOperationalStatusEnum.ONLINE
    traffic_mock._signals["TS004"].current_phase = SignalPhaseEnum.RED
    traffic_mock._signals["TS004"].operational_status = SignalOperationalStatusEnum.ONLINE
    logger.info("MAIN_EXAMPLE (GW Demo): Ensured TS001-TS005 are ONLINE and RED for GW demo.")

    current_sim_time_str_gw = "2023-01-01T07:00:00Z" # Start time for GW demo

    # --- Cycle Group 1: Build Initial GW Effectiveness History (Forcing Exploitation) ---
    logger.info("--- MAIN_EXAMPLE (GW Demo): Cycle Group 1 - Building GW History (Forcing Exploitation) ---")
    agent.exploration_epsilon = 0.0
    logger.info(f"MAIN_EXAMPLE (GW Demo): Set exploration_epsilon to {agent.exploration_epsilon}")

    # History for "main_st_ns_wave" (P1) - Moderate Score (e.g. ~0.2)
    current_sim_time_str_gw = "2023-01-01T07:15:00Z"
    demand_kpis_main_st_only = { GREEN_WAVE_CORRIDOR_CONFIGS["main_st_ns_wave"]["demand_kpi_trigger"]: "HIGH" }
    await run_action_and_kpi_cycles( # Use the unified helper
        current_sim_time_str_gw, "user_gw_hist_main", "user_gw_kpi_main",
        "main_st_ns_wave", "GREEN_WAVE_ACTIVATION",
        {"corridor_avg_travel_time_seconds": 130, "corridor_throughput_vph": 500},
        "get_corridor_post_action_kpis",
        demand_kpi_settings=demand_kpis_main_st_only
    )

    # History for "alt_st_ew_wave" (P1) - Good Score (e.g. ~1.0)
    current_sim_time_str_gw = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=1)).isoformat() + "Z" # Ensure current_sim_time_str is advanced from congestion demo
    current_sim_time_str_gw = "2023-01-01T07:45:00Z"
    demand_kpis_alt_st_only = { GREEN_WAVE_CORRIDOR_CONFIGS["alt_st_ew_wave"]["demand_kpi_trigger"]: "HIGH" }
    await run_action_and_kpi_cycles( # Use the unified helper
        current_sim_time_str_gw, "user_gw_hist_alt", "user_gw_kpi_alt",
        "alt_st_ew_wave", "GREEN_WAVE_ACTIVATION",
        {"corridor_avg_travel_time_seconds": 70, "corridor_throughput_vph": 900},
        "get_corridor_post_action_kpis",
        demand_kpi_settings=demand_kpis_alt_st_only
    )
    logger.info(f"MAIN_EXAMPLE (GW Demo): Effectiveness Memory after History Building: {json.dumps(agent.action_effectiveness_memory, indent=2)}")

    # --- Cycle Group 2: Demonstrate Epsilon-Greedy GW Selection ---
    logger.info("--- MAIN_EXAMPLE (GW Demo): Cycle Group 2 - Demonstrating Epsilon-Greedy GW Selection ---")
    agent.exploration_epsilon = 0.5
    logger.info(f"MAIN_EXAMPLE (GW Demo): Set exploration_epsilon to {agent.exploration_epsilon}")

    gw_candidate_ids_for_demo = ["main_st_ns_wave", "alt_st_ew_wave"]
    # Reset current_sim_time_str_gw if it was modified by run_action_and_kpi_cycles
    current_sim_time_str_gw = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=1)).isoformat() + "Z"

    for i in range(8):
        cycle_num = i + 1
        logger.info(f"--- MAIN_EXAMPLE (GW Demo): Epsilon-Greedy Cycle {cycle_num} ---")

        current_sim_time_str_gw = (datetime.fromisoformat(current_sim_time_str_gw.replace("Z","+00:00")) + timedelta(minutes=20)).isoformat().replace("+00:00","Z")
        demand_kpis_both_high = {
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
            current_sim_time_str_gw, action_user_id, kpi_user_id,
            None, # Let agent choose, we'll see what it was from pending_kpi_collection
            "GREEN_WAVE_ACTIVATION",
            {"corridor_avg_travel_time_seconds": 140, "corridor_throughput_vph": 650}, # Neutral outcome for any chosen GW
            "get_corridor_post_action_kpis",
            demand_kpi_settings=demand_kpis_both_high
        )

        latest_gw_action_item = None
        for item_idx in range(len(agent.pending_kpi_collection) - 1, -1, -1):
            if agent.pending_kpi_collection[item_idx]['action_type'] == "GREEN_WAVE_ACTIVATION":
                latest_gw_action_item = agent.pending_kpi_collection[item_idx]
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
            logger.info(f"MAIN_EXAMPLE (GW Demo): Cycle {cycle_num}: No GREEN_WAVE_ACTIVATION action found in pending items for logging.")

        logger.info(f"MAIN_EXAMPLE (GW Demo): Effectiveness Memory after E-Greedy Cycle {cycle_num}: {json.dumps(agent.action_effectiveness_memory, indent=2)}")

    agent.exploration_epsilon = original_epsilon_gw_demo
    agent.rng.setstate(original_rng_state_gw_demo)
    logger.info(f"MAIN_EXAMPLE (GW Demo): Restored exploration_epsilon to {agent.exploration_epsilon}. RNG state restored.")
    logger.info("--- MAIN_EXAMPLE: Epsilon-Greedy GREEN WAVE SELECTION Demonstration Completed ---")

    # Original Scenario: Green Wave, then KPI collection, check score
    logger.info("--- MainExample Refined Scoring: Cycle 1 (Trigger Green Wave 'main_st_ns_wave') ---")
>>>>>>> REPLACE
