import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient
from datetime import datetime, timezone

from app.main import app # Assuming main app instance is here
from app.dependencies import get_db, get_current_active_user, get_connection_manager
from app.utils.utils import DatabaseManager
from app.websocket.connection_manager import ConnectionManager
from app.models.alerts import Alert as AlertModel # For response model validation
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum, AlertStatusUpdatePayload

# --- Mock Dependencies ---
mock_db_manager = MagicMock(spec=DatabaseManager)
mock_connection_manager = AsyncMock(spec=ConnectionManager) # Use AsyncMock for async methods

async def override_get_db():
    return mock_db_manager

async def override_get_current_active_user():
    return {"username": "testuser", "uid": "testuid123", "role": "admin"}

async def override_get_connection_manager():
    return mock_connection_manager

class TestAlertsRouter(unittest.TestCase):

    def setUp(self):
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_active_user] = override_get_current_active_user
        app.dependency_overrides[get_connection_manager] = override_get_connection_manager

        self.client = TestClient(app)

        # Reset mocks before each test
        mock_db_manager.reset_mock()
        mock_connection_manager.reset_mock()

    def test_delete_alert_success(self):
        alert_id_to_delete = 1
        mock_db_manager.delete_alert = AsyncMock(return_value=True) # Simulate successful deletion

        response = self.client.delete(f"/api/v1/alerts/{alert_id_to_delete}")

        self.assertEqual(response.status_code, 204)
        mock_db_manager.delete_alert.assert_awaited_once_with(alert_id_to_delete)

        # Verify WebSocket broadcast
        mock_connection_manager.broadcast_message_model.assert_awaited_once()
        args, _ = mock_connection_manager.broadcast_message_model.call_args
        sent_message: WebSocketMessage = args[0]

        self.assertEqual(sent_message.event_type, WebSocketMessageTypeEnum.ALERT_STATUS_UPDATE)
        self.assertIsInstance(sent_message.payload, AlertStatusUpdatePayload)
        self.assertEqual(sent_message.payload.alert_id, alert_id_to_delete)
        self.assertEqual(sent_message.payload.status, "dismissed")

    def test_delete_alert_not_found(self):
        alert_id_to_delete = 999
        mock_db_manager.delete_alert = AsyncMock(return_value=False) # Simulate alert not found

        response = self.client.delete(f"/api/v1/alerts/{alert_id_to_delete}")

        self.assertEqual(response.status_code, 404)
        mock_db_manager.delete_alert.assert_awaited_once_with(alert_id_to_delete)
        mock_connection_manager.broadcast_message_model.assert_not_awaited() # No broadcast on failure

    def test_acknowledge_alert_success(self):
        alert_id_to_ack = 1
        request_payload = {"acknowledged": True}

        # Mock DB methods
        mock_db_manager.acknowledge_alert = AsyncMock(return_value=True)
        updated_alert_data_from_db = {
            "id": alert_id_to_ack, "timestamp": datetime.now(timezone.utc).timestamp(),
            "severity": "WARNING", "feed_id": "feed123",
            "message": "Test alert acknowledged", "details": "{}", "acknowledged": True
        }
        mock_db_manager.get_alert_by_id = AsyncMock(return_value=updated_alert_data_from_db)

        response = self.client.patch(f"/api/v1/alerts/{alert_id_to_ack}/acknowledge", json=request_payload)

        self.assertEqual(response.status_code, 200)
        mock_db_manager.acknowledge_alert.assert_awaited_once_with(alert_id=alert_id_to_ack, acknowledge=True)
        mock_db_manager.get_alert_by_id.assert_awaited_once_with(alert_id_to_ack)

        response_data = response.json()
        self.assertEqual(response_data["id"], alert_id_to_ack)
        self.assertTrue(response_data["acknowledged"])

        # Verify WebSocket broadcast
        mock_connection_manager.broadcast_message_model.assert_awaited_once()
        args, _ = mock_connection_manager.broadcast_message_model.call_args
        sent_message: WebSocketMessage = args[0]

        self.assertEqual(sent_message.event_type, WebSocketMessageTypeEnum.ALERT_STATUS_UPDATE)
        self.assertIsInstance(sent_message.payload, AlertStatusUpdatePayload)
        self.assertEqual(sent_message.payload.alert_id, alert_id_to_ack)
        self.assertEqual(sent_message.payload.status, "acknowledged")

    def test_unacknowledge_alert_success(self):
        alert_id_to_unack = 2
        request_payload = {"acknowledged": False}

        mock_db_manager.acknowledge_alert = AsyncMock(return_value=True)
        updated_alert_data_from_db = {
            "id": alert_id_to_unack, "timestamp": datetime.now(timezone.utc).timestamp(),
            "severity": "CRITICAL", "feed_id": "feed456",
            "message": "Test alert unacknowledged", "details": "{}", "acknowledged": False
        }
        mock_db_manager.get_alert_by_id = AsyncMock(return_value=updated_alert_data_from_db)

        response = self.client.patch(f"/api/v1/alerts/{alert_id_to_unack}/acknowledge", json=request_payload)

        self.assertEqual(response.status_code, 200)
        mock_db_manager.acknowledge_alert.assert_awaited_once_with(alert_id=alert_id_to_unack, acknowledge=False)
        response_data = response.json()
        self.assertFalse(response_data["acknowledged"])

        # Verify WebSocket broadcast
        mock_connection_manager.broadcast_message_model.assert_awaited_once()
        args, _ = mock_connection_manager.broadcast_message_model.call_args
        sent_message: WebSocketMessage = args[0]
        self.assertEqual(sent_message.payload.status, "unacknowledged")


    def test_acknowledge_alert_not_found(self):
        alert_id_to_ack = 999
        request_payload = {"acknowledged": True}
        mock_db_manager.acknowledge_alert = AsyncMock(return_value=False) # Simulate alert not found for acknowledge

        response = self.client.patch(f"/api/v1/alerts/{alert_id_to_ack}/acknowledge", json=request_payload)

        self.assertEqual(response.status_code, 404)
        mock_db_manager.acknowledge_alert.assert_awaited_once_with(alert_id=alert_id_to_ack, acknowledge=True)
        mock_db_manager.get_alert_by_id.assert_not_awaited() # Should not be called if ack fails
        mock_connection_manager.broadcast_message_model.assert_not_awaited()

    def tearDown(self):
        # Clear dependency overrides after tests
        app.dependency_overrides = {}

if __name__ == '__main__':
    unittest.main()
