import pytest
import numpy as np
from unittest.mock import MagicMock, patch
import sys
import os

# Ensure backend acts as root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.core.core_module import CoreModule

class TestTrackerLogic:
    @pytest.fixture
    def core_module(self):
        config = {
            "vehicle_detection": {
                "confidence_threshold": 0.4,
                "frame_resolution": [1920, 1080],
                "reid": {
                    "use_appearance_in_tracking": True,
                    "appearance_weight": 0.3
                }
            },
            "performance": {"gpu_acceleration": False},
            "roi_processing": {"enabled": False}
        }
        # Mock dependencies to avoid loading models
        with patch("app.core.core_module.YOLO"), \
             patch("app.core.core_module.ReIDEmbedder"), \
             patch("app.core.core_module.check_system_resources"):
            module = CoreModule(
                feed_id="test_feed",
                model_path="yolov8n.pt",
                config=config,
                fps=30,
                db_queue=MagicMock()
            )
            # Mock ReID embedder
            module.reid_embedder = MagicMock()
            module.reid_embedder.get_batch_embeddings.return_value = []
            return module

    def test_calculate_cost_matrix_reid(self, core_module):
        # Setup: One track and one detection
        track_id = "t1"
        track_emb = np.array([1.0, 0.0]) # Valid normalized embedding
        
        # Track setup
        core_module.vehicle_data = {
            track_id: {
                "vehicle_id": track_id,
                "centroid": (100, 100),
                "predicted_bbox": (90, 90, 110, 110),
                "kalman_filter": MagicMock(),
                "embedding": track_emb,
                "class_id": 2,
                "class_history": [2]*10 # Mature track
            }
        }
        
        # Detection: Perfect spatial match, perfect ReID match
        det_bbox = (90, 90, 110, 110)
        det_emb = np.array([1.0, 0.0])
        detection = (det_bbox, 0.9, 2, det_emb)
        
        # Calculate cost
        cost_matrix = core_module._calculate_cost_matrix([detection], [core_module.vehicle_data[track_id]])
        
        # Expect very low cost
        assert cost_matrix[0, 0] < 0.2
        
        # Detection: Perfect spatial match, OPPOSITE ReID (dissimilar)
        det_emb_diff = np.array([0.0, 1.0])
        detection_diff = (det_bbox, 0.9, 2, det_emb_diff)
        
        cost_matrix_diff = core_module._calculate_cost_matrix([detection_diff], [core_module.vehicle_data[track_id]])
        
        # Expect higher cost due to ReID penalty
        # Motion cost ~0.15 (mostly confidence penalty + base)
        # ReID cost = 1.0 * 0.3 = 0.3
        # Total should be significantly higher than first case
        assert cost_matrix_diff[0, 0] > cost_matrix[0, 0]
        assert cost_matrix_diff[0, 0] > 0.3

    def test_update_tracks_bytetrack_association(self, core_module):
        # Test 2-stage association: High conf matched first, Low conf matched to remaining
        
        # Setup: Two tracks
        # Track A: Close to High Conf Detection
        # Track B: Close to Low Conf Detection
        core_module.vehicle_data = {
            "track_A": {
                "vehicle_id": "track_A", "centroid": (100, 100), "predicted_bbox": (90, 90, 110, 110),
                "kalman_filter": MagicMock(), "last_seen": 0, "status": "active", "class_history": [2]*10, "class_id": 2
            },
            "track_B": {
                "vehicle_id": "track_B", "centroid": (200, 200), "predicted_bbox": (190, 190, 210, 210),
                "kalman_filter": MagicMock(), "last_seen": 0, "status": "active", "class_history": [2]*10, "class_id": 2
            }
        }
        
        # Detections
        # Det 1: High Conf (0.9), near Track A
        det1 = ((90, 90, 110, 110), 0.9, 2, None)
        # Det 2: Low Conf (0.2), near Track B (matches LOW_CONF_THRESH > 0.1)
        det2 = ((190, 190, 210, 210), 0.2, 2, None)
        
        formatted_dets = [det1, det2]
        
        # We need to mock _update_track to avoid actual update logic failing on mocks
        core_module._update_track = MagicMock()
        core_module._create_new_track = MagicMock()
        
        # Run
        core_module._update_tracks(np.zeros((500,500,3)), formatted_dets, 50, 1.0, 1, 0.4)
        
        # Assertions
        # Det 1 should update Track A (Stage 1)
        # Det 2 should update Track B (Stage 2)
        
        assert core_module._update_track.call_count == 2
        
        # Check calls args
        # Call 1: Track A with Det 1
        args1 = core_module._update_track.call_args_list[0]
        assert args1[0][0]["vehicle_id"] == "track_A"
        assert args1[0][1] == det1
        
        # Call 2: Track B with Det 2
        args2 = core_module._update_track.call_args_list[1]
        assert args2[0][0]["vehicle_id"] == "track_B"
        assert args2[0][1] == det2

    def test_new_track_creation_high_conf_only(self, core_module):
        # Only High Conf detections should create new tracks
        core_module.vehicle_data = {}
        
        # Det 1: High Conf (0.9)
        det1 = ((100, 100, 120, 120), 0.9, 2, None)
        # Det 2: Low Conf (0.2)
        det2 = ((200, 200, 220, 220), 0.2, 2, None)
        
        formatted_dets = [det1, det2]
        
        core_module._create_new_track = MagicMock()
        core_module._create_new_track.return_value = {"vehicle_id": "new_1"}
        
        core_module._update_tracks(np.zeros((500,500,3)), formatted_dets, 50, 1.0, 1, 0.4)
        
        # Expect only 1 new track creation call (for det1)
        assert core_module._create_new_track.call_count == 1
        assert core_module._create_new_track.call_args[0][0] == det1
