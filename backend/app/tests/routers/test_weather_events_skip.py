import unittest
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.dependency_injection import (
    get_current_active_user,
    get_weather_service_api,
    get_event_service_api,
)
from app.models.user import User


async def override_get_current_active_user():
    return User(username="testuser", email="test@example.com", full_name="Test User", role="admin")


async def override_weather_uninitialized():
    raise RuntimeError("WeatherService not initialized.")


async def override_event_uninitialized():
    raise RuntimeError("EventService not initialized.")


class TestWeatherEventsSkip(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[get_current_active_user] = override_get_current_active_user
        self.client = TestClient(app)

    def test_weather_current_skipped_maps_to_500(self):
        app.dependency_overrides[get_weather_service_api] = override_weather_uninitialized
        # DI-raised RuntimeError is a real 500 in production; TestClient only
        # surfaces it with raise_server_exceptions disabled.
        client = TestClient(app, raise_server_exceptions=False)
        try:
            response = client.get(
                "/api/v1/weather/current", params={"lat": 34.0, "lon": -118.0}
            )
            self.assertEqual(response.status_code, 500)
        finally:
            del app.dependency_overrides[get_weather_service_api]

    def test_weather_current_passthrough_when_configured(self):
        svc = MagicMock()
        svc.get_current_weather = AsyncMock(return_value={"temp_c": 21.0})
        app.dependency_overrides[get_weather_service_api] = lambda: svc
        try:
            response = self.client.get(
                "/api/v1/weather/current", params={"lat": 34.0, "lon": -118.0}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"temp_c": 21.0})
        finally:
            del app.dependency_overrides[get_weather_service_api]

    def test_events_current_skipped_maps_to_500(self):
        app.dependency_overrides[get_event_service_api] = override_event_uninitialized
        client = TestClient(app, raise_server_exceptions=False)
        try:
            response = client.get("/api/v1/events/current")
            self.assertEqual(response.status_code, 500)
        finally:
            del app.dependency_overrides[get_event_service_api]

    def test_events_current_passthrough_when_configured(self):
        svc = MagicMock()
        svc.get_events = AsyncMock(return_value=[{"id": "e1"}])
        app.dependency_overrides[get_event_service_api] = lambda: svc
        try:
            response = self.client.get("/api/v1/events/current")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), [{"id": "e1"}])
        finally:
            del app.dependency_overrides[get_event_service_api]

    def tearDown(self):
        app.dependency_overrides = {}


if __name__ == "__main__":
    unittest.main()
