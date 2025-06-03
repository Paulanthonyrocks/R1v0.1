import asyncio
import unittest
from unittest.mock import MagicMock, patch, call

from app.core.agent_core import AgentCore
from app.tasks.prediction_scheduler import PredictionScheduler
from app.services.personalized_routing_service import PersonalizedRoutingService
from app.models.traffic import LocationModel

class TestAgentCore(unittest.TestCase):

    def setUp(self):
        self.mock_prediction_scheduler = MagicMock(spec=PredictionScheduler)
        self.mock_personalized_routing_service = MagicMock(spec=PersonalizedRoutingService)

        # Configure async methods on mocks to be awaitable
        self.mock_prediction_scheduler._predict_and_notify = MagicMock(return_value=asyncio.Future())
        self.mock_prediction_scheduler._predict_and_notify.return_value.set_result(None) # Example result

        self.mock_personalized_routing_service.proactively_suggest_route = MagicMock(return_value=asyncio.Future())
        self.mock_personalized_routing_service.proactively_suggest_route.return_value.set_result("Sample suggestion")


        self.agent_core = AgentCore(
            prediction_scheduler=self.mock_prediction_scheduler,
            personalized_routing_service=self.mock_personalized_routing_service
        )

    def test_init_stores_services(self):
        self.assertEqual(self.agent_core.prediction_scheduler, self.mock_prediction_scheduler)
        self.assertEqual(self.agent_core.personalized_routing_service, self.mock_personalized_routing_service)

    @patch('app.core.agent_core.logger')
    async def test_run_decision_cycle_happy_path(self, mock_logger):
        sample_user_id = "test_user_123"

        # Setup mock for _load_monitored_locations and its side effect
        locations_to_monitor = [
            LocationModel(latitude=1.0, longitude=1.0),
            LocationModel(latitude=2.0, longitude=2.0)
        ]
        # _load_monitored_locations is sync and modifies an attribute on the scheduler
        self.mock_prediction_scheduler._load_monitored_locations = MagicMock()
        self.mock_prediction_scheduler.monitored_locations = locations_to_monitor # Set after load is called

        # Make _predict_and_notify an async mock
        self.mock_prediction_scheduler._predict_and_notify = MagicMock(side_effect=lambda loc: asyncio.sleep(0))


        await self.agent_core.run_decision_cycle(sample_user_id=sample_user_id)

        # Verify PredictionScheduler calls
        self.mock_prediction_scheduler._load_monitored_locations.assert_called_once()

        # Check that _predict_and_notify was called for each location
        expected_predict_calls = [call(loc) for loc in locations_to_monitor]
        self.mock_prediction_scheduler._predict_and_notify.assert_has_calls(expected_predict_calls, any_order=True)
        self.assertEqual(self.mock_prediction_scheduler._predict_and_notify.call_count, len(locations_to_monitor))

        # Verify PersonalizedRoutingService calls
        self.mock_personalized_routing_service.proactively_suggest_route.assert_awaited_once_with(sample_user_id)

        # Verify logging
        mock_logger.info.assert_any_call("Starting AgentCore decision cycle...")
        mock_logger.info.assert_any_call("Loading monitored locations for prediction...")
        mock_logger.info.assert_any_call(f"PredictionScheduler will monitor {len(locations_to_monitor)} locations.")
        for loc in locations_to_monitor:
             mock_logger.info.assert_any_call(f"Initiating prediction and notification for location: ({loc.latitude}, {loc.longitude})")
        mock_logger.info.assert_any_call("Prediction and notification phase completed for monitored locations.")
        mock_logger.info.assert_any_call(f"Attempting to generate proactive route suggestion for user: {sample_user_id}...")
        # Assuming proactively_suggest_route returns a non-None value from the mock setup
        suggestion_from_mock = self.mock_personalized_routing_service.proactively_suggest_route.return_value.result()
        mock_logger.info.assert_any_call(f"Proactive route suggestion for user {sample_user_id}: {suggestion_from_mock}")
        mock_logger.info.assert_any_call("AgentCore decision cycle completed.")


    @patch('app.core.agent_core.logger')
    async def test_run_decision_cycle_no_locations(self, mock_logger):
        sample_user_id = "test_user_no_loc"

        self.mock_prediction_scheduler._load_monitored_locations = MagicMock()
        self.mock_prediction_scheduler.monitored_locations = [] # Simulate no locations loaded

        self.mock_prediction_scheduler._predict_and_notify = MagicMock(side_effect=lambda loc: asyncio.sleep(0))


        await self.agent_core.run_decision_cycle(sample_user_id=sample_user_id)

        self.mock_prediction_scheduler._load_monitored_locations.assert_called_once()
        self.mock_prediction_scheduler._predict_and_notify.assert_not_called()
        mock_logger.warning.assert_any_call("No monitored locations loaded by PredictionScheduler. Skipping prediction notifications.")

        self.mock_personalized_routing_service.proactively_suggest_route.assert_awaited_once_with(sample_user_id)
        mock_logger.info.assert_any_call("AgentCore decision cycle completed.")


    @patch('app.core.agent_core.logger')
    async def test_run_decision_cycle_routing_suggestion_is_none(self, mock_logger):
        sample_user_id = "test_user_no_suggestion"

        self.mock_prediction_scheduler._load_monitored_locations = MagicMock()
        self.mock_prediction_scheduler.monitored_locations = [LocationModel(latitude=1.0, longitude=1.0)]
        self.mock_prediction_scheduler._predict_and_notify = MagicMock(side_effect=lambda loc: asyncio.sleep(0))


        # Configure mock for proactively_suggest_route to return None
        self.mock_personalized_routing_service.proactively_suggest_route = MagicMock(return_value=asyncio.Future())
        self.mock_personalized_routing_service.proactively_suggest_route.return_value.set_result(None)


        await self.agent_core.run_decision_cycle(sample_user_id=sample_user_id)

        self.mock_personalized_routing_service.proactively_suggest_route.assert_awaited_once_with(sample_user_id)
        mock_logger.info.assert_any_call(f"No proactive route suggestion generated for user {sample_user_id}.")


# Wrapper for async tests
def async_test(f):
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper

TestAgentCore.test_run_decision_cycle_happy_path = async_test(TestAgentCore.test_run_decision_cycle_happy_path)
TestAgentCore.test_run_decision_cycle_no_locations = async_test(TestAgentCore.test_run_decision_cycle_no_locations)
TestAgentCore.test_run_decision_cycle_routing_suggestion_is_none = async_test(TestAgentCore.test_run_decision_cycle_routing_suggestion_is_none)

if __name__ == '__main__':
    unittest.main()
