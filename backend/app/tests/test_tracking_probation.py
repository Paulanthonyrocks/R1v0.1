import pytest
import numpy as np
from unittest.mock import MagicMock
import sys
import os

# Ensure backend acts as root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.core.tracking import TrackingManager

class TestTrackingProbation:
    @pytest.fixture
    def tracking_manager(self):
        config = {
            "confidence_threshold": 0.4,
            "tracking": {
                "probation_threshold": 3,
                "track_timeout": 30
            }
        }
        manager = TrackingManager(config, fps=30)
        return manager

    def test_new_track_is_tentative(self, tracking_manager):
        # Setup: New high-conf detection
        det = ((100, 100, 120, 120), 2, 0.9, None)
        
        updated_tracks = tracking_manager.update([det], current_time=1.0, frame_shape=(500, 500))
        
        assert len(updated_tracks) == 1
        track = list(updated_tracks.values())[0]
        assert track["status"] == "tentative"
        assert track["hits"] == 1

    def test_probation_upgrade_to_active(self, tracking_manager):
        # 1st hit
        det = ((100, 100, 120, 120), 2, 0.9, None)
        tracking_manager.update([det], current_time=1.0, frame_shape=(500, 500))
        track_id = list(tracking_manager.vehicle_data.keys())[0]
        assert tracking_manager.vehicle_data[track_id]["status"] == "tentative"
        assert tracking_manager.vehicle_data[track_id]["hits"] == 1
        
        # 2nd hit
        tracking_manager.update([det], current_time=1.1, frame_shape=(500, 500))
        assert tracking_manager.vehicle_data[track_id]["status"] == "tentative"
        assert tracking_manager.vehicle_data[track_id]["hits"] == 2
        
        # 3rd hit (threshold met)
        tracking_manager.update([det], current_time=1.2, frame_shape=(500, 500))
        assert tracking_manager.vehicle_data[track_id]["status"] == "active"
        assert tracking_manager.vehicle_data[track_id]["hits"] == 3

    def test_tentative_track_dropped_on_miss(self, tracking_manager):
        # 1st hit
        det = ((100, 100, 120, 120), 2, 0.9, None)
        tracking_manager.update([det], current_time=1.0, frame_shape=(500, 500))
        track_id = list(tracking_manager.vehicle_data.keys())[0]
        assert tracking_manager.vehicle_data[track_id]["status"] == "tentative"
        
        # Miss (no detections)
        updated_tracks = tracking_manager.update([], current_time=1.1, frame_shape=(500, 500))
        
        # Tentative track should be gone
        assert track_id not in updated_tracks
        assert track_id not in tracking_manager.vehicle_data

    def test_active_track_becomes_predicting_on_miss(self, tracking_manager):
        # Reach active status
        det = ((100, 100, 120, 120), 2, 0.9, None)
        for i in range(3):
            tracking_manager.update([det], current_time=1.0 + i*0.1, frame_shape=(500, 500))
            
        track_id = list(tracking_manager.vehicle_data.keys())[0]
        assert tracking_manager.vehicle_data[track_id]["status"] == "active"
        
        # Miss
        updated_tracks = tracking_manager.update([], current_time=2.0, frame_shape=(500, 500))
        
        # Active track should persist as 'predicting'
        assert track_id in updated_tracks
        assert updated_tracks[track_id]["status"] == "predicting"
