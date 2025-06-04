import asyncio
import logging
from typing import Optional, Dict, Any, List # Added List
import json # For pretty printing dicts in logs
from datetime import datetime, timedelta # Added datetime, timedelta

from app.tasks.prediction_scheduler import PredictionScheduler
from app.services.personalized_routing_service import PersonalizedRoutingService, CommonTravelPattern # Ensure CommonTravelPattern is directly importable or accessed via service
from app.services.analytics_service import AnalyticsService
from app.models.traffic import LocationModel
from app.models.websocket import UserSpecificConditionAlert

logger = logging.getLogger(__name__)

PREDICTIVE_ALERT_LIKELIHOOD_THRESHOLD = 60 # Define constant

class AgentCore:
    def __init__(
        self,
        prediction_scheduler: PredictionScheduler,
        personalized_routing_service: PersonalizedRoutingService,
        analytics_service: AnalyticsService, # Added analytics_service
    ):
        """
        Initializes the AgentCore with necessary service components.
        """
        self.prediction_scheduler = prediction_scheduler
        self.personalized_routing_service = personalized_routing_service
        self.analytics_service = analytics_service
        self.logger = logger
        logger.info("AgentCore initialized with PredictionScheduler, PersonalizedRoutingService, and AnalyticsService.")

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

if __name__ == "__main__":
    # This is a simple way to run the example.
    # In a real application, you'd have a proper event loop setup.
    logging.basicConfig(level=logging.INFO)
    # asyncio.run(main_example()) # Commented out as it might run in the test environment
    logger.info("AgentCore module defined. Example main_example() function available for testing (currently commented out).")
