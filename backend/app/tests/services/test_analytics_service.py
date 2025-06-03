import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio
from datetime import datetime, timezone, timedelta
import numpy as np # For np.mean in tests

from app.services.analytics_service import AnalyticsService
from app.ml.data_cache import TrafficDataCache
from app.utils.utils import DatabaseManager # Import DatabaseManager
from app.websocket.connection_manager import ConnectionManager
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum, NodeCongestionUpdatePayload, GeneralNotification
from app.models.alerts import AlertSeverityEnum


class TestAnalyticsService(unittest.TestCase):

    def setUp(self):
        self.mock_config = {
            "analytics_service": {
                "data_retention_hours": 24,
                "node_congestion_broadcast_interval": 0.1
            }
        }
        self.mock_connection_manager = AsyncMock(spec=ConnectionManager)
        self.mock_db_manager = AsyncMock(spec=DatabaseManager) # Mock DatabaseManager

        self.analytics_service = AnalyticsService(
            config=self.mock_config,
            connection_manager=self.mock_connection_manager,
            database_manager=self.mock_db_manager # Pass mock_db_manager
        )
        self.analytics_service._data_cache = MagicMock(spec=TrafficDataCache)
        # No need to mock location_data.keys().__len__ if get_all_location_summaries is mocked properly


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

    def test_get_current_system_kpis_summary_with_data(self):
        mock_cache_summaries = [
            {'congestion_score': 20.0, 'average_speed': 60.0, 'vehicle_count': 50},
            {'congestion_score': 80.0, 'average_speed': 20.0, 'vehicle_count': 100},
            {'congestion_score': 50.0, 'average_speed': 40.0, 'vehicle_count': 70},
        ]
        self.analytics_service._data_cache.get_all_location_summaries.return_value = mock_cache_summaries

        kpis = self.analytics_service.get_current_system_kpis_summary()

        self.assertEqual(kpis['active_monitored_locations'], 3)
        self.assertEqual(kpis['total_vehicle_flow_estimate'], 220) # 50 + 100 + 70
        self.assertAlmostEqual(kpis['average_speed_kmh'], round(np.mean([60,20,40]),1) ) # (60+20+40)/3 = 40
        avg_congestion = np.mean([20,80,50]) # (20+80+50)/3 = 50
        self.assertEqual(kpis['overall_congestion_level'], "MEDIUM") # 50 is MEDIUM
        self.analytics_service._data_cache.get_all_location_summaries.assert_called_once()

    def test_get_current_system_kpis_summary_empty_cache(self):
        self.analytics_service._data_cache.get_all_location_summaries.return_value = []
        kpis = self.analytics_service.get_current_system_kpis_summary()
        expected_kpis = {
            "overall_congestion_level": "UNKNOWN",
            "average_speed_kmh": 0.0,
            "total_vehicle_flow_estimate": 0,
            "active_monitored_locations": 0,
            "system_stability_indicator": "NO_DATA"
        }
        self.assertEqual(kpis, expected_kpis)

    async def test_get_critical_alert_summary_with_alerts(self):
        self.mock_db_manager.count_alerts_filtered = AsyncMock(return_value=2)
        mock_alert_list = [
            {'message': 'Critical Incident A', 'details': json.dumps({'incident_type': 'Collision'})},
            {'message': 'High Severity Issue B', 'details': json.dumps({'incident_type': 'Obstruction'})},
        ]
        self.mock_db_manager.get_alerts_filtered = AsyncMock(return_value=mock_alert_list)

        summary = await self.analytics_service.get_critical_alert_summary()

        expected_filters = {
            "severity_in": [AlertSeverityEnum.CRITICAL.value, AlertSeverityEnum.ERROR.value],
            "acknowledged": False
        }
        self.mock_db_manager.count_alerts_filtered.assert_awaited_once_with(filters=expected_filters)
        self.mock_db_manager.get_alerts_filtered.assert_awaited_once_with(filters=expected_filters, limit=3, offset=0)

        self.assertEqual(summary['critical_unack_alert_count'], 2)
        self.assertIn("Collision: Critical Incident A", summary['recent_critical_types'])
        self.assertIn("Obstruction: High Severity Issue B", summary['recent_critical_types'])

    async def test_get_critical_alert_summary_no_alerts(self):
        self.mock_db_manager.count_alerts_filtered = AsyncMock(return_value=0)
        self.mock_db_manager.get_alerts_filtered = AsyncMock(return_value=[])

        summary = await self.analytics_service.get_critical_alert_summary()

        self.assertEqual(summary['critical_unack_alert_count'], 0)
        self.assertEqual(summary['recent_critical_types'], [])

    async def test_broadcast_operational_alert(self):
        title = "Test Operational Alert"
        message_text = "This is a test alert message from AgentCore."
        severity = "warning"

        await self.analytics_service.broadcast_operational_alert(title, message_text, severity)

        self.mock_connection_manager.broadcast_message_model.assert_awaited_once()
        args, kwargs = self.mock_connection_manager.broadcast_message_model.call_args

        sent_message: WebSocketMessage = args[0]
        self.assertEqual(sent_message.event_type, WebSocketMessageTypeEnum.GENERAL_NOTIFICATION)
        self.assertIsInstance(sent_message.payload, GeneralNotification)
        self.assertEqual(sent_message.payload.message_type, "operational_alert_by_agent")
        self.assertEqual(sent_message.payload.title, title)
        self.assertEqual(sent_message.payload.message, message_text)
        self.assertEqual(sent_message.payload.severity, severity)

        self.assertEqual(kwargs.get('specific_topic'), "operational_alerts")


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
TestAnalyticsService.test_get_critical_alert_summary_with_alerts = async_test(TestAnalyticsService.test_get_critical_alert_summary_with_alerts)
TestAnalyticsService.test_get_critical_alert_summary_no_alerts = async_test(TestAnalyticsService.test_get_critical_alert_summary_no_alerts)
TestAnalyticsService.test_broadcast_operational_alert = async_test(TestAnalyticsService.test_broadcast_operational_alert)
TestAnalyticsService.test_broadcast_node_congestion_updates_direct_call = async_test(TestAnalyticsService.test_broadcast_node_congestion_updates_direct_call)
TestAnalyticsService.test_broadcast_node_congestion_updates_no_data = async_test(TestAnalyticsService.test_broadcast_node_congestion_updates_no_data)
TestAnalyticsService.test_node_congestion_broadcast_loop = async_test(TestAnalyticsService.test_node_congestion_broadcast_loop)


if __name__ == '__main__':
    unittest.main()
