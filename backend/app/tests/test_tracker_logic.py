import pytest
import numpy as np
from unittest.mock import MagicMock
import sys
import os

# Ensure backend acts as root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.core.tracking import TrackingManager

class TestTrackerLogic:
    @pytest.fixture
    def tracking_manager(self):
        config = {
            "confidence_threshold": 0.4,
            "tracking": {
                "dynamic_matching_threshold": 0.7,
                "appearance_weight": 0.3,
                "second_pass_threshold": 0.5,
                "stationary_cleanup_timeout": 300
            }
        }
        manager = TrackingManager(config, fps=30)
        return manager

    def test_calculate_cost_matrix_reid(self, tracking_manager):
        # Setup: One track and one detection
        track_id = "TRK_1"
        track_emb = np.array([1.0, 0.0]) # Valid normalized embedding
        
        # Track setup
        tracking_manager.vehicle_data = {
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
        detection = (det_bbox, 2, 0.9, det_emb) # bbox, cls, conf, emb
        
        # Calculate cost
        cost_matrix = tracking_manager._calculate_cost_matrix([detection], [tracking_manager.vehicle_data[track_id]], use_reid=True)
        
        # Expect very low cost. Motion cost ~0.15 (1.0 - giou), ReID cost 0.0
        assert cost_matrix[0, 0] < 0.2
        
        # Detection: Perfect spatial match, OPPOSITE ReID (dissimilar)
        det_emb_diff = np.array([0.0, 1.0])
        detection_diff = (det_bbox, 2, 0.9, det_emb_diff)
        
        cost_matrix_diff = tracking_manager._calculate_cost_matrix([detection_diff], [tracking_manager.vehicle_data[track_id]], use_reid=True)
        
        # Expect higher cost due to ReID penalty
        # Motion cost ~0.15
        # ReID cost = (1.0 - 0.0) * 0.3 * 2.0 = 0.6
        # Total ~ 0.75
        assert cost_matrix_diff[0, 0] > cost_matrix[0, 0]
        assert cost_matrix_diff[0, 0] > 0.5

    def test_update_tracks_bytetrack_association(self, tracking_manager):
        # Test 2-stage association: High conf matched first, Low conf matched to remaining
        
        # Setup: Two tracks
        tracking_manager.vehicle_data = {
            "track_A": {
                "vehicle_id": "track_A", "centroid": (100, 100), "predicted_bbox": (90, 90, 110, 110),
                "kalman_filter": MagicMock(), "last_seen": 0, "status": "active", "class_history": [2]*10, "class_id": 2,
                "embedding": np.array([1.0, 0.0])
            },
            "track_B": {
                "vehicle_id": "track_B", "centroid": (200, 200), "predicted_bbox": (190, 190, 210, 210),
                "kalman_filter": MagicMock(), "last_seen": 0, "status": "active", "class_history": [2]*10, "class_id": 2,
                "embedding": np.array([0.0, 1.0])
            }
        }
        
        # Detections
        # Det 1: High Conf (0.9), near Track A
        det1 = ((90, 90, 110, 110), 2, 0.9, np.array([1.0, 0.0]))
        # Det 2: Low Conf (0.2), near Track B
        det2 = ((190, 190, 210, 210), 2, 0.2, None)
        
        detections = [det1, det2]
        
        # Run
        updated_tracks = tracking_manager.update(detections, current_time=1.0, frame_shape=(500, 500))
        
        # Assertions
        assert "track_A" in updated_tracks
        assert "track_B" in updated_tracks
        assert updated_tracks["track_A"]["status"] == "active"
        assert updated_tracks["track_B"]["status"] == "active"
        assert updated_tracks["track_A"]["last_seen"] == 1.0
        assert updated_tracks["track_B"]["last_seen"] == 1.0

    def test_new_track_creation_high_conf_only(self, tracking_manager):
        # Only High Conf detections should create new tracks
        tracking_manager.vehicle_data = {}
        
        # Det 1: High Conf (0.9)
        det1 = ((100, 100, 120, 120), 2, 0.9, None)
        # Det 2: Low Conf (0.2)
        det2 = ((200, 200, 220, 220), 2, 0.2, None)
        
        detections = [det1, det2]
        
        updated_tracks = tracking_manager.update(detections, current_time=1.0, frame_shape=(500, 500))
        
        # Expect only 1 new track creation (for det1)
        # Low conf unmatched detection should be ignored if not matched to an existing track
        active_tracks = [t for t in updated_tracks.values() if t["status"] == "active"]
        assert len(active_tracks) == 1
        assert active_tracks[0]["confidence"] == 0.9

    def test_giou_zero_area_guard(self, tracking_manager):
        # Degenerate box
        boxA = (100, 100, 100, 100)
        boxB = (100, 100, 110, 110)
        
        giou = tracking_manager._bbox_giou(boxA, boxB)
        # Should return IoU (0) if e_area is zero or handled gracefully
        assert isinstance(giou, float)
        assert giou <= 1.0

    def test_behavior_classification(self, tracking_manager):
        from collections import deque
        # Create a track with history
        track = {
            "vehicle_id": "T1", "centroid": (100, 100), "predicted_bbox": (90, 90, 110, 110),
            "velocity_history": deque(maxlen=20),
            "hits": 50, "status": "active"
        }
        
        # Simulate constant speed (500 px/s)
        # 10 frames of 500 px/s motion in X direction
        for i in range(10):
            track["velocity_history"].append((500, 0))
            
        tracking_manager._classify_behavior(track)
        assert track["behavior"] == "normal"
        assert abs(track["acceleration"]) < 100
        
        # Simulate hard braking
        # Speed drops from 500 to 100 in 5 frames
        # 500, 400, 300, 200, 100
        track["velocity_history"].clear()
        for v in [500, 400, 300, 200, 100]:
            track["velocity_history"].append((v, 0))
            
        tracking_manager._classify_behavior(track)
        # Accel: (100 - 500) / (4 * 1/30) = -400 / 0.133 = -3000
        assert track["behavior"] == "hard_braking"
        assert track["acceleration"] < -100
