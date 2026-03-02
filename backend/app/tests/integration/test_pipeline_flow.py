import pytest
import asyncio
import numpy as np
import cv2
import time
from unittest.mock import MagicMock, patch
from app.services.feed_manager import FeedManager
from app.models.feeds import FeedOperationalStatusEnum

@pytest.mark.asyncio
async def test_full_pipeline_initialization():
    """
    Verifies that FeedManager and workers can initialize without crashing.
    """
    config = {
        "fps": 15,
        "video_output": {"fps": 5},
        "performance": {"inference_pool_size": 1, "queue_max_size": 100},
        "database": {"db_path": ":memory:"},
        "reid": {"enabled": False}
    }
    
    with patch("ultralytics.YOLO"), \
         patch("app.utils.redis_client.get_redis_client"):
        
        manager = FeedManager(config)
        
        # Check if tasks started
        assert manager._result_reader_task is not None
        assert manager._analytics_reader_task is not None
        assert manager._analytics_process is not None
        assert manager._analytics_process.is_alive()
        
        process = manager._analytics_process
        await manager.shutdown()
        assert not process.is_alive()

@pytest.mark.asyncio
async def test_metric_flow_to_ui():
    """
    Verifies that metrics produced by AnalyticsWorker are correctly broadcasted.
    """
    config = {
        "fps": 15,
        "analytics": {"broadcast_interval": 1},
        "performance": {"inference_pool_size": 1},
        "video_output": {"fps": 10}
    }
    
    with patch("ultralytics.YOLO"), \
         patch("app.services.feed_manager.ConnectionManager") as mock_cm:
        
        manager = FeedManager(config)
        manager.set_connection_manager(mock_cm.return_value)
        
        # Register a fake feed so the reader doesn't drop the result
        manager.process_registry["test_feed"] = {
            "status": FeedOperationalStatusEnum.RUNNING,
            "latest_metrics": {}
        }
        
        # Manually inject a processed result into the analytics output queue
        mock_metrics = {"total_vehicles": 5, "average_speed_kmh": 60.0, "frame_index": 10}
        mock_vehicles = [{"vehicle_id": "V1", "speed": 60.0, "quality_score": 0.9}]
        
        manager._analytics_output_queue.put_nowait(("test_feed", mock_metrics, mock_vehicles, None, None))
        
        # Wait for the reader task to process it
        success = False
        for _ in range(50):
            if mock_cm.return_value.broadcast_to_topic.called:
                success = True
                break
            await asyncio.sleep(0.1)
            
        assert success
        await manager.shutdown()
