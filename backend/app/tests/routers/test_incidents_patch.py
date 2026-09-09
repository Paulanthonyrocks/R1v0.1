import unittest
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.dependency_injection import get_current_admin
from app.services.services import get_incident_manager
from app.models.user import User

mock_manager = MagicMock()
mock_db = MagicMock()
mock_manager._db_manager = mock_db


async def override_get_current_admin():
    return User(username="admin", email="admin@example.com", full_name="Admin", role="admin")


async def override_get_incident_manager():
    return mock_manager


def _incident_row(**over):
    row = {
        "id": "inc1",
        "feed_id": "F1",
        "type": "CONGESTION",
        "severity": "HIGH",
        "description": "congestion",
        "latitude": 0.0,
        "longitude": 0.0,
        "snapshot_path": None,
        "status": "REPORTED",
        "timestamp": 1700000000.0,
        "created_at": "2026-09-01T00:00:00+00:00",
        "updated_at": "2026-09-01T00:00:00+00:00",
        "assigned_to": None,
        "resolution_notes": None,
    }
    row.update(over)
    return row


class TestIncidentsPatch(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[get_current_admin] = override_get_current_admin
        app.dependency_overrides[get_incident_manager] = override_get_incident_manager
        self.client = TestClient(app)
        mock_db.reset_mock()
        mock_db.update_incident = AsyncMock(return_value=True)

    def test_patch_unknown_id_404(self):
        mock_db.get_incident_by_id = AsyncMock(return_value=None)
        response = self.client.patch("/api/v1/incidents/nope", json={"description": "x"})
        self.assertEqual(response.status_code, 404)
        mock_db.update_incident.assert_not_awaited()

    def test_patch_empty_update_returns_existing(self):
        mock_db.get_incident_by_id = AsyncMock(return_value=_incident_row())
        response = self.client.patch("/api/v1/incidents/inc1", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], "inc1")
        mock_db.update_incident.assert_not_awaited()

    def test_patch_success_returns_updated(self):
        mock_db.get_incident_by_id = AsyncMock(
            side_effect=[_incident_row(), _incident_row(description="new desc")]
        )
        mock_db.update_incident = AsyncMock(return_value=True)
        response = self.client.patch("/api/v1/incidents/inc1", json={"description": "new desc"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["description"], "new desc")
        mock_db.update_incident.assert_awaited_once()

    def test_patch_update_failure_500(self):
        mock_db.get_incident_by_id = AsyncMock(return_value=_incident_row())
        mock_db.update_incident = AsyncMock(return_value=False)
        response = self.client.patch("/api/v1/incidents/inc1", json={"description": "x"})
        self.assertEqual(response.status_code, 500)

    def tearDown(self):
        app.dependency_overrides = {}


if __name__ == "__main__":
    unittest.main()
