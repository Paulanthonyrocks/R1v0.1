import pytest
import asyncio
from unittest.mock import AsyncMock, Mock, patch
# We need to ensure app can be imported. 
# Assuming PYTHONPATH is set or we run from root.

# If running from backend/venv/bin/python, we might need to add backend/ to sys.path
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

from app.services.feed_manager import FeedManager
from app.websocket.connection_manager import ConnectionManager
from app.models.websocket import WebSocketMessageTypeEnum

@pytest.mark.asyncio
async def test_feed_broadcast_logic():
    # Mock config
    config = {
        "redis": {"enabled": False},
        "video_output": {"enabled": False},
        "performance": {"inference_pool_size": 1},
        "kpi_broadcast_interval": 1.0,
        "queue_log_interval": 15.0,
        "metrics_averaging_window_seconds": 10
    }
    
    # Use parenthesis for multiline context managers
    with (
        patch("app.services.feed_manager.Process"),
        patch("app.services.feed_manager.check_system_resources", return_value=(10, 10)),
        patch("app.services.feed_manager.inference_worker"),
        patch("app.core.analytics_worker.analytics_worker_process") # Patch where it is defined since it is locally imported
    ):
        
        fm = FeedManager(config)
        
        # Mock ConnectionManager
        mock_cm = AsyncMock(spec=ConnectionManager)
        mock_cm.has_subscribers = Mock(return_value=True)
        mock_cm.broadcast_bytes_to_feed = AsyncMock()
        
        fm.set_connection_manager(mock_cm)
        
        # Add feed to registry so it passes the check in _read_result_queues
        fm.process_registry["test_feed"] = {
            "status": "running",
            "video_writer_queue": None
        }
        
        # Test 1: Direct broadcast call
        # Mock msgpack to avoid dependency issues in test if possible, or just let it run
        # We'll just run it.
        
        await fm._broadcast_video_frame("test_feed", 1, b"frame_bytes", {}, [], extra_payload=None)
        
        # Assert broadcast_bytes_to_feed was called
        assert mock_cm.broadcast_bytes_to_feed.called
        args, _ = mock_cm.broadcast_bytes_to_feed.call_args
        assert args[0] == "test_feed"
        
        # Test 2: Logic inside _read_result_queues (optimization)
        mock_cm.broadcast_bytes_to_feed.reset_mock()
        mock_cm.has_subscribers.return_value = False
        
        # Inject item into queue
        # Item: (feed_id, frame_idx, frame_bytes, metrics, vehicles, extra)
        test_item = ("test_feed", 2, b"frame_bytes_2", {}, [], {})
        fm._central_output_queue.put(test_item)
        
        # Run reader loop briefly
        # We start the task, let it process one item, then cancel/stop it.
        fm._stop_reader_flag = False
        
        # We need to make sure the loop runs at least once.
        # We can run it in a separate task.
        task = asyncio.create_task(fm._read_result_queues())
        
        # Give it time to process
        await asyncio.sleep(0.1)
        
        # Stop
        fm._stop_reader_flag = True
        # We might need to wait for it to finish or cancel
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
            
        # Check: has_subscribers was False, so NO broadcast should happen
        mock_cm.broadcast_bytes_to_feed.assert_not_called()
        
        # Test 3: Logic inside _read_result_queues (optimization = True)
        mock_cm.broadcast_bytes_to_feed.reset_mock()
        mock_cm.has_subscribers.return_value = True
        
        fm._central_output_queue.put(test_item)
        fm._stop_reader_flag = False
        
        task = asyncio.create_task(fm._read_result_queues())
        
        await asyncio.sleep(0.1)
        
        fm._stop_reader_flag = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
            
        # Check: has_subscribers was True, so broadcast SHOULD happen
        assert mock_cm.broadcast_bytes_to_feed.called
        args, _ = mock_cm.broadcast_bytes_to_feed.call_args
        assert args[0] == "test_feed"

if __name__ == "__main__":
    pass
