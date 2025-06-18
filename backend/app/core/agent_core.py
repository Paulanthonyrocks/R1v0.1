import asyncio
import logging
from typing import Optional, Dict, Any, List # Added List
import json # For pretty printing dicts in logs
from datetime import datetime, timedelta # Added datetime, timedelta

from app.tasks.prediction_scheduler import PredictionScheduler
from app.services.personalized_routing_service import PersonalizedRoutingService, CommonTravelPattern # Ensure CommonTravelPattern is directly importable or accessed via service
from app.services.analytics_service import AnalyticsService
from app.services.traffic_signal_service import TrafficSignalService
from app.models.traffic import LocationModel
from app.models.signals import SignalState, SignalPhaseEnum, SignalOperationalStatusEnum, SignalControlCommandResponse, SignalControlStatusEnum
from app.models.websocket import UserSpecificConditionAlert, WebSocketMessage # Added WebSocketMessage
logger = logging.getLogger(__name__)

PREDICTIVE_ALERT_LIKELIHOOD_THRESHOLD = 60 # Define constant

class AgentCore:
    def __init__(
        self,
        prediction_scheduler: PredictionScheduler,
        personalized_routing_service: PersonalizedRoutingService,
        analytics_service: AnalyticsService, # Added analytics_service
        traffic_signal_service: TrafficSignalService,
    ):
        """
        Initializes the AgentCore with necessary service components.
        """
        self.prediction_scheduler = prediction_scheduler
        self.personalized_routing_service = personalized_routing_service
        self.analytics_service = analytics_service
        self.traffic_signal_service = traffic_signal_service
        self._recent_signal_actions: Dict[str, Dict[str, Any]] = {}
        self.SIGNAL_ACTION_COOLDOWN_SECONDS = 120  # 2 minutes
        self.logger = logger
        logger.info("AgentCore initialized with PredictionScheduler, PersonalizedRoutingService, AnalyticsService, and TrafficSignalService.")

    async def _determine_next_travel_prediction_time(self, pattern: CommonTravelPattern, current_dt: datetime) -> Optional[datetime]:
        """
        Determines the next relevant future time to make a prediction for a given travel pattern.
        Looks for the next occurrence of the pattern's time_of_day_group on a valid day_of_week,
        at least 1 hour in the future.
        """
        self.logger.debug(f"Determining next travel time for pattern {pattern.pattern_id} (Time: {pattern.time_of_day_group}, Days: {pattern.days_of_week}) from current_dt: {current_dt}")

        target_hour = -1
        time_group = pattern.time_of_day_group.lower()
        if "morning" in time_group: target_hour = 8 # Example: 8 AM
        elif "midday" in time_group: target_hour = 12 # Example: 12 PM
        elif "afternoon" in time_group: target_hour = 15 # Example: 3 PM
        elif "evening" in time_group: target_hour = 17 # Example: 5 PM
        elif "night" in time_group: target_hour = 21 # Example: 9 PM
        else:
            self.logger.warning(f"Unknown time_of_day_group '{pattern.time_of_day_group}' for pattern {pattern.pattern_id}. Cannot determine target hour.")
            return None

        current_date = current_dt.date()
        for i in range(8): # Check today and next 7 days
            next_date_to_check = current_date + timedelta(days=i)
            if next_date_to_check.weekday() in pattern.days_of_week:
                potential_prediction_dt = datetime(
                    next_date_to_check.year,
                    next_date_to_check.month,
                    next_date_to_check.day,
                    target_hour, 0, 0,
                    tzinfo=current_dt.tzinfo # Preserve timezone if current_dt is aware
                )

                # Prediction must be for at least 1 hour in the future
                if potential_prediction_dt > current_dt + timedelta(hours=1):
                    self.logger.info(f"Determined next prediction time for pattern {pattern.pattern_id}: {potential_prediction_dt}")
                    return potential_prediction_dt

        self.logger.info(f"No suitable future prediction time found within 7 days for pattern {pattern.pattern_id}.")
        return None

    async def run_decision_cycle(self, sample_user_id: str = "user_agent_test_123"):
        """
        Represents the agent's main decision-making loop.
        Coordinates actions between different services based on current context or triggers.
        """
        logger.info("Starting AgentCore decision cycle...")

        # --- System Status Assessment & Priority Location Setting ---
        self.logger.info("Fetching system KPI summary for AgentCore decision making...")
        system_kpis: Dict[str, Any] = self.analytics_service.get_current_system_kpis_summary()
        self.logger.info(f"AgentCore received System KPIs: {json.dumps(system_kpis, indent=2)}")

        self.logger.info("Fetching critical alert summary for AgentCore decision making...")
        alert_summary: Dict[str, Any] = await self.analytics_service.get_critical_alert_summary()
        self.logger.info(f"AgentCore received Critical Alert Summary: {json.dumps(alert_summary, indent=2)}")

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

        # Placeholder: AgentCore determines priority locations based on KPIs/alerts
        # In a future step, this would come from analyzing detailed KPI/congestion data
        sample_priority_locations = [
            LocationModel(latitude=34.0522, longitude=-118.2437, name="Downtown LA"),
            LocationModel(latitude=40.7128, longitude=-74.0060, name="NYC Center"),
            LocationModel(latitude=37.7749, longitude=-122.4194, name="SF Critical Junction") # Example
        ]

        # Example condition: only set priorities if system is highly congested or many critical alerts
        current_congestion = system_kpis.get("overall_congestion_level", "UNKNOWN")
        critical_alerts_count = alert_summary.get("critical_unack_alert_count", 0)

        # For demonstration, let's always set some priority locations, or base it on a simple condition
        # if current_congestion == "HIGH" or critical_alerts_count > 0:
        if True: # For now, always set sample priorities to demonstrate the mechanism
            await self.prediction_scheduler.set_priority_locations(sample_priority_locations)
            priority_location_names = [loc.name for loc in sample_priority_locations if loc.name]
            self.logger.info(f"AgentCore instructed PredictionScheduler to prioritize locations: {priority_location_names if priority_location_names else 'unnamed locations'}")
        else:
            # Optionally, tell scheduler to clear priorities if conditions don't warrant them,
            # or let it fall back to its default logic if no new priorities are set.
            # await self.prediction_scheduler.set_priority_locations([]) # Example of clearing
            self.logger.info("AgentCore: Conditions do not require specific priority locations for PredictionScheduler.")


        # --- Personalized Routing Phase (can remain, as it's user-specific) ---
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
        # This part was already added in the previous step and uses the fetched system_kpis and alert_summary

        system_status_summary_log = (
            f"System Status Summary (for AgentCore decision):\n" # Clarified log source
            f"  Overall Congestion: {system_kpis.get('overall_congestion_level', 'N/A')}\n"
            f"  Average Speed: {system_kpis.get('average_speed_kmh', 'N/A')} km/h\n"
            f"  Total Vehicle Flow Estimate: {system_kpis.get('total_vehicle_flow_estimate', 'N/A')}\n"
            f"  Active Monitored Locations: {system_kpis.get('active_monitored_locations', 'N/A')}\n"
            f"  System Stability: {system_kpis.get('system_stability_indicator', 'N/A')}\n"
            f"  Critical Unacknowledged Alerts: {alert_summary.get('critical_unack_alert_count', 'N/A')}\n"
            f"  Recent Critical Alert Types: {', '.join(alert_summary.get('recent_critical_types', []))}\n"
        )
        self.logger.info(system_status_summary_log)

        # Threshold checking logic for operational alerts
        CONGESTION_THRESHOLD_FOR_ALERT = "HIGH"
        CRITICAL_ALERT_COUNT_THRESHOLD_FOR_HIGH_CONGESTION = 0 # Alert if HIGH congestion AND >0 critical
        CRITICAL_ALERT_COUNT_THRESHOLD_STANDALONE = 2 # Alert if >2 critical alerts, regardless of congestion

        trigger_operational_alert = False
        operational_alert_title = ""
        operational_alert_message = ""
        operational_alert_severity = "info" # Default severity
        suggested_actions_for_alert: List[str] = []

        # Refined KPI extraction
        current_congestion_level = system_kpis.get("overall_congestion_level", "UNKNOWN")
        avg_speed = system_kpis.get("average_speed_kmh", -1.0) # Use -1 to indicate unknown if not present
        total_flow = system_kpis.get("total_vehicle_flow_estimate", -1)
        critical_alerts_count_val = alert_summary.get("critical_unack_alert_count", 0)
        recent_critical_types = alert_summary.get('recent_critical_types', [])

        # More granular alert conditions and suggested actions
        if current_congestion_level == "HIGH":
            if avg_speed != -1 and avg_speed < 15: # Severe congestion if avg speed is very low
                trigger_operational_alert = True
                operational_alert_title = "Severe System Congestion"
                operational_alert_message = (
                    f"System is experiencing SEVERE congestion. Average speed critically low: {avg_speed} km/h. "
                    f"Total vehicle flow estimate: {total_flow}. Immediate attention required."
                )
                operational_alert_severity = "critical"
                suggested_actions_for_alert.extend([
                    "Activate Stage 3 traffic management protocols.",
                    "Consider widespread dynamic rerouting for affected corridors.",
                    "Notify public transit authorities of major expected delays.",
                    "Prepare for potential gridlock; monitor key intersections closely."
                ])
            else: # Standard HIGH congestion
                trigger_operational_alert = True
                operational_alert_title = "High System Congestion"
                operational_alert_message = (
                    f"System is experiencing HIGH congestion. Average speed: {avg_speed} km/h. "
                    f"Total vehicle flow estimate: {total_flow}. Operator review advised."
                )
                operational_alert_severity = "error" # Upgraded from warning
                suggested_actions_for_alert.extend([
                    "Activate Stage 2 traffic management protocols.",
                    "Identify and manage bottleneck areas.",
                    "Increase signal cycle times on outbound routes if applicable.",
                ])
        elif current_congestion_level == "MEDIUM":
            trigger_operational_alert = True # Alert even for medium if other factors exist or just to inform
            operational_alert_title = "Moderate System Congestion"
            operational_alert_message = (
                f"System is experiencing MODERATE congestion. Average speed: {avg_speed} km/h. "
                f"Total vehicle flow estimate: {total_flow}. Monitoring situation."
            )
            operational_alert_severity = "warning"
            suggested_actions_for_alert.extend([
                "Monitor key corridors for escalating congestion.",
                "Ensure all traffic monitoring systems are operational.",
                "Be prepared to implement Stage 1 traffic management if conditions worsen."
            ])

        # Handle critical alerts separately, potentially adding to existing congestion alerts
        if critical_alerts_count_val > CRITICAL_ALERT_COUNT_THRESHOLD_STANDALONE: # e.g., threshold = 2
            if not trigger_operational_alert: # If congestion didn't trigger an alert, this will be the primary
                trigger_operational_alert = True
                operational_alert_title = "Multiple Critical Alerts Active"
                operational_alert_message = f"There are {critical_alerts_count_val} critical unacknowledged alert(s) active. "
                operational_alert_severity = "error" # Higher than warning if many criticals
            else: # Append to existing message
                operational_alert_message += f" Additionally, {critical_alerts_count_val} critical alerts are active."

            operational_alert_message += f" Recent types: {', '.join(recent_critical_types)}. Operator review advised."
            suggested_actions_for_alert.append("Prioritize investigation of critical alerts.")
            if any("ACCIDENT" in str(type_).upper() for type_ in recent_critical_types): # Check if any type string contains "ACCIDENT"
                 suggested_actions_for_alert.append("Verify accident reports and dispatch emergency services if needed.")
                 suggested_actions_for_alert.append("Assess impact of any accidents on traffic flow and adjust signal timings accordingly.")

        # Fallback for very high number of critical alerts, even if congestion is low
        elif critical_alerts_count_val > CRITICAL_ALERT_COUNT_THRESHOLD_FOR_HIGH_CONGESTION and not trigger_operational_alert: # e.g. threshold = 0
             trigger_operational_alert = True
             operational_alert_title = "Notable Critical Alerts Active"
             operational_alert_message = (
                 f"There are {critical_alerts_count_val} critical unacknowledged alert(s) active. "
                 f"Recent types: {', '.join(recent_critical_types)}. System congestion is currently {current_congestion_level}. Review advised."
             )
             operational_alert_severity = "warning"
             suggested_actions_for_alert.append("Review critical alerts and assess potential impact.")


        if trigger_operational_alert:
            # Ensure no duplicate suggested actions
            unique_suggested_actions = sorted(list(set(suggested_actions_for_alert)))

            await self.analytics_service.broadcast_operational_alert(
                title=operational_alert_title,
                message_text=operational_alert_message,
                severity=operational_alert_severity,
                suggested_actions=unique_suggested_actions if unique_suggested_actions else None
            )
            self.logger.info(f"AgentCore action: Issued OPERATIONAL ALERT. Title: '{operational_alert_title}', Severity: {operational_alert_severity}, Actions: {unique_suggested_actions}")
        else:
            self.logger.info("AgentCore action: System status within acceptable parameters, no new global operational alert issued by AgentCore.")

        # --- Autonomous Traffic Signal Control Logic ---
        current_congestion_level = system_kpis.get("overall_congestion_level", "UNKNOWN")
        now_utc = datetime.utcnow() # Use a consistent timestamp for this cycle

        # Cleanup old actions from _recent_signal_actions
        signal_ids_to_clear = [
            sig_id for sig_id, action_info in self._recent_signal_actions.items()
            if (now_utc - action_info['timestamp']).total_seconds() > self.SIGNAL_ACTION_COOLDOWN_SECONDS
        ]
        for sig_id in signal_ids_to_clear:
            del self._recent_signal_actions[sig_id]
            self.logger.info(f"Removed signal {sig_id} from recent actions list (cooldown expired).")

        if current_congestion_level == "HIGH":
            self.logger.info(f"High congestion detected ({current_congestion_level}). Evaluating traffic signal interventions.")
            controlled_a_signal_this_cycle = False
            for signal_state in all_signal_states:
                if signal_state.signal_id in self._recent_signal_actions:
                    self.logger.info(f"Signal {signal_state.signal_id} was recently acted upon. Skipping this cycle. Details: {self._recent_signal_actions[signal_state.signal_id]}")
                    continue

                if signal_state.operational_status == SignalOperationalStatusEnum.ONLINE and \
                   signal_state.current_phase != SignalPhaseEnum.GREEN:
                    self.logger.info(f"Attempting to set signal {signal_state.signal_id} to GREEN due to high congestion.")
                    try:
                        response: SignalControlCommandResponse = await self.traffic_signal_service.set_signal_phase(
                            signal_id=signal_state.signal_id,
                            phase=SignalPhaseEnum.GREEN,
                            duration_seconds=60  # Example duration
                        )
                        self.logger.info(f"Signal control response for {signal_state.signal_id}: Status='{response.status.value}', Message='{response.message}'")
                        if response.status == SignalControlStatusEnum.SUCCESS or response.status == SignalControlStatusEnum.ACCEPTED:
                            self.logger.info(f"Successfully commanded signal {signal_state.signal_id} to GREEN.")
                            self._recent_signal_actions[signal_state.signal_id] = {
                                'timestamp': now_utc, # Use consistent timestamp
                                'phase_commanded': SignalPhaseEnum.GREEN,
                                'duration_commanded': 60
                            }
                            controlled_a_signal_this_cycle = True
                            self.logger.info(f"Signal {signal_state.signal_id} action recorded. Stopping further signal changes this cycle.")
                            break # Control one signal per cycle for now
                    except Exception as e_signal_control:
                        self.logger.error(f"Error controlling signal {signal_state.signal_id}: {e_signal_control}", exc_info=True)
                else:
                    self.logger.debug(
                        f"No action for signal {signal_state.signal_id}: "
                        f"Status: {signal_state.operational_status.value if signal_state.operational_status else 'N/A'}, "
                        f"Phase: {signal_state.current_phase.value if signal_state.current_phase else 'N/A'}. "
                        f"Required: ONLINE and not GREEN. Or recently acted upon."
                    )
            if not controlled_a_signal_this_cycle:
                self.logger.info("High congestion: No traffic signals required intervention or were suitable for autonomous GREEN phase change this cycle (considering cooldowns).")
        else:
            self.logger.info(f"System congestion level ({current_congestion_level}) is not HIGH. No autonomous system-wide signal adjustments made by AgentCore.")

        # --- User-Specific Proactive Notifications (Predictive) ---
        self.logger.info("Starting user-specific predictive alert checks...")
        # sample_user_ids_for_proactive_alerts = ["user_agent_test_123", "another_sample_user_id"] # Example user IDs
        # Use the sample_user_id passed to the method for focused testing, or expand later
        sample_user_ids_for_proactive_alerts = [sample_user_id]

        current_time = datetime.now() # Consider timezone: datetime.now(timezone.utc)

        for user_id in sample_user_ids_for_proactive_alerts:
            self.logger.info(f"Processing predictive alerts for user: {user_id}")
            try:
                common_patterns = await self.personalized_routing_service.get_user_common_travel_patterns(
                    user_id=user_id, top_n=3 # Get top 3 patterns
                )

                if not common_patterns:
                    self.logger.info(f"No common travel patterns found for user {user_id} to make predictions.")
                    continue

                for pattern in common_patterns:
                    self.logger.debug(f"Evaluating pattern for user {user_id}: ID {pattern.pattern_id}, To: {pattern.end_location_summary.get('name', 'Unknown Dest')}, Time Group: {pattern.time_of_day_group}")

                    prediction_target_time = await self._determine_next_travel_prediction_time(pattern, current_time)

                    if not prediction_target_time:
                        self.logger.debug(f"No suitable future prediction time for pattern {pattern.pattern_id} for user {user_id}.")
                        continue

                    dest_summary = pattern.end_location_summary
                    if not dest_summary or not isinstance(dest_summary.get("latitude"), (float, int)) or not isinstance(dest_summary.get("longitude"), (float, int)):
                        self.logger.warning(f"Pattern {pattern.pattern_id} for user {user_id} has invalid destination summary: {dest_summary}. Skipping prediction.")
                        continue

                    destination_location_model = LocationModel(
                        latitude=dest_summary["latitude"],
                        longitude=dest_summary["longitude"],
                        name=dest_summary.get("name")
                    )

                    self.logger.info(f"Requesting incident likelihood prediction for user {user_id}, pattern {pattern.pattern_id} (dest: {destination_location_model.name}), target time: {prediction_target_time}")
                    prediction_result = await self.analytics_service.predict_incident_likelihood(
                        location=destination_location_model,
                        prediction_time=prediction_target_time
                    )

                    if prediction_result and prediction_result.get("likelihood_score_percent", 0) > PREDICTIVE_ALERT_LIKELIHOOD_THRESHOLD:
                        score = prediction_result["likelihood_score_percent"]
                        dest_name = destination_location_model.name or f"area around ({destination_location_model.latitude:.2f}, {destination_location_model.longitude:.2f})"
                        time_formatted = prediction_target_time.strftime("%I:%M %p on %A, %b %d")

                        title = f"Heads-up: Potential Disruption Near {dest_name}"
                        message = (
                            f"Hi {user_id}, we predict a {score:.0f}% chance of incidents or significant disruptions "
                            f"near your common destination '{dest_name}' around {time_formatted}. "
                            f"This is based on your travel pattern: from {pattern.start_location_summary.get('name', 'usual start')} "
                            f"to {dest_name} during {pattern.time_of_day_group.replace('_', ' ')}."
                        )
                        actions = [
                            "Check live traffic conditions closer to your travel time.",
                            "Consider if alternative routes or departure times might be beneficial.",
                            "Stay informed about local advisories."
                        ]

                        alert_payload = UserSpecificConditionAlert(
                            user_id=user_id,
                            alert_type="predicted_disruption_on_common_route",
                            title=title,
                            message=message,
                            severity="warning", # Or "info" depending on likelihood
                            suggested_actions=actions,
                            route_context={
                                "pattern_id": pattern.pattern_id,
                                "start_location_summary": pattern.start_location_summary,
                                "end_location_summary": pattern.end_location_summary,
                                "time_of_day_group": pattern.time_of_day_group,
                                "predicted_for_time": prediction_target_time.isoformat(),
                                "likelihood_score_percent": score
                            }
                        )
                        await self.analytics_service.send_user_specific_alert(
                            user_id=user_id,
                            notification_model=alert_payload
                        )
                        self.logger.info(f"Sent predictive alert to user {user_id} for pattern {pattern.pattern_id} (destination: {dest_name}, score: {score}%).")
                    else:
                        self.logger.info(f"Likelihood for pattern {pattern.pattern_id} (user {user_id}, dest: {destination_location_model.name}) is "
                                         f"{prediction_result.get('likelihood_score_percent', 'N/A')}%. No alert sent.")

            except Exception as e_user_predict_notify:
                self.logger.error(f"Error during predictive alert processing for user {user_id}: {e_user_predict_notify}", exc_info=True)

        self.logger.info("User-specific predictive alert checks completed.")
        logger.info("AgentCore decision cycle completed.")

# Example usage (for illustration, not part of the class itself)
async def main_example():
    # This setup is highly simplified and for demonstration.
    # Real setup would involve proper instantiation of services with dependencies.

    # Mocking dependencies for PredictionScheduler and PersonalizedRoutingService
    class MockAnalyticsServiceForScheduler: # Renamed to avoid conflict if AgentCore's AnalyticsService is also mocked
        def __init__(self):
            self._connection_manager = None # Simplified
        async def predict_incident_likelihood(self, location, prediction_time):
            logger.debug(f"Mock predict_incident_likelihood called for {location} at {prediction_time}")
            return {"likelihood_score_percent": 75, "recommendations": ["Drive carefully"]}

        # Add synchronous mock methods for AgentCore's new calls
        def get_current_system_kpis_summary(self) -> Dict[str, Any]:
            return {"overall_congestion_level": "LOW", "total_vehicle_flow_rate_hourly": 100, "active_feeds_count": 1, "system_stability_indicator": "STABLE"}
        def get_critical_alert_summary(self) -> Dict[str, Any]:
            return {"critical_alert_count": 0, "most_common_critical_types": []}


    class MockTrafficPredictor:
        pass

    class MockDataCache:
        pass

    analytics_service_mock = MockAnalyticsService()
    prediction_scheduler_instance = PredictionScheduler(
        analytics_service=analytics_service_mock,
        prediction_interval_minutes=15
    )
    # This is the AnalyticsService mock for PredictionScheduler, not for AgentCore directly in this example
    analytics_service_for_scheduler_mock = MockAnalyticsServiceForScheduler()
    prediction_scheduler_instance = PredictionScheduler(
        analytics_service=analytics_service_for_scheduler_mock,
        prediction_interval_minutes=15
    )


    personalized_routing_service_instance = PersonalizedRoutingService(
        db_url="sqlite:///:memory:", # In-memory DB for example
        traffic_predictor=MockTrafficPredictor(), # This would be from AnalyticsService in real setup
        data_cache=MockDataCache() # This would be from AnalyticsService in real setup
    )

    # Mock for AnalyticsService instance to be passed to AgentCore
    # This is separate from the one used by PredictionScheduler if they have different mocking needs.
    # For this example, MockAnalyticsServiceForScheduler can serve both if its interface matches.
    actual_analytics_service_mock_for_agentcore = MockAnalyticsServiceForScheduler()

    # Initialize AgentCore
    agent_core = AgentCore(
        prediction_scheduler=prediction_scheduler_instance,
        personalized_routing_service=personalized_routing_service_instance,
        analytics_service=actual_analytics_service_mock_for_agentcore # Pass the new mock
    )

    # Run a decision cycle
    await agent_core.run_decision_cycle(sample_user_id="user_example_456")

async def main_example():
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    logger.info("Starting AgentCore main_example...")

    class MockAnalyticsService:
        def __init__(self):
            self._cycle_count_kpi = 0

        async def get_critical_alert_summary(self) -> Dict[str, Any]:
            logger.info("MockAnalyticsService: get_critical_alert_summary called")
            return {"critical_unack_alert_count": 1, "recent_critical_types": ["SYSTEM_OVERLOAD"]}

        def get_current_system_kpis_summary(self) -> Dict[str, Any]:
            self._cycle_count_kpi += 1
            logger.info(f"MockAnalyticsService: get_current_system_kpis_summary called (cycle {self._cycle_count_kpi})")
            # Keep congestion HIGH for the first 3 agent cycles to test cooldown logic
            congestion = "HIGH" if self._cycle_count_kpi <= 3 else "LOW"
            logger.info(f"MockAnalyticsService: Setting overall_congestion_level to {congestion} for this KPI cycle.")
            return {
                "overall_congestion_level": congestion,
                "average_speed_kmh": 25 if congestion == "HIGH" else 60,
                "total_vehicle_flow_estimate": 5000 if congestion == "HIGH" else 2000,
                "active_monitored_locations": 10,
                "system_stability_indicator": "STABLE"
            }

        async def predict_incident_likelihood(self, location: LocationModel, prediction_time: datetime) -> Dict[str, Any]:
            logger.info(f"MockAnalyticsService: predict_incident_likelihood called for {location.name} at {prediction_time}")
            return {"likelihood_score_percent": 30} # Low likelihood for simplicity

        async def send_user_specific_alert(self, user_id: str, notification_model: UserSpecificConditionAlert):
            logger.info(f"MockAnalyticsService: send_user_specific_alert called for user {user_id}, title: {notification_model.title}")

        async def broadcast_operational_alert(self, title: str, message_text: str, severity: str, suggested_actions: Optional[List[str]] = None):
            logger.info(f"MockAnalyticsService: broadcast_operational_alert called. Title: {title}, Severity: {severity}")

    class MockPredictionScheduler:
        def __init__(self, analytics_service, prediction_interval_minutes):
            self.analytics_service = analytics_service
            self.prediction_interval_minutes = prediction_interval_minutes
            logger.info("MockPredictionScheduler initialized.")

        async def set_priority_locations(self, locations: List[LocationModel]):
            location_names = [loc.name for loc in locations if loc.name]
            logger.info(f"MockPredictionScheduler: set_priority_locations called with {len(locations)} locations: {location_names}")

    class MockPersonalizedRoutingService:
        def __init__(self, db_url: str, traffic_predictor: Any, data_cache: Any):
            logger.info("MockPersonalizedRoutingService initialized.")

        async def proactively_suggest_route(self, user_id: str) -> Optional[Dict[str, Any]]:
            logger.info(f"MockPersonalizedRoutingService: proactively_suggest_route called for user {user_id}")
            return None # No suggestion for simplicity

        async def get_user_common_travel_patterns(self, user_id: str, top_n: int) -> List[CommonTravelPattern]:
            logger.info(f"MockPersonalizedRoutingService: get_user_common_travel_patterns called for user {user_id}")
            # Return one pattern for testing predictive alerts
            return [
                CommonTravelPattern(
                    pattern_id="pattern_mock_1",
                    user_id=user_id,
                    start_location_summary={"latitude": 34.0, "longitude": -118.0, "name": "Home"},
                    end_location_summary={"latitude": 34.0522, "longitude": -118.2437, "name": "Work"},
                    days_of_week=[0, 1, 2, 3, 4], # Mon-Fri
                    time_of_day_group="morning_commute",
                    average_duration_minutes=30,
                    route_representation="mock_route_polyline"
                )
            ]

    class MockConnectionManager: # For TrafficSignalService, if it uses one for broadcasting
        async def broadcast_message_model(self, message_model: Any):
             logger.info(f"MockConnectionManager: broadcast_message_model called with {type(message_model)}")


    class MockTrafficSignalService:
        def __init__(self, config: Optional[Dict[str, Any]] = None, connection_manager: Optional[Any] = None):
            self.config = config or {}
            self.connection_manager = connection_manager
            self._signals: Dict[str, SignalState] = {}
            self._get_states_call_count = 0 # Renamed from _cycle_count for clarity
            logger.info("MockTrafficSignalService initialized.")
            self._initialize_mock_signals()

        def _initialize_mock_signals(self):
            locations_data = [
                {"latitude": 34.052235, "longitude": -118.243683, "name": "Main St & 1st St"}, # TS001
                {"latitude": 34.053200, "longitude": -118.244800, "name": "Main St & 2nd St"}, # TS002
                {"latitude": 34.054165, "longitude": -118.245917, "name": "Spring St & Temple St"} # TS003
            ]
            signal_ids = ["TS001", "TS002", "TS003"]

            # TS001: ONLINE, RED
            # TS002: ONLINE, RED
            # TS003: OFFLINE, OFF
            for i, signal_id in enumerate(signal_ids):
                location = LocationModel(**locations_data[i])
                status = SignalOperationalStatusEnum.ONLINE if i < 2 else SignalOperationalStatusEnum.OFFLINE
                phase = SignalPhaseEnum.RED if status == SignalOperationalStatusEnum.ONLINE else SignalPhaseEnum.OFF

                self._signals[signal_id] = SignalState(
                    signal_id=signal_id,
                    location=location,
                    current_phase=phase,
                    operational_status=status,
                    last_updated=datetime.utcnow() # Use utcnow
                )
            logger.info(f"MockTrafficSignalService: Initialized {len(self._signals)} mock signals: {[(s.signal_id, s.current_phase.value, s.operational_status.value) for s in self._signals.values()]}")

        async def get_all_signal_states(self) -> List[SignalState]:
            self._get_states_call_count += 1
            logger.info(f"MockTrafficSignalService: get_all_signal_states called (invocation {self._get_states_call_count}). Current states:")
            for sig_id, state in self._signals.items():
                 logger.debug(f"  - {sig_id}: Phase={state.current_phase.value}, Status={state.operational_status.value}")
            # The state of signals is now modified by set_signal_phase and persists.
            # No need to artificially change TS001 to GREEN here anymore.
            return list(self._signals.values())

        async def set_signal_phase(self, signal_id: str, phase: SignalPhaseEnum, duration_seconds: Optional[int] = None) -> SignalControlCommandResponse:
            logger.info(f"MockTrafficSignalService: set_signal_phase called for signal {signal_id} to {phase.value}, duration: {duration_seconds}s.")
            if signal_id not in self._signals:
                logger.warning(f"MockTrafficSignalService: Signal {signal_id} not found.")
                return SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.FAILED, message="Signal ID not found.")

            signal = self._signals[signal_id]
            if signal.operational_status != SignalOperationalStatusEnum.ONLINE:
                logger.warning(f"MockTrafficSignalService: Signal {signal_id} is not ONLINE (status: {signal.operational_status.value}). Command REJECTED.")
                return SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.REJECTED, message="Signal is not online.")

            signal.current_phase = phase
            signal.last_updated = datetime.now()
            logger.info(f"MockTrafficSignalService: Signal {signal_id} phase set to {phase.value}. Command ACCEPTED.")
            return SignalControlCommandResponse(signal_id=signal_id, status=SignalControlStatusEnum.ACCEPTED, message=f"Phase set to {phase.value}")

    # Instantiate mock services
    mock_analytics = MockAnalyticsService()
    mock_scheduler = MockPredictionScheduler(analytics_service=mock_analytics, prediction_interval_minutes=15)
    # These mocks for PersonalizedRoutingService are simplified; real ones might come from Analytics or other services
    mock_traffic_predictor_for_prs = object()
    mock_data_cache_for_prs = object()
    mock_routing = MockPersonalizedRoutingService(
        db_url="sqlite:///:memory:",
        traffic_predictor=mock_traffic_predictor_for_prs,
        data_cache=mock_data_cache_for_prs
    )
    mock_conn_manager_for_tss = MockConnectionManager()
    mock_traffic_signals = MockTrafficSignalService(connection_manager=mock_conn_manager_for_tss)

    # Instantiate AgentCore with all mocks
    agent_core = AgentCore(
        prediction_scheduler=mock_scheduler,
        personalized_routing_service=mock_routing,
        analytics_service=mock_analytics,
        traffic_signal_service=mock_traffic_signals # New service
    )

    logger.info("Running AgentCore decision cycle 1...")
    await agent_core.run_decision_cycle(sample_user_id="user_cycle_1")

    logger.info("======== Running AgentCore decision cycle 2 (expect TS001 cooldown, TS002 GREEN) ========")
    # Simulate time passing for cooldown testing, though AgentCore uses real datetime.utcnow()
    # This is more for conceptual clarity in the log reading for the mock.
    # The actual cooldown is tested by AgentCore's `_recent_signal_actions` and `SIGNAL_ACTION_COOLDOWN_SECONDS`.
    await asyncio.sleep(0.1) # Small delay to ensure log timestamps are different if needed
    await agent_core.run_decision_cycle(sample_user_id="user_cycle_2")

    logger.info("======== Running AgentCore decision cycle 3 (expect TS001 & TS002 cooldown) ========")
    await asyncio.sleep(0.1)
    await agent_core.run_decision_cycle(sample_user_id="user_cycle_3")

    logger.info("AgentCore main_example completed.")


if __name__ == "__main__":
    # This is a simple way to run the example.
    # In a real application, you'd have a proper event loop setup.
    # logging.basicConfig(level=logging.INFO) # Moved into main_example for DEBUG level
    # asyncio.run(main_example()) # Commented out as per instruction
    logger.info("AgentCore module defined. Example main_example() function available for testing (currently commented out).")
