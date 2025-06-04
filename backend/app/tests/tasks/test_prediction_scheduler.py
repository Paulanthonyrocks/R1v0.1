import asyncio
import unittest
from unittest.mock import patch, MagicMock, call, AsyncMock # Added AsyncMock
import random
from datetime import datetime, timedelta # Added datetime, timedelta

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
        # Setup AsyncMock for analytics_service methods that will be called by new tests
        self.mock_analytics_service.record_prediction_log = AsyncMock(return_value=str(random.randint(1000,2000))) # Returns a mock log_id
        self.mock_analytics_service.get_prediction_outcome_summary = AsyncMock()


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

        # Assert that record_prediction_log was called
        self.mock_analytics_service.record_prediction_log.assert_awaited_once()
        log_call_args = self.mock_analytics_service.record_prediction_log.call_args[0][0] # Get the log_data dict

        self.assertEqual(log_call_args['location_latitude'], sample_location.latitude)
        self.assertEqual(log_call_args['location_longitude'], sample_location.longitude)
        self.assertEqual(log_call_args['prediction_type'], "incident_likelihood")
        self.assertEqual(log_call_args['predicted_value'], mock_prediction_result)
        self.assertEqual(log_call_args['source_of_prediction'], "PredictionScheduler_HighLikelihood")
        # Check that logger was called for successful recording
        self.assertTrue(any(
            "Successfully recorded high-likelihood prediction" in log_call.args[0]
            for log_call in self.scheduler.logger.info.call_args_list # Use self.scheduler.logger as it's mocked
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


# --- Tests for accuracy-based location selection ---

@patch('app.tasks.prediction_scheduler.random.choices') # Patch random.choices used in _load_monitored_locations
async def test_load_monitored_locations_adapts_to_accuracy(self, mock_random_choices):
    # Ensure no priority locations
    await self.scheduler.set_priority_locations([])

    # Use a subset of original_hardcoded_locations_with_names for this test
    test_locations = self.original_hardcoded_locations_with_names[:3]
    loc_a, loc_b, loc_c = test_locations[0], test_locations[1], test_locations[2]

    # Mock the return value of _load_monitored_locations's internal `all_locations` list
    # to ensure we are testing against a known set.
    with patch.object(self.scheduler, '_load_monitored_locations') as mock_load_method_internal_logic:
        # This is tricky. Instead of patching the whole method, we rely on the hardcoded list
        # in the actual _load_monitored_locations matching our self.original_hardcoded_locations_with_names.
        # For this test, we'll assume `all_locations` inside the method is `self.original_hardcoded_locations_with_names`.
        pass # No need to patch the method itself, just its dependencies.

    def get_summary_side_effect(location_latitude, location_longitude, time_since, **kwargs):
        if location_latitude == loc_a.latitude and location_longitude == loc_a.longitude:
            return {"accuracy_metrics": {"incident_hit_rate": 0.8}, "total_verified_predictions": 10, "outcomes": {"incident_occurred": 8, "no_event_detected": 2}}
        elif location_latitude == loc_b.latitude and location_longitude == loc_b.longitude:
            return {"accuracy_metrics": {"incident_hit_rate": 0.2}, "total_verified_predictions": 10, "outcomes": {"incident_occurred": 2, "no_event_detected": 8}}
        elif location_latitude == loc_c.latitude and location_longitude == loc_c.longitude:
            return {"accuracy_metrics": {"incident_hit_rate": 0.5}, "total_verified_predictions": 2, "outcomes": {"incident_occurred": 1, "no_event_detected": 1}} # Low data
        return {"accuracy_metrics": {"incident_hit_rate": 0.5}, "total_verified_predictions": 0} # Default for others

    self.mock_analytics_service.get_prediction_outcome_summary.side_effect = get_summary_side_effect

    # Mock random.choices to return a fixed selection but capture weights
    # We expect _load_monitored_locations to select up to half of all_locations (5 in this case), so k can be 1, 2. Let's say it tries to pick 2.
    mock_random_choices.return_value = [loc_a, loc_b] # Dummy return, we care about weights

    await self.scheduler._load_monitored_locations()

    # Assert get_prediction_outcome_summary was called for all locations in the hardcoded list
    # (because the cache is initially empty)
    expected_calls = [
        call(location_latitude=loc.latitude, location_longitude=loc.longitude, time_since=unittest.mock.ANY)
        for loc in self.original_hardcoded_locations_with_names
    ]
    self.mock_analytics_service.get_prediction_outcome_summary.assert_has_calls(expected_calls, any_order=True)
    self.assertEqual(self.mock_analytics_service.get_prediction_outcome_summary.call_count, len(self.original_hardcoded_locations_with_names))

    # Assert that the cache is populated
    self.assertIn(self.scheduler._get_location_key(loc_a), self.scheduler._location_accuracy_cache)
    self.assertEqual(self.scheduler._location_accuracy_cache[self.scheduler._get_location_key(loc_a)]["accuracy_metrics"]["incident_hit_rate"], 0.8)

    # Assert weights passed to random.choices
    self.assertTrue(mock_random_choices.called)
    args, kwargs = mock_random_choices.call_args
    passed_weights = kwargs.get('weights', [])
    self.assertEqual(len(passed_weights), len(self.original_hardcoded_locations_with_names))

    # Expected weights based on logic:
    # LocA (hit_rate=0.8, N=10): (0.8*0.8) = 0.64
    # LocB (hit_rate=0.2, N=10): (0.2*0.2) = 0.04
    # LocC (hit_rate=0.5, N=2, low data): 0.75
    # Other 2 locations (no specific mock, default from get_summary_side_effect is 0.5 hit_rate, 0 predictions): weight = 0.8 (no accuracy data initially then default summary)
    # The logic is: if accuracy_data: (if total_verified < 5: 0.75 else: (hit_rate**2) or 0.1) else: 0.8

    idx_a = self.original_hardcoded_locations_with_names.index(loc_a)
    idx_b = self.original_hardcoded_locations_with_names.index(loc_b)
    idx_c = self.original_hardcoded_locations_with_names.index(loc_c)

    self.assertAlmostEqual(passed_weights[idx_a], 0.8 * 0.8)
    self.assertAlmostEqual(passed_weights[idx_b], 0.2 * 0.2)
    self.assertAlmostEqual(passed_weights[idx_c], 0.75)

    # Check other locations got the "no data initially then default summary" weight
    for i, loc in enumerate(self.original_hardcoded_locations_with_names):
        if i not in [idx_a, idx_b, idx_c]:
            # If get_summary_side_effect returned data (total_verified_predictions=0), then hit_rate = 0.5.
            # Since total_verified < 5, weight should be 0.75
             self.assertAlmostEqual(passed_weights[i], 0.75)


async def test_load_monitored_locations_uses_accuracy_cache(self):
    await self.scheduler.set_priority_locations([])
    self.mock_analytics_service.get_prediction_outcome_summary.side_effect = \
        lambda **kwargs: {"accuracy_metrics": {"incident_hit_rate": 0.6}, "total_verified_predictions": 10}

    # First call - should populate cache
    with patch('app.tasks.prediction_scheduler.random.choices') as mock_choices_1:
        mock_choices_1.return_value = [self.original_hardcoded_locations_with_names[0]]
        await self.scheduler._load_monitored_locations()

    call_count_after_first_run = self.mock_analytics_service.get_prediction_outcome_summary.call_count
    self.assertTrue(call_count_after_first_run > 0) # Ensure it was called

    # Second call - should use cache
    with patch('app.tasks.prediction_scheduler.random.choices') as mock_choices_2:
        mock_choices_2.return_value = [self.original_hardcoded_locations_with_names[0]]
        await self.scheduler._load_monitored_locations()

    self.assertEqual(self.mock_analytics_service.get_prediction_outcome_summary.call_count, call_count_after_first_run)
    self.assertIsNotNone(self.scheduler._last_accuracy_cache_refresh) # Cache timestamp should be set


async def test_load_monitored_locations_refreshes_accuracy_cache_after_ttl(self):
    await self.scheduler.set_priority_locations([])
    self.scheduler._accuracy_cache_ttl = timedelta(milliseconds=10) # Short TTL for test

    # Initial outcome summary mock
    initial_summary_call_count = 0
    def initial_summary_side_effect(**kwargs):
        nonlocal initial_summary_call_count
        initial_summary_call_count +=1
        return {"accuracy_metrics": {"incident_hit_rate": 0.7}, "total_verified_predictions": 20}
    self.mock_analytics_service.get_prediction_outcome_summary.side_effect = initial_summary_side_effect

    # First call - populates cache
    with patch('app.tasks.prediction_scheduler.random.choices') as mc1:
        mc1.return_value = [self.original_hardcoded_locations_with_names[0]]
        await self.scheduler._load_monitored_locations()

    first_call_count = initial_summary_call_count
    self.assertTrue(first_call_count > 0)

    # Second call - should use cache (TTL not expired yet, assuming execution is fast)
    with patch('app.tasks.prediction_scheduler.random.choices') as mc2:
        mc2.return_value = [self.original_hardcoded_locations_with_names[0]]
        await self.scheduler._load_monitored_locations()
    self.assertEqual(initial_summary_call_count, first_call_count, "Cache should have been used for second call")

    # Simulate time passing to expire TTL
    await asyncio.sleep(0.02) # Sleep a bit longer than TTL
    # Or, more reliably for unit tests, manipulate _last_accuracy_cache_refresh directly:
    # self.scheduler._last_accuracy_cache_refresh = datetime.now() - timedelta(seconds=5)


    # Third call - should refresh cache
    refreshed_summary_call_count = 0
    def refreshed_summary_side_effect(**kwargs):
        nonlocal refreshed_summary_call_count
        refreshed_summary_call_count +=1
        return {"accuracy_metrics": {"incident_hit_rate": 0.75}, "total_verified_predictions": 22} # Slightly different data
    self.mock_analytics_service.get_prediction_outcome_summary.side_effect = refreshed_summary_side_effect

    with patch('app.tasks.prediction_scheduler.random.choices') as mc3:
        mc3.return_value = [self.original_hardcoded_locations_with_names[0]]
        await self.scheduler._load_monitored_locations()

    self.assertTrue(refreshed_summary_call_count > 0, "Cache should have been refreshed")
    # Verify the cache has new data (optional, main point is call count)
    loc_key = self.scheduler._get_location_key(self.original_hardcoded_locations_with_names[0])
    if self.scheduler._location_accuracy_cache.get(loc_key): # Ensure key exists
         self.assertAlmostEqual(self.scheduler._location_accuracy_cache[loc_key]["accuracy_metrics"]["incident_hit_rate"], 0.75)


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
