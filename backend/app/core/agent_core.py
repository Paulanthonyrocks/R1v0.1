import asyncio
import logging
from typing import Optional

from app.tasks.prediction_scheduler import PredictionScheduler
from app.services.personalized_routing_service import PersonalizedRoutingService
from app.models.traffic import LocationModel # For potential sample locations

logger = logging.getLogger(__name__)

class AgentCore:
    def __init__(
        self,
        prediction_scheduler: PredictionScheduler,
        personalized_routing_service: PersonalizedRoutingService,
    ):
        """
        Initializes the AgentCore with necessary service components.
        """
        self.prediction_scheduler = prediction_scheduler
        self.personalized_routing_service = personalized_routing_service
        logger.info("AgentCore initialized with PredictionScheduler and PersonalizedRoutingService.")

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

        logger.info("AgentCore decision cycle completed.")

# Example usage (for illustration, not part of the class itself)
async def main_example():
    # This setup is highly simplified and for demonstration.
    # Real setup would involve proper instantiation of services with dependencies.

    # Mocking dependencies for PredictionScheduler and PersonalizedRoutingService
    class MockAnalyticsService:
        def __init__(self):
            self._connection_manager = None # Simplified
        async def predict_incident_likelihood(self, location, prediction_time):
            logger.debug(f"Mock predict_incident_likelihood called for {location} at {prediction_time}")
            return {"likelihood_score_percent": 75, "recommendations": ["Drive carefully"]}

    class MockTrafficPredictor:
        pass

    class MockDataCache:
        pass

    analytics_service_mock = MockAnalyticsService()
    prediction_scheduler_instance = PredictionScheduler(
        analytics_service=analytics_service_mock,
        prediction_interval_minutes=15
    )

    personalized_routing_service_instance = PersonalizedRoutingService(
        db_url="sqlite:///:memory:", # In-memory DB for example
        traffic_predictor=MockTrafficPredictor(),
        data_cache=MockDataCache()
    )

    # Initialize AgentCore
    agent_core = AgentCore(
        prediction_scheduler=prediction_scheduler_instance,
        personalized_routing_service=personalized_routing_service_instance
    )

    # Run a decision cycle
    await agent_core.run_decision_cycle(sample_user_id="user_example_456")

if __name__ == "__main__":
    # This is a simple way to run the example.
    # In a real application, you'd have a proper event loop setup.
    logging.basicConfig(level=logging.INFO)
    # asyncio.run(main_example()) # Commented out as it might run in the test environment
    logger.info("AgentCore module defined. Example main_example() function available for testing (currently commented out).")
