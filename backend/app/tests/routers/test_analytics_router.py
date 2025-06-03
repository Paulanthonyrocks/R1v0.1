import unittest
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta

# Assuming your FastAPI app instance is accessible for TestClient
# This might need adjustment based on your project structure (e.g., from app.main import app)
from app.main import app
from app.services.analytics_service import AnalyticsService
from app.dependencies import get_analytics_service, get_current_active_user

# Define a dummy user for authentication override
async def override_get_current_active_user():
    return {"username": "testuser", "role": "admin"}

# Define a mock AnalyticsService for dependency override
mock_analytics_service_instance = MagicMock(spec=AnalyticsService)

async def override_get_analytics_service():
    return mock_analytics_service_instance


class TestAnalyticsRouterNodesCongestion(unittest.TestCase):

    def setUp(self):
        # Override dependencies for this test class
        app.dependency_overrides[get_current_active_user] = override_get_current_active_user
        app.dependency_overrides[get_analytics_service] = override_get_analytics_service

        self.client = TestClient(app)

        # Reset mocks before each test if they are instance attributes of the test class
        # For global mocks like mock_analytics_service_instance, reset them here or per test.
        mock_analytics_service_instance.reset_mock()


    def test_get_nodes_congestion_success(self):
        # Prepare mock data from the service
        mock_node_data = [
            {
                "id": "34.05,-118.25",
                "name": "Downtown Intersection",
                "latitude": 34.05,
                "longitude": -118.25,
                "congestion_score": 75.5,
                "vehicle_count": 120,
                "average_speed": 15.2,
                "timestamp": datetime.now(timezone.utc).isoformat()
            },
            {
                "id": "40.71,-74.00",
                "name": "Midtown Crossing",
                "latitude": 40.71,
                "longitude": -74.00,
                "congestion_score": 40.0,
                "vehicle_count": 60,
                "average_speed": 35.0,
                "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
            }
        ]
        # Configure the async mock for get_all_location_congestion_data
        mock_analytics_service_instance.get_all_location_congestion_data = AsyncMock(return_value=mock_node_data)

        response = self.client.get("/api/v1/analytics/nodes/congestion")

        self.assertEqual(response.status_code, 200)
        response_json = response.json()

        self.assertIn("nodes", response_json)
        self.assertEqual(len(response_json["nodes"]), len(mock_node_data))

        # Detailed check of one item to ensure structure and data match
        self.assertEqual(response_json["nodes"][0]["id"], mock_node_data[0]["id"])
        self.assertEqual(response_json["nodes"][0]["name"], mock_node_data[0]["name"])
        self.assertEqual(response_json["nodes"][0]["congestion_score"], mock_node_data[0]["congestion_score"])
        self.assertEqual(response_json["nodes"][0]["timestamp"], mock_node_data[0]["timestamp"]) # Timestamps are ISO strings

        mock_analytics_service_instance.get_all_location_congestion_data.assert_awaited_once()


    def test_get_nodes_congestion_empty_data(self):
        mock_analytics_service_instance.get_all_location_congestion_data = AsyncMock(return_value=[])

        response = self.client.get("/api/v1/analytics/nodes/congestion")

        self.assertEqual(response.status_code, 200)
        response_json = response.json()
        self.assertIn("nodes", response_json)
        self.assertEqual(len(response_json["nodes"]), 0)
        mock_analytics_service_instance.get_all_location_congestion_data.assert_awaited_once()

    def test_get_nodes_congestion_service_error(self):
        mock_analytics_service_instance.get_all_location_congestion_data = AsyncMock(side_effect=Exception("Service unavailable"))

        response = self.client.get("/api/v1/analytics/nodes/congestion")

        self.assertEqual(response.status_code, 500)
        response_json = response.json()
        self.assertIn("detail", response_json)
        self.assertEqual(response_json["detail"], "Failed to retrieve node congestion data.")
        mock_analytics_service_instance.get_all_location_congestion_data.assert_awaited_once()

    def tearDown(self):
        # Clean up dependency overrides
        app.dependency_overrides.clear()

if __name__ == '__main__':
    # This is for running the test file directly, e.g. with `python -m unittest ...`
    # Note: TestClient and FastAPI app setup might need careful handling if tests are run outside a managed test runner like pytest.
    unittest.main()
