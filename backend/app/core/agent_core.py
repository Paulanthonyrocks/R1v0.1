import asyncio
import logging
from typing import Optional, Dict, Any, List, Set, Tuple, Union
import json
from datetime import datetime, timedelta, time
import math
from uuid import UUID, uuid4
import os
import random

import enum
from pydantic import BaseModel, Field, validator

from app.tasks.prediction_scheduler import PredictionScheduler
from app.services.personalized_routing_service import PersonalizedRoutingService, CommonTravelPattern
from app.services.analytics_service import AnalyticsService
from app.services.traffic_signal_service import TrafficSignalService
from app.services.dms_service import DmsService
from app.models.traffic import LocationModel, IncidentSeverityEnum, IncidentTypeEnum
from app.models.signals import SignalState, SignalPhaseEnum, SignalOperationalStatusEnum, SignalControlCommandResponse, SignalControlStatusEnum
from app.models.dms import DmsState, DmsMessage
from app.models.websocket import UserSpecificConditionAlert, WebSocketMessage
from unittest.mock import MagicMock, patch, AsyncMock

from app.core.dynamic_planner import AgentPlanner, Goal


logger = logging.getLogger(__name__)

# --- Plan Structures for Advanced Planning/Reasoning ---
class PlanAction(BaseModel):
    action_type: str # e.g., "SET_SIGNAL_PHASE", "SET_DMS_MESSAGE", "UPDATE_VSL"
    target_ids: List[str]
    parameters: Dict[str, Any] = Field(default_factory=dict)
    # Example parameters for SET_SIGNAL_PHASE: {"phase": SignalPhaseEnum.RED, "duration_seconds": 300}
    # Example parameters for SET_DMS_MESSAGE: {"messages": [DmsMessage(text="ACCIDENT AHEAD")], "duration_minutes": 30}

class PlanStepStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"

class ConditionType(str, enum.Enum):
    KPI_THRESHOLD = "kpi_threshold"         # Step completes if a KPI meets a certain threshold
    EVENT_OCCURRENCE = "event_occurrence"   # Step completes if a specific event/alert is observed
    TIME_ELAPSED = "time_elapsed"           # Step completes after a certain time has passed since activation
    MANUAL_ADVANCE = "manual_advance"       # Step completes upon an external manual command (e.g., from UI)
    # ALL_ACTIONS_SUCCESSFUL = "all_actions_successful" # Implicitly handled, or could be explicit

class KpiCondition(BaseModel):
    condition_id: str = Field(default_factory=lambda: f"kpi_cond_{uuid4().hex[:6]}")
    kpi_source_type: str = Field(..., description="Source of the KPI, e.g., 'signal', 'system_kpi', 'incident_area', 'dms_state'")
    kpi_target_id: Optional[str] = Field(None, description="Specific ID of the target entity (e.g., signal_id, dms_id) if not system-wide.")
    metric_path: List[str] = Field(..., description="Path to the metric within the KPI data structure, e.g., ['queue_lengths_meters', 'N'] or ['avg_speed_kmh']")
    operator: str = Field(..., description="Comparison operator, e.g., '>=', '<=', '==', '!='")
    threshold_value: Union[float, int, str, bool] = Field(..., description="Value to compare the KPI against.")
    # Example: {"kpi_source_type": "signal", "kpi_target_id": "TS001", "metric_path": ["queue_lengths_meters", "N"], "operator": "<=", "threshold_value": 10}

class EventCondition(BaseModel):
    condition_id: str = Field(default_factory=lambda: f"event_cond_{uuid4().hex[:6]}")
    event_type_expected: str = Field(..., description="The type of event/alert to look for (e.g., 'INCIDENT_CLEARED', 'USER_CONFIRM_STEP_X').")
    event_details_filter: Optional[Dict[str, Any]] = Field(None, description="Key-value pairs to match within the event/alert details. All must match if provided.")
    # Example: {"event_type_expected": "INCIDENT_CLEARED", "event_details_filter": {"incident_id": "specific_incident_123"}}

class StepCompletionCondition(BaseModel):
    condition_id: str = Field(default_factory=lambda: f"step_cond_{uuid4().hex[:6]}")
    description: Optional[str] = Field(None, description="Human-readable description of the condition.")
    condition_type: ConditionType
    kpi_details: Optional[KpiCondition] = None
    event_details: Optional[EventCondition] = None
    time_elapsed_seconds: Optional[int] = Field(None, description="Time in seconds to wait after step activation for TIME_ELAPSED type.")
    # timeout_seconds: Optional[int] = Field(None, description="Overall timeout for this condition to be met, if applicable.") # Maybe for later

    @validator('kpi_details', always=True)
    def check_kpi_details(cls, v, values):
        if values.get('condition_type') == ConditionType.KPI_THRESHOLD and v is None:
            raise ValueError("kpi_details must be provided for KPI_THRESHOLD condition type")
        return v

    @validator('event_details', always=True)
    def check_event_details(cls, v, values):
        if values.get('condition_type') == ConditionType.EVENT_OCCURRENCE and v is None:
            raise ValueError("event_details must be provided for EVENT_OCCURRENCE condition type")
        return v

    @validator('time_elapsed_seconds', always=True)
    def check_time_elapsed(cls, v, values):
        if values.get('condition_type') == ConditionType.TIME_ELAPSED and v is None:
            raise ValueError("time_elapsed_seconds must be provided for TIME_ELAPSED condition type")
        return v

class PlanStep(BaseModel):
    step_id: str
    description: Optional[str] = None
    actions: List[PlanAction] = Field(default_factory=list)
    status: PlanStepStatus = Field(default=PlanStepStatus.PENDING)
    step_activation_time: Optional[datetime] = Field(None, description="Timestamp when this step's actions were last executed or when it became active waiting for conditions.")
    completion_logic: str = Field(default="ANY_MET", description="Logic for multiple conditions: 'ANY_MET' or 'ALL_MET'.")
    completion_conditions: List[StepCompletionCondition] = Field(default_factory=list, description="List of conditions that determine step completion.")
    # Future: Add dependencies, trigger_conditions, completion_criteria

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
ACCIDENT_KPI_AREA_RADIUS_METERS = 250 # Defined for clarity, assuming it was meant to be used.

GREEN_WAVE_CORRIDOR_CONFIGS = {
    "main_st_ns_wave": { "description": "Main Street NS Wave", "signals_in_order": ["TS001", "TS002", "TS004"], "target_green_phase": SignalPhaseEnum.GREEN, "wave_green_time_seconds": 50, "offsets_seconds": [0, 18, 36], "corridor_flow_direction_assumption": "NS", "time_windows": [{"start": "07:00", "end": "09:00"}, {"start": "16:00", "end": "18:00"}], "demand_kpi_trigger": "corridor_main_st_ns_demand_high", "priority": 1 },
    "alt_st_ew_wave": { "description": "Alternative Street EW Wave (Prio 1)", "signals_in_order": ["TS005", "TS003"], "target_green_phase": SignalPhaseEnum.GREEN, "wave_green_time_seconds": 45, "offsets_seconds": [0, 22], "corridor_flow_direction_assumption": "EW", "time_windows": [{"start": "07:00", "end": "09:00"}], "demand_kpi_trigger": "corridor_alt_st_ew_demand", "priority": 1 },
    "oak_ave_ew_wave": { "description": "Oak Avenue EW Mid-day Wave", "signals_in_order": ["TS003", "TS005"], "target_green_phase": SignalPhaseEnum.GREEN, "wave_green_time_seconds": 40, "offsets_seconds": [0, 25], "corridor_flow_direction_assumption": "EW", "time_windows": [{"start": "11:00", "end": "13:00"}], "demand_kpi_trigger": "corridor_oak_ave_ew_demand_moderate", "priority": 2 }
}
ALL_CORRIDOR_DEMAND_KPIS = list(set([c.get("demand_kpi_trigger") for c in GREEN_WAVE_CORRIDOR_CONFIGS.values() if c.get("demand_kpi_trigger")]))
ACTION_KPI_CONFIG = {
    "SET_SIGNAL_GREEN_CONGESTION": {"pre_action_kpi_query_config": {"service_method_name": "get_signal_current_kpis", "metrics_to_collect": ["queue_lengths_meters", "current_flow_vph", "typical_flow_vph"], "arg_mapping": {"signal_id": "target_ids[0]"}}, "delay_seconds": 5, "metrics": ["flow_rate_absolute", "local_congestion_level", "cross_traffic_queue_lengths_meters"], "eval_window_minutes": 1, "service_method": "get_signal_post_action_kpis"},
    "INCIDENT_RESPONSE_ACCIDENT": {"pre_action_kpi_query_config": {"service_method_name": "get_incident_area_current_kpis", "metrics_to_collect": ["avg_speed_kmh", "vehicle_count"], "arg_mapping": {"incident_location": "action_parameters.incident_location", "radius_meters": "action_parameters.radius_meters"}}, "delay_seconds": 10, "metrics": ["clearance_time_seconds", "avg_speed_kmh_incident_zone"], "eval_window_minutes": 2, "service_method": "get_incident_response_post_action_kpis"},
    "SET_SIGNAL_RED_ROAD_CLOSURE": {"pre_action_kpi_query_config": {"service_method_name": "get_signal_current_kpis", "metrics_to_collect": ["current_green_approach_flow_vph"], "arg_mapping": {"signal_id": "target_ids[0]"}}, "delay_seconds": 5, "metrics": ["upstream_flow_rate_reduction_percentage", "flow_rate_towards_closure_absolute"], "eval_window_minutes": 1, "service_method": "get_signal_post_action_kpis"},
    "GREEN_WAVE_ACTIVATION": {"pre_action_kpi_query_config": {"service_method_name": "get_corridor_current_kpis", "metrics_to_collect": ["avg_travel_time_seconds", "throughput_vph", "corridor_baseline_avg_travel_time_seconds", "corridor_baseline_throughput_vph"], "arg_mapping": {"corridor_id": "target_ids[0]"}}, "delay_seconds": 10, "metrics": ["corridor_avg_travel_time_seconds", "corridor_throughput_vph", "side_street_avg_queue_increase_meters"], "eval_window_minutes": 2, "service_method": "get_corridor_post_action_kpis"}
}
ACTION_EFFECTIVENESS_CONFIG = {
    "SET_SIGNAL_GREEN_CONGESTION": {
        "relevant_kpis": [
            {"source": "pre", "key_path": ["overall_system_congestion_at_decision"], "as": "pre_decision_overall_congestion"},
            {"source": "pre", "key_path": ["current_flow_vph"], "as": "pre_snapshot_flow_vph"},
            {"source": "pre", "key_path": ["typical_flow_vph"], "as": "baseline_typical_flow_vph"},
            {"source": "post", "key_path": ["local_congestion_level"], "as": "post_local_congestion"},
            {"source": "post", "key_path": ["flow_rate_absolute"], "as": "post_action_flow_rate_vph"},
            {"source": "post", "key_path": ["cross_traffic_queue_lengths_meters", "total"], "as": "post_cross_traffic_queue_total_meters"}
        ],
        "scoring_logic_type": "congestion_improvement"
    },
    "GREEN_WAVE_ACTIVATION": {
        "relevant_kpis": [
            {"source": "pre", "key_path": ["corridor_id"], "as": "gw_corridor_id"},
            {"source": "pre", "key_path": ["avg_travel_time_seconds"], "as": "pre_gw_avg_travel_time"},
            {"source": "pre", "key_path": ["throughput_vph"], "as": "pre_gw_throughput"},
            {"source": "pre", "key_path": ["corridor_baseline_avg_travel_time_seconds"], "as": "baseline_gw_avg_travel_time"},
            {"source": "pre", "key_path": ["corridor_baseline_throughput_vph"], "as": "baseline_gw_throughput_vph"},
            {"source": "post", "key_path": ["corridor_avg_travel_time_seconds"], "as": "gw_post_avg_travel_time"},
            {"source": "post", "key_path": ["corridor_throughput_vph"], "as": "gw_post_throughput"},
            {"source": "post", "key_path": ["side_street_avg_queue_increase_meters"], "as": "post_side_street_avg_queue_increase_meters"}
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
            {"source": "post", "key_path": ["flow_rate_towards_closure_absolute"], "as": "post_closure_flow_towards_vph"},
            {"source": "action_parameters", "key_path": ["closure_direction_affected"], "as": "context_closure_direction_affected"},
            {"source": "action_parameters", "key_path": ["signal_main_flow_direction"], "as": "context_signal_main_flow_direction"},
            {"source": "pre", "key_path": ["closure_location", "latitude"], "as": "context_closure_latitude"},
            {"source": "pre", "key_path": ["closure_location", "longitude"], "as": "context_closure_longitude"}
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
                 traffic_signal_service: TrafficSignalService,
                 dms_service: DmsService): # Add DmsService
        self.prediction_scheduler = prediction_scheduler
        self.personalized_routing_service = personalized_routing_service
        self.analytics_service = analytics_service
        self.traffic_signal_service = traffic_signal_service
        self.dms_service = dms_service # Store DmsService

        self._recent_signal_actions: Dict[str, Dict[str, Any]] = {}
        self.green_wave_corridor_configs = GREEN_WAVE_CORRIDOR_CONFIGS
        self.action_kpi_config = ACTION_KPI_CONFIG
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

        self.congestion_duration_options: List[int] = [30, 45, 60, 75, 90] # seconds
        self.logger.info(f"Congestion signal green duration options set to: {self.congestion_duration_options}")

        # For Advanced Planning/Reasoning
        self.active_plan: Optional[List[PlanStep]] = None
        self.current_plan_step_index: int = -1 # -1 indicates no plan active or current step not set
        self.active_plan_id: Optional[str] = None # To identify the type of plan active
        self.active_goal: Optional[Goal] = None
        self.planner = AgentPlanner(self)
        self.logger.info("Advanced planning attributes initialized (active_plan: None).")

    def _get_hardcoded_hwy_closure_plan(self, incident_alert: Dict[str, Any]) -> List[PlanStep]:
        incident_id = incident_alert.get('id', 'unknown_incident')
        incident_location = LocationModel(**incident_alert.get("location", {}))
        incident_loc_name = incident_location.name if incident_location.name else f"Lat:{incident_location.latitude:.3f},Lon:{incident_location.longitude:.3f}"

        closure_dir_affected = incident_alert.get("details", {}).get("direction_affected", "ALL").upper()
        closure_dir_display = f" {closure_dir_affected}BOUND" if closure_dir_affected != "ALL" else ""


        self.logger.info(f"Generating hardcoded highway closure plan for incident: {incident_id} at {incident_loc_name} affecting {closure_dir_affected} direction.")

        # These IDs are examples and should match DMS/signals in the mock inventory or real system
        # Ideally, these would be dynamically selected based on incident location and network topology.
        # For a Northbound closure at TS002 (lat: 34.05, lon: -118.24):
        # Upstream signal (south): TS004 (lat: 34.04, lon: -118.24, flow: "N")
        # Upstream DMS (south, for NB traffic): DMS_UPSTREAM_NB_01 (lat: 34.035, lon: -118.24)
        # Secondary DMS (e.g., on an approach route): DMS_UPSTREAM_EB_01 (lat: 34.05, lon: -118.255)

        # Customize based on closure_direction_affected if needed, or make generic for "ALL LANES"
        # This example is somewhat generic but uses closure_dir_display for messaging.

        plan_steps = [
            PlanStep(
                step_id="HWY_CLOSURE_S1_CONTAIN_ALERT",
                description="Immediate containment: Set key upstream signals to RED, activate primary DMS.",
                actions=[
                    PlanAction( # Action to set an upstream signal (e.g., on-ramp or approach) to RED
                        action_type="SET_SIGNAL_PHASE",
                        target_ids=["TS004"], # Example: Signal South of TS002, controlling Northbound traffic
                        parameters={"phase": SignalPhaseEnum.RED.value, "duration_seconds": 7200} # Long red for closure (2 hours)
                    ),
                    PlanAction(
                        action_type="SET_DMS_MESSAGE",
                        target_ids=["DMS_UPSTREAM_NB_01"], # Example: DMS South of TS002, viewable by NB traffic
                        parameters={
                            "messages": [
                                DmsMessage(text=f"HWY CLOSED{closure_dir_display}", page_number=1),
                                DmsMessage(text=f"AT {incident_loc_name.upper()}", page_number=2),
                                DmsMessage(text="ALL LANES BLOCKED", page_number=3),
                                DmsMessage(text="USE ALT ROUTE", page_number=4)
                            ]
                        }
                    )
                ]
            ),
            PlanStep(
                step_id="HWY_CLOSURE_S2_SECONDARY_ALERTS",
                description="Activate secondary DMS for wider area notification or alternative routes.",
                actions=[
                    PlanAction(
                        action_type="SET_DMS_MESSAGE",
                        target_ids=["DMS_UPSTREAM_EB_01"], # Example: DMS on a cross-street or alternative approach
                        parameters={
                            "messages": [
                                DmsMessage(text=f"HWY CLSD{closure_dir_display} @ {incident_loc_name.upper()}", page_number=1),
                                DmsMessage(text="SEVERE DELAYS", page_number=2),
                                DmsMessage(text="CONSIDER ALT ROUTE", page_number=3)
                            ]
                        }
                    )
                ]
            ),
            PlanStep(
                step_id="HWY_CLOSURE_S3_MONITOR",
                description="Monitoring phase. Agent will continue standard reactive logic or specific plan monitoring KPIs.",
                actions=[],
                completion_conditions=[
                    StepCompletionCondition(
                        condition_id="monitor_duration",
                        condition_type=ConditionType.TIME_ELAPSED,
                        time_elapsed_seconds=120 # Simulate a 2-minute monitoring period
                    )
                ]
            ),
            # Example of a hypothetical clear step (actions would need to be defined)
            # PlanStep(
            #     step_id="HWY_CLOSURE_S4_CLEAR",
            #     description="Clear DMS messages and normalize signals after incident resolution.",
            #     actions=[
            #         PlanAction(action_type="CLEAR_DMS_MESSAGE", target_ids=[primary_dms_id]),
            #         PlanAction(action_type="CLEAR_DMS_MESSAGE", target_ids=[secondary_dms_id]),
            #         # Action to revert signal TS004 might be complex:
            #         # e.g., set to a default plan, or remove override to allow normal logic.
            #         # For now, this step is illustrative.
            #     ],
            #     duration_seconds=0 # Immediate actions
            # )
        ]
        self.logger.info(f"Generated plan '{self.active_plan_id}' with {len(plan_steps)} steps for incident {incident_id}.")
        return plan_steps

    async def _execute_plan_action(self, plan_action: PlanAction) -> bool:
        """Executes a single PlanAction and returns True if successful, False otherwise."""
        self.logger.info(f"Executing Plan Action: Type='{plan_action.action_type}', Targets='{plan_action.target_ids}', Params='{plan_action.parameters}'")
        try:
            if plan_action.action_type == "SET_SIGNAL_PHASE":
                for target_id in plan_action.target_ids:
                    phase_val = plan_action.parameters.get("phase")
                    duration = plan_action.parameters.get("duration_seconds")
                    if phase_val:
                        phase_enum = SignalPhaseEnum(phase_val)
                        response = await self.traffic_signal_service.set_signal_phase(
                            signal_id=target_id, phase=phase_enum, duration_seconds=duration
                        )
                        if response.status not in [SignalControlStatusEnum.ACCEPTED, SignalControlStatusEnum.SUCCESS]:
                            self.logger.error(f"Plan Action SET_SIGNAL_PHASE for {target_id} failed: {response.message}")
                            return False
                    else:
                        self.logger.error(f"Plan Action SET_SIGNAL_PHASE for {target_id} missing 'phase' parameter.")
                        return False

            elif plan_action.action_type == "SET_DMS_MESSAGE":
                for target_id in plan_action.target_ids:
                    messages_data = plan_action.parameters.get("messages", [])
                    dms_messages = [DmsMessage(**msg_data) for msg_data in messages_data] # Assumes msg_data is dict
                    duration_mins = plan_action.parameters.get("duration_minutes")
                    if dms_messages:
                        response = await self.dms_service.set_dms_message(
                            dms_id=target_id, messages=dms_messages, duration_minutes=duration_mins
                        )
                        if response.status not in [SignalControlStatusEnum.ACCEPTED, SignalControlStatusEnum.SUCCESS]:
                            self.logger.error(f"Plan Action SET_DMS_MESSAGE for {target_id} failed: {response.message}")
                            return False
                    else:
                        self.logger.error(f"Plan Action SET_DMS_MESSAGE for {target_id} missing 'messages' or empty.")
                        return False

            elif plan_action.action_type == "CLEAR_DMS_MESSAGE":
                for target_id in plan_action.target_ids:
                    response = await self.dms_service.clear_dms_message(dms_id=target_id)
                    if response.status not in [SignalControlStatusEnum.ACCEPTED, SignalControlStatusEnum.SUCCESS]:
                        self.logger.error(f"Plan Action CLEAR_DMS_MESSAGE for {target_id} failed: {response.message}")
                        return False
            else:
                self.logger.warning(f"Plan Action: Unknown action_type '{plan_action.action_type}'")
                return False

            # Log successful action execution to ActionPerformanceLog if needed (simplified for now)
            # This could be expanded to create more detailed logs for plan actions
            self.action_performance_logs.append(ActionPerformanceLog(
                action_timestamp=datetime.utcnow(), # Mocked time in tests
                action_type=f"PLAN_{self.active_plan_id}_{plan_action.action_type}", # Prefix to distinguish from reactive
                target_ids=plan_action.target_ids,
                action_parameters=plan_action.parameters,
                # Effectiveness for plan actions might be assessed at plan level or step level later
            ))

        except Exception as e:
            self.logger.error(f"Exception executing plan action {plan_action.action_type} for targets {plan_action.target_ids}: {e}", exc_info=True)
            return False
        return True

    async def _process_active_plan_step(self):
        if not self.active_plan or \
           self.current_plan_step_index < 0 or \
           self.current_plan_step_index >= len(self.active_plan):
            self.logger.debug("No active plan or invalid step index for processing.")
            return

        current_step = self.active_plan[self.current_plan_step_index]
        now = datetime.utcnow() # Use mocked time in tests

        if current_step.status == PlanStepStatus.PENDING:
            self.logger.info(f"Activating Plan Step '{current_step.step_id}': {current_step.description}")
            current_step.status = PlanStepStatus.ACTIVE
            current_step.step_activation_time = now

            step_actions_all_successful = True
            if not current_step.actions: # If a step has no actions (e.g. a pure monitoring/wait step)
                 self.logger.info(f"Plan Step '{current_step.step_id}' has no actions to execute directly.")
            else:
                for action in current_step.actions:
                    success = await self._execute_plan_action(action)
                    if not success:
                        step_actions_all_successful = False
                        # Decide on plan failure strategy: halt plan, or just mark step failed and continue?
                        # For now, mark step failed and let plan continue to next step unless logic changes.

            if not step_actions_all_successful:
                current_step.status = PlanStepStatus.FAILED
                self.logger.error(f"Plan Step '{current_step.step_id}' failed due to action failure.")
                # Optionally, could halt the entire plan here:
                # self._complete_active_plan(final_status="FAILED_STEP")
                # return

            # If step has no completion conditions, or all actions succeeded and no conditions, mark completed and try to advance.
            if not current_step.completion_conditions:
                if current_step.status != PlanStepStatus.FAILED:
                    current_step.status = PlanStepStatus.COMPLETED
                self.logger.info(f"Plan Step '{current_step.step_id}' (no conditions) status: {current_step.status.value}. Attempting to advance.")
                self.current_plan_step_index += 1
                if self.current_plan_step_index >= len(self.active_plan):
                    self._complete_active_plan()
                return # Step processed for this cycle

        elif current_step.status == PlanStepStatus.ACTIVE:
            if not current_step.completion_conditions: # Should have been caught above if PENDING -> ACTIVE
                self.logger.warning(f"Plan Step '{current_step.step_id}' is ACTIVE but has no completion_conditions. Marking completed.")
                current_step.status = PlanStepStatus.COMPLETED
            else:
                condition_met_this_cycle = False
                # For "ANY_MET" logic, if one condition is met, the step is complete.
                # For "ALL_MET", all conditions must be met (more complex to track over time, not fully implemented here).
                # Assuming "ANY_MET" for now if multiple conditions are present.

                for condition in current_step.completion_conditions:
                    if condition.condition_type == ConditionType.TIME_ELAPSED:
                        if condition.time_elapsed_seconds is not None and current_step.step_activation_time is not None:
                            elapsed_time = (now - current_step.step_activation_time).total_seconds()
                            if elapsed_time >= condition.time_elapsed_seconds:
                                self.logger.info(f"Plan Step '{current_step.step_id}' TIME_ELAPSED condition met ({condition.time_elapsed_seconds}s).")
                                condition_met_this_cycle = True
                                break # For ANY_MET logic
                            else:
                                self.logger.debug(f"Plan Step '{current_step.step_id}' waiting for TIME_ELAPSED. {elapsed_time:.0f}/{condition.time_elapsed_seconds}s.")
                        else:
                             self.logger.warning(f"TIME_ELAPSED condition for step '{current_step.step_id}' is missing time_elapsed_seconds or step_activation_time.")
                    elif condition.condition_type == ConditionType.KPI_THRESHOLD:
                        self.logger.debug(f"Plan Step '{current_step.step_id}': KPI_THRESHOLD condition evaluation TBD. Details: {condition.kpi_details}")
                        # Placeholder: KPI condition evaluation logic would go here
                        # if evaluate_kpi_condition(condition.kpi_details): condition_met_this_cycle = True; break
                    elif condition.condition_type == ConditionType.EVENT_OCCURRENCE:
                        self.logger.debug(f"Plan Step '{current_step.step_id}': EVENT_OCCURRENCE condition evaluation TBD. Details: {condition.event_details}")
                        # Placeholder: Event condition evaluation logic would go here
                        # if evaluate_event_condition(condition.event_details): condition_met_this_cycle = True; break
                    elif condition.condition_type == ConditionType.MANUAL_ADVANCE:
                         self.logger.debug(f"Plan Step '{current_step.step_id}': MANUAL_ADVANCE condition waiting for external trigger.")
                         # This would require an external mechanism to set the step to COMPLETED.

                if condition_met_this_cycle:
                    current_step.status = PlanStepStatus.COMPLETED

            # If step completed (either by condition or immediate if no conditions post-actions)
            if current_step.status == PlanStepStatus.COMPLETED:
                self.logger.info(f"Plan Step '{current_step.step_id}' status: {current_step.status.value}. Attempting to advance.")
                self.current_plan_step_index += 1
                if self.current_plan_step_index >= len(self.active_plan):
                    self._complete_active_plan()

        # If step is FAILED or SKIPPED, it typically means manual intervention or a different plan path might be needed.
        # For now, the plan will just halt if a step fails and its actions don't complete.
        # If a step is already COMPLETED (from a previous cycle), the logic above will just try to advance current_plan_step_index
        # if it wasn't advanced in the cycle it completed. This needs to be robust.
        # The current logic: if a step is PENDING, it's acted upon. If ACTIVE, its conditions are checked.
        # If COMPLETED/FAILED/SKIPPED already, this method effectively does nothing for that step in this cycle,
        # relying on index advancement to move to the next PENDING step.

    def _complete_active_plan(self, final_status_message: str = "Completed successfully"):
        """Resets plan-related attributes when a plan is finished or terminated."""
        self.logger.info(f"Complex plan '{self.active_plan_id}' is now complete. Final status: {final_status_message}")
        # Optionally, perform any final logging or cleanup related to the plan
        # For example, log all step statuses:
        if self.active_plan:
            for step in self.active_plan:
                self.logger.info(f"  Plan '{self.active_plan_id}' - Step '{step.step_id}' final status: {step.status.value}")

        self.active_plan = None
        self.active_plan_id = None
        self.current_plan_step_index = -1
        # Potentially notify other systems or log overall plan outcome/effectiveness if measurable

    def _load_effectiveness_memory(self) -> Dict[str, List[float]]:
        if not os.path.exists(self.effectiveness_memory_filepath):
            self.logger.info("Effectiveness memory file not found. Initializing empty memory.")
            return {}
        try:
            with open(self.effectiveness_memory_filepath, 'r') as f:
                data = json.load(f)
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
            elif kpi_spec["source"] == "action_parameters" and key_part in source_dict: # Check root for action_parameters
                current_val = source_dict[key_part]
                break
            else:
                self.logger.debug(f"Key path {key_path} not fully found in {source_dict}. Missing '{key_part}'.")
                return None
        return current_val

    def _score_congestion_improvement(self, metrics: Dict[str, Any]) -> Optional[float]:
        self.logger.debug(f"Scoring congestion improvement with metrics: {metrics}")
        score = 0.0
        metrics_counted = 0
        component_scores = {}

        post_local_congestion = metrics.get("post_local_congestion")
        pre_overall_congestion = metrics.get("pre_decision_overall_congestion")

        if post_local_congestion is not None:
            congestion_score_component = 0
            if pre_overall_congestion == "HIGH":
                if post_local_congestion == "MEDIUM": congestion_score_component = 0.5
                elif post_local_congestion == "LOW": congestion_score_component = 1.0
                else: congestion_score_component = -0.2
            elif pre_overall_congestion == "MEDIUM":
                if post_local_congestion == "LOW": congestion_score_component = 0.7
                elif post_local_congestion == "MEDIUM": congestion_score_component = 0.1
                else: congestion_score_component = -0.5
            elif pre_overall_congestion == "LOW":
                 if post_local_congestion != "LOW": congestion_score_component = -0.5
                 else: congestion_score_component = 0.1
            else:
                if post_local_congestion == "LOW": congestion_score_component = 0.2
                elif post_local_congestion == "MEDIUM": congestion_score_component = 0.0
                else: congestion_score_component = -0.2
            score += congestion_score_component; metrics_counted += 1
            component_scores["congestion_reduction"] = congestion_score_component
            self.logger.debug(f"Congestion reduction component: {congestion_score_component} (Pre: {pre_overall_congestion}, Post: {post_local_congestion})")

        pre_snapshot_flow = metrics.get("pre_snapshot_flow_vph")
        post_action_flow = metrics.get("post_action_flow_rate_vph")
        baseline_flow = metrics.get("baseline_typical_flow_vph")

        if post_action_flow is not None:
            flow_score_component = 0
            primary_comparison_flow = baseline_flow if baseline_flow is not None else pre_snapshot_flow
            comparison_type = "baseline" if baseline_flow is not None else "pre-snapshot"

            if primary_comparison_flow is not None:
                if post_action_flow > primary_comparison_flow * 1.2: flow_score_component = 0.4
                elif post_action_flow > primary_comparison_flow * 1.05: flow_score_component = 0.15
                elif post_action_flow < primary_comparison_flow * 0.8: flow_score_component = -0.4
                else: flow_score_component = 0.0
                self.logger.debug(f"Flow improvement component ({comparison_type}): {flow_score_component} (Post: {post_action_flow}, Comparison: {primary_comparison_flow})")
            else:
                if post_action_flow > 500: flow_score_component = 0.1
                self.logger.debug(f"Flow component (only post-action): {flow_score_component} (Post: {post_action_flow})")
            score += flow_score_component; metrics_counted += 1
            component_scores["flow_improvement"] = flow_score_component

        cross_traffic_q_total = metrics.get("post_cross_traffic_queue_total_meters")
        if cross_traffic_q_total is not None:
            externality_score_component = 0
            if cross_traffic_q_total > 100: externality_score_component = -0.5
            elif cross_traffic_q_total > 50: externality_score_component = -0.2
            elif cross_traffic_q_total > 20: externality_score_component = -0.05
            self.logger.debug(f"Cross-traffic externality component: {externality_score_component} (Queue total: {cross_traffic_q_total}m)")
            score += externality_score_component; metrics_counted += 1
            component_scores["cross_traffic_impact"] = externality_score_component

        if metrics_counted == 0: self.logger.warning("Congestion scoring: No relevant KPIs found or countable."); return None
        self.logger.info(f"Congestion improvement final score: {score / metrics_counted if metrics_counted > 0 else 0.0}, based on components: {component_scores}")
        return max(-1.0, min(1.0, score / metrics_counted if metrics_counted > 0 else 0.0))


    def _score_green_wave_efficiency(self, metrics: Dict[str, Any]) -> Optional[float]:
        self.logger.debug(f"Scoring green wave efficiency with metrics: {metrics}")
        score = 0.0; metrics_counted = 0; component_scores = {}

        post_tt = metrics.get("post_gw_avg_travel_time")
        baseline_tt = metrics.get("baseline_gw_avg_travel_time")
        pre_snapshot_tt = metrics.get("pre_gw_avg_travel_time")

        if post_tt is not None:
            tt_score_component = 0
            reference_tt = baseline_tt if baseline_tt is not None and baseline_tt > 0 else pre_snapshot_tt
            comparison_tt_type = "baseline" if baseline_tt is not None and baseline_tt > 0 else "pre-snapshot"

            if reference_tt is not None and reference_tt > 0:
                if post_tt < reference_tt * 0.7: tt_score_component = 0.6
                elif post_tt < reference_tt * 0.9: tt_score_component = 0.3
                elif post_tt < reference_tt * 1.1: tt_score_component = 0.05
                else: tt_score_component = -0.5
                self.logger.debug(f"GW Travel Time component ({comparison_tt_type}): {tt_score_component} (Post: {post_tt}s, Ref: {reference_tt}s)")
            else:
                if post_tt < 120 : tt_score_component = 0.1
                self.logger.debug(f"GW Travel Time component (only post): {tt_score_component} (Post: {post_tt}s)")
            score += tt_score_component; metrics_counted +=1
            component_scores["travel_time_reduction"] = tt_score_component

        post_tp = metrics.get("post_gw_throughput")
        baseline_tp = metrics.get("baseline_gw_throughput_vph")
        pre_snapshot_tp = metrics.get("pre_gw_throughput")

        if post_tp is not None:
            tp_score_component = 0
            reference_tp = baseline_tp if baseline_tp is not None else pre_snapshot_tp
            comparison_tp_type = "baseline" if baseline_tp is not None else "pre-snapshot"

            if reference_tp is not None:
                if reference_tp > 0:
                    if post_tp > reference_tp * 1.2: tp_score_component = 0.6
                    elif post_tp > reference_tp * 1.05: tp_score_component = 0.2
                    elif post_tp < reference_tp * 0.7: tp_score_component = -0.4
                    else: tp_score_component = 0.0
                elif post_tp > 100:
                    tp_score_component = 0.3
                self.logger.debug(f"GW Throughput component ({comparison_tp_type}): {tp_score_component} (Post: {post_tp}vph, Ref: {reference_tp}vph)")
            else:
                if post_tp > 800: tp_score_component = 0.1
                self.logger.debug(f"GW Throughput component (only post): {tp_score_component} (Post: {post_tp}vph)")
            score += tp_score_component; metrics_counted +=1
            component_scores["throughput_increase"] = tp_score_component

        side_street_q_increase = metrics.get("post_side_street_avg_queue_increase_meters")
        if side_street_q_increase is not None:
            externality_gw_score_component = 0
            if side_street_q_increase > 50: externality_gw_score_component = -0.6
            elif side_street_q_increase > 25: externality_gw_score_component = -0.3
            elif side_street_q_increase > 10: externality_gw_score_component = -0.1
            self.logger.debug(f"GW Side-street queue externality component: {externality_gw_score_component} (Increase: {side_street_q_increase}m)")
            score += externality_gw_score_component; metrics_counted += 1
            component_scores["side_street_impact"] = externality_gw_score_component

        if metrics_counted == 0: self.logger.warning(f"GW efficiency: No relevant KPIs found or countable in {metrics}."); return None
        self.logger.info(f"GW efficiency final score: {score / metrics_counted if metrics_counted > 0 else 0.0}, based on components: {component_scores}")
        return max(-1.0, min(1.0, score / metrics_counted if metrics_counted > 0 else 0.0))

    def _score_incident_clearance_speed(self, metrics: Dict[str, Any]) -> Optional[float]:
        self.logger.debug(f"Scoring incident clearance with metrics: {metrics}")
        score = 0.0
        metrics_counted = 0

        clear_time_minutes = metrics.get("post_incident_clearance_time_minutes")
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
            if pre_speed is not None and pre_speed < 20:
                if post_speed > pre_speed * 1.5 and post_speed > 15 : score += 0.4
                elif post_speed > pre_speed + 5 : score += 0.1
                elif post_speed < pre_speed * 0.8 : score -= 0.2
            elif post_speed > 30: score += 0.2
            elif post_speed < 10: score -= 0.3

        if metrics_counted == 0: self.logger.warning("Incident scoring: No relevant KPIs found."); return None
        return max(-1.0, min(1.0, score / metrics_counted if metrics_counted > 0 else 0.0))

    def _score_closure_effectiveness(self, metrics: Dict[str, Any]) -> Optional[float]:
        self.logger.debug(f"Scoring closure effectiveness with metrics: {metrics}")
        post_flow = metrics.get("post_closure_flow_towards_vph")
        if post_flow is None: self.logger.warning("Closure scoring: Missing post_closure_flow_towards_vph."); return None

        score = 0.0
        if post_flow < 5: score = 1.0
        elif post_flow < 15: score = 0.6
        elif post_flow < 30: score = 0.1
        else: score = -0.7

        pre_flow_on_green = metrics.get("pre_closure_flow_on_green_vph")
        if pre_flow_on_green is not None and pre_flow_on_green > 100 and score > 0.5:
            score = min(1.0, score + 0.2)

        return max(-1.0, min(1.0, score))

    def _calculate_effectiveness_score(self, log_entry_data: Dict[str,Any]) -> Tuple[Optional[float],Optional[Dict[str,Any]]]:
        action_type = log_entry_data.get("action_type")
        config = self.action_effectiveness_config.get(action_type)

        if not config: return None, None

        metrics_for_scoring: Dict[str, Any] = {}
        for kpi_spec in config.get("relevant_kpis", []):
            source_name = kpi_spec["source"]
            source_data = None
            if source_name == "pre":
                source_data = log_entry_data.get("pre_action_context_kpis")
            elif source_name == "post":
                source_data = log_entry_data.get("post_action_kpis")
            elif source_name == "action_parameters":
                source_data = log_entry_data.get("action_parameters")

            value = self._extract_kpi_value(source_data, kpi_spec["key_path"])
            if value is not None: metrics_for_scoring[kpi_spec["as"]] = value

        if not metrics_for_scoring and config.get("relevant_kpis"):
            # Only return 0.0 if relevant_kpis were defined but none were found.
            # If relevant_kpis is empty, it implies scoring doesn't depend on these dynamic metrics.
            self.logger.warning(f"No relevant dynamic KPIs found for {action_type} (ID: {log_entry_data.get('action_id')}) based on config, but relevant_kpis were specified. Score might be 0 or based on static logic if any.")
            # The decision to return 0.0 here or None depends on policy.
            # If some actions have scores not based on dynamic KPIs, this might need adjustment.
            # For now, if relevant KPIs are expected but not found, a neutral score of 0.0 is returned.
            return 0.0, metrics_for_scoring

        logic_type = config.get("scoring_logic_type"); score: Optional[float] = 0.0
        if logic_type == "congestion_improvement": score = self._score_congestion_improvement(metrics_for_scoring)
        elif logic_type == "green_wave_efficiency": score = self._score_green_wave_efficiency(metrics_for_scoring)
        elif logic_type == "incident_clearance_speed": score = self._score_incident_clearance_speed(metrics_for_scoring)
        elif logic_type == "closure_effectiveness": score = self._score_closure_effectiveness(metrics_for_scoring)
        else: self.logger.warning(f"Unknown scoring_logic_type: {logic_type}"); return None, metrics_for_scoring

        self.logger.info(f"Effectiveness score for {action_type} (ID: {log_entry_data.get('action_id')}): {score}. Metrics used: {json.dumps(metrics_for_scoring, default=str)}")
        return score, metrics_for_scoring

    def _is_signal_upstream_of_closure(
        self,
        signal_state: SignalState,
        closure_location: LocationModel,
        closure_direction_affected: str
    ) -> bool:
        if not signal_state.location or not signal_state.main_flow_direction:
            self.logger.debug(f"Signal {signal_state.signal_id} missing location or main_flow_direction. Cannot determine if upstream.")
            return False

        sig_lat = signal_state.location.latitude
        sig_lon = signal_state.location.longitude
        cls_lat = closure_location.latitude
        cls_lon = closure_location.longitude

        lat_epsilon = 0.0001
        lon_epsilon = 0.0001

        signal_flow_dir = signal_state.main_flow_direction.upper()
        closure_dir = closure_direction_affected.upper()

        self.logger.debug(f"Checking upstream status: Signal {signal_state.signal_id} (Lat:{sig_lat}, Lon:{sig_lon}, Flow:{signal_flow_dir}) "
                          f"for Closure (Lat:{cls_lat}, Lon:{cls_lon}, AffectedDir:{closure_dir})")

        is_upstream = False

        if closure_dir == "ALL":
            if "N" in signal_flow_dir and (sig_lat < cls_lat - lat_epsilon):
                is_upstream = True
            elif "S" in signal_flow_dir and (sig_lat > cls_lat + lat_epsilon):
                is_upstream = True
            elif "E" in signal_flow_dir and (sig_lon < cls_lon - lon_epsilon):
                is_upstream = True
            elif "W" in signal_flow_dir and (sig_lon > cls_lon + lon_epsilon):
                is_upstream = True

            if is_upstream:
                 self.logger.debug(f"Signal {signal_state.signal_id} considered potentially upstream for 'ALL' directions closure based on its flow components and relative position.")
                 return True
            self.logger.debug(f"Signal {signal_state.signal_id} not conclusively upstream for 'ALL' directions based on its flow components.")
            return False

        if closure_dir == "N":
            if ("N" in signal_flow_dir) and (sig_lat < cls_lat - lat_epsilon):
                is_upstream = True

        elif closure_dir == "S":
            if ("S" in signal_flow_dir) and (sig_lat > cls_lat + lat_epsilon):
                is_upstream = True

        elif closure_dir == "E":
            if ("E" in signal_flow_dir) and (sig_lon < cls_lon - lon_epsilon):
                is_upstream = True

        elif closure_dir == "W":
            if ("W" in signal_flow_dir) and (sig_lon > cls_lon + lon_epsilon):
                is_upstream = True

        if is_upstream:
            self.logger.debug(f"Signal {signal_state.signal_id} is UPSTREAM of closure affecting {closure_dir} traffic.")
        else:
            self.logger.debug(f"Signal {signal_state.signal_id} is NOT upstream of closure affecting {closure_dir} traffic (or closure_dir '{closure_dir}' is not N/S/E/W).")

        return is_upstream

    async def _find_signals_near_location(self, target_location: LocationModel, all_signals: List[SignalState], radius_meters: int) -> List[SignalState]:
        # This is a placeholder. Actual implementation would use geospatial queries.
        self.logger.debug(f"Placeholder: Finding signals near {target_location} within {radius_meters}m. Returning all for now in mock.")
        # In a real system, this would filter `all_signals`.
        # For mock purposes or if all signals are always checked by directional logic, returning all is fine.
        # However, for the original intent of `nearby_signals` before directional logic, it should filter.
        # Given it's not used by the new directional closure logic directly, its exact mock behavior is less critical here.
        return all_signals

    async def _determine_next_travel_prediction_time(self, pattern: CommonTravelPattern, current_datetime: datetime) -> Optional[datetime]:
        # ... (implementation as before)
        return None

    async def _execute_green_wave(
        self, corridor_id: str, signals_in_order: List[str], green_phase: SignalPhaseEnum,
        green_time_seconds: int, offsets_seconds: List[int],
        all_current_signal_states: Dict[str, SignalState],
        processed_signals_for_coordination: Set[str], now_utc: datetime
    ) -> bool:
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
        fetched_kpis: Dict[str, Any] = {}
        # config = ACTION_KPI_CONFIG.get(action_type_str) # This was AgentCore's self.action_kpi_config
        config = self.action_kpi_config.get(action_type_str) # Corrected to use instance member

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
                # Ensure metrics_to_collect is always passed if the method expects it.
                if "metrics_to_collect" in service_method.__code__.co_varnames:
                    fetched_kpis = await service_method(**query_args, metrics_to_collect=metrics_to_collect)
                else: # If the service method does not expect metrics_to_collect (e.g. it returns a fixed set or all)
                    fetched_kpis = await service_method(**query_args)
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

        # --- Complex Plan Activation & Execution ---
        if self.active_plan and self.current_plan_step_index >= 0: # current_plan_step_index can be >= len(self.active_plan) if plan just completed
            if self.current_plan_step_index < len(self.active_plan):
                self.logger.info(f"Processing active plan '{self.active_plan_id}', step index {self.current_plan_step_index}.")
                await self._process_active_plan_step()
            else: # Plan likely just completed, _process_active_plan_step would have called _complete_active_plan
                self.logger.info(f"Plan '{self.active_plan_id}' appears to have completed all steps (index {self.current_plan_step_index} >= {len(self.active_plan)} steps). Will check for new plan triggers.")
                # _complete_active_plan should have reset relevant attributes.

        # Check for new plan triggers ONLY if no plan is currently active or being processed.
        # _complete_active_plan sets self.active_plan to None.
        if not self.active_plan and self.active_goal:
            self.logger.info(f"Active goal detected: {self.active_goal.description}. Creating a new plan.")
            plan_steps_data = self.planner.create_plan(self.active_goal, system_kpis)
            if plan_steps_data:
                self.active_plan = [PlanStep(**step_data) for step_data in plan_steps_data]
                self.active_plan_id = f"PLAN_FOR_GOAL_{self.active_goal.id}"
                self.current_plan_step_index = 0
                self.logger.info(f"Activated plan '{self.active_plan_id}' with {len(self.active_plan)} steps for goal '{self.active_goal.description}'.")
            else:
                self.logger.warning(f"Planner failed to create a plan for goal: {self.active_goal.description}")

        if not self.active_plan:
            if self.active_plan_id is not None: # Indicates a plan just finished and was cleared by _complete_active_plan
                self.logger.info(f"Complex plan formerly ID'd as '{self.active_plan_id}' was recently completed and cleared.")
                self.active_plan_id = None
                self.current_plan_step_index = -1

            # Check for conditions that trigger a new complex plan
            for alert_item_for_plan in active_alerts:
                alert_type_for_plan = alert_item_for_plan.get("type", "UNKNOWN").upper()
                alert_severity_for_plan_str = alert_item_for_plan.get("severity", IncidentSeverityEnum.LOW.value).lower()
                alert_details_for_plan = alert_item_for_plan.get("details", {})

                is_critical_road_closure_all_lanes = (
                    alert_type_for_plan == IncidentTypeEnum.ROAD_CLOSURE.value.upper() and
                    alert_severity_for_plan_str == IncidentSeverityEnum.CRITICAL.value and
                    alert_details_for_plan.get("lanes_affected", "").upper() == "ALL"
                )

                if is_critical_road_closure_all_lanes:
                    incident_id_for_plan = alert_item_for_plan.get('id', 'unknown_incident')
                    self.logger.info(f"COMPLEX SCENARIO TRIGGERED: Critical all-lanes road closure (Alert ID: {incident_id_for_plan}). Activating predefined plan.")
                    new_plan_steps = self._get_hardcoded_hwy_closure_plan(alert_item_for_plan)
                    if new_plan_steps:
                        self.active_plan = new_plan_steps # Assign the list of PlanStep objects
                        # Reset status of all steps in the new plan to PENDING (already default, but good practice)
                        for step in self.active_plan:
                            step.status = PlanStepStatus.PENDING
                        self.active_plan_id = f"HWY_CLOSURE_{incident_id_for_plan}"
                        self.current_plan_step_index = 0
                        self.logger.info(f"Activated plan '{self.active_plan_id}' with {len(self.active_plan)} steps. Starting at step index {self.current_plan_step_index}.")
                        # Once a plan is activated, break from checking other alerts for *new* plans this cycle.
                        break
                    else:
                        self.logger.warning(f"Failed to load/generate highway closure plan for incident {incident_id_for_plan}.")

        processed_pending_indices: List[int] = []
        for idx, item in enumerate(self.pending_kpi_collection):
            if now_utc >= item['query_after_timestamp']:
                try:
                    self.logger.info(f"Processing pending KPI collection for action ID {item['action_id']} ({item['action_type']})")
                    # Corrected: use self.action_kpi_config
                    action_kpi_detail_cfg = self.action_kpi_config.get(item['action_type'])
                    if not action_kpi_detail_cfg:
                        self.logger.error(f"No KPI config found for action type {item['action_type']} in pending item {item['action_id']}. Skipping KPI processing.")
                        processed_pending_indices.append(idx) # Mark as processed to remove
                        continue

                    post_action_kpis = await getattr(self.analytics_service, action_kpi_detail_cfg["service_method"])(
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
                                action_signature_key = f"{action_type_for_key}:{primary_target_id_for_memory}"
                        else:
                            self.logger.warning(f"Action {item['action_id']} (type {item['action_type']}) has no target_ids for effectiveness memory key. Score not stored.")

                        if action_signature_key:
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
                            # KPI logging for this autonomous action could be added here if desired
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
                    nearby_signals = await self._find_signals_near_location(alert_location_model, all_signal_states, self.ACCIDENT_PRE_KPI_RADIUS_METERS)
                    for signal in nearby_signals:
                        if signal.signal_id in processed_signals_for_incident or signal.signal_id in processed_signals_for_coordination: continue
                        if signal.operational_status == SignalOperationalStatusEnum.ONLINE:
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
                                    processed_signals_for_incident.add(signal.signal_id)
                                    processed_signals_for_coordination.add(signal.signal_id)
                                    strategy_name_to_execute = chosen_strategy_dict_entry['name']
                                    alert_context_for_execution = { "alert_id": alert_id, "alert_type": alert_type }

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
                                        action_type_str_incident = "INCIDENT_RESPONSE_ACCIDENT"
                                        action_kpi_cfg_incident = self.action_kpi_config.get(action_type_str_incident) # Corrected

                                        if action_kpi_cfg_incident:
                                            action_timestamp_utc_incident = datetime.utcnow()
                                            current_action_parameters_for_kpi = {
                                                "incident_id": alert_id, "signal_id": signal.signal_id,
                                                "strategy_applied": strategy_name_to_execute,
                                                "selection_method": incident_strategy_selection_method,
                                            }
                                            # Corrected: Use a new variable for fetched_kpis_for_incident
                                            fetched_kpis_for_incident = await self._fetch_pre_action_kpis(
                                                action_type_str_incident,
                                                [signal.signal_id, f"incident_area:{alert_id}"], # Target IDs
                                                {"incident_location": alert_location_model, "radius_meters": self.ACCIDENT_PRE_KPI_RADIUS_METERS}, # Params for KPI fetch
                                                system_kpis
                                            )
                                            pre_action_kpis_for_log_incident = fetched_kpis_for_incident.copy()
                                            pre_action_kpis_for_log_incident.update({
                                                "alert_type": alert_type,
                                                "incident_id_for_response": alert_id,
                                                "signal_initial_phase_at_decision": signal.current_phase.value if signal.current_phase else "N/A",
                                                "chosen_strategy_name": chosen_strategy_dict_entry['name'],
                                                "chosen_strategy_avg_score": chosen_strategy_dict_entry['avg_score'],
                                                "num_strategy_candidates": len(candidate_accident_strategies),
                                                "strategy_candidate_scores": {s['name']: round(s['avg_score'], 3) for s in candidate_accident_strategies}
                                            })
                                            pending_item_id_incident = uuid4()
                                            self.pending_kpi_collection.append({
                                                'action_id': pending_item_id_incident,
                                                'action_type': action_type_str_incident,
                                                'target_ids': [signal.signal_id, f"incident_area:{alert_id}"],
                                                'action_timestamp': action_timestamp_utc_incident,
                                                'action_parameters': current_action_parameters_for_kpi,
                                                'pre_action_context_kpis': pre_action_kpis_for_log_incident,
                                                'query_after_timestamp': action_timestamp_utc_incident + timedelta(seconds=action_kpi_cfg_incident["delay_seconds"]),
                                                'metrics_to_collect': action_kpi_cfg_incident["metrics"],
                                                'evaluation_window_minutes': action_kpi_cfg_incident["eval_window_minutes"],
                                                'kpi_query_details': {
                                                    'service_method_name': action_kpi_cfg_incident["service_method"],
                                                    'method_specific_args': {
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
                                        )
                        else:
                            self.logger.debug(f"Signal {signal.signal_id} is not ONLINE, skipping ACCIDENT strategy selection.")

                elif alert_type == "ROAD_CLOSURE":
                    closure_details = alert_item.get("details", {})
                    closure_direction_affected = closure_details.get("direction_affected", "ALL")
                    self.logger.info(f"Processing ROAD_CLOSURE alert {alert_id} at {alert_location_model}. Direction affected: {closure_direction_affected}")

                    for signal_state in all_signal_states:
                        if signal_state.signal_id in processed_signals_for_incident or signal_state.signal_id in processed_signals_for_coordination:
                            continue
                        if not (signal_state.operational_status == SignalOperationalStatusEnum.ONLINE and signal_state.current_phase == SignalPhaseEnum.GREEN):
                            continue

                        is_upstream = self._is_signal_upstream_of_closure(
                            signal_state=signal_state,
                            closure_location=alert_location_model,
                            closure_direction_affected=closure_direction_affected
                        )

                        if is_upstream:
                            self.logger.info(f"ROAD_CLOSURE {alert_id}: Signal {signal_state.signal_id} is UPSTREAM and controls affected flow. Setting to RED.")
                            action_type_str = "SET_SIGNAL_RED_ROAD_CLOSURE"
                            params_for_pre_kpi_fetch = {}
                            fetched_kpis_closure = await self._fetch_pre_action_kpis( # Renamed fetched_kpis
                                action_type_str, [signal_state.signal_id], params_for_pre_kpi_fetch, system_kpis
                            )
                            try:
                                response = await self.traffic_signal_service.set_signal_phase(
                                    signal_id=signal_state.signal_id,
                                    phase=SignalPhaseEnum.RED,
                                    duration_seconds=self.INCIDENT_SIGNAL_COOLDOWN_SECONDS
                                )
                                if response.status in [SignalControlStatusEnum.ACCEPTED, SignalControlStatusEnum.SUCCESS]:
                                    action_ts = datetime.utcnow()
                                    action_parameters_for_log = {
                                        "phase": SignalPhaseEnum.RED.value,
                                        "duration_seconds": self.INCIDENT_SIGNAL_COOLDOWN_SECONDS,
                                        "incident_id": alert_id,
                                        "signal_id": signal_state.signal_id,
                                        "closure_direction_affected": closure_direction_affected,
                                        "signal_main_flow_direction": signal_state.main_flow_direction
                                    }
                                    pre_action_kpis_for_log = {
                                        "alert_type": alert_type,
                                        "incident_id_for_response": alert_id,
                                        "signal_initial_phase_at_decision": signal_state.current_phase.value,
                                        "closure_location": alert_location_model.model_dump()
                                    }
                                    if fetched_kpis_closure: # Use renamed variable
                                        pre_action_kpis_for_log.update(fetched_kpis_closure)

                                    action_kpi_cfg = self.action_kpi_config.get(action_type_str) # Corrected
                                    if action_kpi_cfg:
                                        pending_item_id_closure = uuid4()
                                        self.pending_kpi_collection.append({
                                            'action_id': pending_item_id_closure,
                                            'action_type': action_type_str,
                                            'target_ids': [signal_state.signal_id],
                                            'action_timestamp': action_ts,
                                            'action_parameters': action_parameters_for_log,
                                            'pre_action_context_kpis': pre_action_kpis_for_log,
                                            'query_after_timestamp': action_ts + timedelta(seconds=action_kpi_cfg["delay_seconds"]),
                                            'metrics_to_collect': action_kpi_cfg["metrics"],
                                            'evaluation_window_minutes': action_kpi_cfg["eval_window_minutes"],
                                            'kpi_query_details': {
                                                'service_method_name': action_kpi_cfg["service_method"],
                                                'method_specific_args': {'signal_id': signal_state.signal_id}
                                            }
                                        })
                                        self.logger.info(f"Scheduled KPI collection for {action_type_str} (ID: {pending_item_id_closure}) on {signal_state.signal_id} for ROAD_CLOSURE {alert_id}.")

                                    processed_signals_for_incident.add(signal_state.signal_id)
                                    processed_signals_for_coordination.add(signal_state.signal_id)
                            except Exception as e:
                                self.logger.error(f"Error setting signal {signal_state.signal_id} to RED for ROAD_CLOSURE {alert_id}: {e}", exc_info=True)
                        else:
                            self.logger.debug(f"ROAD_CLOSURE {alert_id}: Signal {signal_state.signal_id} is not upstream or does not control affected flow. No action taken.")

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

                # --- Start of New Duration Selection Logic ---
                selected_duration = 60 # Default duration
                duration_choice_method = "DEFAULT_DURATION"
                signal_selection_method = action_choice_method # Save original signal selection method

                candidate_durations_scores: List[Dict[str, Any]] = []
                for duration_opt in self.congestion_duration_options:
                    duration_action_signature = f"{action_type_str}:{signal_to_control_state.signal_id}:{duration_opt}s"
                    duration_scores = self.action_effectiveness_memory.get(duration_action_signature, [])
                    avg_duration_score = sum(duration_scores) / len(duration_scores) if duration_scores else 0.0
                    candidate_durations_scores.append({'duration': duration_opt, 'avg_score': avg_duration_score})
                    self.logger.debug(f"  Duration option for '{signal_to_control_state.signal_id}': {duration_opt}s, Avg historical score: {avg_duration_score:.2f} from {len(duration_scores)} scores")

                if candidate_durations_scores:
                    # Use a separate epsilon for duration exploration, or reuse self.exploration_epsilon
                    # For simplicity, reusing self.exploration_epsilon here.
                    if self.rng.random() < self.exploration_epsilon:
                        selected_duration_entry = self.rng.choice(candidate_durations_scores)
                        selected_duration = selected_duration_entry['duration']
                        duration_choice_method = f"EXPLORATORY_RANDOM_DURATION (AvgScore: {selected_duration_entry['avg_score']:.2f})"
                    else:
                        candidate_durations_scores.sort(key=lambda x: x['avg_score'], reverse=True)
                        if all(cds['avg_score'] == 0.0 for cds in candidate_durations_scores) and len(candidate_durations_scores) > 0 :
                             # If all scores are 0 (e.g. no history for any), pick a sensible default like the middle option or a pre-defined default.
                            selected_duration = self.congestion_duration_options[len(self.congestion_duration_options) // 2]
                            duration_choice_method = "EXPLOITATIVE_NO_HISTORY_DEFAULT_DURATION"
                        elif candidate_durations_scores: # Should have at least one entry
                            selected_duration_entry = candidate_durations_scores[0]
                            selected_duration = selected_duration_entry['duration']
                            duration_choice_method = f"EXPLOITATIVE_BEST_DURATION_SCORE (AvgScore: {selected_duration_entry['avg_score']:.2f})"
                        # else: selected_duration remains default 60s, duration_choice_method "DEFAULT_DURATION"
                self.logger.info(f"Duration Selection for {signal_to_control_state.signal_id} (Signal Choice: {signal_selection_method}): Chose duration {selected_duration}s via {duration_choice_method}")
                # --- End of New Duration Selection Logic ---

                self.logger.info(f"General Congestion ({signal_selection_method}, Duration: {selected_duration}s via {duration_choice_method}): Setting signal '{signal_to_control_state.signal_id}' to GREEN.")
                try:
                    response = await self.traffic_signal_service.set_signal_phase(
                        signal_id=signal_to_control_state.signal_id, phase=SignalPhaseEnum.GREEN, duration_seconds=selected_duration) # Use selected_duration
                    if response.status in [SignalControlStatusEnum.ACCEPTED, SignalControlStatusEnum.SUCCESS]:
                        action_timestamp_utc = datetime.utcnow()
                        self._recent_signal_actions[signal_to_control_state.signal_id] = {
                            'timestamp': action_timestamp_utc, 'phase_commanded': SignalPhaseEnum.GREEN,
                            'duration_commanded': selected_duration, 'reason': 'general_congestion',
                            'selection_method': signal_selection_method, 'duration_selection_method': duration_choice_method
                        }
                        processed_signals_for_coordination.add(signal_to_control_state.signal_id)

                        action_parameters_for_log = {
                            "phase": SignalPhaseEnum.GREEN.value,
                            "duration_seconds": selected_duration, # Log chosen duration
                            "selection_method": signal_selection_method, # Original signal selection
                            "duration_selection_method": duration_choice_method, # How duration was chosen
                            "candidate_duration_scores": {str(cds['duration'])+"s": round(cds['avg_score'],3) for cds in candidate_durations_scores}
                        }
                        pre_action_kpis_for_log = {
                            "overall_system_congestion_at_decision": current_congestion_level,
                            "signal_initial_phase_at_decision": signal_to_control_state.current_phase.value if signal_to_control_state.current_phase else 'N/A',
                            "chosen_candidate_avg_score": selected_candidate_dict_entry['avg_score'],
                            "num_candidates_considered": len(candidate_signals_for_congestion_relief),
                            "all_candidate_scores": {c['signal_id']: c['avg_score'] for c in candidate_signals_for_congestion_relief}
                        }
                        if fetched_pre_action_kpis: pre_action_kpis_for_log.update(fetched_pre_action_kpis)

                        action_kpi_cfg = self.action_kpi_config.get(action_type_str) # Corrected
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
                action_kpi_cfg = self.action_kpi_config.get(action_type_str) # Corrected
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

        if self._memory_updated_this_cycle:
            self._save_effectiveness_memory()
        self.logger.info(f"--- AgentCore cycle completed for {sample_user_id} at {datetime.utcnow().isoformat()} ---")

    def set_goal(self, goal: Goal):
        self.active_goal = goal
        self.logger.info(f"New goal set: {goal.description}")

    def clear_goal(self):
        if self.active_goal:
            self.logger.info(f"Clearing goal: {self.active_goal.description}")
            self.active_goal = None
        else:
            self.logger.info("No active goal to clear.")

    async def _formulate_dms_message(self, incident_type: str, incident_location: LocationModel, closure_direction_affected: Optional[str]) -> List[DmsMessage]:
        messages: List[DmsMessage] = []
        location_name = incident_location.name if incident_location.name else "AREA"

        if incident_type.upper() == "ROAD_CLOSURE":
            line1 = f"ROAD CLOSED"
            line2 = f"AT {location_name.upper()}"
            if closure_direction_affected:
                line2 += f" {closure_direction_affected.upper()}BOUND"
            line3 = "USE ALT ROUTE"
            messages.append(DmsMessage(text=f"{line1} {line2}", page_number=1))
            messages.append(DmsMessage(text=line3, page_number=2))
            # Could add a third page with "EXPECT DELAYS"

        elif incident_type.upper() == "ACCIDENT":
            line1 = f"ACCIDENT"
            line2 = f"AT {location_name.upper()}"
            line3 = "EXPECT DELAYS"
            line4 = "REDUCE SPEED"
            messages.append(DmsMessage(text=f"{line1} {line2}", page_number=1))
            messages.append(DmsMessage(text=line3, page_number=2))
            messages.append(DmsMessage(text=line4, page_number=3)) # Example of a third page

        else: # Default message for other incident types if DMS activation is expanded
            messages.append(DmsMessage(text=f"{incident_type.upper()} AT {location_name.upper()}", page_number=1))
            messages.append(DmsMessage(text="PROCEED WITH CAUTION", page_number=2))

        # Ensure messages are within typical DMS display limits (e.g., character count per line/page)
        # This is a basic placeholder; real DMS have specific constraints.
        # For now, assuming the DmsMessage model or display hardware handles truncation/formatting.
        # Max characters per message object can be enforced here if needed.
        for msg in messages:
            if len(msg.text) > 30: # Arbitrary limit for example
                 self.logger.warning(f"DMS message text too long, may be truncated: '{msg.text}'")
                 # msg.text = msg.text[:30] # Example truncation

        self.logger.debug(f"Formulated DMS messages for {incident_type} at {location_name}: {[(m.page_number, m.text) for m in messages]}")
        return messages

    async def _handle_dms_for_incident(self, incident_alert: Dict[str, Any], system_kpis: Dict[str, Any]):
        all_dms_states = await self.dms_service.get_all_dms_states()
        incident_type = incident_alert.get("type", "UNKNOWN_INCIDENT").upper()
        incident_location_data = incident_alert.get("location")

        if not incident_location_data:
            self.logger.warning(f"DMS Handling: Incident {incident_alert.get('id')} has no location data. Skipping DMS activation.")
            return

        try:
            incident_location = LocationModel(**incident_location_data)
        except Exception as e:
            self.logger.error(f"DMS Handling: Could not parse incident location for {incident_alert.get('id')}: {e}. Skipping DMS.")
            return

        alert_severity_str = incident_alert.get("severity", "low")
        if alert_severity_str.lower() == "critical":
            alert_severity = IncidentSeverityEnum.CRITICAL
        elif alert_severity_str.lower() == "high":
            alert_severity = IncidentSeverityEnum.HIGH
        elif alert_severity_str.lower() == "medium":
            alert_severity = IncidentSeverityEnum.MEDIUM
        else:
            alert_severity = IncidentSeverityEnum.LOW

        if not (incident_type == "ROAD_CLOSURE" or (incident_type == "ACCIDENT" and alert_severity in [IncidentSeverityEnum.HIGH, IncidentSeverityEnum.CRITICAL])):
            self.logger.debug(f"DMS Handling: Incident type {incident_type} with severity {alert_severity.value} not configured for DMS activation. Skipping.")
            return

        closure_direction_affected = incident_alert.get("details", {}).get("direction_affected")
        MAX_DMS_ACTIVATION_RADIUS_METERS = 2000
        relevant_dms_found = False

        for dms_state in all_dms_states:
            if dms_state.operational_status == DmsStatusEnum.ONLINE and dms_state.location:
                # Simplified distance check (Manhattan distance for example on a grid)
                # Replace with Haversine or proper geospatial query in a real system
                lat_diff = abs(dms_state.location.latitude - incident_location.latitude)
                lon_diff = abs(dms_state.location.longitude - incident_location.longitude)
                # Rough conversion: 0.01 degrees ~ 1.1km. So 2km ~ 0.018 degrees
                if lat_diff < 0.018 * (MAX_DMS_ACTIVATION_RADIUS_METERS / 1000.0) and \
                   lon_diff < 0.018 * (MAX_DMS_ACTIVATION_RADIUS_METERS / 1000.0): # Approx check

                    # TODO: Add more sophisticated logic here:
                    # - Is DMS upstream of incident for relevant traffic flow?
                    # - Does DMS viewable_directions match traffic that would be affected?
                    # - Is DMS target_roadway_segment_id relevant?

                    messages_to_display = await self._formulate_dms_message(incident_type, incident_location, closure_direction_affected)

                    if messages_to_display:
                        self.logger.info(f"DMS Handling: Activating DMS {dms_state.dms_id} for incident {incident_alert.get('id')}. Message: '{messages_to_display[0].text}'")
                        try:
                            response = await self.dms_service.set_dms_message(dms_state.dms_id, messages_to_display)
                            if response.status == SignalControlStatusEnum.SUCCESS or response.status == SignalControlStatusEnum.ACCEPTED:
                                self.logger.info(f"DMS {dms_state.dms_id} message set successfully for incident {incident_alert.get('id')}.")
                                relevant_dms_found = True
                                # Log this action (simplified logging for now)
                                self.action_performance_logs.append(ActionPerformanceLog(
                                    action_timestamp=datetime.utcnow(),
                                    action_type="SET_DMS_MESSAGE",
                                    target_ids=[dms_state.dms_id],
                                    action_parameters={
                                        "incident_id": incident_alert.get('id'),
                                        "incident_type": incident_type,
                                        "messages": [msg.model_dump() for msg in messages_to_display]
                                    },
                                    # Pre/Post KPIs for DMS are TBD, can be added later if direct effectiveness is measurable
                                ))
                                # For now, only activate one DMS per incident for simplicity in this initial step
                                break
                            else:
                                self.logger.error(f"DMS Handling: Failed to set message on DMS {dms_state.dms_id} for incident {incident_alert.get('id')}: {response.message}")
                        except Exception as e:
                            self.logger.error(f"DMS Handling: Exception setting message on DMS {dms_state.dms_id}: {e}", exc_info=True)
        if not relevant_dms_found:
            self.logger.info(f"DMS Handling: No suitable/available DMS found or activated for incident {incident_alert.get('id')}.")


async def main_example_run_with_mock_time(iso_time_str: str, user_id_for_cycle: str, agent_instance: AgentCore, analytics_service_mock: MagicMock, kpis: Dict[str, Any]):
    """Helper to run agent cycle with mocked time and system KPIs."""
    dt_object = datetime.fromisoformat(iso_time_str.replace("Z", "+00:00"))
    with patch('backend.app.core.agent_core.datetime') as mock_dt:
        mock_dt.utcnow = MagicMock(return_value=dt_object)
        mock_dt.fromisoformat = datetime.fromisoformat # Keep original for parsing
        mock_dt.strptime = datetime.strptime # Keep original for parsing time windows

        # Configure get_current_system_kpis_summary for this specific call
        analytics_service_mock.get_current_system_kpis_summary = MagicMock(return_value=kpis)
        await agent_instance.run_decision_cycle(sample_user_id=user_id_for_cycle)

async def main_example():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s') # Changed to INFO for less noise
    logger.info(f"--- Setting up main_example for All Demonstrations ---")
    # pytest and other imports might be needed if this were a formal test file
    import pytest # Added for pytest.approx

    os.makedirs(EFFECTIVENESS_MEMORY_DIR, exist_ok=True)
    if os.path.exists(EFFECTIVENESS_MEMORY_FILEPATH):
        try:
            os.remove(EFFECTIVENESS_MEMORY_FILEPATH)
            logger.info(f"Removed existing effectiveness memory file: {EFFECTIVENESS_MEMORY_FILEPATH}")
        except OSError as e:
            logger.error(f"Error removing effectiveness memory file {EFFECTIVENESS_MEMORY_FILEPATH}: {e}")


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
            logger.debug(f"MockAnalytics CONFIGURED PRE-KPIs for {service_method_name}:{target_id} = {kpi_data}")

        def configure_post_action_kpis(self, target_id: str, service_method_name: str, kpi_data: Dict[str, Any]):
            self._post_configured_kpis[f"{service_method_name}:{target_id}"] = kpi_data
            logger.debug(f"MockAnalytics CONFIGURED POST-KPIs for {service_method_name}:{target_id} = {kpi_data}")

        async def get_critical_alert_summary(self): return {"active_alerts":[]}
        def get_current_system_kpis_summary(self):
            # This will be overridden by main_example_run_with_mock_time's specific mock for each cycle
            return {"overall_congestion_level": "LOW", "default_sys_kpi": True}


        async def get_signal_current_kpis(self, signal_id: str, metrics_to_collect: List[str]):
            lookup_key = f"get_signal_current_kpis:{signal_id}"
            logger.debug(f"MockAnalytics: Attempting PRE-KPI lookup for {lookup_key} with metrics: {metrics_to_collect}")
            if lookup_key in self._pre_configured_kpis:
                logger.debug(f"MockAnalytics: Found pre-configured PRE-KPIs for {lookup_key}")
                return self._pre_configured_kpis[lookup_key]
            # Fallback dynamic KPIs if not configured (should ideally be configured by tests)
            kpis = {"queried_signal_id_pre_action": signal_id, "data_timestamp": datetime.utcnow().isoformat()}
            if "queue_lengths_meters" in metrics_to_collect: kpis["queue_lengths_meters"] = {"N": self._dynamic_value(signal_id, "pre_q_n", 10, 50), "S": self._dynamic_value(signal_id, "pre_q_s", 5, 30)}
            if "current_flow_vph" in metrics_to_collect: kpis["current_flow_vph"] = self._dynamic_value(signal_id, "pre_flow", 150, 450)
            if "typical_flow_vph" in metrics_to_collect: kpis["typical_flow_vph"] = self._dynamic_value(signal_id, "pre_typical_flow", 100, 400)
            logger.debug(f"MockAnalytics: Returning DYNAMIC PRE-KPIs for {signal_id}: {kpis}")
            return kpis

        async def get_corridor_current_kpis(self, corridor_id: str, metrics_to_collect: List[str]):
            lookup_key = f"get_corridor_current_kpis:{corridor_id}"
            logger.debug(f"MockAnalytics: Attempting PRE-KPI lookup for {lookup_key} with metrics: {metrics_to_collect}")
            if lookup_key in self._pre_configured_kpis:
                logger.debug(f"MockAnalytics: Found pre-configured PRE-KPIs for {lookup_key}")
                return self._pre_configured_kpis[lookup_key]
            kpis = {"queried_corridor_id_pre_action": corridor_id, "data_timestamp": datetime.utcnow().isoformat()}
            if "avg_travel_time_seconds" in metrics_to_collect: kpis["avg_travel_time_seconds"] = self._dynamic_value(corridor_id, "pre_tt", 100, 220)
            if "throughput_vph" in metrics_to_collect: kpis["throughput_vph"] = self._dynamic_value(corridor_id, "pre_tp", 300, 650)
            if "corridor_baseline_avg_travel_time_seconds" in metrics_to_collect: kpis["corridor_baseline_avg_travel_time_seconds"] = self._dynamic_value(corridor_id, "pre_base_tt", 90, 180)
            if "corridor_baseline_throughput_vph" in metrics_to_collect: kpis["corridor_baseline_throughput_vph"] = self._dynamic_value(corridor_id, "pre_base_tp", 250, 600)
            logger.debug(f"MockAnalytics: Returning DYNAMIC PRE-KPIs for {corridor_id}: {kpis}")
            return kpis

        async def get_incident_area_current_kpis(self, incident_location: LocationModel, radius_meters: int, metrics_to_collect: List[str]):
            mock_config_key = f"{incident_location.latitude}_{incident_location.longitude}"
            lookup_key = f"get_incident_area_current_kpis:{mock_config_key}" # Key by lat_lon string
            logger.debug(f"MockAnalytics: Attempting PRE-KPI lookup for {lookup_key} (incident) with metrics: {metrics_to_collect}")
            if lookup_key in self._pre_configured_kpis:
                logger.debug(f"MockAnalytics: Found pre-configured PRE-KPIs for incident area {lookup_key}")
                return self._pre_configured_kpis[lookup_key]
            kpis = {"avg_speed_kmh": self._dynamic_value(str(incident_location), "pre_inc_speed", 5,25),
                    "vehicle_count": self._dynamic_value(str(incident_location), "pre_inc_vc", 50,100)}
            logger.debug(f"MockAnalytics: Returning DYNAMIC PRE-KPIs for incident area: {kpis}")
            return kpis

        async def get_signal_post_action_kpis(self, signal_id: str, metrics_to_collect: List[str] = None, **kwargs) -> Dict[str, Any]:
            if metrics_to_collect is None: metrics_to_collect = []
            lookup_key = f"get_signal_post_action_kpis:{signal_id}"
            logger.debug(f"MockAnalytics: Attempting POST-KPI lookup for {lookup_key} with metrics_to_collect: {metrics_to_collect}")
            if lookup_key in self._post_configured_kpis:
                logger.debug(f"MockAnalytics: Found pre-configured POST-KPIs for {lookup_key}")
                return self._post_configured_kpis[lookup_key]

            kpis = {"local_congestion_level": "LOW", "flow_rate_absolute": self._dynamic_value(signal_id,"post_flow",700,1500)}
            if "cross_traffic_queue_lengths_meters" in metrics_to_collect or not metrics_to_collect: # if no specific metrics, return all
                kpis["cross_traffic_queue_lengths_meters"] = {
                    "total": self._dynamic_value(signal_id, "post_cross_q_total", 10, 100),
                    "E": self._dynamic_value(signal_id, "post_cross_q_e", 5, 50),
                    "W": self._dynamic_value(signal_id, "post_cross_q_w", 5, 50)
                }
            if "upstream_flow_rate_reduction_percentage" in metrics_to_collect: # For SET_SIGNAL_RED_ROAD_CLOSURE
                 kpis["upstream_flow_rate_reduction_percentage"] = self._dynamic_value(signal_id, "post_flow_reduct", 50, 95)
            if "flow_rate_towards_closure_absolute" in metrics_to_collect: # For SET_SIGNAL_RED_ROAD_CLOSURE
                 kpis["flow_rate_towards_closure_absolute"] = self._dynamic_value(signal_id, "post_flow_closure", 0, 20)

            logger.debug(f"MockAnalytics: Returning DYNAMIC POST-KPIs for {signal_id}: {kpis}")
            return kpis

        async def get_corridor_post_action_kpis(self, corridor_id: str, metrics_to_collect: List[str] = None, **kwargs) -> Dict[str, Any]:
            if metrics_to_collect is None: metrics_to_collect = []
            lookup_key = f"get_corridor_post_action_kpis:{corridor_id}"
            logger.debug(f"MockAnalytics: Attempting POST-KPI lookup for {lookup_key} with metrics_to_collect: {metrics_to_collect}")
            if lookup_key in self._post_configured_kpis:
                logger.debug(f"MockAnalytics: Found pre-configured POST-KPIs for {lookup_key}")
                return self._post_configured_kpis[lookup_key]

            kpis = {"corridor_avg_travel_time_seconds": self._dynamic_value(corridor_id, "post_tt", 70,150),
                    "corridor_throughput_vph": self._dynamic_value(corridor_id,"post_tp",600,1200)}
            if "side_street_avg_queue_increase_meters" in metrics_to_collect or not metrics_to_collect:
                kpis["side_street_avg_queue_increase_meters"] = self._dynamic_value(corridor_id, "post_side_q_inc", 5, 25)
            logger.debug(f"MockAnalytics: Returning DYNAMIC POST-KPIs for {corridor_id}: {kpis}")
            return kpis

        async def get_incident_response_post_action_kpis(self, incident_id: str, affected_signal_ids: Optional[List[str]] = None, **kwargs) -> Dict[str, Any]:
            lookup_key = f"get_incident_response_post_action_kpis:{incident_id}"
            logger.debug(f"MockAnalytics: Attempting POST-KPI lookup for {lookup_key} (incident)")
            if lookup_key in self._post_configured_kpis:
                logger.debug(f"MockAnalytics: Found pre-configured POST-KPIs for incident {lookup_key}")
                return self._post_configured_kpis[lookup_key]

            kpis = {"area_clearance_time_minutes": self._dynamic_value(incident_id,"clear_time_min",10,60),
                    "avg_speed_kmh_incident_zone": self._dynamic_value(incident_id,"post_inc_speed",20,50)}
            logger.debug(f"MockAnalytics: Returning DYNAMIC POST-KPIs for incident: {kpis}")
            return kpis

    class MockTraffic(MagicMock):
        _signals = {}
        def __init__(self, *args, **kwargs): super().__init__(*args, **kwargs); self._initialize_mock_signals()
        def _initialize_mock_signals(self):
            self._signals.clear()
            sids = ["TS001","TS002","TS003","TS004","TS005"]
            signal_configs = {
                "TS001": {"lat": 34.06, "lon": -118.24, "flow": "S", "name": "Signal North"},
                "TS002": {"lat": 34.05, "lon": -118.24, "flow": "NS", "name": "Signal Center NS"},
                "TS003": {"lat": 34.05, "lon": -118.25, "flow": "E", "name": "Signal West"},
                "TS004": {"lat": 34.04, "lon": -118.24, "flow": "N", "name": "Signal South"},
                "TS005": {"lat": 34.05, "lon": -118.23, "flow": "W", "name": "Signal East"}
            }
            for sid in sids:
                config = signal_configs.get(sid)
                if config:
                    self._signals[sid]=SignalState(
                        signal_id=sid,
                        location=LocationModel(latitude=config["lat"], longitude=config["lon"], name=config["name"]),
                        current_phase=SignalPhaseEnum.RED,
                        operational_status=SignalOperationalStatusEnum.ONLINE,
                        last_updated=datetime.utcnow(),
                        main_flow_direction=config["flow"]
                    )
                else:
                     self._signals[sid]=SignalState(
                        signal_id=sid,
                        location=LocationModel(latitude=34.00,longitude=-118.00, name=f"Signal {sid} Fallback"),
                        current_phase=SignalPhaseEnum.RED,
                        operational_status=SignalOperationalStatusEnum.ONLINE,
                        last_updated=datetime.utcnow(),
                        main_flow_direction="NS"
                    )
        async def get_all_signal_states(self): return list(self._signals.values())
        async def set_signal_phase(self, signal_id,phase,duration_seconds): # Added duration_seconds to match call
            if signal_id in self._signals:
                self._signals[signal_id].current_phase=phase
                self._signals[signal_id].last_updated=datetime.utcnow()
                # Log the duration that was commanded
                logger.info(f"MockTraffic: Signal {signal_id} set to {phase.value} for {duration_seconds}s.")
                return SignalControlCommandResponse(signal_id=signal_id,status=SignalControlStatusEnum.ACCEPTED, requested_phase=phase)
            return SignalControlCommandResponse(signal_id=signal_id,status=SignalControlStatusEnum.FAILED, message="Not found")


    analytics_mock = MockAnalytics(); traffic_mock = MockTraffic()

    class MockDmsService(MagicMock):
        _dms_states: Dict[str, DmsState] = {}
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.logger = logging.getLogger(__name__ + ".MockDmsService")
            self._dms_states: Dict[str, DmsState] = {
                "DMS_UPSTREAM_NB_01": DmsState(
                    dms_id="DMS_UPSTREAM_NB_01",
                    location=LocationModel(latitude=34.035, longitude=-118.24, name="DMS Upstream of TS002 for NB Traffic"), # South of TS002
                    current_messages=[],
                    operational_status=DmsStatusEnum.ONLINE,
                    last_updated=datetime.utcnow(),
                    capabilities=["set_custom_message", "clear_message", "max_pages_2"],
                    viewable_directions=["N"] # Viewable by Northbound traffic
                ),
                "DMS_UPSTREAM_EB_01": DmsState(
                    dms_id="DMS_UPSTREAM_EB_01",
                    location=LocationModel(latitude=34.05, longitude=-118.255, name="DMS Upstream of TS002 for EB Traffic"), # West of TS002
                    current_messages=[],
                    operational_status=DmsStatusEnum.ONLINE,
                    last_updated=datetime.utcnow(),
                    capabilities=["set_custom_message", "clear_message"],
                    viewable_directions=["E"] # Viewable by Eastbound traffic
                ),
                "DMS_DOWNSTREAM_SB_01": DmsState(
                    dms_id="DMS_DOWNSTREAM_SB_01",
                    location=LocationModel(latitude=34.065, longitude=-118.24, name="DMS Downstream (North) of TS002"), # North of TS002
                    current_messages=[],
                    operational_status=DmsStatusEnum.ONLINE,
                    last_updated=datetime.utcnow(),
                    capabilities=["set_custom_message", "clear_message"],
                    viewable_directions=["S"] # Viewable by Southbound traffic
                ),
                 "DMS_OFFLINE_01": DmsState(
                    dms_id="DMS_OFFLINE_01",
                    location=LocationModel(latitude=34.045, longitude=-118.235, name="Offline DMS"),
                    current_messages=[],
                    operational_status=DmsStatusEnum.OFFLINE,
                    last_updated=datetime.utcnow(),
                    capabilities=[]
                )
            }
            self.logger.info(f"MockDmsService initialized with {len(self._dms_states)} DMS units.")

        async def get_all_dms_states(self) -> List[DmsState]:
            self.logger.debug(f"MockDmsService.get_all_dms_states called, returning {len(self._dms_states)} states.")
            return list(self._dms_states.values())

        async def set_dms_message(self, dms_id: str, messages: List[DmsMessage], duration_minutes: Optional[int] = None) -> DmsCommandResponse:
            self.logger.info(f"MockDmsService.set_dms_message called for {dms_id} with {len(messages)} pages, duration {duration_minutes} mins.")
            if dms_id in self._dms_states:
                 if self._dms_states[dms_id].operational_status == DmsStatusEnum.ONLINE:
                    self._dms_states[dms_id].current_messages = messages
                    self._dms_states[dms_id].last_updated = datetime.utcnow()
                    return DmsCommandResponse(dms_id=dms_id, status=SignalControlStatusEnum.SUCCESS, message="Mock DMS message set.")
                 else:
                    return DmsCommandResponse(dms_id=dms_id, status=SignalControlStatusEnum.REJECTED, message="Mock DMS not ONLINE.")
            return DmsCommandResponse(dms_id=dms_id, status=SignalControlStatusEnum.FAILED, message="Mock DMS not found.")

        async def clear_dms_message(self, dms_id: str) -> DmsCommandResponse:
            self.logger.info(f"MockDmsService.clear_dms_message called for {dms_id}")
            if dms_id in self._dms_states:
                if self._dms_states[dms_id].operational_status == DmsStatusEnum.ONLINE:
                    self._dms_states[dms_id].current_messages = []
                    self._dms_states[dms_id].last_updated = datetime.utcnow()
                    return DmsCommandResponse(dms_id=dms_id, status=SignalControlStatusEnum.SUCCESS, message="Mock DMS message cleared.")
                else:
                    return DmsCommandResponse(dms_id=dms_id, status=SignalControlStatusEnum.REJECTED, message="Mock DMS not ONLINE.")
            return DmsCommandResponse(dms_id=dms_id, status=SignalControlStatusEnum.FAILED, message="Mock DMS not found.")

    dms_mock = MockDmsService()

    def reset_mock_traffic_signals_for_congestion_demo(phase=SignalPhaseEnum.RED):
        for sig_id_congestion in ["TS001", "TS002", "TS004"]:
            if sig_id_congestion in traffic_mock._signals:
                traffic_mock._signals[sig_id_congestion].current_phase = phase
                traffic_mock._signals[sig_id_congestion].operational_status = SignalOperationalStatusEnum.ONLINE
        if "TS003" in traffic_mock._signals: traffic_mock._signals["TS003"].current_phase = SignalPhaseEnum.GREEN
        if "TS005" in traffic_mock._signals: traffic_mock._signals["TS005"].operational_status = SignalOperationalStatusEnum.OFFLINE
        logger.debug(f"MAIN_EXAMPLE: Reset signals for congestion demo (TS001,TS002,TS004 to {phase.value}).")

    agent = AgentCore(
        MagicMock(spec=PredictionScheduler),
        MagicMock(spec=PersonalizedRoutingService),
        analytics_mock,
        traffic_mock,
        dms_mock # Pass the DmsService mock
    )

    logger.info("--- MAIN_EXAMPLE: Starting Epsilon-Greedy General Congestion Demonstration ---")
    original_epsilon = agent.exploration_epsilon
    original_rng_state = agent.rng.getstate()
    agent.rng.seed(123)

    # kpi_collection_delay = ACTION_KPI_CONFIG["SET_SIGNAL_GREEN_CONGESTION"]["delay_seconds"] # Not directly used in this version of helper
    current_sim_time_str = "2023-01-01T10:00:00Z"

    logger.info("--- MAIN_EXAMPLE: Cycle Group 1 - Building Initial History (Forcing Exploitation) ---")
    agent.exploration_epsilon = 0.0
    logger.info(f"MAIN_EXAMPLE: Temporarily set exploration_epsilon to {agent.exploration_epsilon}")

    async def run_action_and_kpi_cycles(action_time_str, action_user_id, kpi_user_id,
                                        target_id_for_kpi_config,
                                        action_type_for_kpi_config,
                                        post_kpi_payload,
                                        kpi_service_method_name,
                                        demand_kpi_settings=None,
                                        overall_congestion_level_action="HIGH",
                                        overall_congestion_level_kpi="LOW"):
        nonlocal current_sim_time_str

        logger.info(f"MAIN_EXAMPLE: Running ACTION cycle at {action_time_str} for {action_user_id} targeting {target_id_for_kpi_config if target_id_for_kpi_config else 'any'}")

        current_kpi_snapshot = {"overall_congestion_level": overall_congestion_level_action}
        if demand_kpi_settings:
            for gw_cfg_id_inner in GREEN_WAVE_CORRIDOR_CONFIGS:
                demand_kpi_inner = GREEN_WAVE_CORRIDOR_CONFIGS[gw_cfg_id_inner].get("demand_kpi_trigger")
                if demand_kpi_inner and demand_kpi_inner not in demand_kpi_settings:
                    current_kpi_snapshot[demand_kpi_inner] = "LOW"
            current_kpi_snapshot.update(demand_kpi_settings)

        await main_example_run_with_mock_time(
            action_time_str, action_user_id, agent, analytics_mock,
            kpis=current_kpi_snapshot
        )

        latest_action_item = None
        if agent.pending_kpi_collection:
            for item_idx in range(len(agent.pending_kpi_collection) - 1, -1, -1):
                pending_item = agent.pending_kpi_collection[item_idx]
                if pending_item['action_type'] == action_type_for_kpi_config:
                    if target_id_for_kpi_config is None or pending_item['target_ids'][0] == target_id_for_kpi_config:
                        latest_action_item = pending_item
                        break

        if latest_action_item:
            actual_target_id = latest_action_item['target_ids'][0]
            # Corrected: Use the service_method from the action_kpi_cfg for the specific action type
            action_kpi_cfg_for_post = agent.action_kpi_config.get(action_type_for_kpi_config)
            if action_kpi_cfg_for_post:
                 post_kpi_service_method = action_kpi_cfg_for_post["service_method"]
                 analytics_mock.configure_post_action_kpis(
                    actual_target_id,
                    post_kpi_service_method, # Use specific service method for post KPIs
                    post_kpi_payload
                )
                 logger.info(f"MAIN_EXAMPLE: Configured POST-KPIs for {actual_target_id} ({action_type_for_kpi_config}) using {post_kpi_service_method} to yield: {post_kpi_payload}")
            else:
                logger.error(f"MAIN_EXAMPLE: No ACTION_KPI_CONFIG found for {action_type_for_kpi_config} to determine post-KPI service method.")

        elif target_id_for_kpi_config:
             logger.warning(f"MAIN_EXAMPLE: Expected action for {target_id_for_kpi_config} ({action_type_for_kpi_config}) but none found in pending items.")

        current_kpi_collection_delay = agent.action_kpi_config[action_type_for_kpi_config]["delay_seconds"]
        kpi_collection_time = datetime.fromisoformat(action_time_str.replace("Z","+00:00")) + timedelta(seconds=current_kpi_collection_delay + 15)
        kpi_collection_time_str = kpi_collection_time.isoformat().replace("+00:00", "Z")
        logger.info(f"MAIN_EXAMPLE: Running KPI PROCESSING cycle at {kpi_collection_time_str} for {kpi_user_id}")
        await main_example_run_with_mock_time(
            kpi_collection_time_str, kpi_user_id, agent, analytics_mock,
            kpis={"overall_congestion_level": overall_congestion_level_kpi}
        )
        current_sim_time_str = (kpi_collection_time + timedelta(minutes=15)).isoformat().replace("+00:00", "Z")

    logger.info("--- MAIN_EXAMPLE: Cycle 1.1 (TS001 Good Score - Congestion Demo) ---")
    reset_mock_traffic_signals_for_congestion_demo()
    traffic_mock._signals["TS002"].current_phase = SignalPhaseEnum.GREEN
    traffic_mock._signals["TS004"].current_phase = SignalPhaseEnum.GREEN
    await run_action_and_kpi_cycles(current_sim_time_str, "user_hist_ts001", "user_kpi_ts001",
                                    "TS001", "SET_SIGNAL_GREEN_CONGESTION",
                                    {"local_congestion_level": "LOW", "flow_rate_absolute": 800, "cross_traffic_queue_lengths_meters": {"total": 10}},
                                    "get_signal_post_action_kpis" # This kpi_service_method_name is for configuring the mock, not directly used by agent logic here
                                    )

    logger.info("--- MAIN_EXAMPLE: Cycle 1.2 (TS002 Bad Score - Congestion Demo) ---")
    reset_mock_traffic_signals_for_congestion_demo()
    traffic_mock._signals["TS001"].current_phase = SignalPhaseEnum.GREEN
    agent._recent_signal_actions.clear()
    agent._recent_signal_actions["TS001"] = {'timestamp': datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) - timedelta(seconds=agent.SIGNAL_ACTION_COOLDOWN_SECONDS + 10), 'reason':'demo'}
    traffic_mock._signals["TS004"].current_phase = SignalPhaseEnum.GREEN
    await run_action_and_kpi_cycles(current_sim_time_str, "user_hist_ts002", "user_kpi_ts002",
                                    "TS002", "SET_SIGNAL_GREEN_CONGESTION",
                                    {"local_congestion_level": "HIGH", "flow_rate_absolute": 100, "cross_traffic_queue_lengths_meters": {"total": 120}},
                                    "get_signal_post_action_kpis"
                                    )

    logger.info("--- MAIN_EXAMPLE: Cycle 1.3 (TS004 Neutral Score - Congestion Demo) ---")
    reset_mock_traffic_signals_for_congestion_demo()
    agent._recent_signal_actions.clear()
    agent._recent_signal_actions["TS001"] = {'timestamp': datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) - timedelta(seconds=agent.SIGNAL_ACTION_COOLDOWN_SECONDS + 10), 'reason':'demo'}
    agent._recent_signal_actions["TS002"] = {'timestamp': datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) - timedelta(seconds=agent.SIGNAL_ACTION_COOLDOWN_SECONDS + 10), 'reason':'demo'}
    await run_action_and_kpi_cycles(current_sim_time_str, "user_hist_ts004", "user_kpi_ts004",
                                    "TS004", "SET_SIGNAL_GREEN_CONGESTION",
                                    {"local_congestion_level": "MEDIUM", "flow_rate_absolute": 400, "cross_traffic_queue_lengths_meters": {"total": 30}},
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

        action_time_str = current_sim_time_str
        action_user_id = f"user_egreedy_action_{cycle_num}"
        kpi_user_id = f"user_egreedy_kpi_{cycle_num}"

        await run_action_and_kpi_cycles(
            action_time_str, action_user_id, kpi_user_id,
            None,
            "SET_SIGNAL_GREEN_CONGESTION",
            {"local_congestion_level": "MEDIUM", "flow_rate_absolute": 500, "cross_traffic_queue_lengths_meters": {"total": 25}},
            "get_signal_post_action_kpis"
        )
        latest_congestion_action_item = None
        for item_idx in range(len(agent.pending_kpi_collection) - 1, -1, -1):
            item = agent.pending_kpi_collection[item_idx]
            if item['action_type'] == "SET_SIGNAL_GREEN_CONGESTION": # Check it's a congestion action
                # Ensure it's from the current e-greedy cycle, not a leftover from history building
                if item['action_parameters'].get('selection_method') in ["EXPLORATORY_RANDOM", "EXPLOITATIVE_BEST_SCORE"]:
                    latest_congestion_action_item = item
                    break
        if latest_congestion_action_item:
            chosen_signal_id = latest_congestion_action_item['target_ids'][0]
            selection_method = latest_congestion_action_item['action_parameters'].get('selection_method', 'UNKNOWN_METHOD')
            chosen_score_raw = latest_congestion_action_item['pre_action_context_kpis'].get('chosen_candidate_avg_score', 'N/A')
            chosen_score_str = f"{chosen_score_raw:.2f}" if isinstance(chosen_score_raw, float) else str(chosen_score_raw)
            logger.info(f"MAIN_EXAMPLE (Congestion Demo): Cycle {cycle_num} action: {selection_method} chose {chosen_signal_id} (score {chosen_score_str})")
        else:
            logger.info(f"MAIN_EXAMPLE (Congestion Demo): Cycle {cycle_num}: No e-greedy congestion action found in pending items this iteration.")

        logger.info(f"MAIN_EXAMPLE: Effectiveness Memory after E-Greedy Cycle {cycle_num} (Congestion Demo): {json.dumps(agent.action_effectiveness_memory, indent=2)}")

    agent.exploration_epsilon = original_epsilon
    agent.rng.setstate(original_rng_state)
    logger.info(f"MAIN_EXAMPLE: Restored exploration_epsilon to {agent.exploration_epsilon}. RNG state restored.")
    logger.info("--- MAIN_EXAMPLE: Epsilon-Greedy General Congestion Demonstration Completed ---")

    logger.info("--- MAIN_EXAMPLE: Starting Epsilon-Greedy GREEN WAVE SELECTION Demonstration ---")
    original_epsilon_gw_demo = agent.exploration_epsilon
    original_rng_state_gw_demo = agent.rng.getstate()
    agent.exploration_epsilon = 0.5
    agent.rng.seed(456)
    logger.info(f"MAIN_EXAMPLE (GW Demo): Temporarily set exploration_epsilon to {agent.exploration_epsilon}.")

    gw_demo_signals = ["TS001", "TS002", "TS003", "TS004", "TS005"]
    for sig_id_gw_setup in gw_demo_signals:
        if sig_id_gw_setup in traffic_mock._signals:
            traffic_mock._signals[sig_id_gw_setup].current_phase = SignalPhaseEnum.RED
            traffic_mock._signals[sig_id_gw_setup].operational_status = SignalOperationalStatusEnum.ONLINE
        else:
            logger.warning(f"MAIN_EXAMPLE (GW Demo): Signal {sig_id_gw_setup} not found in traffic_mock for setup.")
    logger.info(f"MAIN_EXAMPLE (GW Demo): Ensured GW demo signals ({', '.join(gw_demo_signals)}) are ONLINE and RED.")

    current_sim_time_str_gw_demo = "2023-01-02T07:00:00Z"

    logger.info("--- MAIN_EXAMPLE (GW Demo): Cycle Group 1 - Building GW History (Forcing Exploitation) ---")
    agent.exploration_epsilon = 0.0
    logger.info(f"MAIN_EXAMPLE (GW Demo): Set exploration_epsilon to {agent.exploration_epsilon} for GW History Building.")

    current_sim_time_str_gw_demo = "2023-01-02T07:15:00Z"
    demand_kpis_main_st_only_gw = {
        GREEN_WAVE_CORRIDOR_CONFIGS["main_st_ns_wave"]["demand_kpi_trigger"]: "HIGH",
        GREEN_WAVE_CORRIDOR_CONFIGS["alt_st_ew_wave"]["demand_kpi_trigger"]: "LOW"
    }
    await run_action_and_kpi_cycles(
        current_sim_time_str_gw_demo, "user_gw_hist_main", "user_gw_kpi_main",
        "main_st_ns_wave", "GREEN_WAVE_ACTIVATION",
        {"corridor_avg_travel_time_seconds": 130, "corridor_throughput_vph": 500, "side_street_avg_queue_increase_meters": 15},
        "get_corridor_post_action_kpis",
        demand_kpi_settings=demand_kpis_main_st_only_gw,
        overall_congestion_level_action="LOW"
    )
    gw_kpi_collection_delay_val_hist = agent.action_kpi_config["GREEN_WAVE_ACTIVATION"]["delay_seconds"]
    current_sim_time_str_gw_demo = (datetime.fromisoformat(current_sim_time_str_gw_demo.replace("Z","+00:00")) + timedelta(seconds=gw_kpi_collection_delay_val_hist + 15 + 5*60)).isoformat() + "Z"

    demand_kpis_alt_st_only_gw = {
        GREEN_WAVE_CORRIDOR_CONFIGS["main_st_ns_wave"]["demand_kpi_trigger"]: "LOW",
        GREEN_WAVE_CORRIDOR_CONFIGS["alt_st_ew_wave"]["demand_kpi_trigger"]: "HIGH"
    }
    await run_action_and_kpi_cycles(
        current_sim_time_str_gw_demo, "user_gw_hist_alt", "user_gw_kpi_alt",
        "alt_st_ew_wave", "GREEN_WAVE_ACTIVATION",
        {"corridor_avg_travel_time_seconds": 70, "corridor_throughput_vph": 900, "side_street_avg_queue_increase_meters": 5},
        "get_corridor_post_action_kpis",
        demand_kpi_settings=demand_kpis_alt_st_only_gw,
        overall_congestion_level_action="LOW"
    )
    current_sim_time_str_gw_demo = (datetime.fromisoformat(current_sim_time_str_gw_demo.replace("Z","+00:00")) + timedelta(seconds=gw_kpi_collection_delay_val_hist + 15 + 5*60)).isoformat() + "Z"
    logger.info(f"MAIN_EXAMPLE (GW Demo): Effectiveness Memory after History Building: {json.dumps(agent.action_effectiveness_memory, indent=2)}")

    logger.info("--- MAIN_EXAMPLE (GW Demo): Cycle Group 2 - Demonstrating Epsilon-Greedy GW Selection ---")
    agent.exploration_epsilon = 0.5
    logger.info(f"MAIN_EXAMPLE (GW Demo): Set exploration_epsilon to {agent.exploration_epsilon}")

    gw_candidate_ids_for_demo = ["main_st_ns_wave", "alt_st_ew_wave"]
    current_sim_time_str_gw_demo = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=1)).isoformat() + "Z" # Reset from general sim time

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
            None,
            "GREEN_WAVE_ACTIVATION",
            {"corridor_avg_travel_time_seconds": 140, "corridor_throughput_vph": 650, "side_street_avg_queue_increase_meters": 20},
            "get_corridor_post_action_kpis",
            demand_kpi_settings=demand_kpis_both_high_gw,
            overall_congestion_level_action="LOW"
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

    logger.info("--- MAIN_EXAMPLE: Starting Specific KPI-based Scoring Demonstrations ---")
    agent.action_effectiveness_memory = {}
    agent.action_performance_logs = []
    agent.pending_kpi_collection = []
    agent.exploration_epsilon = 0.0
    current_sim_time_str = "2023-01-03T10:00:00Z"

    logger.info("--- Demo: SET_SIGNAL_GREEN_CONGESTION - Scoring with Pre/Post KPIs ---")
    action_target_signal_id = "TS001"
    action_type_congestion = "SET_SIGNAL_GREEN_CONGESTION"

    traffic_mock._signals[action_target_signal_id].current_phase = SignalPhaseEnum.RED
    traffic_mock._signals[action_target_signal_id].operational_status = SignalOperationalStatusEnum.ONLINE
    for sid, sig_state in traffic_mock._signals.items():
        if sid != action_target_signal_id:
            sig_state.current_phase = SignalPhaseEnum.GREEN

    pre_action_kpis_for_ts001_congestion = {
        "current_flow_vph": 100,
        "queue_lengths_meters": {"N": 60, "S": 10},
        "typical_flow_vph": 90,
        "typical_queue_lengths_meters": {"N": 50, "S": 8}
    }
    analytics_mock.configure_pre_action_kpis(
        action_target_signal_id,
        agent.action_kpi_config[action_type_congestion]["pre_action_kpi_query_config"]["service_method_name"],
        pre_action_kpis_for_ts001_congestion
    )
    post_kpi_payload_congestion = {
        "local_congestion_level": "LOW",
        "flow_rate_absolute": 350,
        "cross_traffic_queue_lengths_meters": {"total": 25, "E": 10, "W": 15}
    }
    await run_action_and_kpi_cycles(
        action_time_str=current_sim_time_str,
        action_user_id="user_congestion_score_action",
        kpi_user_id="user_congestion_score_kpi",
        target_id_for_kpi_config=action_target_signal_id,
        action_type_for_kpi_config=action_type_congestion,
        post_kpi_payload=post_kpi_payload_congestion,
        kpi_service_method_name=agent.action_kpi_config[action_type_congestion]["service_method"],
        overall_congestion_level_action="HIGH"
    )
    congestion_action_log = next((log for log in agent.action_performance_logs if log.action_type == action_type_congestion and log.target_ids[0] == action_target_signal_id), None)
    assert congestion_action_log is not None, f"Action log for {action_type_congestion} on {action_target_signal_id} not found."
    logger.info(f"MAIN_EXAMPLE ({action_type_congestion}): Final ActionPerformanceLog: {congestion_action_log.model_dump_json(indent=2, default=str)}")
    assert congestion_action_log.pre_action_context_kpis.get("current_flow_vph") == 100, "Snapshot flow missing/wrong"
    assert congestion_action_log.pre_action_context_kpis.get("typical_flow_vph") == 90, "Baseline flow missing/wrong"
    assert congestion_action_log.post_action_kpis.get("local_congestion_level") == "LOW"
    assert congestion_action_log.post_action_kpis.get("cross_traffic_queue_lengths_meters", {}).get("total") == 25, "Cross traffic queue missing/wrong"
    assert congestion_action_log.effectiveness_metrics_used.get("pre_snapshot_flow_vph") == 100
    assert congestion_action_log.effectiveness_metrics_used.get("baseline_typical_flow_vph") == 90
    assert congestion_action_log.effectiveness_metrics_used.get("post_action_flow_rate_vph") == 350
    assert congestion_action_log.effectiveness_metrics_used.get("post_cross_traffic_queue_total_meters") == 25
    logger.info(f"MAIN_EXAMPLE ({action_type_congestion}): Score ({congestion_action_log.effectiveness_score}) now reflects baseline comparison and externality penalties.")
    current_sim_time_str = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")

    logger.info("--- Demo: GREEN_WAVE_ACTIVATION - Scoring with Pre/Post KPIs (including Baselines & Externalities) ---")
    action_target_corridor_id = "main_st_ns_wave"
    action_type_gw = "GREEN_WAVE_ACTIVATION"
    for sig_id_gw in GREEN_WAVE_CORRIDOR_CONFIGS[action_target_corridor_id]["signals_in_order"]:
        if sig_id_gw in traffic_mock._signals:
            traffic_mock._signals[sig_id_gw].current_phase = SignalPhaseEnum.RED
            traffic_mock._signals[sig_id_gw].operational_status = SignalOperationalStatusEnum.ONLINE
        else:
            traffic_mock._signals[sig_id_gw] = SignalState(signal_id=sig_id_gw, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, location=LocationModel(latitude=0,longitude=0,name=sig_id_gw), last_updated=datetime.utcnow())
    pre_action_kpis_for_gw = {
        "avg_travel_time_seconds": 190,
        "throughput_vph": 450,
        "corridor_baseline_avg_travel_time_seconds": 200,
        "corridor_baseline_throughput_vph": 400
    }
    analytics_mock.configure_pre_action_kpis(
        action_target_corridor_id,
        agent.action_kpi_config[action_type_gw]["pre_action_kpi_query_config"]["service_method_name"],
        pre_action_kpis_for_gw
    )
    post_kpi_payload_gw = {
        "corridor_avg_travel_time_seconds": 95,
        "corridor_throughput_vph": 880,
        "side_street_avg_queue_increase_meters": 40
    }
    demand_kpi_gw_demo = GREEN_WAVE_CORRIDOR_CONFIGS[action_target_corridor_id].get("demand_kpi_trigger")
    demand_kpi_settings_gw_demo = {demand_kpi_gw_demo: "HIGH"} if demand_kpi_gw_demo else {}
    await run_action_and_kpi_cycles(
        action_time_str=current_sim_time_str,
        action_user_id="user_gw_score_action",
        kpi_user_id="user_gw_score_kpi",
        target_id_for_kpi_config=action_target_corridor_id,
        action_type_for_kpi_config=action_type_gw,
        post_kpi_payload=post_kpi_payload_gw,
        kpi_service_method_name=agent.action_kpi_config[action_type_gw]["service_method"],
        demand_kpi_settings=demand_kpi_settings_gw_demo,
        overall_congestion_level_action="MEDIUM"
    )
    gw_action_log = next((log for log in agent.action_performance_logs if log.action_type == action_type_gw and log.target_ids[0] == action_target_corridor_id), None)
    assert gw_action_log is not None, f"Action log for {action_type_gw} on {action_target_corridor_id} not found."
    logger.info(f"MAIN_EXAMPLE ({action_type_gw}): Final ActionPerformanceLog: {gw_action_log.model_dump_json(indent=2, default=str)}")
    assert gw_action_log.pre_action_context_kpis.get("avg_travel_time_seconds") == 190, "Snapshot GW TT missing/wrong"
    assert gw_action_log.pre_action_context_kpis.get("corridor_baseline_avg_travel_time_seconds") == 200, "Baseline GW TT missing/wrong"
    assert gw_action_log.pre_action_context_kpis.get("throughput_vph") == 450, "Snapshot GW TP missing/wrong"
    assert gw_action_log.pre_action_context_kpis.get("corridor_baseline_throughput_vph") == 400, "Baseline GW TP missing/wrong"
    assert gw_action_log.post_action_kpis.get("corridor_avg_travel_time_seconds") == 95
    assert gw_action_log.post_action_kpis.get("side_street_avg_queue_increase_meters") == 40, "Side street queue missing/wrong"
    assert gw_action_log.effectiveness_metrics_used.get("pre_gw_avg_travel_time") == 190
    assert gw_action_log.effectiveness_metrics_used.get("baseline_gw_avg_travel_time") == 200
    assert gw_action_log.effectiveness_metrics_used.get("pre_gw_throughput") == 450
    assert gw_action_log.effectiveness_metrics_used.get("baseline_gw_throughput_vph") == 400
    assert gw_action_log.effectiveness_metrics_used.get("gw_post_avg_travel_time") == 95
    assert gw_action_log.effectiveness_metrics_used.get("gw_post_throughput") == 880
    assert gw_action_log.effectiveness_metrics_used.get("post_side_street_avg_queue_increase_meters") == 40
    logger.info(f"MAIN_EXAMPLE ({action_type_gw}): Score ({gw_action_log.effectiveness_score}) now reflects baseline comparison and externality penalties.")
    current_sim_time_str = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")

    logger.info("--- Demo: INCIDENT_RESPONSE_ACCIDENT - Scoring with Pre/Post KPIs (no new baselines/externalities in this example for this action) ---")
    action_type_incident = "INCIDENT_RESPONSE_ACCIDENT"
    incident_signal_target = "TS002"
    mock_incident_id = f"test_accident_for_{incident_signal_target}"
    if incident_signal_target not in traffic_mock._signals:
        traffic_mock._signals[incident_signal_target] = SignalState(signal_id=incident_signal_target, current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, location=LocationModel(latitude=1.01,longitude=1.0,name=incident_signal_target), last_updated=datetime.utcnow())
    mock_incident_location = traffic_mock._signals[incident_signal_target].location
    traffic_mock._signals[incident_signal_target].current_phase = SignalPhaseEnum.RED
    traffic_mock._signals[incident_signal_target].operational_status = SignalOperationalStatusEnum.ONLINE
    pre_kpi_incident_target_key = f"{mock_incident_location.latitude}_{mock_incident_location.longitude}"
    analytics_mock.configure_pre_action_kpis(
        target_id=pre_kpi_incident_target_key,
        service_method_name="get_incident_area_current_kpis", # This is what ACTION_KPI_CONFIG uses
        kpi_data={"avg_speed_kmh": 5, "vehicle_count": 80}
    )
    post_kpi_payload_incident = {"avg_speed_kmh_incident_zone": 25, "area_clearance_time_minutes": 12}
    system_kpis_for_incident_action = {"overall_congestion_level": "MEDIUM"}
    active_alerts_for_incident = [{
        "id": mock_incident_id, "type": "ACCIDENT",
        "location": mock_incident_location.model_dump(),
        "description": f"Mock accident near {incident_signal_target} for scoring demo",
        "severity": IncidentSeverityEnum.CRITICAL # Added severity to match IncidentReport potential field
    }]
    original_get_critical_alert_summary = analytics_mock.get_critical_alert_summary
    analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts": active_alerts_for_incident})
    logger.info(f"MAIN_EXAMPLE ({action_type_incident}): Running ACTION cycle at {current_sim_time_str}")
    await main_example_run_with_mock_time(
        current_sim_time_str, f"user_{action_type_incident}_action", agent, analytics_mock,
        kpis=system_kpis_for_incident_action
    )
    analytics_mock.get_critical_alert_summary = original_get_critical_alert_summary
    analytics_mock.configure_post_action_kpis(
        target_id=mock_incident_id,
        service_method_name=agent.action_kpi_config[action_type_incident]["service_method"], # "get_incident_response_post_action_kpis"
        kpi_data=post_kpi_payload_incident
    )
    kpi_collection_delay_incident = agent.action_kpi_config[action_type_incident]["delay_seconds"]
    kpi_collection_time_incident = datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(seconds=kpi_collection_delay_incident + 15)
    kpi_collection_time_str_incident = kpi_collection_time_incident.isoformat().replace("+00:00", "Z")
    logger.info(f"MAIN_EXAMPLE ({action_type_incident}): Running KPI PROCESSING cycle at {kpi_collection_time_str_incident}")
    await main_example_run_with_mock_time(
        kpi_collection_time_str_incident, f"user_{action_type_incident}_kpi", agent, analytics_mock,
        kpis={"overall_congestion_level": "LOW"}
    )
    incident_action_log = next((log for log in agent.action_performance_logs if log.action_type == action_type_incident and log.action_parameters.get("incident_id") == mock_incident_id), None)
    assert incident_action_log is not None, f"Action log for {action_type_incident} with incident_id {mock_incident_id} not found."
    logger.info(f"MAIN_EXAMPLE ({action_type_incident}): Final ActionPerformanceLog: {incident_action_log.model_dump_json(indent=2, default=str)}")
    assert incident_action_log.pre_action_context_kpis.get("avg_speed_kmh") == 5
    assert incident_action_log.post_action_kpis.get("area_clearance_time_minutes") == 12
    assert incident_action_log.effectiveness_metrics_used.get("pre_incident_avg_speed") == 5
    assert incident_action_log.effectiveness_metrics_used.get("post_incident_clearance_time_minutes") == 12
    assert incident_action_log.effectiveness_metrics_used.get("post_incident_avg_speed") == 25
    assert incident_action_log.effectiveness_score == pytest.approx(0.5)
    current_sim_time_str = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts":[]})

    logger.info("--- MAIN_EXAMPLE: Starting Epsilon-Greedy ACCIDENT Response Strategy Demonstration ---")
    agent.exploration_epsilon = 0.5
    agent.rng.seed(789)
    target_signal_id_incident_demo = "TS002"
    mock_incident_location_ts002 = traffic_mock._signals[target_signal_id_incident_demo].location
    mock_accident_alert_details_ts002 = {
        "id": "demo_acc_ts002", "type": "ACCIDENT",
        "location": mock_incident_location_ts002.model_dump(),
        "description": f"Mock accident near {target_signal_id_incident_demo} for strategy demo",
        "severity": IncidentSeverityEnum.CRITICAL
    }
    pre_kpi_incident_target_key_ts002 = f"{mock_incident_location_ts002.latitude}_{mock_incident_location_ts002.longitude}"
    logger.info("--- MAIN_EXAMPLE (Accident Strategy Demo): Cycle Group 1 - Building Strategy History ---")
    original_epsilon_hist_build = agent.exploration_epsilon
    agent.exploration_epsilon = 1.0
    strategies_to_build_history_for = [
        (STRATEGY_ACCIDENT_EXTEND_GREEN_LONG, {"area_clearance_time_minutes": 10, "avg_speed_kmh_incident_zone": 35}, 0.8),
        (STRATEGY_ACCIDENT_EXTEND_GREEN_MODERATE, {"area_clearance_time_minutes": 25, "avg_speed_kmh_incident_zone": 25}, 0.2),
        (STRATEGY_ACCIDENT_PULSE_GREEN, {"area_clearance_time_minutes": 50, "avg_speed_kmh_incident_zone": 15}, -0.5)
    ]
    for i, (strategy_name, post_kpi_payload, expected_score_approx) in enumerate(strategies_to_build_history_for):
        logger.info(f"--- MAIN_EXAMPLE (Accident Strategy Demo): History Build for {strategy_name} ---")
        current_sim_time_str = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=30*i)).isoformat().replace("+00:00", "Z")
        analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts": [mock_accident_alert_details_ts002]})
        traffic_mock._signals[target_signal_id_incident_demo].current_phase = SignalPhaseEnum.RED
        traffic_mock._signals[target_signal_id_incident_demo].operational_status = SignalOperationalStatusEnum.ONLINE
        agent._recent_signal_actions.clear()
        analytics_mock.configure_pre_action_kpis(
            pre_kpi_incident_target_key_ts002, # This key is for the location
            agent.action_kpi_config["INCIDENT_RESPONSE_ACCIDENT"]["pre_action_kpi_query_config"]["service_method_name"], # "get_incident_area_current_kpis"
            {"avg_speed_kmh": 10, "vehicle_count": 60}
        )
        forced_choice_candidate = {'name': strategy_name, 'avg_score': 0.0}
        with patch.object(agent.rng, 'choice', return_value=forced_choice_candidate):
            await main_example_run_with_mock_time(
                current_sim_time_str, f"user_acc_hist_{i}_action", agent, analytics_mock,
                kpis={"overall_congestion_level": "MEDIUM"}
            )
        analytics_mock.configure_post_action_kpis(
            mock_accident_alert_details_ts002["id"],
            agent.action_kpi_config["INCIDENT_RESPONSE_ACCIDENT"]["service_method"], # "get_incident_response_post_action_kpis"
            post_kpi_payload
        )
        kpi_delay = agent.action_kpi_config["INCIDENT_RESPONSE_ACCIDENT"]["delay_seconds"]
        kpi_time = datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(seconds=kpi_delay + 20)
        await main_example_run_with_mock_time(
            kpi_time.isoformat().replace("+00:00", "Z"), f"user_acc_hist_{i}_kpi", agent, analytics_mock,
            kpis={"overall_congestion_level": "LOW"}
        )
        logger.info(f"MAIN_EXAMPLE (Accident Strategy Demo): Memory after {strategy_name}: {json.dumps(agent.action_effectiveness_memory, indent=2)}")
    agent.exploration_epsilon = original_epsilon_hist_build
    logger.info(f"MAIN_EXAMPLE (Accident Strategy Demo): Effectiveness Memory after History Building: {json.dumps(agent.action_effectiveness_memory, indent=2)}")
    logger.info("--- MAIN_EXAMPLE (Accident Strategy Demo): Cycle Group 2 - Demonstrating Epsilon-Greedy Selection ---")
    agent.exploration_epsilon = 0.5
    for i in range(6):
        cycle_num_acc_demo = i + 1
        current_sim_time_str = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
        logger.info(f"--- MAIN_EXAMPLE (Accident Strategy Demo): Epsilon-Greedy Cycle {cycle_num_acc_demo} ---")
        analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts": [mock_accident_alert_details_ts002]})
        traffic_mock._signals[target_signal_id_incident_demo].current_phase = SignalPhaseEnum.RED
        traffic_mock._signals[target_signal_id_incident_demo].operational_status = SignalOperationalStatusEnum.ONLINE
        if target_signal_id_incident_demo in agent._recent_signal_actions:
            del agent._recent_signal_actions[target_signal_id_incident_demo]
        logger.info(f"MAIN_EXAMPLE (Accident Strategy Demo): Scores before cycle {cycle_num_acc_demo}:")
        for strat_name in ALL_ACCIDENT_STRATEGIES:
            mem_key = f"INCIDENT_RESPONSE_ACCIDENT:{target_signal_id_incident_demo}:{strat_name}"
            scores = agent.action_effectiveness_memory.get(mem_key, [])
            avg_s = sum(scores)/len(scores) if scores else 0.0
            logger.info(f"  Strategy {strat_name}: Avg Score = {avg_s:.2f} (History: {scores})")
        await main_example_run_with_mock_time(
            current_sim_time_str, f"user_acc_egreedy_{cycle_num_acc_demo}_action", agent, analytics_mock,
            kpis={"overall_congestion_level": "MEDIUM"}
        )
        chosen_strategy_for_kpi = "UNKNOWN"
        if agent.pending_kpi_collection:
            last_pending_item = agent.pending_kpi_collection[-1]
            if last_pending_item['action_type'] == "INCIDENT_RESPONSE_ACCIDENT":
                chosen_strategy_for_kpi = last_pending_item['action_parameters'].get('strategy_applied', "ERROR_NO_STRATEGY")
                selection_method_log = last_pending_item['action_parameters'].get('selection_method', "ERROR_NO_METHOD")
                chosen_score_log = last_pending_item['pre_action_context_kpis'].get('chosen_strategy_avg_score', 'N/A')
                logger.info(f"MAIN_EXAMPLE (Accident Strategy Demo): Cycle {cycle_num_acc_demo} chose '{chosen_strategy_for_kpi}' via '{selection_method_log}' (score: {chosen_score_log}).")
        analytics_mock.configure_post_action_kpis(
            mock_accident_alert_details_ts002["id"],
            agent.action_kpi_config["INCIDENT_RESPONSE_ACCIDENT"]["service_method"],
            {"area_clearance_time_minutes": 28, "avg_speed_kmh_incident_zone": 22}
        )
        kpi_delay = agent.action_kpi_config["INCIDENT_RESPONSE_ACCIDENT"]["delay_seconds"]
        kpi_time = datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(seconds=kpi_delay + 20)
        await main_example_run_with_mock_time(
            kpi_time.isoformat().replace("+00:00", "Z"), f"user_acc_egreedy_{cycle_num_acc_demo}_kpi", agent, analytics_mock,
            kpis={"overall_congestion_level": "LOW"}
        )
        logger.info(f"MAIN_EXAMPLE (Accident Strategy Demo): Memory after E-Greedy Cycle {cycle_num_acc_demo}: {json.dumps(agent.action_effectiveness_memory, indent=2)}")
    agent.exploration_epsilon = original_epsilon
    agent.rng.setstate(original_rng_state)
    analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts":[]})
    logger.info("--- MAIN_EXAMPLE: Epsilon-Greedy ACCIDENT Response Strategy Demonstration Completed ---")

    # --- Demonstration of Directional ROAD_CLOSURE Logic ---
    logger.info("--- MAIN_EXAMPLE: Starting Directional ROAD_CLOSURE Logic Demonstration ---")
    agent.action_performance_logs = []
    agent.pending_kpi_collection = []
    agent.exploration_epsilon = 0.0

    def reset_signals_for_closure_demo(initial_phases: Dict[str, SignalPhaseEnum]):
        for sid, sig_state_obj in traffic_mock._signals.items():
            sig_state_obj.current_phase = initial_phases.get(sid, SignalPhaseEnum.GREEN)
            sig_state_obj.operational_status = SignalOperationalStatusEnum.ONLINE
        agent._recent_signal_actions.clear()
        logger.debug(f"MAIN_EXAMPLE (Road Closure Demo): Reset signals. Initial phases: {initial_phases}")

    closure_sim_time_base = "2023-01-04T10:00:00Z"

    logger.info("--- Road Closure Demo: Scenario 1 - Northbound Closure near TS002 ---")
    current_sim_time_str = closure_sim_time_base
    reset_signals_for_closure_demo({
        "TS001": SignalPhaseEnum.GREEN,
        "TS002": SignalPhaseEnum.GREEN,
        "TS003": SignalPhaseEnum.GREEN,
        "TS004": SignalPhaseEnum.GREEN,
        "TS005": SignalPhaseEnum.GREEN
    })

    closure_loc_ts002 = traffic_mock._signals["TS002"].location
    mock_closure_alert_nb = {
        "id": "closure_nb_ts002", "type": "ROAD_CLOSURE",
        "location": closure_loc_ts002.model_dump(),
        "details": {"direction_affected": "N", "description": "Northbound closure on Main St at Center"},
        "severity": IncidentSeverityEnum.CRITICAL
    }
    analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts": [mock_closure_alert_nb]})

    await main_example_run_with_mock_time(
        current_sim_time_str, "user_closure_nb_action", agent, analytics_mock,
        kpis={"overall_congestion_level": "LOW"}
    )

    assert traffic_mock._signals["TS004"].current_phase == SignalPhaseEnum.RED, "TS004 (South, N-Flow) should be RED for NB closure"
    assert traffic_mock._signals["TS001"].current_phase == SignalPhaseEnum.GREEN, "TS001 (North, S-Flow) should remain GREEN for NB closure"
    assert traffic_mock._signals["TS003"].current_phase == SignalPhaseEnum.GREEN, "TS003 (West, E-Flow) should remain GREEN for NB closure"
    logger.info("Road Closure Demo: Scenario 1 (NB Closure) assertions passed for TS004, TS001, TS003.")

    logger.info("--- Road Closure Demo: Scenario 2 - Eastbound Closure near TS002 ---")
    current_sim_time_str = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    reset_signals_for_closure_demo({
        "TS001": SignalPhaseEnum.GREEN,
        "TS003": SignalPhaseEnum.GREEN,
        "TS005": SignalPhaseEnum.GREEN
    })
    mock_closure_alert_eb = {
        "id": "closure_eb_ts002", "type": "ROAD_CLOSURE",
        "location": closure_loc_ts002.model_dump(),
        "details": {"direction_affected": "E", "description": "Eastbound closure on Cross St at Center"},
        "severity": IncidentSeverityEnum.CRITICAL
    }
    analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts": [mock_closure_alert_eb]})
    await main_example_run_with_mock_time(
        current_sim_time_str, "user_closure_eb_action", agent, analytics_mock,
        kpis={"overall_congestion_level": "LOW"}
    )
    assert traffic_mock._signals["TS003"].current_phase == SignalPhaseEnum.RED, "TS003 (West, E-Flow) should be RED for EB closure"
    assert traffic_mock._signals["TS005"].current_phase == SignalPhaseEnum.GREEN, "TS005 (East, W-Flow) should remain GREEN for EB closure"
    logger.info("Road Closure Demo: Scenario 2 (EB Closure) assertions passed for TS003, TS005.")

    logger.info("--- Road Closure Demo: Scenario 3 - ALL Directions Closure near TS002 ---")
    current_sim_time_str = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    reset_signals_for_closure_demo({
        "TS001": SignalPhaseEnum.GREEN,
        "TS003": SignalPhaseEnum.GREEN,
        "TS004": SignalPhaseEnum.GREEN,
        "TS005": SignalPhaseEnum.GREEN
    })
    mock_closure_alert_all = {
        "id": "closure_all_ts002", "type": "ROAD_CLOSURE",
        "location": closure_loc_ts002.model_dump(),
        "details": {"direction_affected": "ALL", "description": "Full intersection closure at Center"},
        "severity": IncidentSeverityEnum.CRITICAL
    }
    analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts": [mock_closure_alert_all]})
    await main_example_run_with_mock_time(
        current_sim_time_str, "user_closure_all_action", agent, analytics_mock,
        kpis={"overall_congestion_level": "LOW"}
    )
    assert traffic_mock._signals["TS001"].current_phase == SignalPhaseEnum.RED, "TS001 (N, S-flow) should be RED for ALL closure"
    assert traffic_mock._signals["TS003"].current_phase == SignalPhaseEnum.RED, "TS003 (W, E-flow) should be RED for ALL closure"
    assert traffic_mock._signals["TS004"].current_phase == SignalPhaseEnum.RED, "TS004 (S, N-flow) should be RED for ALL closure"
    assert traffic_mock._signals["TS005"].current_phase == SignalPhaseEnum.RED, "TS005 (E, W-flow) should be RED for ALL closure"
    logger.info("Road Closure Demo: Scenario 3 (ALL Closure) assertions passed.")

    closure_log_nb = next((log for log in agent.action_performance_logs if log.action_parameters.get("incident_id") == "closure_nb_ts002" and log.target_ids[0] == "TS004"), None)
    assert closure_log_nb is not None, "Log for NB closure action on TS004 not found"
    if closure_log_nb:
        assert closure_log_nb.action_parameters.get("closure_direction_affected") == "N"
        assert closure_log_nb.action_parameters.get("signal_main_flow_direction") == "N"
        assert "closure_location" in closure_log_nb.pre_action_context_kpis
        logger.info("Road Closure Demo: Log parameters verified for NB closure action.")

    analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts":[]})
    logger.info("--- MAIN_EXAMPLE: Directional ROAD_CLOSURE Logic Demonstration Completed ---")

    # --- Demonstration of DMS Activation Logic ---
    logger.info("--- MAIN_EXAMPLE: Starting DMS Activation Logic Demonstration ---")
    agent.action_performance_logs = [] # Clear logs
    agent.pending_kpi_collection = [] # Clear pending KPIs

    # Ensure DMS mock states are pristine for these tests
    dms_mock._dms_states["DMS_UPSTREAM_NB_01"].current_messages = []
    dms_mock._dms_states["DMS_UPSTREAM_EB_01"].current_messages = []
    dms_mock._dms_states["DMS_DOWNSTREAM_SB_01"].current_messages = []
    dms_mock._dms_states["DMS_OFFLINE_01"].current_messages = []
    dms_mock._dms_states["DMS_OFFLINE_01"].operational_status = DmsStatusEnum.OFFLINE # Ensure it's offline for test

    dms_sim_time_base = "2023-01-05T10:00:00Z"
    closure_loc_for_dms_test = traffic_mock._signals["TS002"].location # Closure near center

    # Scenario 1: Road Closure activating upstream DMS for Northbound traffic
    logger.info("--- DMS Demo: Scenario 1 - Road Closure NB, Activate Upstream DMS ---")
    current_sim_time_str = dms_sim_time_base
    # Reset signals to a neutral state (e.g., all green or a mix, doesn't strictly matter for DMS test focus)
    reset_signals_for_closure_demo({"TS001": SignalPhaseEnum.GREEN, "TS004": SignalPhaseEnum.GREEN})

    mock_dms_closure_alert_nb = {
        "id": "dms_closure_nb_01", "type": "ROAD_CLOSURE",
        "location": closure_loc_for_dms_test.model_dump(),
        "details": {"direction_affected": "N", "description": "NB lane closed at Main/Center"},
        "severity": IncidentSeverityEnum.CRITICAL.value # Ensure severity is a string value for the dict
    }
    analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts": [mock_dms_closure_alert_nb]})

    # Mock the set_dms_message to track calls
    dms_mock.set_dms_message = AsyncMock(wraps=dms_mock.set_dms_message) # Wrap to retain original logic but allow call tracking

    await main_example_run_with_mock_time(
        current_sim_time_str, "user_dms_closure_nb", agent, analytics_mock,
        kpis={"overall_congestion_level": "LOW"}
    )

    # Assertions for DMS_UPSTREAM_NB_01 (ID: DMS_UPSTREAM_NB_01, loc: 34.035, -118.24, viewable N)
    # Closure is at 34.05, -118.24, affecting N. DMS is South, viewable N. Should be activated.
    dms_upstream_nb_state = dms_mock._dms_states["DMS_UPSTREAM_NB_01"]
    assert dms_upstream_nb_state.current_messages is not None and len(dms_upstream_nb_state.current_messages) > 0, \
        "DMS_UPSTREAM_NB_01 should have messages for NB closure."
    if dms_upstream_nb_state.current_messages:
        logger.info(f"DMS_UPSTREAM_NB_01 message: {dms_upstream_nb_state.current_messages[0].text}")
        assert "ROAD CLOSED" in dms_upstream_nb_state.current_messages[0].text
        assert "NBOUND" in dms_upstream_nb_state.current_messages[0].text # Check if direction is in message

    # Assert other DMS were not activated or have old messages
    assert not dms_mock._dms_states["DMS_UPSTREAM_EB_01"].current_messages, "DMS_UPSTREAM_EB_01 should not be activated for NB closure."
    assert not dms_mock._dms_states["DMS_DOWNSTREAM_SB_01"].current_messages, "DMS_DOWNSTREAM_SB_01 should not be activated for NB closure."

    # Check if SET_DMS_MESSAGE action was logged
    dms_action_log = next((log for log in agent.action_performance_logs if log.action_type == "SET_DMS_MESSAGE" and log.target_ids[0] == "DMS_UPSTREAM_NB_01"), None)
    assert dms_action_log is not None, "SET_DMS_MESSAGE action for DMS_UPSTREAM_NB_01 not found in logs."
    if dms_action_log:
        assert dms_action_log.action_parameters.get("incident_id") == "dms_closure_nb_01"
        assert len(dms_action_log.action_parameters.get("messages", [])) > 0
    logger.info("DMS Demo: Scenario 1 (NB Road Closure) assertions passed.")
    dms_mock.set_dms_message.reset_mock() # Reset call counts for next test

    # Scenario 2: High-Severity Accident activating DMS
    logger.info("--- DMS Demo: Scenario 2 - Critical Accident, Activate Nearby DMS ---")
    current_sim_time_str = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    dms_mock._dms_states["DMS_UPSTREAM_EB_01"].current_messages = [] # Reset its messages

    # Accident location near DMS_UPSTREAM_EB_01 (loc: 34.05, -118.255)
    accident_loc_dms_eb = LocationModel(latitude=34.0505, longitude=-118.2545, name="Near DMS EB")
    mock_dms_accident_alert = {
        "id": "dms_acc_crit_01", "type": "ACCIDENT",
        "location": accident_loc_dms_eb.model_dump(),
        "details": {"description": "Major accident on Cross St."},
        "severity": IncidentSeverityEnum.CRITICAL.value
    }
    analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts": [mock_dms_accident_alert]})
    dms_mock.set_dms_message = AsyncMock(wraps=dms_mock.set_dms_message)

    await main_example_run_with_mock_time(
        current_sim_time_str, "user_dms_accident", agent, analytics_mock,
        kpis={"overall_congestion_level": "MEDIUM"}
    )

    dms_upstream_eb_state = dms_mock._dms_states["DMS_UPSTREAM_EB_01"]
    assert dms_upstream_eb_state.current_messages is not None and len(dms_upstream_eb_state.current_messages) > 0, \
        "DMS_UPSTREAM_EB_01 should have messages for critical accident."
    if dms_upstream_eb_state.current_messages:
        logger.info(f"DMS_UPSTREAM_EB_01 message: {dms_upstream_eb_state.current_messages[0].text}")
        assert "ACCIDENT" in dms_upstream_eb_state.current_messages[0].text

    dms_action_log_acc = next((log for log in agent.action_performance_logs if log.action_type == "SET_DMS_MESSAGE" and log.target_ids[0] == "DMS_UPSTREAM_EB_01"), None)
    assert dms_action_log_acc is not None, "SET_DMS_MESSAGE action for DMS_UPSTREAM_EB_01 (accident) not found."
    logger.info("DMS Demo: Scenario 2 (Critical Accident) assertions passed.")
    dms_mock.set_dms_message.reset_mock()

    # Scenario 3: Offline DMS is not activated
    logger.info("--- DMS Demo: Scenario 3 - Offline DMS Not Activated ---")
    current_sim_time_str = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")

    offline_dms_location = dms_mock._dms_states["DMS_OFFLINE_01"].location
    mock_dms_offline_test_alert = {
        "id": "dms_offline_test_01", "type": "ROAD_CLOSURE",
        "location": offline_dms_location.model_dump(),
        "details": {"direction_affected": "E", "description": "Closure near offline DMS"},
        "severity": IncidentSeverityEnum.CRITICAL.value
    }
    analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts": [mock_dms_offline_test_alert]})
    dms_mock.set_dms_message = AsyncMock(wraps=dms_mock.set_dms_message)

    await main_example_run_with_mock_time(
        current_sim_time_str, "user_dms_offline_test", agent, analytics_mock,
        kpis={"overall_congestion_level": "LOW"}
    )

    dms_mock.set_dms_message.assert_not_called() # Check that set_dms_message was not called for the offline DMS or any DMS if it's the only one in range
    # More specific check: ensure the offline DMS state did not change (if it was the only one in range)
    assert not dms_mock._dms_states["DMS_OFFLINE_01"].current_messages, "Offline DMS should not have messages."
    logger.info("DMS Demo: Scenario 3 (Offline DMS) assertions passed.")

    analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts":[]}) # Reset alerts
    logger.info("--- MAIN_EXAMPLE: DMS Activation Logic Demonstration Completed ---")

    # --- Demonstration of Complex Plan Execution Logic ---
    logger.info("--- MAIN_EXAMPLE: Starting Complex Plan Execution Demonstration ---")
    agent.action_performance_logs = []
    agent.pending_kpi_collection = []
    agent.active_plan = None # Ensure no active plan initially
    agent.current_plan_step_index = -1
    agent.active_plan_id = None

    # Ensure relevant mock devices exist and are in a known state for the plan
    # Plan uses: TS004, DMS_UPSTREAM_NB_01, DMS_UPSTREAM_EB_01
    if "TS004" in traffic_mock._signals:
        traffic_mock._signals["TS004"].current_phase = SignalPhaseEnum.GREEN # Start it green to see it change
        traffic_mock._signals["TS004"].operational_status = SignalOperationalStatusEnum.ONLINE
    if "DMS_UPSTREAM_NB_01" in dms_mock._dms_states:
        dms_mock._dms_states["DMS_UPSTREAM_NB_01"].current_messages = []
        dms_mock._dms_states["DMS_UPSTREAM_NB_01"].operational_status = DmsStatusEnum.ONLINE
    if "DMS_UPSTREAM_EB_01" in dms_mock._dms_states:
        dms_mock._dms_states["DMS_UPSTREAM_EB_01"].current_messages = []
        dms_mock._dms_states["DMS_UPSTREAM_EB_01"].operational_status = DmsStatusEnum.ONLINE

    plan_test_sim_time_base = "2023-01-06T10:00:00Z"
    current_sim_time_str = plan_test_sim_time_base

    # Create the complex incident alert
    complex_incident_loc = LocationModel(latitude=34.05, longitude=-118.24, name="HWY 101 Main Segment") # Matches TS002 area
    mock_complex_alert = {
        "id": "complex_closure_001", "type": "ROAD_CLOSURE", # Type must be ROAD_CLOSURE
        "location": complex_incident_loc.model_dump(),
        "details": {"lanes_affected": "ALL", "description": "Major pileup, all lanes blocked NB on HWY 101"},
        "severity": IncidentSeverityEnum.CRITICAL.value
    }
    analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts": [mock_complex_alert]})

    # Cycle 1: Plan Activation and Step 1 Execution
    logger.info("--- Complex Plan Demo: Cycle 1 - Plan Activation & Step 1 ---")
    await main_example_run_with_mock_time(
        current_sim_time_str, "user_plan_cycle1", agent, analytics_mock,
        kpis={"overall_congestion_level": "HIGH"} # System state
    )
    assert agent.active_plan is not None, "Complex plan should be active."
    assert agent.active_plan_id == f"HWY_CLOSURE_{mock_complex_alert['id']}", "Active plan ID is incorrect."
    assert agent.current_plan_step_index == 1, "Should have processed step 0 and moved to index 1 (for next cycle's step)."
    assert agent.active_plan[0].status == PlanStepStatus.COMPLETED, "Plan Step 1 should be marked COMPLETED."
    assert traffic_mock._signals["TS004"].current_phase == SignalPhaseEnum.RED, "TS004 should be RED by plan."
    assert dms_mock._dms_states["DMS_UPSTREAM_NB_01"].current_messages is not None and \
           len(dms_mock._dms_states["DMS_UPSTREAM_NB_01"].current_messages) == 4, "DMS_UPSTREAM_NB_01 should have messages."
    if dms_mock._dms_states["DMS_UPSTREAM_NB_01"].current_messages: # Check content of first page
        assert "HWY CLOSED" in dms_mock._dms_states["DMS_UPSTREAM_NB_01"].current_messages[0].text
    logger.info("Complex Plan Demo: Cycle 1 assertions passed.")

    # Cycle 2: Step 2 Execution
    current_sim_time_str = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    logger.info("--- Complex Plan Demo: Cycle 2 - Step 2 ---")
    # No new alerts, plan should continue
    analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts": [mock_complex_alert]}) # Keep alert active
    await main_example_run_with_mock_time(
        current_sim_time_str, "user_plan_cycle2", agent, analytics_mock,
        kpis={"overall_congestion_level": "HIGH"}
    )
    assert agent.active_plan is not None, "Plan should still be active."
    assert agent.current_plan_step_index == 2, "Should have processed step 1 and moved to index 2."
    assert agent.active_plan[1].status == PlanStepStatus.COMPLETED, "Plan Step 2 should be marked COMPLETED."
    assert dms_mock._dms_states["DMS_UPSTREAM_EB_01"].current_messages is not None and \
           len(dms_mock._dms_states["DMS_UPSTREAM_EB_01"].current_messages) == 3, "DMS_UPSTREAM_EB_01 should have messages from plan step 2."
    if dms_mock._dms_states["DMS_UPSTREAM_EB_01"].current_messages: # Check content of first page
        # Example check, adjust if message format from _get_hardcoded_hwy_closure_plan changes
        assert "HWY CLSD" in dms_mock._dms_states["DMS_UPSTREAM_EB_01"].current_messages[0].text
    logger.info("Complex Plan Demo: Cycle 2 assertions passed.")

    # Cycle 3: Step 3 (HWY_CLOSURE_S3_MONITOR) Activation. This step has a TIME_ELAPSED condition.
    monitor_step_sim_activation_time_str = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=1)).isoformat().replace("+00:00", "Z") # e.g., 10:02:00Z
    logger.info(f"--- Complex Plan Demo: Cycle 3 - Step 3 (Monitor) Activation at {monitor_step_sim_activation_time_str} ---")
    analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts": [mock_complex_alert]}) # Keep alert active
    await main_example_run_with_mock_time(
        monitor_step_sim_activation_time_str, "user_plan_cycle3_monitor_activation", agent, analytics_mock,
        kpis={"overall_congestion_level": "HIGH"}
    )
    assert agent.active_plan is not None, "Plan should still be active."
    assert agent.current_plan_step_index == 2, "Should be at index 2, processing HWY_CLOSURE_S3_MONITOR."
    assert agent.active_plan[2].status == PlanStepStatus.ACTIVE, "Plan Step 3 (HWY_CLOSURE_S3_MONITOR) should be ACTIVE."
    assert agent.active_plan[2].step_activation_time is not None, "Step 3 activation time should be recorded."
    step3_actual_activation_time = agent.active_plan[2].step_activation_time
    monitor_duration_seconds = agent.active_plan[2].completion_conditions[0].time_elapsed_seconds
    assert monitor_duration_seconds == 120, "Monitor step duration not as expected."
    logger.info(f"Complex Plan Demo: Step 3 ('{agent.active_plan[2].step_id}') is ACTIVE. Activation: {step3_actual_activation_time.isoformat()}, Duration: {monitor_duration_seconds}s.")

    # Cycle 3.1: Simulate time passing, but not enough for the monitor step's duration to complete.
    time_after_partial_wait_str = (step3_actual_activation_time + timedelta(seconds=monitor_duration_seconds - 60)).isoformat().replace("+00:00", "Z") # e.g., 10:02:00Z + 60s = 10:03:00Z
    logger.info(f"--- Complex Plan Demo: Cycle 3.1 - Step 3 (Monitor) still active after partial wait at {time_after_partial_wait_str} ---")
    await main_example_run_with_mock_time(
        time_after_partial_wait_str, "user_plan_cycle3_monitor_waiting", agent, analytics_mock,
        kpis={"overall_congestion_level": "HIGH"}
    )
    assert agent.active_plan is not None, "Plan should still be active."
    assert agent.current_plan_step_index == 2, "Still on index 2 (HWY_CLOSURE_S3_MONITOR)."
    assert agent.active_plan[2].status == PlanStepStatus.ACTIVE, "Plan Step 3 (HWY_CLOSURE_S3_MONITOR) should still be ACTIVE."
    logger.info("Complex Plan Demo: Cycle 3.1 assertions passed (monitor step still active).")

    # Cycle 3.2: Simulate enough time for the monitor step's TIME_ELAPSED duration to complete.
    time_after_full_wait_str = (step3_actual_activation_time + timedelta(seconds=monitor_duration_seconds + 5)).isoformat().replace("+00:00", "Z") # e.g., 10:02:00Z + 125s = 10:04:05Z
    logger.info(f"--- Complex Plan Demo: Cycle 3.2 - Step 3 (Monitor) completion after full wait at {time_after_full_wait_str} ---")
    await main_example_run_with_mock_time(
        time_after_full_wait_str, "user_plan_cycle3_monitor_completion", agent, analytics_mock,
        kpis={"overall_congestion_level": "HIGH"}
    )
    assert agent.active_plan is not None, "Plan should ideally still be active as it transitions step, or None if it was the last step."
    # If HWY_CLOSURE_S3_MONITOR was the last step with conditions, plan might complete.
    # The plan has 3 steps. HWY_CLOSURE_S3_MONITOR is index 2.
    # After it completes, current_plan_step_index should become 3.
    assert agent.current_plan_step_index == 3, f"Should have advanced past step 2. Current index: {agent.current_plan_step_index}"
    assert agent.active_plan[2].status == PlanStepStatus.COMPLETED, "Plan Step 3 (HWY_CLOSURE_S3_MONITOR) should now be COMPLETED."
    logger.info("Complex Plan Demo: Cycle 3.2 assertions passed (monitor step completed, index advanced).")

    current_sim_time_str = time_after_full_wait_str # Update current_sim_time_str for next cycle

    # Cycle 4: Plan should complete processing as all steps are done.
    # Since current_plan_step_index is 3, and len(active_plan) is 3, _process_active_plan_step will call _complete_active_plan.
    current_sim_time_str = (datetime.fromisoformat(current_sim_time_str.replace("Z","+00:00")) + timedelta(minutes=1)).isoformat().replace("+00:00", "Z") # e.g., 10:05:05Z
    logger.info(f"--- Complex Plan Demo: Cycle 4 - Plan Completion Check at {current_sim_time_str} ---")
    analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts": [mock_complex_alert]}) # Incident might still be active
    await main_example_run_with_mock_time(
        current_sim_time_str, "user_plan_cycle4", agent, analytics_mock,
        kpis={"overall_congestion_level": "MEDIUM"} # Congestion might be reducing
    )
    assert agent.active_plan is None, "Plan should be completed and cleared."
    assert agent.active_plan_id is None, "Active plan ID should be cleared."
    assert agent.current_plan_step_index == -1, "Plan step index should be reset."
    logger.info("Complex Plan Demo: Cycle 4 assertions (plan completion) passed.")

    analytics_mock.get_critical_alert_summary = AsyncMock(return_value={"active_alerts":[]}) # Reset alerts
    logger.info("--- MAIN_EXAMPLE: Complex Plan Execution Demonstration Completed ---")

    if os.path.exists(EFFECTIVENESS_MEMORY_FILEPATH): os.remove(EFFECTIVENESS_MEMORY_FILEPATH)
    logger.info("--- AgentCore main_example for All Scoring Demonstrations completed ---")

if __name__ == "__main__":
    asyncio.run(main_example())
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
                    days_of_week=[0, 1, 2, 3, 4],
                    time_of_day="08:30",
                    frequency=5
                )
            ]
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
                    current_phase=SignalPhaseEnum.OFF,
                    operational_status=SignalOperationalStatusEnum.OFFLINE,
                    last_updated=datetime.utcnow()
                ),
            }
            self._cycle_count = 0
            logger_subtask.debug(f"MockTrafficSignalService initialized with {len(self._signals)} signals.")

        async def get_all_signal_states(self) -> List[SignalState]:
            self._cycle_count += 1
            logger_subtask.debug(f"MockTrafficSignalService.get_all_signal_states called (cycle {self._cycle_count}).")
            if self._cycle_count == 2:
                logger_subtask.info("MockTrafficSignalService: Cycle 2, attempting to change a signal to GREEN.")
                for signal_id in self._signals:
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

    mock_analytics_service = MockAnalyticsService()
    mock_prediction_scheduler = MockPredictionScheduler()
    mock_personalized_routing_service = MockPersonalizedRoutingService()
    mock_connection_manager = MockConnectionManager()
    mock_traffic_signal_service = MockTrafficSignalService(config={}, connection_manager=mock_connection_manager)
    # Add a basic MockDmsService for main_example_subtask if it needs AgentCore
    mock_dms_service_subtask = MockDmsService()


    agent_core = AgentCore(
        prediction_scheduler=mock_prediction_scheduler,
        personalized_routing_service=mock_personalized_routing_service,
        analytics_service=mock_analytics_service,
        traffic_signal_service=mock_traffic_signal_service,
        dms_service=mock_dms_service_subtask # Pass DmsService mock
    )

    logger_subtask.info("--- Running main_example_subtask: decision cycle 1 ---")
    await agent_core.run_decision_cycle(sample_user_id="cycle_1_subtask_user")

    logger_subtask.info("--- Running main_example_subtask: decision cycle 2 ---")
    await agent_core.run_decision_cycle(sample_user_id="cycle_2_subtask_user")

    logger_subtask.info("--- main_example_subtask completed ---")

# To run this specific example if needed (though subtask says keep __main__ commented):
# if __name__ == "__main__":
#     asyncio.run(main_example_subtask())
