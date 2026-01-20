
import unittest
from unittest.mock import MagicMock, patch
import numpy as np
from app.core.core_module import CoreModule

class TestLaneDetectionConfig(unittest.TestCase):
    def setUp(self):
        self.config_disabled = {
            "lane_detection": {
                "dynamic_lane_detection_enabled": False,
                "lane_detection_interval": 1,
                "num_lanes": 4
            },
            "vehicle_detection": {
                "frame_resolution": [640, 480],
                "confidence_threshold": 0.5,
                "proximity_threshold": 50,
                "track_timeout": 5
            },
            "performance": {},
            "behavior_analysis": {},
            "ocr_engine": {},
            "kalman_filter_params": {}
        }
        
        self.config_enabled = {
            "lane_detection": {
                "dynamic_lane_detection_enabled": True,
                "lane_detection_interval": 1,
                "num_lanes": 4
            },
            "vehicle_detection": {
                "frame_resolution": [640, 480],
                "confidence_threshold": 0.5,
                "proximity_threshold": 50,
                "track_timeout": 5
            },
            "performance": {},
            "behavior_analysis": {},
            "ocr_engine": {},
            "kalman_filter_params": {}
        }

    @patch('app.core.core_module.process_frame_for_lanes')
    @patch('app.core.core_module.get_lane_boundaries_from_lines')
    def test_lane_detection_disabled(self, mock_get_lines, mock_process):
        # Setup
        core = CoreModule(
            feed_id="test_feed",
            model_path="yolov8n.pt",
            config=self.config_disabled,
            fps=30,
            db_queue=MagicMock()
        )
        
        # Mock methods to avoid running full detection
        core._detect_vehicles = MagicMock(return_value=[])
        core._update_tracks = MagicMock(return_value={})
        core._remove_stale_tracks = MagicMock()
        core._save_vehicle_data = MagicMock()
        core._process_ocr_results = MagicMock()
        core.model = MagicMock() # Mock model to avoid loading error
        
        # Act
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        core.detect_and_track(frame, frame_index=10)
        
        # Assert
        mock_process.assert_not_called()

    @patch('app.core.core_module.process_frame_for_lanes')
    @patch('app.core.core_module.get_lane_boundaries_from_lines')
    def test_lane_detection_enabled(self, mock_get_lines, mock_process):
        # Setup
        core = CoreModule(
            feed_id="test_feed",
            model_path="yolov8n.pt",
            config=self.config_enabled,
            fps=30,
            db_queue=MagicMock()
        )
        
        # Mock methods
        core._detect_vehicles = MagicMock(return_value=[])
        core._update_tracks = MagicMock(return_value={})
        core._remove_stale_tracks = MagicMock()
        core._save_vehicle_data = MagicMock()
        core._process_ocr_results = MagicMock()
        core.model = MagicMock()
        
        # Mock process return value to ensure it proceeds
        mock_process.return_value = [[[0, 0, 10, 10]]]
        
        # Act
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        # Ensure frame_index meets interval requirement (interval is 1, so any change triggers)
        core.last_lane_detection_frame = 0
        core.detect_and_track(frame, frame_index=10)
        
        # Assert
        mock_process.assert_called()

    def test_update_config(self):
        core = CoreModule(
            feed_id="test_feed",
            model_path="yolov8n.pt",
            config=self.config_disabled,
            fps=30,
            db_queue=MagicMock()
        )
        core.model = MagicMock()
        
        self.assertFalse(core.dynamic_lane_detection_enabled)
        
        # Act
        core.update_config({"lane_detection": self.config_enabled["lane_detection"]})
        
        # Assert
        self.assertTrue(core.dynamic_lane_detection_enabled)

