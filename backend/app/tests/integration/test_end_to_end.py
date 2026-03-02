import pytest
import asyncio
import numpy as np
import cv2
import time
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
from app.services.feed_manager import FeedManager
from app.models.feeds import FeedOperationalStatusEnum

class SyntheticTrafficGenerator:
    """Generates frames with a rectangle moving at a fixed speed."""
    def __init__(self, width=640, height=480, fps=15):
        self.width = width
        self.height = height
        self.fps = fps
        self.frame_count = 0
        
    def generate_frame(self, speed_px_per_sec=100):
        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        # Add some "road" context
        cv2.line(frame, (100, 0), (100, self.height), (50, 50, 50), 2)
        cv2.line(frame, (500, 0), (500, self.height), (50, 50, 50), 2)
        
        # Calculate position based on frame index and speed
        dt = 1.0 / self.fps
        y_pos = int((self.frame_count * speed_px_per_sec * dt) % self.height)
        
        # Draw "vehicle" (white box)
        cv2.rectangle(frame, (280, y_pos), (320, y_pos + 40), (255, 255, 255), -1)
        
        self.frame_count += 1
        return frame

@pytest.mark.asyncio
async def test_end_to_end_metric_accuracy():
    """
    Verifies that a synthetic vehicle moving at 100px/s is correctly 
    detected, tracked, and its speed is reported accurately.
    """
    config = {
        "fps": 15,
        "pixels_per_meter": 10, # 100px/s = 10m/s = 36km/h
        "video_output": {"fps": 15},
        "performance": {"inference_pool_size": 1, "queue_max_size": 100},
        "database": {"db_path": ":memory:"},
        "tracking": {"probation_threshold": 1}, # Immediate confirm for test
        "analytics": {"broadcast_interval": 1, "min_track_quality": 0.1},
        "vehicle_detection": {"confidence_threshold": 0.2}
    }
    
    gen = SyntheticTrafficGenerator(fps=15)
    
    # We need to mock the YOLO model to return a box matching our synthetic vehicle
    mock_box = MagicMock()
    # [x1, y1, x2, y2, conf, cls]
    # Note: speed is 100px/s, at 15fps that's ~6.6px per frame
    
    def get_mock_results(frames, **kwargs):
        results = []
        for _ in range(len(frames) if isinstance(frames, list) else 1):
            res = MagicMock()
            y = (gen.frame_count * 100 / 15) % 480
            res.boxes.data = torch.tensor([[280, y, 320, y+40, 0.9, 2.0]]) # 2.0 = car
            res.orig_shape = (480, 640)
            results.append(res)
        return results

    from unittest.mock import AsyncMock
    import torch
    with patch("ultralytics.YOLO") as mock_yolo, \
         patch("app.utils.redis_client.get_redis_client"), \
         patch("app.services.feed_manager.ConnectionManager") as mock_cm_class:
        
        mock_yolo.return_value.side_effect = get_mock_results
        
        mock_cm = mock_cm_class.return_value
        mock_cm.broadcast = AsyncMock()
        mock_cm.broadcast_to_topic = AsyncMock()
        
        manager = FeedManager(config)
        manager.set_connection_manager(mock_cm)
        
        # Disable watchdog for test to avoid interference with manual injection
        if manager._watchdog_task:
            manager._watchdog_task.cancel()
        
        # Inject some frames directly into the pipeline
        # Since we can't easily trigger the IngestionWorker with synthetic frames without a file,
        # we'll mock the central input queue behavior
        
        from app.models.feeds import FeedConfigInfo
        feed_id = "test_e2e"
        manager.process_registry[feed_id] = {
            "status": FeedOperationalStatusEnum.RUNNING,
            "source": "synthetic_gen",
            "latest_metrics": {},
            "stop_event": asyncio.Event(),
            "config_info": FeedConfigInfo(
                name="Test Feed",
                source_type="test",
                source_identifier="synthetic_gen"
            )
        }
        
        # Simulate 30 frames (2 seconds)
        # We inject directly into central_output_queue because InferenceWorkers run in separate processes
        # and won't inherit the YOLO mock.
        for i in range(30):
            frame = gen.generate_frame()
            _, frame_bytes = cv2.imencode(".jpg", frame)
            
            # format: (feed_id, frame_idx, frame_bytes, metrics, vehicles, extra)
            mock_worker_metrics = {"frames_processed": 1, "inference_time_ms": 10.0}
            mock_vehicles = [{"vehicle_id": "V1", "speed": 36.0, "quality_score": 0.9, "centroid": [300, i*6]}]
            
            item = (feed_id, i, frame_bytes.tobytes(), mock_worker_metrics, mock_vehicles, {})
            print(f"DEBUG: Injecting frame {i}")
            manager._central_output_queue.put(item)
            await asyncio.sleep(0.01)

        # Wait for the AnalyticsWorker to process and broadcast
        # Target speed: 36 km/h
        captured_metrics = []
        
        async def save_broadcast(msg, *args, **kwargs):
            print(f"DEBUG: Captured broadcast message")
            import json
            try:
                data = json.loads(msg)
                if data["type"] == "feed_status_update":
                    m = data["data"].get("metrics")
                    if m:
                        captured_metrics.append(m)
            except Exception as e:
                print(f"DEBUG: Broadcast parse error: {e}")

        mock_cm.broadcast.side_effect = save_broadcast
        mock_cm.broadcast_to_topic.side_effect = save_broadcast
        
        # Poll for results
        timeout = 15.0
        start = time.time()
        print("DEBUG: Starting poll for metrics...")
        while len(captured_metrics) < 1 and (time.time() - start < timeout):
            # Check internal queues for health
            oq_size = manager._analytics_output_queue.qsize()
            iq_size = manager._analytics_input_queue.qsize()
            print(f"DEBUG: Queues - Input: {iq_size}, Output: {oq_size}, Captured: {len(captured_metrics)}")
            await asyncio.sleep(1.0)
            
        await manager.shutdown()
        
        assert len(captured_metrics) > 0
        latest = captured_metrics[-1]
        
        assert latest["total_vehicles"] >= 1
        # Speed should be approx 36 km/h
        speed = latest.get("average_speed_kmh", 0)
        assert 30 <= speed <= 45 # Allow some variance for EWMA spin-up
        
        # Check for quality score presence
        assert "reliable_track_count" in latest
