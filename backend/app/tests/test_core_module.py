import unittest
import numpy as np
import os
import time
from unittest.mock import MagicMock, patch
from collections import deque
from app.core.core_module import CoreModule


class TestCoreModule(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_config = {
            "vehicle_detection": {
                "model_path": "backend/models/yolov8n.onnx",
                "vehicle_class_ids": [2, 3, 5, 7],
                "confidence_threshold": 0.4,
                "proximity_threshold": 60,
                "track_timeout": 5,
                "reid_timeout": 10,
                "max_active_tracks": 50,
                "yolo_imgsz": 320,
                "frame_resolution": [640, 480],
                "occlusion_confidence_threshold": 0.2,
            },
            "lane_detection": {"num_lanes": 4},
            "roi_processing": {"polygon_points": None},
            "performance": {"gpu_acceleration": False},
            "ocr_engine": {
                "gemini_api_key": os.environ.get("TEST_GEMINI_API_KEY", ""),
                "roi_top_margin_factor": 0.4,
                "roi_bottom_margin_factor": 0.1,
                "roi_left_margin_factor": 0.1,
                "roi_right_margin_factor": 0.1,
            },
            "kalman_filter_params": {},
            "pixels_per_meter": 40,
            "speed_limit": 60,
            "behavior_analysis": {
                "stopped_speed_threshold_kmh": 5,
                "speed_limit": 60,
                "accel_threshold_mps2": 0.5,
                "lane_change_buffer": 20,
                "ewma_alpha": 0.2,
            },
            "project_root_dir": "/home/user/R1v0.1/",
        }
        self.dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        self.dummy_db_queue = MagicMock()

        # Mock CoreModule dependencies
        with patch('ultralytics.YOLO'), \
             patch('onnxruntime.InferenceSession'), \
             patch.object(CoreModule, '_load_model', return_value=None):
            self.core_module = CoreModule(
                feed_id="test_feed",
                model_path="dummy_path.onnx",
                config=self.test_config,
                fps=30,
                db_queue=self.dummy_db_queue,
            )

        # Mock internal methods that rely on external libraries or complex logic
        self.core_module._initialize_kalman_filter = MagicMock(return_value=MagicMock(x=np.array([0,0,0,0]), predict=MagicMock(), update=MagicMock()))
        self.core_module._get_vehicle_type = MagicMock(return_value="car")
        self.core_module._calculate_iou = MagicMock(return_value=0.8)
        self.core_module._save_vehicle_data = MagicMock()
        self.core_module._classify_behavior = MagicMock()

    async def test_update_tracks_new_track_initialization(self):
        detections = [
            (100, 100, 0.9, 2, 0, [50, 50, 150, 150]),  # x, y, conf, class_id, frame_idx, bbox
        ]
        # Ensure _initialize_new_track is called
        with patch.object(self.core_module, '_initialize_new_track', wraps=self.core_module._initialize_new_track) as mock_init_new_track:
            # Mock the actual _initialize_new_track to return a predictable ID and add to vehicle_data
            tracked_vehicles = self.core_module._update_tracks(
                self.dummy_frame, detections, self.test_config["vehicle_detection"]["proximity_threshold"], time.time(), 0
            )

            mock_init_new_track.assert_called_once()
            self.assertIn("test_feed-1", tracked_vehicles)
            self.assertEqual(tracked_vehicles["test_feed-1"]["status"], "active")

    async def test_update_tracks_existing_track_update(self):
        # Setup an existing track
        existing_vehicle_id = "test_feed-1"
        self.core_module.vehicle_data[existing_vehicle_id] = {
            "vehicle_id": existing_vehicle_id,
            "first_seen": time.time() - 5, # Seen 5 seconds ago
            "last_seen": time.time() - 5,
            "frame_index": 0,
            "bbox": [50, 50, 150, 150],
            "confidence": 0.8,
            "kalman_filter": MagicMock(x=np.array([100,100,0,0]), predict=MagicMock(), update=MagicMock()),
            "license_plate": "Unknown",
            "plate_attempts": 0,
            "lane": 1,
            "lane_history": MagicMock(),
            "speed": 0.0,
            "speed_history": MagicMock(),
            "behavior": "unknown",
            "class_id": 2,
            "timestamp": time.time() - 5,
            "status": "active",
            "is_occluded": False,
        }

        detections = [
            (105, 105, 0.9, 2, 1, [55, 55, 155, 155]), # New detection for the existing track
        ]
        current_time = time.time()
        frame_index = 1

        with patch.object(self.core_module, '_update_track', wraps=self.core_module._update_track) as mock_update_track, patch.object(self.core_module, '_initialize_new_track') as mock_init_new_track:

            tracked_vehicles = self.core_module._update_tracks(
                self.dummy_frame, detections, self.test_config["vehicle_detection"]["proximity_threshold"], current_time, frame_index
            )

            mock_update_track.assert_called_once()
            mock_init_new_track.assert_not_called()
            self.assertIn(existing_vehicle_id, tracked_vehicles)
            self.assertEqual(tracked_vehicles[existing_vehicle_id]["status"], "active")
            self.assertAlmostEqual(tracked_vehicles[existing_vehicle_id]["last_seen"], current_time, delta=0.1)

    async def test_update_tracks_reidentify_lost_track(self):
        # Setup a lost track
        lost_vehicle_id = "test_feed-lost"
        self.core_module.vehicle_data[lost_vehicle_id] = {
            "vehicle_id": lost_vehicle_id,
            "first_seen": time.time() - 20, # Seen long ago
            "last_seen": time.time() - 15, # Last seen > reid_timeout ago
            "frame_index": 0,
            "bbox": [50, 50, 150, 150],
            "confidence": 0.7,
            "kalman_filter": MagicMock(x=np.array([100,100,0,0]), predict=MagicMock(), update=MagicMock()),
            "license_plate": "Unknown",
            "plate_attempts": 0,
            "lane": 1,
            "lane_history": MagicMock(),
            "speed": 0.0,
            "speed_history": MagicMock(),
            "behavior": "unknown",
            "class_id": 2,
            "timestamp": time.time() - 15,
            "status": "lost", # Explicitly lost
            "is_occluded": False,
        }

        detections = [
            (102, 102, 0.9, 2, 1, [52, 52, 152, 152]), # Detection to re-identify the lost track
        ]
        current_time = time.time()
        frame_index = 1

        with patch.object(self.core_module, '_update_track', wraps=self.core_module._update_track) as mock_update_track, patch.object(self.core_module, '_initialize_new_track') as mock_init_new_track:

            tracked_vehicles = self.core_module._update_tracks(
                self.dummy_frame, detections, self.test_config["vehicle_detection"]["proximity_threshold"], current_time, frame_index
            )

            mock_update_track.assert_called_once()
            mock_init_new_track.assert_not_called()
            self.assertIn(lost_vehicle_id, tracked_vehicles)
            self.assertEqual(tracked_vehicles[lost_vehicle_id]["status"], "active") # Should become active again
            self.assertAlmostEqual(tracked_vehicles[lost_vehicle_id]["last_seen"], current_time, delta=0.1)

    async def test_estimate_speed_kalman(self):
        # Setup a mock Kalman filter with known velocity
        mock_kf = MagicMock()
        mock_kf.x = np.array([0, 0, 10.0, 5.0]) # px, py, vx, vy (pixels/sec) 
        
        track = {
            "vehicle_id": "speed_test_vehicle",
            "kalman_filter": mock_kf,
            "speed_history": deque(), # Mock deque
            "last_seen": time.time() - 1 # Simulate 1 second ago
        }
        
        # Mock _get_dynamic_pixels_per_meter to return a constant value for simplicity
        with patch.object(self.core_module, '_get_dynamic_pixels_per_meter', return_value=40.0):
            current_time = time.time()
            speed = self.core_module._estimate_speed_kalman(track, current_time, track["last_seen"])
            
            # Expected speed: sqrt(10^2 + 5^2) = sqrt(125) = 11.18 pixels/sec
            # 11.18 pixels/sec / 40 pixels/meter = 0.2795 m/s
            # 0.2795 m/s * 3.6 km/h per m/s = 1.0062 km/h
            self.assertAlmostEqual(speed, 1.0, places=1) # Rounded to 1 decimal place

    async def test_estimate_lane(self):
        # Mock the external lane detection functions
        with patch('app.core.core_module.process_frame_for_lanes', return_value=[[[100, 200, 300, 400]]]), \
             patch('app.core.core_module.get_lane_boundaries_from_lines', return_value=[0, 160, 320, 480, 640]):
            
            # Test case 1: Vehicle in lane 1
            bbox_lane1 = [10, 10, 50, 50] # Center at 30
            lane = self.core_module._estimate_lane(self.dummy_frame, bbox_lane1, 0)
            self.assertEqual(lane, 1)

            # Test case 2: Vehicle in lane 2
            bbox_lane2 = [200, 10, 250, 50] # Center at 225
            lane = self.core_module._estimate_lane(self.dummy_frame, bbox_lane2, 0)
            self.assertEqual(lane, 2)

            # Test case 3: Vehicle outside lanes (left)
            bbox_outside = [-100, 10, -50, 50] # Center at -75
            lane = self.core_module._estimate_lane(self.dummy_frame, bbox_outside, 0)
            self.assertEqual(lane, -1) # Or whatever the fallback is for outside

    async def test_remove_stale_tracks(self):
        current_time = time.time()
        # Active track, should remain
        self.core_module.vehicle_data["active_track"] = {
            "last_seen": current_time - 1,
            "status": "active",
            "vehicle_id": "active_track"
        }
        # Lost track, should remain (within reid_timeout)
        self.core_module.vehicle_data["lost_track_reid"] = {
            "last_seen": current_time - self.test_config["vehicle_detection"]["track_timeout"] - 1,
            "status": "lost",
            "vehicle_id": "lost_track_reid"
        }
        # Stale track, should be removed (beyond reid_timeout)
        self.core_module.vehicle_data["stale_track"] = {
            "last_seen": current_time - self.test_config["vehicle_detection"]["reid_timeout"] - 1,
            "status": "lost",
            "vehicle_id": "stale_track"
        }

        self.core_module._remove_stale_tracks(current_time, self.test_config["vehicle_detection"]["track_timeout"])

        self.assertIn("active_track", self.core_module.vehicle_data)
        self.assertIn("lost_track_reid", self.core_module.vehicle_data)
        self.assertNotIn("stale_track", self.core_module.vehicle_data)
        self.assertEqual(self.core_module.vehicle_data["lost_track_reid"]["status"], "lost")
        self.assertEqual(self.core_module.vehicle_data["active_track"]["status"], "active")

    async def test_remove_stale_tracks_max_active_tracks(self):
        current_time = time.time()
        self.core_module.max_active_tracks = 2

        # Add more tracks than max_active_tracks
        self.core_module.vehicle_data = {
            "active_1": {"last_seen": current_time - 1, "status": "active", "vehicle_id": "active_1"},
            "active_2": {"last_seen": current_time - 2, "status": "active", "vehicle_id": "active_2"},
            "lost_1": {"last_seen": current_time - 15, "status": "lost", "vehicle_id": "lost_1"},
            "lost_2": {"last_seen": current_time - 20, "status": "lost", "vehicle_id": "lost_2"},
        }

        self.core_module._remove_stale_tracks(current_time, self.test_config["vehicle_detection"]["track_timeout"])

        self.assertEqual(len(self.core_module.vehicle_data), 2)
        # Expect lost tracks to be removed first, then oldest active if needed
        self.assertIn("active_1", self.core_module.vehicle_data)
        self.assertIn("active_2", self.core_module.vehicle_data)
        self.assertNotIn("lost_1", self.core_module.vehicle_data)
        self.assertNotIn("lost_2", self.core_module.vehicle_data)


if __name__ == "__main__":
    unittest.main()

