import asyncio
import unittest
from unittest.mock import patch, MagicMock, call
import random

from app.tasks.prediction_scheduler import PredictionScheduler
from app.models.traffic import LocationModel
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum, GeneralNotification

class TestPredictionScheduler(unittest.TestCase):

    def setUp(self):
        self.mock_analytics_service = MagicMock()
        # Mock the _connection_manager attribute within the analytics_service mock
        self.mock_analytics_service._connection_manager = MagicMock()

        self.scheduler = PredictionScheduler(
            analytics_service=self.mock_analytics_service,
            prediction_interval_minutes=15
        )
        # Keep a reference to the original list of locations for testing _load_monitored_locations
        self.original_hardcoded_locations_with_names = [
            LocationModel(latitude=34.0522, longitude=-118.2437, name="Los Angeles Downtown"),
            LocationModel(latitude=40.7128, longitude=-74.0060, name="New York Times Square"),
            LocationModel(latitude=41.8781, longitude=-87.6298, name="Chicago The Loop"),
            LocationModel(latitude=37.7749, longitude=-122.4194, name="San Francisco Embarcadero"),
            LocationModel(latitude=33.7490, longitude=-84.3880, name="Atlanta Centennial Park")
        ]
        # Store the logger directly for easier patching/asserting if needed for specific log messages
        self.scheduler.logger = MagicMock()


    async def test_set_priority_locations(self):
        mock_locations = [
            LocationModel(latitude=1.0, longitude=1.0, name="Prio 1"),
            LocationModel(latitude=2.0, longitude=2.0, name="Prio 2")
        ]
        await self.scheduler.set_priority_locations(mock_locations)

        # Access private member for verification, which is okay in tests
        self.assertEqual(self.scheduler._priority_locations, mock_locations)
        self.scheduler.logger.info.assert_called_once() # Check for logging

    @patch('app.tasks.prediction_scheduler.random.sample')
    @patch('app.tasks.prediction_scheduler.random.randint')
    async def test_load_monitored_locations_uses_priority_when_set(self, mock_randint, mock_random_sample):
        priority_locs = [
            LocationModel(latitude=1.23, longitude=4.56, name="Priority Spot A"),
            LocationModel(latitude=7.89, longitude=0.12, name="Priority Spot B")
        ]
        await self.scheduler.set_priority_locations(priority_locs)

        await self.scheduler._load_monitored_locations()

        self.assertEqual(self.scheduler.monitored_locations, priority_locs)
        self.assertEqual(self.scheduler._priority_locations, []) # Should be cleared after use
        self.scheduler.logger.info.assert_any_call(f"Using {len(priority_locs)} priority locations for prediction: {['Priority Spot A', 'Priority Spot B']}")
        mock_randint.assert_not_called() # Random selection should be skipped
        mock_random_sample.assert_not_called()

    @patch('app.tasks.prediction_scheduler.random.sample')
    @patch('app.tasks.prediction_scheduler.random.randint')
    async def test_load_monitored_locations_uses_default_when_no_priority(self, mock_randint, mock_random_sample):
        # Ensure no priority locations are set
        await self.scheduler.set_priority_locations([])

        # Configure random.sample to return a predictable subset from the hardcoded list
        sample_subset = self.original_hardcoded_locations_with_names[:2]
        mock_random_sample.return_value = sample_subset
        mock_randint.return_value = 2 # Simulate selecting 2 locations

        await self.scheduler._load_monitored_locations()

        # Asserts for default/random selection logic
        self.assertEqual(len(self.scheduler.monitored_locations), 2)
        self.assertEqual(self.scheduler.monitored_locations, sample_subset)
        mock_randint.assert_called_once_with(1, len(self.original_hardcoded_locations_with_names))
        # The ANY here is because the list is constructed inside _load_monitored_locations
        mock_random_sample.assert_called_once_with(unittest.mock.ANY, 2)

        # Check that the argument to random.sample was a list of LocationModel instances with names
        args, _ = mock_random_sample.call_args
        self.assertIsInstance(args[0], list)
        for item in args[0]:
            self.assertIsInstance(item, LocationModel)
            self.assertIsNotNone(item.name) # Check that names are present in the default list

        self.scheduler.logger.info.assert_any_call("No priority locations set, using default/random locations for prediction.")
        self.scheduler.logger.info.assert_any_call(f"Dynamically selected 2 default locations: {[f'{loc.name} ({loc.latitude},{loc.longitude})' for loc in sample_subset]}")


    @patch('app.tasks.prediction_scheduler.logger') # Patch original module logger if self.scheduler.logger isn't used by determine_autonomous_actions
    def test_determine_autonomous_actions(self, mock_module_logger):
        sample_prediction = {"likelihood_score_percent": 85}
        sample_location = LocationModel(latitude=10.0, longitude=20.0)

        action = self.scheduler.determine_autonomous_actions(sample_prediction, sample_location)

        expected_action_substring = "High incident likelihood of 85%"
        self.assertIn(expected_action_substring, action)
        self.assertIn(str(sample_location.latitude), action)
        self.assertIn(str(sample_location.longitude), action)
        self.assertIn("Placeholder action:", action)

        mock_logger.info.assert_called_once_with(f"Determined autonomous action: {action}")

    @patch.object(PredictionScheduler, 'determine_autonomous_actions')
    @patch('app.tasks.prediction_scheduler.logger') # logger used in _predict_and_notify
    async def test_predict_and_notify_high_likelihood(
        self, mock_internal_logger, mock_determine_actions
    ):
        sample_location = LocationModel(latitude=34.0522, longitude=-118.2437)
        mock_prediction_result = {"likelihood_score_percent": 90, "recommendations": ["Slow down"]}
        mock_action_string = "Test action: dispatch drones"

        self.mock_analytics_service.predict_incident_likelihood.return_value = asyncio.Future()
        self.mock_analytics_service.predict_incident_likelihood.return_value.set_result(mock_prediction_result)

        mock_determine_actions.return_value = mock_action_string

        # Mock the connection manager's broadcast method
        self.mock_analytics_service._connection_manager.broadcast_message_model = MagicMock(return_value=asyncio.Future())
        self.mock_analytics_service._connection_manager.broadcast_message_model.return_value.set_result(None)


        await self.scheduler._predict_and_notify(sample_location)

        self.mock_analytics_service.predict_incident_likelihood.assert_awaited_once()
        mock_determine_actions.assert_called_once_with(mock_prediction_result, sample_location)

        self.mock_analytics_service._connection_manager.broadcast_message_model.assert_awaited_once()

        # Check the details of the broadcast message
        args, kwargs = self.mock_analytics_service._connection_manager.broadcast_message_model.call_args
        sent_message_model = args[0]
        self.assertIsInstance(sent_message_model, WebSocketMessage)
        self.assertEqual(sent_message_model.event_type, WebSocketMessageTypeEnum.PREDICTION_ALERT)

        notification_payload = sent_message_model.payload
        self.assertIsInstance(notification_payload, GeneralNotification)
        self.assertIn("High likelihood", notification_payload.message)
        self.assertEqual(notification_payload.details["autonomous_action"], mock_action_string)
        self.assertEqual(notification_payload.details["likelihood_percent"], mock_prediction_result["likelihood_score_percent"])
        self.assertEqual(notification_payload.details["location"], sample_location.model_dump())

        # Check logging within _predict_and_notify
        self.assertTrue(any(
            f"Sent high likelihood notification for location {sample_location.latitude},{sample_location.longitude}" in log_call.args[0]
            for log_call in mock_internal_logger.info.call_args_list
        ))


    @patch.object(PredictionScheduler, 'determine_autonomous_actions')
    async def test_predict_and_notify_low_likelihood(self, mock_determine_actions):
        sample_location = LocationModel(latitude=40.7128, longitude=-74.0060)
        mock_prediction_result = {"likelihood_score_percent": 50} # Below threshold

        self.mock_analytics_service.predict_incident_likelihood.return_value = asyncio.Future()
        self.mock_analytics_service.predict_incident_likelihood.return_value.set_result(mock_prediction_result)

        # Mock the connection manager's broadcast method (though it shouldn't be called)
        self.mock_analytics_service._connection_manager.broadcast_message_model = MagicMock(return_value=asyncio.Future())
        self.mock_analytics_service._connection_manager.broadcast_message_model.return_value.set_result(None)

        await self.scheduler._predict_and_notify(sample_location)

        self.mock_analytics_service.predict_incident_likelihood.assert_awaited_once()
        mock_determine_actions.assert_not_called() # Should not be called for low likelihood
        self.mock_analytics_service._connection_manager.broadcast_message_model.assert_not_awaited() # Notification should not be sent

    def tearDown(self):
        # Ensure any pending asyncio tasks are cancelled or completed if necessary
        # For this specific set of tests, direct awaited calls should complete.
        pass

# To run these tests (if you have a way to execute in the environment)
# if __name__ == '__main__':
#     unittest.main()

# Need to wrap async tests for unittest execution if not using a runner like pytest-asyncio
# For simplicity, I'm using await directly in test methods, assuming an async-compatible test runner
# or that these will be adapted. If using `python -m unittest`, async test methods need `asyncio.run`.

def async_test(f):
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper

TestPredictionScheduler.test_set_priority_locations = async_test(TestPredictionScheduler.test_set_priority_locations)
TestPredictionScheduler.test_load_monitored_locations_uses_priority_when_set = async_test(TestPredictionScheduler.test_load_monitored_locations_uses_priority_when_set)
TestPredictionScheduler.test_load_monitored_locations_uses_default_when_no_priority = async_test(TestPredictionScheduler.test_load_monitored_locations_uses_default_when_no_priority)
TestPredictionScheduler.test_predict_and_notify_high_likelihood = async_test(TestPredictionScheduler.test_predict_and_notify_high_likelihood)
TestPredictionScheduler.test_predict_and_notify_low_likelihood = async_test(TestPredictionScheduler.test_predict_and_notify_low_likelihood)

# Simplified run loop test
async def test_run_loop_uses_priority_then_default(self):
    self.scheduler.logger = MagicMock() # Re-mock logger for this specific test if needed for call count isolation
    self.scheduler._predict_and_notify = AsyncMock() # Mock out actual prediction

    priority_locs = [LocationModel(latitude=1.0, longitude=1.0, name="P1")]

    # Cycle 1: With priority locations
    await self.scheduler.set_priority_locations(priority_locs)
    await self.scheduler._load_monitored_locations() # Simulates what run() would do
    self.assertEqual(self.scheduler.monitored_locations, priority_locs)
    self.assertTrue(self.scheduler.logger.info.call_args_list[-1][0][0].startswith("Using 1 priority locations"))


    # Cycle 2: Priority locations should be cleared, defaults used
    # Mock random selection for default
    default_locs = [self.original_hardcoded_locations_with_names[0]]
    with patch('app.tasks.prediction_scheduler.random.sample', return_value=default_locs), \
         patch('app.tasks.prediction_scheduler.random.randint', return_value=1):
        await self.scheduler._load_monitored_locations() # Simulates next cycle in run()

    self.assertEqual(self.scheduler.monitored_locations, default_locs)
    self.assertTrue(self.scheduler.logger.info.call_args_list[-2][0][0].startswith("No priority locations set")) # -2 because last is dynamic selected
    self.assertTrue(self.scheduler.logger.info.call_args_list[-1][0][0].startswith("Dynamically selected 1 default locations"))

TestPredictionScheduler.test_run_loop_uses_priority_then_default = async_test(test_run_loop_uses_priority_then_default)


if __name__ == '__main__':
    unittest.main()
