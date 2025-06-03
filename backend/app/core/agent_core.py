import asyncio
import logging
from typing import Optional, Dict, Any # Added Dict, Any
import json # For pretty printing dicts in logs

from app.tasks.prediction_scheduler import PredictionScheduler
from app.services.personalized_routing_service import PersonalizedRoutingService
from app.services.analytics_service import AnalyticsService # Added AnalyticsService import
from app.models.traffic import LocationModel

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
        operational_alert_severity = "info"

        current_congestion_level = system_kpis.get("overall_congestion_level", "UNKNOWN")
        critical_alerts_count_val = alert_summary.get("critical_unack_alert_count", 0)

        if current_congestion_level == CONGESTION_THRESHOLD_FOR_ALERT and \
           critical_alerts_count_val > CRITICAL_ALERT_COUNT_THRESHOLD_FOR_HIGH_CONGESTION:
            trigger_operational_alert = True
            operational_alert_title = "High System Load & Critical Alerts"
            operational_alert_message = (
                f"System is experiencing HIGH congestion (Avg Speed: {system_kpis.get('average_speed_kmh')} km/h, "
                f"Flow Estimate: {system_kpis.get('total_vehicle_flow_estimate')}). "
                f"Additionally, there are {critical_alerts_count_val} critical unacknowledged alert(s). "
                f"Recent types: {', '.join(alert_summary.get('recent_critical_types',[]))}. Operator review advised."
            )
            operational_alert_severity = "error"
        elif current_congestion_level == CONGESTION_THRESHOLD_FOR_ALERT:
            trigger_operational_alert = True
            operational_alert_title = "High System Congestion"
            operational_alert_message = (
                f"System is experiencing HIGH congestion (Avg Speed: {system_kpis.get('average_speed_kmh')} km/h, "
                f"Flow Estimate: {system_kpis.get('total_vehicle_flow_estimate')}). "
                f"Operator review advised for traffic management."
            )
            operational_alert_severity = "warning"
        elif critical_alerts_count_val > CRITICAL_ALERT_COUNT_THRESHOLD_STANDALONE:
            trigger_operational_alert = True
            operational_alert_title = "Multiple Critical Alerts Active"
            operational_alert_message = (
                f"There are {critical_alerts_count_val} critical unacknowledged alert(s) active. "
                f"Recent types: {', '.join(alert_summary.get('recent_critical_types',[]))}. Operator review advised."
            )
            operational_alert_severity = "warning"

        if trigger_operational_alert:
            await self.analytics_service.broadcast_operational_alert(
                title=operational_alert_title,
                message_text=operational_alert_message,
                severity=operational_alert_severity
            )
            self.logger.info(f"AgentCore action: Issued OPERATIONAL ALERT. Title: '{operational_alert_title}', Severity: {operational_alert_severity}")
        else:
            self.logger.info("AgentCore action: System status within acceptable parameters, no new global operational alert issued by AgentCore.")

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
