
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock

# Mock firebase_admin before importing the app
_mock_firebase_admin = MagicMock()
_mock_firebase_admin.auth.verify_id_token = AsyncMock(return_value={'uid': 'test_uid'})
patch('firebase_admin.auth', _mock_firebase_admin.auth).start()
patch('firebase_admin.credentials.Certificate', MagicMock()).start()
patch('firebase_admin.initialize_app', MagicMock()).start()

from app.main import app  # Now it's safe to import the app

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

def test_video_ws_endpoint_subscription(client):
    stream_id = "test_stream"
    with client.websocket_connect(f"/video/ws/{stream_id}?token=test_token") as websocket:
        # The connection itself tests the initial subscription
        # Now let's test changing subscription
        new_stream_id = "new_test_stream"
        websocket.send_json({
            "type": "subscribe_to_feed",
            "data": {"feed_id": new_stream_id}
        })
        # Give some time for the server to process the message
        import time
        time.sleep(1)
        # If no exception is raised, the test is considered passed.
        # We are primarily testing that the AttributeError does not occur.
