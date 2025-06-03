import unittest
from unittest.mock import MagicMock, patch, AsyncMock # Added AsyncMock
import asyncio
from datetime import datetime, timezone, timedelta # Added timedelta

from app.services.analytics_service import AnalyticsService
from app.ml.data_cache import TrafficDataCache
from app.websocket.connection_manager import ConnectionManager
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum, NodeCongestionUpdatePayload # Added WS Models

class TestAnalyticsService(unittest.TestCase):

    def setUp(self):
        self.mock_config = {
            "analytics_service": {
                "data_retention_hours": 24,
                "node_congestion_broadcast_interval": 0.1 # Short interval for testing loop
            }
        }
        # ConnectionManager methods like broadcast_message_model are async
        self.mock_connection_manager = AsyncMock(spec=ConnectionManager)

        self.analytics_service = AnalyticsService(
            config=self.mock_config,
            connection_manager=self.mock_connection_manager
        )
        # Mock _data_cache on the instance
        self.analytics_service._data_cache = MagicMock(spec=TrafficDataCache)
        # Ensure data_cache.location_data.keys().__len__() works for KPI summary test
        self.analytics_service._data_cache.location_data = MagicMock()
        self.analytics_service._data_cache.location_data.keys().__len__.return_value = 3 # Mock active feeds


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

    def test_get_current_system_kpis_summary(self):
        # This method is synchronous and currently returns hardcoded data
        # We also mocked the __len__ for active_feeds_count in setUp
        expected_kpis = {
            "overall_congestion_level": "MEDIUM",
            "total_vehicle_flow_rate_hourly": 1575,
            "active_feeds_count": 3, # From mock in setUp
            "average_incident_response_time_minutes": None,
            "system_stability_indicator": "STABLE"
        }
        kpis = self.analytics_service.get_current_system_kpis_summary()
        self.assertEqual(kpis, expected_kpis)

    def test_get_critical_alert_summary(self):
        # This method is synchronous and currently returns hardcoded data
        expected_summary = {
            "critical_alert_count": 2,
            "most_common_critical_types": ["major_collision", "stopped_vehicle_on_highway"],
            "recent_critical_locations": ["Main St & 1st Ave", "HWY 101 Exit 4B"],
            "oldest_unresolved_critical_alert_age_hours": 3.5
        }
        summary = self.analytics_service.get_critical_alert_summary()
        self.assertEqual(summary, expected_summary)

    async def test_broadcast_node_congestion_updates_direct_call(self):
        mock_node_data_list = [
            {'id': 'node1', 'name': 'Node 1', 'latitude': 1.0, 'longitude': 1.0,
             'congestion_score': 50.0, 'vehicle_count': 10, 'average_speed': 30.0,
             'timestamp': datetime.now(timezone.utc)}
        ]
        # Mock the async method get_all_location_congestion_data
        self.analytics_service.get_all_location_congestion_data = AsyncMock(return_value=mock_node_data_list)

        await self.analytics_service._broadcast_node_congestion_updates()

        self.analytics_service.get_all_location_congestion_data.assert_awaited_once()
        self.mock_connection_manager.broadcast_message_model.assert_awaited_once()

        # Check the call arguments for broadcast_message_model
        args, kwargs = self.mock_connection_manager.broadcast_message_model.call_args
        sent_message: WebSocketMessage = args[0]
        self.assertEqual(sent_message.event_type, WebSocketMessageTypeEnum.NODE_CONGESTION_UPDATE)
        self.assertIsInstance(sent_message.payload, NodeCongestionUpdatePayload)
        self.assertEqual(len(sent_message.payload.nodes), 1)
        # Pydantic would have converted dict to NodeCongestionUpdateData instance if models are compatible
        # Here we check if the data passed to NodeCongestionUpdatePayload matches our mock
        self.assertEqual(sent_message.payload.nodes[0]['id'], mock_node_data_list[0]['id'])
        self.assertEqual(kwargs.get('specific_topic'), "node_congestion")

    async def test_broadcast_node_congestion_updates_no_data(self):
        self.analytics_service.get_all_location_congestion_data = AsyncMock(return_value=[])

        await self.analytics_service._broadcast_node_congestion_updates()

        self.analytics_service.get_all_location_congestion_data.assert_awaited_once()
        self.mock_connection_manager.broadcast_message_model.assert_not_awaited()

    async def test_node_congestion_broadcast_loop(self):
        mock_node_data_list = [
            {'id': 'node1', 'name': 'Node 1', 'latitude': 1.0, 'longitude': 1.0,
             'congestion_score': 50.0, 'vehicle_count': 10, 'average_speed': 30.0,
             'timestamp': datetime.now(timezone.utc)}
        ]
        self.analytics_service.get_all_location_congestion_data = AsyncMock(return_value=mock_node_data_list)

        await self.analytics_service.start_background_tasks()

        # Allow the loop to run a couple of times
        # Interval is 0.1s, so sleep for 0.25s should get at least two calls
        await asyncio.sleep(0.25)

        self.assertTrue(self.mock_connection_manager.broadcast_message_model.call_count >= 2)

        # Verify one of the calls (e.g., the first one)
        args, kwargs = self.mock_connection_manager.broadcast_message_model.call_args_list[0]
        sent_message: WebSocketMessage = args[0]
        self.assertEqual(sent_message.event_type, WebSocketMessageTypeEnum.NODE_CONGESTION_UPDATE)
        self.assertEqual(sent_message.payload.nodes[0]['id'], 'node1')

        await self.analytics_service.stop_background_tasks()
        self.assertIsNone(self.analytics_service._node_congestion_task)


# Helper to run async tests with unittest
def async_test(f):
    def wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop_policy().new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(f(*args, **kwargs))
        loop.close()
        return result
    return wrapper

# Apply the wrapper to all async test methods
TestAnalyticsService.test_get_all_location_congestion_data_success = async_test(TestAnalyticsService.test_get_all_location_congestion_data_success)
TestAnalyticsService.test_get_all_location_congestion_data_empty_cache = async_test(TestAnalyticsService.test_get_all_location_congestion_data_empty_cache)
TestAnalyticsService.test_get_all_location_congestion_data_missing_lat_lon_in_summary = async_test(TestAnalyticsService.test_get_all_location_congestion_data_missing_lat_lon_in_summary)
TestAnalyticsService.test_broadcast_node_congestion_updates_direct_call = async_test(TestAnalyticsService.test_broadcast_node_congestion_updates_direct_call)
TestAnalyticsService.test_broadcast_node_congestion_updates_no_data = async_test(TestAnalyticsService.test_broadcast_node_congestion_updates_no_data)
TestAnalyticsService.test_node_congestion_broadcast_loop = async_test(TestAnalyticsService.test_node_congestion_broadcast_loop)


if __name__ == '__main__':
    unittest.main()
