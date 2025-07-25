import unittest
import numpy as np
import os
from unittest.mock import MagicMock


class TestCoreModule(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Define a dummy config for testing
        self.test_config = {
            'vehicle_detection': {
                'model_path': 'backend/models/yolov8n.onnx', # Default to non-quantized for general tests
                'vehicle_class_ids': [2, 3, 5, 7],
                'confidence_threshold': 0.4,
                'proximity_threshold': 60,
                'track_timeout': 5,
                'max_active_tracks': 50,
                'yolo_imgsz': 320,
                'frame_resolution': [640, 480]
            },
            'lane_detection': {'num_lanes': 4},
            'performance': {'gpu_acceleration': False}, # Default to CPU for tests
            'ocr_engine': {
                'gemini_api_key': os.environ.get("TEST_GEMINI_API_KEY", ""),
                'roi_top_margin_factor': 0.4,
                'roi_bottom_margin_factor': 0.1,
                'roi_left_margin_factor': 0.1,
                'roi_right_margin_factor': 0.1
            },
            'kalman_filter_params': {},
            'pixels_per_meter': 40,
            'speed_limit': 60,
        }
        self.dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        self.dummy_db_queue = MagicMock() # Mock the multiprocessing queue

    

if __name__ == '__main__':
    unittest.main()
