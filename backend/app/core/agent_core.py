import asyncio
import logging
from typing import Optional, Dict, Any, List, Set, Tuple, Union # Added Union
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
        self.logger=logger; self.logger.info(f"AgentCore initialized (mem loaded: {len(self.action_effectiveness_memory)}).")

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


    async def run_decision_cycle(self, sample_user_id: str = "user_agent_test_123"):
        # ... (Full logic including KPI scheduling and processing, and other agent phases) ...
        # For brevity, only showing the relevant parts for KPI scheduling and processing
        now_utc = datetime.utcnow()
        self.logger.info(f"--- Starting AgentCore cycle for {sample_user_id} at {now_utc.isoformat()} ---")
        self._memory_updated_this_cycle = False
        system_kpis = self.analytics_service.get_current_system_kpis_summary()

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

    analytics_mock = MockAnalytics(); traffic_mock = MockTraffic()
    agent = AgentCore(MagicMock(spec=PredictionScheduler), MagicMock(spec=PersonalizedRoutingService), analytics_mock, traffic_mock)

    # Scenario: Green Wave, then KPI collection, check score
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
    # asyncio.run(main_example())
    logger.info("AgentCore module defined. Example main_example() function available for testing.")

[end of backend/app/core/agent_core.py]
