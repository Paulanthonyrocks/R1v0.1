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

        # --- Prediction Phase ---
        logger.info("Loading monitored locations for prediction...")
        # _load_monitored_locations is synchronous and modifies scheduler's internal state
        self.prediction_scheduler._load_monitored_locations()

        if not self.prediction_scheduler.monitored_locations:
            logger.warning("No monitored locations loaded by PredictionScheduler. Skipping prediction notifications.")
        else:
            logger.info(f"PredictionScheduler will monitor {len(self.prediction_scheduler.monitored_locations)} locations.")
            # For this cycle, let's predict for all dynamically selected locations.
            # In a real scenario, this might be triggered by specific events or schedules.
            prediction_tasks = []
            for location in self.prediction_scheduler.monitored_locations:
                logger.info(f"Initiating prediction and notification for location: ({location.latitude}, {location.longitude})")
                prediction_tasks.append(
                    self.prediction_scheduler._predict_and_notify(location)
                )
            await asyncio.gather(*prediction_tasks)
            logger.info("Prediction and notification phase completed for monitored locations.")

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

        # --- System Status Assessment & Global Action ---
        self.logger.info("Fetching system KPI summary...")
        system_kpis: Dict[str, Any] = self.analytics_service.get_current_system_kpis_summary()
        self.logger.info(f"Current System KPIs: {json.dumps(system_kpis, indent=2)}")

        self.logger.info("Fetching critical alert summary...")
        alert_summary: Dict[str, Any] = self.analytics_service.get_critical_alert_summary()
        self.logger.info(f"Critical Alert Summary: {json.dumps(alert_summary, indent=2)}")

        system_status_summary_log = (
            f"System Status Summary:\n"
            f"  Overall Congestion: {system_kpis.get('overall_congestion_level', 'N/A')}\n"
            f"  Total Vehicle Flow Rate (hourly): {system_kpis.get('total_vehicle_flow_rate_hourly', 'N/A')}\n"
            f"  Active Feeds: {system_kpis.get('active_feeds_count', 'N/A')}\n"
            f"  System Stability: {system_kpis.get('system_stability_indicator', 'N/A')}\n"
            f"  Critical Alerts: {alert_summary.get('critical_alert_count', 'N/A')}\n"
            f"  Critical Alert Types: {', '.join(alert_summary.get('most_common_critical_types', []))}\n"
        )
        self.logger.info(system_status_summary_log)

        # Placeholder for more complex decision logic based on KPIs and alerts
        if system_kpis.get('overall_congestion_level') == "HIGH" or alert_summary.get('critical_alert_count', 0) > 1:
            self.logger.info("AgentCore Suggestion: System parameters indicate potential need for operator review or automated intervention scaling.")
        else:
            self.logger.info("AgentCore Suggestion: System operating within normal parameters. Continue monitoring.")


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
