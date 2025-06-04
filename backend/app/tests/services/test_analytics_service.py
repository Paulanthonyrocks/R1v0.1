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


# --- New Test Class for DB-dependent tests for AnalyticsService ---
import uuid # For generating unique IDs
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker as sqlalchemy_sessionmaker # Alias to avoid conflict
from contextlib import asynccontextmanager # For async session context manager mock

# Import Base and models needed for table creation and direct querying in tests
from app.services.analytics_service import PredictionLogBase, PredictionLogModel
from app.models.websocket import UserSpecificConditionAlert # Updated for testing send_user_specific_alert
from app.models.traffic import LocationModel, IncidentReport, IncidentTypeEnum, IncidentSeverityEnum # For test_correlate...
from app.services.analytics_service import AnalyticsService # Re-import for clarity, though already available

# Helper data for tests
USER_ID_ANALYTICS_TEST_1 = "analytics_user_1"
PREDICTION_ID_ANALYTICS_TEST_1 = str(uuid.uuid4())

# Mock for ActiveWebSocketConnection if ConnectionManager's structure is complex
class MockActiveWebSocketConnection:
    def __init__(self, client_id: str, user_info: Optional[Dict[str, Any]] = None):
        self.client_id = client_id
        self.user_info = user_info

    async def send_text(self, data: str):
        pass # Mock send

    async def send_json(self, data: dict, mode: str = "text"):
        pass

    async def send(self, msg): # For send_personal_message_model if it uses 'send'
        pass


class TestAnalyticsServiceWithDb(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        PredictionLogBase.metadata.create_all(self.engine)

        self.TestSessionLocal = sqlalchemy_sessionmaker(autocommit=False, autoflush=False, bind=self.engine, class_=AsyncMock) # Use AsyncMock for session for now

        # Mock DatabaseManager more carefully for async session usage
        self.mock_db_manager_for_new_tests = MagicMock(spec=DatabaseManager)
        self.mock_db_manager_for_new_tests.engine = self.engine # Allow table creation check

        @asynccontextmanager
        async def get_mock_session():
            # Create a real session for test DB operations but from an async-compatible maker
            real_session_maker = sqlalchemy_sessionmaker(autocommit=False, autoflush=False, bind=self.engine, class_=Session) # Use standard Session for actual ops
            db = real_session_maker()
            try:
                yield db
                await db.commit() # This commit might be an issue if tests expect to control it.
                                 # Tests should ideally manage their own commits or use a transactional approach.
                                 # For simplicity, let's assume test methods will manage their data carefully.
                                 # Or, remove this commit and let tests handle it.
                                 # Let's remove it: tests will commit via service or helper.
            except Exception:
                await db.rollback() # If service method fails, it should rollback.
                raise
            finally:
                await db.close() # Close after use.

        # Re-patching get_session to use a real session for the test DB
        # This is tricky with AsyncMock. Let's use a MagicMock for get_session for now
        # and have test methods create their own sessions if direct DB access is needed for setup/assert.
        # The service itself will use this mock.

        # For the service calls, we want it to use a session that can be awaited.
        # The actual DB operations in helpers will use a direct session.
        self.mock_db_manager_for_new_tests.get_session = MagicMock(return_value=get_mock_session())


        self.mock_connection_manager_for_new_tests = MagicMock(spec=ConnectionManager)
        self.mock_traffic_predictor_for_new_tests = MagicMock()
        self.mock_data_cache_for_new_tests = MagicMock()

        self.analytics_service_db_test = AnalyticsService(
            config={"analytics_service": {}}, # Minimal config
            connection_manager=self.mock_connection_manager_for_new_tests,
            database_manager=self.mock_db_manager_for_new_tests
        )
        # Override the service's _db_manager.get_session to use our mock that provides a real session
        # This is essential for service methods to interact with the test DB.
        # The mock setup for get_session needs to be an async context manager.

        # Re-think: The service uses "async with self._db_manager.get_session() as session:"
        # So, self._db_manager.get_session needs to BE an async context manager.

        class AsyncContextManagerSession:
            def __init__(self, engine_):
                self.engine_ = engine_
                self.real_session_maker_ = sqlalchemy_sessionmaker(autocommit=False, autoflush=False, bind=self.engine_, class_=Session)
            async def __aenter__(self):
                self.db_ = self.real_session_maker_()
                return self.db_
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                if exc_type: # If an exception occurred
                    await self.db_.rollback() # Use await if Session is async, else db.rollback()
                else:
                    await self.db_.commit() # Use await if Session is async, else db.commit()
                await self.db_.close() # Use await if Session is async, else db.close()

        self.mock_db_manager_for_new_tests.get_session = lambda: AsyncContextManagerSession(self.engine)


    async def asyncTearDown(self):
        async with self.engine.connect() as conn:
             await conn.run_sync(PredictionLogBase.metadata.drop_all)
        await self.engine.dispose()

    async def _add_prediction_log(self, session: Session, **kwargs) -> PredictionLogModel:
        entry_data = {
            "id": str(uuid.uuid4()),
            "prediction_made_at": datetime.utcnow() - timedelta(days=1),
            "location_latitude": 34.0522,
            "location_longitude": -118.2437,
            "predicted_event_start_time": datetime.utcnow() - timedelta(hours=12),
            "predicted_event_end_time": datetime.utcnow() - timedelta(hours=11),
            "prediction_type": "incident_likelihood",
            "predicted_value": {"likelihood_score_percent": 80},
            "source_of_prediction": "TestScheduler",
            "outcome_verified": False,
            **kwargs
        }
        entry = PredictionLogModel(**entry_data)
        session.add(entry)
        await session.commit() # Assuming service methods using this session will commit.
                              # For direct setup, commit here.
        return entry

    # 1. Test PredictionLogModel CRUD (simplified to create/query for now)
    async def test_prediction_log_model_crud(self):
        async with self.mock_db_manager_for_new_tests.get_session() as session:
            created_entry = await self._add_prediction_log(session, id=PREDICTION_ID_ANALYTICS_TEST_1, outcome_verified=True, actual_outcome_type="test_event")
            retrieved_entry = await session.get(PredictionLogModel, PREDICTION_ID_ANALYTICS_TEST_1)
            self.assertIsNotNone(retrieved_entry)
            self.assertEqual(retrieved_entry.id, PREDICTION_ID_ANALYTICS_TEST_1)
            self.assertTrue(retrieved_entry.outcome_verified)
            self.assertEqual(retrieved_entry.actual_outcome_type, "test_event")

    # 2. Test record_prediction_log
    async def test_record_prediction_log_success(self):
        log_data = {
            "location_latitude": 34.0, "location_longitude": -118.0,
            "predicted_event_start_time": datetime.utcnow() + timedelta(hours=1),
            "predicted_event_end_time": datetime.utcnow() + timedelta(hours=2),
            "prediction_type": "congestion_spike",
            "predicted_value": {"intensity": "high"},
            "source_of_prediction": "TestSource"
        }
        log_id = await self.analytics_service_db_test.record_prediction_log(log_data)
        self.assertIsNotNone(log_id)
        async with self.mock_db_manager_for_new_tests.get_session() as session:
            entry = await session.get(PredictionLogModel, log_id)
            self.assertIsNotNone(entry)
            self.assertEqual(entry.prediction_type, "congestion_spike")
            self.assertEqual(entry.predicted_value["intensity"], "high")

    async def test_record_prediction_log_missing_data(self):
        log_data = {"location_latitude": 34.0} # Missing many required fields
        log_id = await self.analytics_service_db_test.record_prediction_log(log_data)
        self.assertIsNone(log_id) # Should fail instantiation or DB constraint

    # 3. Test correlate_predictions_with_outcomes_no_incidents
    async def test_correlate_predictions_with_outcomes_no_incidents(self):
        async with self.mock_db_manager_for_new_tests.get_session() as session:
            pred1 = await self._add_prediction_log(session, outcome_verified=False, predicted_event_end_time=datetime.utcnow() - timedelta(hours=3)) # Window passed

        # Patch _fetch_relevant_incidents to return empty list
        self.analytics_service_db_test._fetch_relevant_incidents = AsyncMock(return_value=[])

        await self.analytics_service_db_test.correlate_predictions_with_outcomes()

        async with self.mock_db_manager_for_new_tests.get_session() as session:
            updated_pred1 = await session.get(PredictionLogModel, pred1.id)
            self.assertTrue(updated_pred1.outcome_verified)
            self.assertEqual(updated_pred1.actual_outcome_type, "no_event_detected")

    # 4. Test correlate_predictions_with_outcomes_with_incidents
    async def test_correlate_predictions_with_outcomes_with_incidents(self):
        async with self.mock_db_manager_for_new_tests.get_session() as session:
            pred1 = await self._add_prediction_log(session, outcome_verified=False, predicted_event_start_time=datetime.utcnow()-timedelta(hours=2), predicted_event_end_time=datetime.utcnow()-timedelta(hours=1))

        mock_incident = IncidentReport(
            id="incident1", timestamp=datetime.utcnow()-timedelta(hours=1, minutes=30), type=IncidentTypeEnum.ACCIDENT, severity=IncidentSeverityEnum.HIGH,
            location=LocationModel(latitude=34.0522, longitude=-118.2437), description="Test accident"
        )
        self.analytics_service_db_test._fetch_relevant_incidents = AsyncMock(return_value=[mock_incident])

        await self.analytics_service_db_test.correlate_predictions_with_outcomes()

        async with self.mock_db_manager_for_new_tests.get_session() as session:
            updated_pred1 = await session.get(PredictionLogModel, pred1.id)
            self.assertTrue(updated_pred1.outcome_verified)
            self.assertEqual(updated_pred1.actual_outcome_type, "incident_occurred")
            self.assertIsNotNone(updated_pred1.actual_outcome_details)
            self.assertEqual(len(updated_pred1.actual_outcome_details["incidents"]), 1)
            self.assertEqual(updated_pred1.actual_outcome_details["incidents"][0]["id"], "incident1")

    # 5. Test correlate_predictions_with_outcomes_window_not_passed
    async def test_correlate_predictions_with_outcomes_window_not_passed(self):
        async with self.mock_db_manager_for_new_tests.get_session() as session:
             # Prediction window ends in future, so correlation window also in future
            pred1 = await self._add_prediction_log(session, outcome_verified=False, predicted_event_end_time=datetime.utcnow() + timedelta(hours=3))

        self.analytics_service_db_test._fetch_relevant_incidents = AsyncMock(return_value=[])
        await self.analytics_service_db_test.correlate_predictions_with_outcomes()

        async with self.mock_db_manager_for_new_tests.get_session() as session:
            updated_pred1 = await session.get(PredictionLogModel, pred1.id)
            self.assertFalse(updated_pred1.outcome_verified) # Should not be verified yet

    # 6. Test get_prediction_outcome_summary_calculates_correctly
    async def test_get_prediction_outcome_summary_calculates_correctly(self):
        async with self.mock_db_manager_for_new_tests.get_session() as session:
            # Verified, incident occurred
            await self._add_prediction_log(session, outcome_verified=True, actual_outcome_type="incident_occurred", source_of_prediction="A")
            await self._add_prediction_log(session, outcome_verified=True, actual_outcome_type="incident_occurred", source_of_prediction="A")
            # Verified, no event
            await self._add_prediction_log(session, outcome_verified=True, actual_outcome_type="no_event_detected", source_of_prediction="A")
            # Unverified
            await self._add_prediction_log(session, outcome_verified=False, source_of_prediction="A")
            # Different source
            await self._add_prediction_log(session, outcome_verified=True, actual_outcome_type="incident_occurred", source_of_prediction="B")

        summary_A = await self.analytics_service_db_test.get_prediction_outcome_summary(source_of_prediction="A")
        self.assertEqual(summary_A["total_verified_predictions"], 3)
        self.assertEqual(summary_A["outcomes"].get("incident_occurred", 0), 2)
        self.assertEqual(summary_A["outcomes"].get("no_event_detected", 0), 1)
        self.assertAlmostEqual(summary_A["accuracy_metrics"]["incident_hit_rate"], 2/3)

        summary_loc = await self.analytics_service_db_test.get_prediction_outcome_summary(location_latitude=34.0522, location_longitude=-118.2437, location_radius_km=1)
        # All 4 verified predictions are at the default lat/lon
        self.assertEqual(summary_loc["total_verified_predictions"], 4)
        self.assertEqual(summary_loc["outcomes"].get("incident_occurred",0),3)


    # 7. Test get_prediction_outcome_summary_no_data
    async def test_get_prediction_outcome_summary_no_data(self):
        summary = await self.analytics_service_db_test.get_prediction_outcome_summary(source_of_prediction="NonExistentSource")
        self.assertEqual(summary["total_verified_predictions"], 0)
        self.assertEqual(len(summary["outcomes"]), 0)
        self.assertEqual(summary["accuracy_metrics"]["incident_hit_rate"], 0.0)

    # 8. Test send_user_specific_alert (refactored from send_user_specific_notification)
    async def test_send_user_specific_alert(self):
        user_to_notify = "user_for_notification"
        mock_conn1 = MockActiveWebSocketConnection(client_id="client1", user_info={"uid": user_to_notify})
        mock_conn2 = MockActiveWebSocketConnection(client_id="client2", user_info={"uid": "other_user"})
        mock_conn3 = MockActiveWebSocketConnection(client_id="client3", user_info={"uid": user_to_notify})

        self.mock_connection_manager_for_new_tests.active_connections = {
            "client1": mock_conn1, "client2": mock_conn2, "client3": mock_conn3
        }
        self.mock_connection_manager_for_new_tests.send_personal_message_model = AsyncMock()

        sample_alert_payload = UserSpecificConditionAlert( # Updated model
            user_id=user_to_notify,
            alert_type="test_user_alert", # Renamed field
            title="Test Title for Alert",
            message="Test message for user alert.",
            severity="warning", # Added field
            route_context={"destination_name": "Downtown"} # Updated field
        )
        # Call the refactored method
        await self.analytics_service_db_test.send_user_specific_alert(
            user_id=user_to_notify,
            notification_model=sample_alert_payload
        )

        self.assertEqual(self.mock_connection_manager_for_new_tests.send_personal_message_model.call_count, 2)

        called_client_ids = {
            call_args[0][0] for call_args in self.mock_connection_manager_for_new_tests.send_personal_message_model.call_args_list
        }
        self.assertIn("client1", called_client_ids)
        self.assertIn("client3", called_client_ids)

        _, first_call_args, _ = self.mock_connection_manager_for_new_tests.send_personal_message_model.mock_calls[0]
        sent_ws_message: WebSocketMessage = first_call_args[1]
        self.assertEqual(sent_ws_message.event_type, WebSocketMessageTypeEnum.USER_SPECIFIC_ALERT) # Updated enum
        self.assertIsInstance(sent_ws_message.payload, UserSpecificConditionAlert) # Check instance type
        self.assertEqual(sent_ws_message.payload.alert_type, "test_user_alert")
        self.assertEqual(sent_ws_message.payload.severity, "warning")
        self.assertEqual(sent_ws_message.payload.route_context, {"destination_name": "Downtown"})

    async def test_send_user_specific_alert_no_active_connections(self): # Renamed test method
        user_to_notify = "user_with_no_connections"
        self.mock_connection_manager_for_new_tests.active_connections = {}
        self.mock_connection_manager_for_new_tests.send_personal_message_model = AsyncMock()

        sample_alert_payload = UserSpecificConditionAlert( # Updated model
            user_id=user_to_notify,
            alert_type="test_alert_no_connection",
            title="Test Title",
            message="Test message.",
            severity="info"
        )
        # Call the refactored method
        await self.analytics_service_db_test.send_user_specific_alert(
            user_id=user_to_notify,
            notification_model=sample_alert_payload
        )
        self.mock_connection_manager_for_new_tests.send_personal_message_model.assert_not_called()
