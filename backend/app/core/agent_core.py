import asyncio
import logging
from typing import Optional, Dict, Any, List # Added List
import json # For pretty printing dicts in logs
from datetime import datetime, timedelta # Added datetime, timedelta

from app.tasks.prediction_scheduler import PredictionScheduler
from app.services.personalized_routing_service import PersonalizedRoutingService # CommonTravelPattern is defined here
from app.services.analytics_service import AnalyticsService
from app.models.traffic import LocationModel
from app.models.websocket import UserSpecificConditionAlert # Updated model import

logger = logging.getLogger(__name__)

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
        self.analytics_service = analytics_service # Store analytics_service
        self.logger = logger # Use the module-level logger, or assign one if passed
        logger.info("AgentCore initialized with PredictionScheduler, PersonalizedRoutingService, and AnalyticsService.")

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

        # --- User-Specific Proactive Notifications ---
        self.logger.info("Starting user-specific proactive notification checks...")
        sample_user_ids_for_proactive_alerts = ["user_agent_test_123", "another_sample_user_id"] # Example user IDs

        current_time = datetime.now()
        current_weekday = current_time.weekday() # Monday=0, Sunday=6

        for user_id in sample_user_ids_for_proactive_alerts:
            self.logger.info(f"Processing proactive notifications for user: {user_id}")
            try:
                common_patterns = await self.personalized_routing_service.get_user_common_travel_patterns(
                    user_id=user_id, top_n=3
                )

                if not common_patterns:
                    self.logger.info(f"No common travel patterns found for user {user_id}.")
                    continue

                for pattern in common_patterns:
                    self.logger.debug(f"Checking pattern for user {user_id}: {pattern.pattern_id} - {pattern.time_of_day_group}")

                    # Determine if pattern is relevant now
                    is_relevant_now = False
                    time_group_parts = pattern.time_of_day_group.split('_') # e.g., "morning_weekday"
                    pattern_time = time_group_parts[0]
                    pattern_day_type = time_group_parts[1] if len(time_group_parts) > 1 else "any" # any day type if not specified

                    # Simple time matching logic
                    current_hour = current_time.hour
                    if pattern_time == "morning" and not (6 <= current_hour < 10): continue
                    if pattern_time == "midday" and not (10 <= current_hour < 16): continue
                    if pattern_time == "evening" and not (16 <= current_hour < 20): continue
                    # Add more specific night checks if needed, e.g. night_early (20-24), night_late (0-6)

                    # Day matching logic
                    if pattern_day_type == "weekday" and not (0 <= current_weekday <= 4): continue # Monday to Friday
                    if pattern_day_type == "weekend" and not (5 <= current_weekday <= 6): continue # Saturday, Sunday
                    # Also check against pattern.days_of_week if more granularity is needed (e.g. pattern only on MWF)
                    if not (current_weekday in pattern.days_of_week): # More precise check
                         self.logger.debug(f"Pattern {pattern.pattern_id} for user {user_id} not active on day {current_weekday}. Active days: {pattern.days_of_week}")
                         continue

                    is_relevant_now = True # If all checks pass

                    if is_relevant_now:
                        self.logger.info(f"Pattern {pattern.pattern_id} is relevant now for user {user_id}.")

                        # Simplified condition: If general system congestion is HIGH, issue a warning for this user's pattern
                        if system_kpis.get("overall_congestion_level") in ["HIGH", "SEVERE"]:
                            notification_title = "Potential Congestion on Your Usual Route"
                            pattern_start_name = pattern.start_location_summary.get('name', 'your usual start area')
                            pattern_end_name = pattern.end_location_summary.get('name', 'your usual destination')

                            notification_message = (
                                f"Hi {user_id}, general traffic congestion is currently {system_kpis.get('overall_congestion_level')}. "
                                f"This might affect your usual travel from {pattern_start_name} to {pattern_end_name} "
                                f"during this {pattern.time_of_day_group.replace('_', ' ')} period."
                            )
                            suggested_actions_list = [
                                "Consider checking real-time traffic conditions before you leave.",
                                "You might want to explore alternative routes or adjust your departure time."
                            ]

                            # Construct related_location from pattern's start_location_summary
                            # Ensure that start_location_summary has 'latitude' and 'longitude'
                            related_loc_data = pattern.start_location_summary
                            related_location_model = None
                            if 'latitude' in related_loc_data and 'longitude' in related_loc_data:
                                try:
                                   related_location_model = LocationModel(**related_loc_data)
                                except Exception as loc_e:
                                    self.logger.warning(f"Could not create LocationModel from pattern start_location_summary for user {user_id}: {loc_e}")

                            # Use the new UserSpecificConditionAlert model
                            user_alert_payload = UserSpecificConditionAlert(
                                user_id=user_id,
                                alert_type="predicted_congestion_on_usual_route", # Maps to old notification_type
                                title=notification_title,
                                message=notification_message,
                                severity="warning", # Example severity
                                suggested_actions=suggested_actions_list,
                                route_context=related_location_model.model_dump() if related_location_model else None # Convert LocationModel to dict
                            )

                            await self.analytics_service.send_user_specific_alert( # Updated method name
                                user_id=user_id,
                                notification_model=user_alert_payload
                            )
                            self.logger.info(f"Sent user-specific congestion alert for user {user_id} regarding pattern {pattern.pattern_id}.")
                        else:
                            self.logger.info(f"System congestion not high enough to warrant proactive alert for user {user_id}, pattern {pattern.pattern_id}.")
                    # else:
                        # self.logger.debug(f"Pattern {pattern.pattern_id} for user {user_id} not relevant at current time/day.")

            except Exception as e_user_notify:
                self.logger.error(f"Error during user-specific notification processing for user {user_id}: {e_user_notify}", exc_info=True)

        self.logger.info("User-specific proactive notification checks completed.")
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
