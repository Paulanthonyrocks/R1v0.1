import unittest
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.dependency_injection import (
    get_current_active_user,
    get_traffic_signal_service,
)
from app.models.user import User
from app.models.signals import SignalControlStatusEnum
from app.services.traffic_signal_service import TrafficSignalControlError

mock_service = MagicMock()


async def override_get_current_active_user():
    return User(username="testuser", email="test@example.com", full_name="Test User", role="admin")


async def override_get_traffic_signal_service():
    return mock_service


class TestSignalsRouter(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[get_current_active_user] = override_get_current_active_user
        app.dependency_overrides[get_traffic_signal_service] = override_get_traffic_signal_service
        self.client = TestClient(app)
        mock_service.reset_mock()

    def test_get_signals_empty_when_unconfigured(self):
        mock_service.get_all_signal_states = AsyncMock(return_value=[])
        response = self.client.get("/api/v1/signals/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_get_signals_503_on_control_error(self):
        mock_service.get_all_signal_states = AsyncMock(
            side_effect=TrafficSignalControlError("No traffic signal controller configured")
        )
        response = self.client.get("/api/v1/signals/")
        self.assertEqual(response.status_code, 503)

    def test_set_phase_invalid_phase_400(self):
        response = self.client.post("/api/v1/signals/s1/set_phase", params={"phase": "purple"})
        self.assertEqual(response.status_code, 400)
        mock_service.set_signal_phase.assert_not_awaited()

    def test_set_phase_accepted_200(self):
        accepted = MagicMock()
        accepted.status = SignalControlStatusEnum.ACCEPTED
        mock_service.set_signal_phase = AsyncMock(return_value=accepted)
        response = self.client.post("/api/v1/signals/s1/set_phase", params={"phase": "green"})
        self.assertEqual(response.status_code, 200)
        mock_service.set_signal_phase.assert_awaited_once_with("s1", "green")

    def test_set_phase_unconfigured_503(self):
        mock_service.set_signal_phase = AsyncMock(
            side_effect=TrafficSignalControlError("No traffic signal controller configured")
        )
        response = self.client.post("/api/v1/signals/s1/set_phase", params={"phase": "red"})
        self.assertEqual(response.status_code, 503)

    def test_set_phase_service_refusal_500(self):
        refused = MagicMock()
        refused.status = SignalControlStatusEnum.FAILED
        mock_service.set_signal_phase = AsyncMock(return_value=refused)
        response = self.client.post("/api/v1/signals/s1/set_phase", params={"phase": "red"})
        self.assertEqual(response.status_code, 500)

    def tearDown(self):
        app.dependency_overrides = {}


if __name__ == "__main__":
    unittest.main()
