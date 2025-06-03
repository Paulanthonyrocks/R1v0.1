import unittest
from unittest.mock import MagicMock, patch
import asyncio
from datetime import datetime, timezone

from app.services.analytics_service import AnalyticsService
from app.ml.data_cache import TrafficDataCache # For type hinting if needed, though mock will be used
from app.websocket.connection_manager import ConnectionManager # For instantiating AnalyticsService

class TestAnalyticsService(unittest.TestCase):

    def setUp(self):
        # Mock dependencies for AnalyticsService
        self.mock_config = {
            "analytics_service": {
                "data_retention_hours": 24
            }
            # Add other necessary config for TrafficPredictor if its methods are called
        }
        self.mock_connection_manager = MagicMock(spec=ConnectionManager)

        # We will mock _data_cache directly on the instance or use patch.object
        self.analytics_service = AnalyticsService(
            config=self.mock_config,
            connection_manager=self.mock_connection_manager
        )
        # Replace the real _data_cache with a mock for testing get_all_location_congestion_data
        self.analytics_service._data_cache = MagicMock(spec=TrafficDataCache)

    async def test_get_all_location_congestion_data_success(self):
        mock_summaries = [
            {
                'id': '34.05,-118.25',
                'name': 'Node at (34.0500, -118.2500)',
                'latitude': 34.05,
                'longitude': -118.25,
                'timestamp': datetime.now(timezone.utc),
                'vehicle_count': 100,
                'average_speed': 45.5,
                'congestion_score': 30.2,
                'extra_field_from_cache': 'test_value'
            },
            {
                'id': '40.71,-74.00',
                'name': 'Node at (40.7100, -74.0000)',
                'latitude': 40.71,
                'longitude': -74.00,
                'timestamp': datetime.now(timezone.utc) - timedelta(minutes=5),
                'vehicle_count': None, # Test handling of None values
                'average_speed': 60.0,
                'congestion_score': None, # Test handling of None values
            }
        ]
        self.analytics_service._data_cache.get_all_location_summaries.return_value = mock_summaries

        result = await self.analytics_service.get_all_location_congestion_data()

        self.assertEqual(len(result), 2)

        # Check first item (fully populated)
        self.assertEqual(result[0]['id'], mock_summaries[0]['id'])
        self.assertEqual(result[0]['name'], mock_summaries[0]['name'])
        self.assertEqual(result[0]['latitude'], mock_summaries[0]['latitude'])
        self.assertEqual(result[0]['longitude'], mock_summaries[0]['longitude'])
        self.assertEqual(result[0]['congestion_score'], mock_summaries[0]['congestion_score'])
        self.assertEqual(result[0]['vehicle_count'], mock_summaries[0]['vehicle_count'])
        self.assertEqual(result[0]['average_speed'], mock_summaries[0]['average_speed'])
        self.assertEqual(result[0]['timestamp'], mock_summaries[0]['timestamp']) # Timestamp should be passed through

        # Check second item (with None values)
        self.assertEqual(result[1]['id'], mock_summaries[1]['id'])
        self.assertEqual(result[1]['name'], mock_summaries[1]['name'])
        self.assertEqual(result[1]['congestion_score'], mock_summaries[1]['congestion_score']) # Should be None
        self.assertEqual(result[1]['vehicle_count'], mock_summaries[1]['vehicle_count']) # Should be None
        self.assertEqual(result[1]['average_speed'], mock_summaries[1]['average_speed'])

        # Verify the mock was called
        self.analytics_service._data_cache.get_all_location_summaries.assert_called_once()

    async def test_get_all_location_congestion_data_empty_cache(self):
        self.analytics_service._data_cache.get_all_location_summaries.return_value = []

        result = await self.analytics_service.get_all_location_congestion_data()

        self.assertEqual(result, [])
        self.analytics_service._data_cache.get_all_location_summaries.assert_called_once()

    async def test_get_all_location_congestion_data_missing_lat_lon_in_summary(self):
        # Test the filtering for entries missing lat/lon
        mock_summaries = [
            {
                'id': 'valid_node',
                'name': 'Valid Node',
                'latitude': 34.05,
                'longitude': -118.25,
                'timestamp': datetime.now(timezone.utc),
                'congestion_score': 30.0
            },
            {
                'id': 'invalid_node_no_lat',
                'name': 'Invalid Node No Lat',
                'latitude': None, # Missing latitude
                'longitude': -74.00,
                'timestamp': datetime.now(timezone.utc),
                'congestion_score': 20.0
            },
             {
                'id': 'invalid_node_no_lon',
                'name': 'Invalid Node No Lon',
                'latitude': 40.71,
                'longitude': None, # Missing longitude
                'timestamp': datetime.now(timezone.utc),
                'congestion_score': 25.0
            }
        ]
        self.analytics_service._data_cache.get_all_location_summaries.return_value = mock_summaries

        result = await self.analytics_service.get_all_location_congestion_data()

        self.assertEqual(len(result), 1) # Only one valid node should remain
        self.assertEqual(result[0]['id'], 'valid_node')
        self.analytics_service._data_cache.get_all_location_summaries.assert_called_once()

# Helper to run async tests with unittest
def async_test(f):
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper

TestAnalyticsService.test_get_all_location_congestion_data_success = async_test(TestAnalyticsService.test_get_all_location_congestion_data_success)
TestAnalyticsService.test_get_all_location_congestion_data_empty_cache = async_test(TestAnalyticsService.test_get_all_location_congestion_data_empty_cache)
TestAnalyticsService.test_get_all_location_congestion_data_missing_lat_lon_in_summary = async_test(TestAnalyticsService.test_get_all_location_congestion_data_missing_lat_lon_in_summary)


if __name__ == '__main__':
    unittest.main()
