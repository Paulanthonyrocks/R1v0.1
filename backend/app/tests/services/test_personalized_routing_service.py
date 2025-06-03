import asyncio
import unittest
from unittest.mock import patch, MagicMock, ANY
from collections import Counter

from app.services.personalized_routing_service import PersonalizedRoutingService, RouteHistoryModel
from app.models.routing import RouteHistoryEntry # Assuming this is the correct model for entries
from app.ml.preference_learner import UserPreferenceLearner # For mock
from app.ml.route_optimizer import RouteOptimizer # For mock


class TestPersonalizedRoutingService(unittest.TestCase):

    def setUp(self):
        # Mock dependencies for PersonalizedRoutingService
        self.mock_db_url = "sqlite:///:memory:" # Not actually used due to session mocking
        self.mock_traffic_predictor = MagicMock()
        self.mock_data_cache = MagicMock()

        # Patch __init__ of dependencies if they have complex setup
        with patch.object(UserPreferenceLearner, '__init__', return_value=None), \
             patch.object(RouteOptimizer, '__init__', return_value=None):
            self.service = PersonalizedRoutingService(
                db_url=self.mock_db_url,
                traffic_predictor=self.mock_traffic_predictor,
                data_cache=self.mock_data_cache
            )

        # Mock the SQLAlchemy Session
        self.mock_session = MagicMock()
        self.service.Session = MagicMock(return_value=self.mock_session)

    def test_get_most_frequent_destination_success(self):
        user_id = "user1"
        sample_location_1 = {"latitude": 10.0, "longitude": 20.0, "name": "Work"}
        sample_location_2 = {"latitude": 30.0, "longitude": 40.0, "name": "Home"}

        # Mock RouteHistoryModel instances (what query would return)
        history_records_mocks = [
            MagicMock(spec=RouteHistoryModel, end_location=sample_location_1), # Freq: 2
            MagicMock(spec=RouteHistoryModel, end_location=sample_location_1),
            MagicMock(spec=RouteHistoryModel, end_location=sample_location_2), # Freq: 1
        ]

        # Configure the mock query chain
        self.mock_session.query(RouteHistoryModel.end_location).filter().order_by().limit().all.return_value = history_records_mocks

        result = self.service._get_most_frequent_destination(user_id, limit=3)

        self.assertEqual(result, sample_location_1)
        self.mock_session.query(RouteHistoryModel.end_location).filter(RouteHistoryModel.user_id == user_id).order_by(RouteHistoryModel.start_time.desc()).limit(3).all.assert_called_once()

    def test_get_most_frequent_destination_single_entry(self):
        user_id = "user_single"
        sample_location_1 = {"latitude": 10.0, "longitude": 20.0}
        history_records_mocks = [
            MagicMock(spec=RouteHistoryModel, end_location=sample_location_1),
        ]
        self.mock_session.query(RouteHistoryModel.end_location).filter().order_by().limit().all.return_value = history_records_mocks
        result = self.service._get_most_frequent_destination(user_id, limit=1)
        self.assertEqual(result, sample_location_1)

    def test_get_most_frequent_destination_no_history(self):
        user_id = "user_no_history"
        self.mock_session.query(RouteHistoryModel.end_location).filter().order_by().limit().all.return_value = []
        result = self.service._get_most_frequent_destination(user_id)
        self.assertIsNone(result)

    def test_get_most_frequent_destination_no_single_frequent(self):
        user_id = "user_no_frequent"
        # All destinations appear only once, and there's more than one
        history_records_mocks = [
            MagicMock(spec=RouteHistoryModel, end_location={"lat": 10, "lon": 20}),
            MagicMock(spec=RouteHistoryModel, end_location={"lat": 30, "lon": 40}),
        ]
        self.mock_session.query(RouteHistoryModel.end_location).filter().order_by().limit().all.return_value = history_records_mocks
        result = self.service._get_most_frequent_destination(user_id)
        self.assertIsNone(result)

    @patch('app.services.personalized_routing_service.logger')
    async def test_proactively_suggest_route_suggestion_generated(self, mock_logger):
        user_id = "user_proactive_test"
        common_destination = {"latitude": 12.34, "longitude": 56.78}

        # Mock _get_most_frequent_destination to return our sample destination
        with patch.object(self.service, '_get_most_frequent_destination', return_value=common_destination) as mock_get_freq_dest:
            suggestion = await self.service.proactively_suggest_route(user_id)

            mock_get_freq_dest.assert_called_once_with(user_id)
            self.assertIsNotNone(suggestion)
            self.assertIn(str(common_destination['latitude']), suggestion)
            self.assertIn(str(common_destination['longitude']), suggestion)
            self.assertIn("Proactive suggestion:", suggestion)

            # Check logger call
            mock_logger.info.assert_any_call(f"Proactive suggestion for user {user_id}: {suggestion}")

    @patch('app.services.personalized_routing_service.logger')
    async def test_proactively_suggest_route_no_common_destination(self, mock_logger):
        user_id = "user_proactive_none"

        # Mock _get_most_frequent_destination to return None
        with patch.object(self.service, '_get_most_frequent_destination', return_value=None) as mock_get_freq_dest:
            suggestion = await self.service.proactively_suggest_route(user_id)

            mock_get_freq_dest.assert_called_once_with(user_id)
            self.assertIsNone(suggestion)
            mock_logger.info.assert_any_call(f"No common destination found for user {user_id} to make a proactive suggestion.")


# Wrapper for async tests
def async_test(f):
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper

TestPersonalizedRoutingService.test_proactively_suggest_route_suggestion_generated = async_test(TestPersonalizedRoutingService.test_proactively_suggest_route_suggestion_generated)
TestPersonalizedRoutingService.test_proactively_suggest_route_no_common_destination = async_test(TestPersonalizedRoutingService.test_proactively_suggest_route_no_common_destination)


if __name__ == '__main__':
    unittest.main()
