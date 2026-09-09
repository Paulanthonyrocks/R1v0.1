import unittest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.dependency_injection import (
    get_current_active_user,
    get_route_optimization_service,
)
from app.models.user import User

mock_ros = MagicMock()


async def override_get_current_active_user():
    return User(username="testuser", email="test@example.com", full_name="Test User", role="admin")


async def override_get_route_optimization_service():
    return mock_ros


def _optimize_body():
    return {
        "start_location": {"latitude": 34.0, "longitude": -118.0},
        "end_location": {"latitude": 34.1, "longitude": -118.1},
    }


class TestRoutingHonesty(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[get_current_active_user] = override_get_current_active_user
        app.dependency_overrides[get_route_optimization_service] = (
            override_get_route_optimization_service
        )
        self.client = TestClient(app)

    def test_optimize_501_without_road_network(self):
        response = self.client.post("/api/v1/routes/optimize", json=_optimize_body())
        self.assertEqual(response.status_code, 501)
        self.assertIn("road network", response.json()["detail"])

    def test_supported_areas_empty(self):
        response = self.client.get("/api/v1/routes/supported-areas")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["supported_areas"], [])
        self.assertIn("last_updated", body)

    def tearDown(self):
        app.dependency_overrides = {}


if __name__ == "__main__":
    unittest.main()
