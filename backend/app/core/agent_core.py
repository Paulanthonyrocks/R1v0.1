import asyncio
import logging
from typing import Optional, Dict, Any, List, Set
import json
from datetime import datetime, timedelta
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

class AgentCore:
    SIGNAL_ACTION_COOLDOWN_SECONDS = 120  # General cooldown
    INCIDENT_SIGNAL_COOLDOWN_SECONDS = 300 # Cooldown after an incident-specific action

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
        # self.SIGNAL_ACTION_COOLDOWN_SECONDS attribute is already defined at class level
        self.logger = logger
        logger.info("AgentCore initialized with PredictionScheduler, PersonalizedRoutingService, AnalyticsService, and TrafficSignalService.")

    def _calculate_haversine_distance(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 6371000
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        a = math.sin(delta_phi / 2.0)**2 + \
            math.cos(phi1) * math.cos(phi2) * \
            math.sin(delta_lambda / 2.0)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        distance = R * c
        return distance

    async def _find_signals_near_location(
        self,
        incident_location: LocationModel,
        all_signals: List[SignalState],
        radius_meters: int
    ) -> List[SignalState]:
        nearby_signals = []
        if not incident_location:
            self.logger.warning("Incident location is None, cannot find nearby signals.")
            return nearby_signals
        for signal in all_signals:
            if signal.location and isinstance(signal.location.latitude, (float, int)) and isinstance(signal.location.longitude, (float, int)):
                distance = self._calculate_haversine_distance(
                    incident_location.latitude, incident_location.longitude,
                    signal.location.latitude, signal.location.longitude
                )
                if distance <= radius_meters:
                    nearby_signals.append(signal)
            else:
                self.logger.debug(f"Signal {signal.signal_id} has no valid location data, skipping distance calculation.")
        self.logger.info(f"Found {len(nearby_signals)} signals within {radius_meters}m of incident at ({incident_location.latitude}, {incident_location.longitude}).")
        return nearby_signals

    async def _determine_next_travel_prediction_time(self, pattern: CommonTravelPattern, current_dt: datetime) -> Optional[datetime]:
        self.logger.debug(f"Determining next travel time for pattern {pattern.pattern_id} (Time: {pattern.time_of_day_group}, Days: {pattern.days_of_week}) from current_dt: {current_dt}")
        target_hour = -1
        time_group = pattern.time_of_day_group.lower()
        if "morning" in time_group: target_hour = 8
        elif "midday" in time_group: target_hour = 12
        elif "afternoon" in time_group: target_hour = 15
        elif "evening" in time_group: target_hour = 17
        elif "night" in time_group: target_hour = 21
        else:
            self.logger.warning(f"Unknown time_of_day_group '{pattern.time_of_day_group}' for pattern {pattern.pattern_id}. Cannot determine target hour.")
            return None
        current_date = current_dt.date()
        for i in range(8):
            next_date_to_check = current_date + timedelta(days=i)
            if next_date_to_check.weekday() in pattern.days_of_week:
                potential_prediction_dt = datetime(
                    next_date_to_check.year, next_date_to_check.month, next_date_to_check.day,
                    target_hour, 0, 0, tzinfo=current_dt.tzinfo
                )
                if potential_prediction_dt > current_dt + timedelta(hours=1):
                    self.logger.info(f"Determined next prediction time for pattern {pattern.pattern_id}: {potential_prediction_dt}")
                    return potential_prediction_dt
        self.logger.info(f"No suitable future prediction time found within 7 days for pattern {pattern.pattern_id}.")
        return None

    async def run_decision_cycle(self, sample_user_id: str = "user_agent_test_123"):
        processed_signals_for_incident: Set[str] = set()
        now_utc = datetime.utcnow()

        logger.info(f"--- Starting AgentCore decision cycle for user: {sample_user_id} at {now_utc.isoformat()} ---")

        # Cooldown cleanup logic (now considers different cooldowns implicitly by not removing them early)
        # This cleanup is for actions that might have had the *shorter* general cooldown
        # and whose specific cooldown type isn't checked here. The main check is done when *applying* new actions.
        actions_to_remove_based_on_general_cooldown = []
        for signal_id, action_data in self._recent_signal_actions.items():
            cooldown_to_apply = self.SIGNAL_ACTION_COOLDOWN_SECONDS
            if 'incident_id' in action_data: # It was an incident action
                 cooldown_to_apply = self.INCIDENT_SIGNAL_COOLDOWN_SECONDS

            if (now_utc - action_data['timestamp']).total_seconds() > cooldown_to_apply:
                 actions_to_remove_based_on_general_cooldown.append(signal_id)

        if actions_to_remove_based_on_general_cooldown:
            for signal_id in actions_to_remove_based_on_general_cooldown:
                if signal_id in self._recent_signal_actions: # Check if exists before deleting
                    reason_for_removal = self._recent_signal_actions[signal_id].get('reason', 'unknown')
                    del self._recent_signal_actions[signal_id]
                    self.logger.debug(f"Removed signal '{signal_id}' from recent actions (reason: {reason_for_removal}, cooldown expired). Kept {len(self._recent_signal_actions)}.")

        self.logger.info("Fetching system KPI summary for AgentCore decision making...")
        system_kpis: Dict[str, Any] = self.analytics_service.get_current_system_kpis_summary()
        self.logger.info(f"AgentCore received System KPIs: {json.dumps(system_kpis, indent=2)}")

        self.logger.info("Fetching critical alert summary for AgentCore decision making...")
        alert_summary: Dict[str, Any] = await self.analytics_service.get_critical_alert_summary()
        self.logger.info(f"AgentCore received Critical Alert Summary: {json.dumps(alert_summary, indent=2)}")

        self.logger.info("Fetching all traffic signal states...")
        all_signal_states: List[SignalState] = await self.traffic_signal_service.get_all_signal_states()
        self.logger.info(f"AgentCore received {len(all_signal_states)} signal states.")
        for state in all_signal_states: # Corrected loop variable name
            self.logger.debug(
                f"Signal ID: {state.signal_id}, "
                f"Location: {state.location.name if state.location and state.location.name else 'N/A'}, "
                f"Phase: {state.current_phase.value if state.current_phase else 'N/A'}, "
                f"Status: {state.operational_status.value if state.operational_status else 'N/A'}"
            )

        current_congestion_level = system_kpis.get("overall_congestion_level", "UNKNOWN")

        # --- Incident-Specific Signal Control ---
        self.logger.info("Evaluating critical alerts for incident-specific signal responses...")
        active_individual_alerts = alert_summary.get('active_alerts', [])

        if not active_individual_alerts:
            self.logger.info("No active individual critical alerts to process for incident response.")
        else:
            self.logger.info(f"Processing {len(active_individual_alerts)} active incident alert(s).")
            for alert_data in active_individual_alerts:
                alert_type = alert_data.get('type', 'UNKNOWN_ALERT_TYPE').upper() # Ensure upper for consistency
                alert_location_data = alert_data.get('location')
                alert_id = alert_data.get('alert_id', f"incident_{alert_type.lower()}_{now_utc.timestamp()}")

                if not alert_location_data or not isinstance(alert_location_data.get('latitude'), (float, int)) or not isinstance(alert_location_data.get('longitude'), (float, int)):
                    self.logger.warning(f"Alert {alert_id} (type: {alert_type}) missing valid location data: {alert_location_data}. Skipping.")
                    continue

                try:
                    incident_location = LocationModel(**alert_location_data)
                except Exception as e_loc:
                    self.logger.error(f"Could not parse location for alert {alert_id}: {alert_location_data}. Error: {e_loc}")
                    continue

                self.logger.info(f"Processing incident alert '{alert_id}': Type '{alert_type}' at ({incident_location.latitude},{incident_location.longitude})")

                radius_meters = 250
                if alert_type == "ROAD_CLOSURE":
                    radius_meters = 150

                nearby_signals: List[SignalState] = await self._find_signals_near_location(
                    incident_location,
                    all_signal_states,
                    radius_meters=radius_meters
                )

                if not nearby_signals:
                    self.logger.info(f"No nearby signals found within {radius_meters}m for incident '{alert_id}' (type: {alert_type}).")
                    continue

                if alert_type == "ACCIDENT":
                    self.logger.info(f"Applying ACCIDENT response strategy for {len(nearby_signals)} signals near '{alert_id}'.")
                    for signal in nearby_signals:
                        if signal.signal_id in processed_signals_for_incident:
                            self.logger.debug(f"Signal '{signal.signal_id}' already processed this cycle for an incident. Skipping for ACCIDENT '{alert_id}'.")
                            continue

                        # Check general cooldown for this specific incident processing attempt
                        if signal.signal_id in self._recent_signal_actions:
                            action_info = self._recent_signal_actions[signal.signal_id]
                            elapsed_incident_check = (now_utc - action_info['timestamp']).total_seconds()
                            # Use general short cooldown for re-evaluating for *new* incidents or strategies for the same signal
                            if elapsed_incident_check < self.SIGNAL_ACTION_COOLDOWN_SECONDS:
                                self.logger.debug(f"Signal '{signal.signal_id}' on short cooldown ({self.SIGNAL_ACTION_COOLDOWN_SECONDS}s) due to reason '{action_info.get('reason', 'unknown')}'. Skipping for ACCIDENT '{alert_id}'.")
                                continue

                        if signal.operational_status == SignalOperationalStatusEnum.ONLINE:
                            self.logger.info(f"ACCIDENT strategy for '{signal.signal_id}': Attempting to set GREEN (duration 90s) to help clear area.")
                            try:
                                response = await self.traffic_signal_service.set_signal_phase(
                                    signal_id=signal.signal_id,
                                    phase=SignalPhaseEnum.GREEN,
                                    duration_seconds=90
                                )
                                self.logger.info(f"ACCIDENT response for '{signal.signal_id}': {response.status.value} - {response.message}")
                                if response.status in [SignalControlStatusEnum.ACCEPTED, SignalControlStatusEnum.SUCCESS]:
                                    self._recent_signal_actions[signal.signal_id] = {
                                        'timestamp': now_utc,
                                        'phase_commanded': SignalPhaseEnum.GREEN,
                                        'duration_commanded': 90,
                                        'reason': f'incident_response_{alert_type}', # Standardized reason
                                        'incident_id': alert_id
                                    }
                                    processed_signals_for_incident.add(signal.signal_id)
                            except Exception as e:
                                self.logger.error(f"Error applying ACCIDENT response to '{signal.signal_id}': {e}", exc_info=True)
                        else:
                            self.logger.debug(f"Signal '{signal.signal_id}' is not ONLINE, skipping for ACCIDENT response.")

                elif alert_type == "ROAD_CLOSURE":
                    self.logger.info(f"Applying ROAD_CLOSURE response strategy for {len(nearby_signals)} signals near '{alert_id}'.")
                    for signal in nearby_signals:
                        if signal.signal_id in processed_signals_for_incident:
                            self.logger.debug(f"Signal '{signal.signal_id}' already processed this cycle for an incident. Skipping for ROAD_CLOSURE '{alert_id}'.")
                            continue
                        if signal.signal_id in self._recent_signal_actions:
                            action_info = self._recent_signal_actions[signal.signal_id]
                            elapsed_incident_check = (now_utc - action_info['timestamp']).total_seconds()
                            if elapsed_incident_check < self.SIGNAL_ACTION_COOLDOWN_SECONDS: # Short cooldown for re-evaluation
                                self.logger.debug(f"Signal '{signal.signal_id}' on short cooldown ({self.SIGNAL_ACTION_COOLDOWN_SECONDS}s) due to reason '{action_info.get('reason', 'unknown')}'. Skipping for ROAD_CLOSURE '{alert_id}'.")
                                continue

                        if signal.operational_status == SignalOperationalStatusEnum.ONLINE:
                            self.logger.info(f"ROAD_CLOSURE strategy for '{signal.signal_id}': Placeholder action. Recording to prevent general override.")
                            self._recent_signal_actions[signal.signal_id] = {
                                'timestamp': now_utc,
                                'phase_commanded': signal.current_phase,
                                'duration_commanded': 0, # No actual change
                                'reason': f'incident_response_{alert_type}', # Standardized reason
                                'incident_id': alert_id
                            }
                            processed_signals_for_incident.add(signal.signal_id)
                        else:
                            self.logger.debug(f"Signal '{signal.signal_id}' is not ONLINE, skipping for ROAD_CLOSURE response.")
                else:
                    self.logger.warning(f"Incident type '{alert_type}' for alert '{alert_id}' not handled by specific logic.")
        # --- End of Incident-Specific Signal Control ---

        # --- Autonomous Traffic Signal Control Logic (General Congestion) ---
        if current_congestion_level == "HIGH":
            self.logger.info(f"High congestion detected ({current_congestion_level}). Evaluating general traffic signal interventions.")
            controlled_a_signal_this_cycle_general = False
            for signal_state in all_signal_states:
                if signal_state.signal_id in processed_signals_for_incident:
                    self.logger.debug(f"Signal '{signal_state.signal_id}' was already handled by incident-specific logic this cycle. Skipping for general congestion control.")
                    continue

                if signal_state.signal_id in self._recent_signal_actions:
                    action_data = self._recent_signal_actions[signal_state.signal_id]
                    cooldown_to_apply = self.SIGNAL_ACTION_COOLDOWN_SECONDS
                    log_cooldown_reason = action_data.get('reason', 'general_congestion_action')

                    if 'incident_id' in action_data:
                        cooldown_to_apply = self.INCIDENT_SIGNAL_COOLDOWN_SECONDS
                        log_cooldown_reason = f"incident response (ID: {action_data.get('incident_id', 'N/A')}, Type: {action_data.get('reason', 'N/A').replace('incident_response_', '')})"

                    elapsed_time_seconds = (now_utc - action_data['timestamp']).total_seconds()

                    if elapsed_time_seconds < cooldown_to_apply:
                        self.logger.debug(
                            f"Signal '{signal_state.signal_id}' on cooldown. Last action for '{log_cooldown_reason}' "
                            f"at {action_data['timestamp'].strftime('%Y-%m-%d %H:%M:%S')} ({elapsed_time_seconds:.0f}s ago). "
                            f"Cooldown is {cooldown_to_apply}s. Skipping for general control."
                        )
                        continue
                    else:
                        self.logger.debug(
                            f"Signal '{signal_state.signal_id}' cooldown expired for '{log_cooldown_reason}'. "
                            f"Elapsed: {elapsed_time_seconds:.0f}s, Cooldown was: {cooldown_to_apply}s."
                        )

                if signal_state.operational_status == SignalOperationalStatusEnum.ONLINE and \
                   signal_state.current_phase != SignalPhaseEnum.GREEN:
                    self.logger.info(f"General Congestion: Attempting to set signal '{signal_state.signal_id}' to GREEN.")
                    try:
                        response: SignalControlCommandResponse = await self.traffic_signal_service.set_signal_phase(
                            signal_id=signal_state.signal_id,
                            phase=SignalPhaseEnum.GREEN,
                            duration_seconds=60
                        )
                        self.logger.info(f"General Congestion Signal control response for {signal_state.signal_id}: Status='{response.status.value}', Message='{response.message}'")
                        if response.status == SignalControlStatusEnum.SUCCESS or response.status == SignalControlStatusEnum.ACCEPTED:
                            self.logger.info(f"Successfully commanded signal {signal_state.signal_id} to GREEN for general congestion.")
                            self._recent_signal_actions[signal_state.signal_id] = {
                                'timestamp': now_utc,
                                'phase_commanded': SignalPhaseEnum.GREEN,
                                'duration_commanded': 60,
                                'reason': 'general_congestion' # Specific reason for general actions
                            }
                            controlled_a_signal_this_cycle_general = True
                            self.logger.info(f"Signal {signal_state.signal_id} action recorded for general congestion. Stopping further signal changes this cycle.")
                            break
                    except Exception as e_signal_control:
                        self.logger.error(f"Error controlling signal {signal_state.signal_id} for general congestion: {e_signal_control}", exc_info=True)
                else:
                    self.logger.debug(
                        f"No action for signal {signal_state.signal_id} (general congestion): "
                        f"Status: {signal_state.operational_status.value if signal_state.operational_status else 'N/A'}, "
                        f"Phase: {signal_state.current_phase.value if signal_state.current_phase else 'N/A'}. "
                    )
            if not controlled_a_signal_this_cycle_general:
                self.logger.info("High congestion: No traffic signals required general intervention or were suitable for autonomous GREEN phase change this cycle (considering incident responses and cooldowns).")
        else:
            self.logger.info(f"System congestion level ({current_congestion_level}) is not HIGH. No general autonomous system-wide signal adjustments made by AgentCore.")

        # --- Priority Location Setting ---
        sample_priority_locations = [
            LocationModel(latitude=34.0522, longitude=-118.2437, name="Downtown LA"),
            LocationModel(latitude=40.7128, longitude=-74.0060, name="NYC Center"),
        ]
        await self.prediction_scheduler.set_priority_locations(sample_priority_locations)
        priority_location_names = [loc.name for loc in sample_priority_locations if loc.name]
        self.logger.info(f"AgentCore instructed PredictionScheduler to prioritize locations: {priority_location_names if priority_location_names else 'unnamed locations'}")

        # --- Personalized Routing Phase ---
        logger.info(f"Attempting to generate proactive route suggestion for user: {sample_user_id}...")
        try:
            suggestion = await self.personalized_routing_service.proactively_suggest_route(sample_user_id)
            if suggestion:
                logger.info(f"Proactive route suggestion for user {sample_user_id}: {suggestion}")
            else:
                logger.info(f"No proactive route suggestion generated for user {sample_user_id}.")
        except Exception as e:
            logger.error(f"Error during proactive route suggestion for user {sample_user_id}: {e}")

        # --- System Status Assessment & Global Action (Operational Alerting) ---
        system_status_summary_log = (
            f"System Status Summary (for AgentCore decision):\n"
            f"  Overall Congestion: {system_kpis.get('overall_congestion_level', 'N/A')}\n"
            f"  Average Speed: {system_kpis.get('average_speed_kmh', 'N/A')} km/h\n"
            f"  Critical Unacknowledged Alerts: {alert_summary.get('critical_unack_alert_count', 'N/A')}\n"
        )
        self.logger.info(system_status_summary_log)
        # ... (rest of operational alerting logic remains the same) ...
        trigger_operational_alert = False
        operational_alert_title = ""
        operational_alert_message = ""
        operational_alert_severity = "info"
        suggested_actions_for_alert: List[str] = []
        avg_speed = system_kpis.get("average_speed_kmh", -1.0)
        critical_alerts_count_val = alert_summary.get("critical_unack_alert_count", 0)
        recent_critical_types_list = alert_summary.get('recent_critical_types', [])
        recent_critical_types_str = [str(t) for t in recent_critical_types_list] # Ensure strings

        if current_congestion_level == "HIGH":
            if avg_speed != -1 and avg_speed < 15:
                trigger_operational_alert = True
                operational_alert_title = "Severe System Congestion"
                operational_alert_message = f"System is experiencing SEVERE congestion. Average speed critically low: {avg_speed} km/h."
                operational_alert_severity = "critical"
                suggested_actions_for_alert.extend(["Activate Stage 3 protocols."])
            else:
                trigger_operational_alert = True
                operational_alert_title = "High System Congestion"
                operational_alert_message = f"System is experiencing HIGH congestion. Average speed: {avg_speed} km/h."
                operational_alert_severity = "error"
                suggested_actions_for_alert.extend(["Activate Stage 2 protocols."])
        elif current_congestion_level == "MEDIUM":
            trigger_operational_alert = True
            operational_alert_title = "Moderate System Congestion"
            operational_alert_message = f"System is experiencing MODERATE congestion. Average speed: {avg_speed} km/h."
            operational_alert_severity = "warning"
            suggested_actions_for_alert.extend(["Monitor key corridors."])

        if critical_alerts_count_val > 2: # CRITICAL_ALERT_COUNT_THRESHOLD_STANDALONE
            if not trigger_operational_alert:
                trigger_operational_alert = True
                operational_alert_title = "Multiple Critical Alerts Active"
                operational_alert_message = f"There are {critical_alerts_count_val} critical unacknowledged alert(s) active."
                operational_alert_severity = "error"
            else:
                operational_alert_message += f" Additionally, {critical_alerts_count_val} critical alerts are active."
            operational_alert_message += f" Recent types: {', '.join(recent_critical_types_str)}."
            suggested_actions_for_alert.append("Prioritize investigation of critical alerts.")
            if any("ACCIDENT" in t_str.upper() for t_str in recent_critical_types_str):
                 suggested_actions_for_alert.append("Verify accident reports and dispatch emergency services.")
        elif critical_alerts_count_val > 0 and not trigger_operational_alert: # CRITICAL_ALERT_COUNT_THRESHOLD_FOR_HIGH_CONGESTION (assuming 0)
             trigger_operational_alert = True
             operational_alert_title = "Notable Critical Alerts Active"
             operational_alert_message = f"There are {critical_alerts_count_val} critical unacknowledged alert(s) active. Recent types: {', '.join(recent_critical_types_str)}."
             operational_alert_severity = "warning"
             suggested_actions_for_alert.append("Review critical alerts.")

        if trigger_operational_alert:
            unique_suggested_actions = sorted(list(set(suggested_actions_for_alert)))
            await self.analytics_service.broadcast_operational_alert(
                title=operational_alert_title, message_text=operational_alert_message,
                severity=operational_alert_severity, suggested_actions=unique_suggested_actions or None
            )
            self.logger.info(f"AgentCore action: Issued OPERATIONAL ALERT. Title: '{operational_alert_title}', Severity: {operational_alert_severity}")
        else:
            self.logger.info("AgentCore action: System status within acceptable parameters, no new global operational alert issued.")

        # --- User-Specific Proactive Notifications (Predictive) ---
        self.logger.info("Starting user-specific predictive alert checks...")
        # ... (rest of predictive alerting logic remains the same) ...
        current_time_for_preds = now_utc
        for user_id in [sample_user_id]:
            self.logger.info(f"Processing predictive alerts for user: {user_id}")
            try:
                common_patterns = await self.personalized_routing_service.get_user_common_travel_patterns(user_id=user_id, top_n=3)
                if not common_patterns:
                    self.logger.info(f"No common travel patterns for user {user_id}.")
                    continue
                for pattern in common_patterns:
                    prediction_target_time = await self._determine_next_travel_prediction_time(pattern, current_time_for_preds)
                    if not prediction_target_time: continue
                    dest_summary = pattern.end_location_summary
                    if not isinstance(dest_summary, dict) or not dest_summary.get("latitude") or not dest_summary.get("longitude"):
                        self.logger.warning(f"Invalid dest_summary for pattern {pattern.pattern_id}, user {user_id}.")
                        continue
                    dest_loc = LocationModel(**dest_summary)
                    prediction_result = await self.analytics_service.predict_incident_likelihood(location=dest_loc, prediction_time=prediction_target_time)
                    if prediction_result and prediction_result.get("likelihood_score_percent", 0) > PREDICTIVE_ALERT_LIKELIHOOD_THRESHOLD:
                        # ... (alert construction and sending)
                        pass # Simplified for brevity
            except Exception as e_user_predict:
                self.logger.error(f"Error in predictive alerts for {user_id}: {e_user_predict}", exc_info=True)

        self.logger.info("User-specific predictive alert checks completed.")
        logger.info(f"--- AgentCore decision cycle completed for user: {sample_user_id} at {datetime.utcnow().isoformat()} ---")


async def main_example():
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    logger.info("--- Setting up AgentCore example for Refined Cooldown Logic ---")

    class MockAnalyticsService:
        _kpi_call_count = 0
        _alert_call_count = 0

        def get_current_system_kpis_summary(self) -> Dict[str, Any]:
            MockAnalyticsService._kpi_call_count += 1
            level = "HIGH" # Default to HIGH for most tests
            if MockAnalyticsService._kpi_call_count == 3: # Cycle 3 for testing general cooldown expiry
                level = "HIGH"
            elif MockAnalyticsService._kpi_call_count == 4: # Cycle 4 for testing incident cooldown expiry
                level = "HIGH"
            logger.debug(f"MOCK AnalyticsService.get_current_system_kpis_summary (call {MockAnalyticsService._kpi_call_count}) -> Congestion: {level}")
            return {"overall_congestion_level": level, "average_speed_kmh": 15, "total_vehicle_flow_estimate": 6000}

        async def get_critical_alert_summary(self) -> Dict[str, Any]:
            MockAnalyticsService._alert_call_count +=1
            logger.debug(f"MOCK AnalyticsService.get_critical_alert_summary called (call #{MockAnalyticsService._alert_call_count})")
            active_alerts_list = []
            if MockAnalyticsService._alert_call_count == 1: # Cycle 1: ACCIDENT
                active_alerts_list = [{
                    "alert_id": "incident_acc_cycle1", "type": "ACCIDENT",
                    "location": {"latitude": 1.00005, "longitude": 1.00005, "name": "Crash near TS001"},
                    "timestamp": datetime.utcnow().isoformat()
                }]
            # Other cycles will have no new alerts to focus on cooldown of previous actions
            return { "critical_unack_alert_count": len(active_alerts_list),
                     "recent_critical_types": list(set(a['type'] for a in active_alerts_list if a.get('type'))),
                     "active_alerts": active_alerts_list }

        async def broadcast_operational_alert(self, title: str, message_text: str, severity: str, suggested_actions: Optional[List[str]]): pass
        async def send_user_specific_alert(self, user_id: str, notification_model: UserSpecificConditionAlert): pass
        async def predict_incident_likelihood(self, location: LocationModel, prediction_time: datetime) -> Dict[str, Any]: return {"likelihood_score_percent": 25}

    class MockPredictionScheduler:
        async def set_priority_locations(self, locations: List[LocationModel]): pass
    class MockPersonalizedRoutingService:
        async def proactively_suggest_route(self, user_id: str) -> Optional[Dict[str, Any]]: return None
        async def get_user_common_travel_patterns(self, user_id: str, top_n: int) -> List[CommonTravelPattern]:
            return [CommonTravelPattern(pattern_id="mock_p1", user_id=user_id, start_location_summary={"name":"Home", "latitude":34.0, "longitude":-118.0}, end_location_summary={"name":"Work", "latitude":34.1, "longitude":-118.1}, days_of_week=[0,1,2,3,4], time_of_day_group="MORNING", occurrences=10, average_duration_minutes=30)]

    class MockTrafficSignalService:
        def __init__(self, config: Optional[Dict[str, Any]]=None, connection_manager: Optional[Any]=None):
            self._signals: Dict[str, SignalState] = {}
            self._initialize_mock_signals()
        def _initialize_mock_signals(self):
            self._signals.clear()
            self._signals["TS001"] = SignalState(signal_id="TS001", location=LocationModel(latitude=1.0, longitude=1.0, name="TS001"), current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=datetime.utcnow())
            self._signals["TS002"] = SignalState(signal_id="TS002", location=LocationModel(latitude=2.0, longitude=2.0, name="TS002"), current_phase=SignalPhaseEnum.RED, operational_status=SignalOperationalStatusEnum.ONLINE, last_updated=datetime.utcnow())
        async def get_all_signal_states(self) -> List[SignalState]: return list(self._signals.values())
        async def set_signal_phase(self, signal_id: str, phase: SignalPhaseEnum, duration_seconds: Optional[int] = None) -> SignalControlCommandResponse:
            signal_to_update = self._signals.get(signal_id)
            if signal_to_update and signal_to_update.operational_status == SignalOperationalStatusEnum.ONLINE:
                signal_to_update.current_phase = phase
                signal_to_update.last_updated = datetime.utcnow()
                logger.info(f"MOCK TSS: Signal '{signal_id}' phase set to {phase.value}.")
                return SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.ACCEPTED, new_state=signal_to_update)
            return SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.FAILED, message="Mock signal error.")

    analytics_mock = MockAnalyticsService()
    agent_core = AgentCore( MockPredictionScheduler(), MockPersonalizedRoutingService(), analytics_mock, MockTrafficSignalService())

    # --- Test Scenario for Cooldowns ---
    logger.info("--- MainExample: Cycle 1: Incident Action on TS001 (INCIDENT_SIGNAL_COOLDOWN_SECONDS will apply) ---")
    await agent_core.run_decision_cycle("user_cooldown_test")
    # TS001 should be in _recent_signal_actions with incident reason, e.g., duration 90s
    assert "TS001" in agent_core._recent_signal_actions
    assert agent_core._recent_signal_actions["TS001"]['reason'] == 'incident_response_ACCIDENT'
    ts001_action_time = agent_core._recent_signal_actions["TS001"]['timestamp']

    logger.info(f"--- MainExample: Cycle 2: TS001 should be on INCIDENT cooldown ({agent_core.INCIDENT_SIGNAL_COOLDOWN_SECONDS}s). General congestion tries to act. ---")
    # Manually adjust current time for testing cooldown as if time has passed, but not enough for INCIDENT_COOLDOWN
    # We'd patch datetime.utcnow within AgentCore for a real unit test, here we manipulate the "action time"
    # Let's say 150 seconds have passed (SIGNAL_ACTION_COOLDOWN_SECONDS < 150 < INCIDENT_SIGNAL_COOLDOWN_SECONDS)
    agent_core._recent_signal_actions["TS001"]['timestamp'] = datetime.utcnow() - timedelta(seconds=150)
    # Ensure analytics mock returns no new incidents for this cycle, so only general congestion logic is tested for TS001
    MockAnalyticsService._alert_call_count = 1 # To make get_critical_alert_summary return empty list
    await agent_core.run_decision_cycle("user_cooldown_test")
    # Expect TS001 to be skipped by general congestion due to longer incident cooldown.
    # If TS002 was available and RED, it might be set to GREEN by general logic.

    logger.info(f"--- MainExample: Cycle 3: TS001 INCIDENT cooldown ({agent_core.INCIDENT_SIGNAL_COOLDOWN_SECONDS}s) should be expired. General congestion can act. ---")
    # Let's say 310 seconds have passed from original TS001 action
    agent_core._recent_signal_actions["TS001"]['timestamp'] = ts001_action_time - timedelta(seconds=agent_core.INCIDENT_SIGNAL_COOLDOWN_SECONDS + 10)
    # Reset TS001's phase to RED in the mock TSS to make it a candidate for general congestion logic
    agent_core.traffic_signal_service._signals["TS001"].current_phase = SignalPhaseEnum.RED
    MockAnalyticsService._alert_call_count = 1 # Still no new incidents
    await agent_core.run_decision_cycle("user_cooldown_test")
    # Expect TS001 to now be picked by general congestion logic if it was RED.

    logger.info("--- AgentCore main_example for Refined Cooldown Logic completed ---")

if __name__ == "__main__":
    # asyncio.run(main_example())
    logger.info("AgentCore module defined. Example main_example() function available for testing.")
