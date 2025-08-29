import pytest
import os
import cv2
from unittest.mock import MagicMock, patch
from multiprocessing import Queue, Event, Value
import numpy as np
from pathlib import Path
from app.core.processing_worker import process_video

# Mock the FrameReader and CoreModule imports
# We need to mock them at the level they are imported in processing_worker.py
# which is from ..utils.video and ..core.core_module
@patch('app.core.processing_worker.FrameReader')
@patch('cv2.VideoCapture')
def mock_imports(mock_frame_reader, mock_video_capture, mock_core_module, mock_traffic_monitor, mock_visualize_data):
        
        # Configure FrameReader mock
        mock_frame_reader.return_value = MagicMock() # This will prevent __init__ from running
        mock_frame_reader.return_value.isOpened = True
        mock_frame_reader.return_value.read.side_effect = [(1, np.zeros((100, 100, 3), dtype=np.uint8)), (2, np.zeros((100, 100, 3), dtype=np.uint8)), None] # Simulate 2 frames then EOF
        mock_frame_reader.return_value.end_of_video = False

        # Configure VideoCapture mock
        mock_video_capture.return_value.isOpened.return_value = True
        mock_video_capture.return_value.read.return_value = (True, np.zeros((100, 100, 3), dtype=np.uint8))

        # Configure CoreModule mock
        mock_core_module_instance = MagicMock()
        mock_core_module.return_value = mock_core_module_instance
        mock_core_module_instance.detect_and_track.return_value = {} # No vehicles detected

        # Configure TrafficMonitor mock
        mock_traffic_monitor_instance = MagicMock()
        mock_traffic_monitor.return_value = mock_traffic_monitor_instance
        mock_traffic_monitor_instance.get_metrics.return_value = {}

        # Configure visualize_data mock
        mock_visualize_data.return_value = np.zeros((100, 100, 3), dtype=np.uint8) # Return a dummy frame

        yield

@pytest.fixture
def mock_queues():
    frame_q = Queue()
    stop_e = Event()
    alerts_q = Queue()
    db_q = Queue()
    error_q = Queue()
    reduce_fps_e = Event()
    global_fps_v = Value('i', 30)
    global_skip_factor_v = Value('f', 1.0)
    yield frame_q, stop_e, alerts_q, db_q, error_q, reduce_fps_e, global_fps_v, global_skip_factor_v
    frame_q.close()
    alerts_q.close()
    db_q.close()
    error_q.close()

@pytest.fixture
def mock_config():
    return {
        "logging": {"level": "DEBUG", "log_path": "./test_logs/worker.log"},
        "video_input": {
            "webcam_buffer_size": 1,
            "max_queue_size": 10,
            "queue_put_timeout_ms": 100
        },
        "vehicle_detection": {
            "model_path": "dummy_model.onnx",
            "confidence_threshold": 0.5,
            "proximity_threshold": 10,
            "track_timeout": 30,
            "frame_resolution": [100, 100],
            "skip_frames": 1
        },
        "interface": {"camera_warmup_time": 0.1},
        "ocr_engine": {"gemini_api_key": "test_key"},
        "video_output": {
            "enabled": True,
            "output_directory": "./test_processed_videos",
            "codec": "mp4v",
            "fps": 10
        }
    }

@patch('cv2.VideoWriter')
@patch('cv2.VideoWriter_fourcc', return_value=cv2.VideoWriter_fourcc(*'mp4v'))
def test_processed_video_storage_enabled(mock_fourcc, mock_video_writer, mock_queues, mock_config):
    frame_q, stop_e, alerts_q, db_q, error_q, reduce_fps_e, global_fps_v, global_skip_factor_v = mock_queues
    
    feed_id = "test_feed_video_output"
    video_path = "dummy_video.mp4"

    # Run the process_video function in a separate thread or process if it's blocking
    # For unit testing, we'll let it run directly and rely on the mocked FrameReader to stop
    process_video(
        video_path=video_path,
        frame_queue=frame_q,
        stop_event=stop_e,
        alerts_queue=alerts_q,
        config=mock_config,
        feed_id=feed_id,
        confidence_threshold=mock_config["vehicle_detection"]["confidence_threshold"],
        proximity_threshold=mock_config["vehicle_detection"]["proximity_threshold"],
        track_timeout=mock_config["vehicle_detection"]["track_timeout"],
        vis_options=set(),
        reduce_frame_rate_event=reduce_fps_e,
        global_fps=global_fps_v,
        db_queue=db_q,
        error_queue=error_q,
        feed_config_info={"latitude": 0.0, "longitude": 0.0} # Dummy config info
    )

    # Assert that VideoWriter was initialized
    output_dir = Path(mock_config["video_output"]["output_directory"])
    expected_output_path = str(output_dir / f"{feed_id}.mp4")
    
    mock_video_writer.assert_called_once_with(
        expected_output_path,
        mock_fourcc.return_value,
        mock_config["video_output"]["fps"],
        tuple(mock_config["vehicle_detection"]["frame_resolution"])
    )

    # Assert that write was called for each frame
    mock_video_writer_instance = mock_video_writer.return_value
    assert mock_video_writer_instance.write.call_count == 2 # Because we simulated 2 frames

    # Assert that release was called
    mock_video_writer_instance.release.assert_called_once()

    # Clean up the created directory
    if output_dir.exists():
        os.rmdir(output_dir)

@patch('cv2.VideoWriter')
@patch('cv2.VideoWriter_fourcc')
def test_processed_video_storage_disabled(mock_fourcc, mock_video_writer, mock_queues, mock_config):
    frame_q, stop_e, alerts_q, db_q, error_q, reduce_fps_e, global_fps_v, global_skip_factor_v = mock_queues
    
    # Disable video output in config
    mock_config["video_output"]["enabled"] = False
    
    feed_id = "test_feed_video_output_disabled"
    video_path = "dummy_video.mp4"

    process_video(
        video_path=video_path,
        frame_queue=frame_q,
        stop_event=stop_e,
        alerts_queue=alerts_q,
        config=mock_config,
        feed_id=feed_id,
        confidence_threshold=mock_config["vehicle_detection"]["confidence_threshold"],
        proximity_threshold=mock_config["vehicle_detection"]["proximity_threshold"],
        track_timeout=mock_config["vehicle_detection"]["track_timeout"],
        vis_options=set(),
        reduce_frame_rate_event=reduce_fps_e,
        global_fps=global_fps_v,
        db_queue=db_q,
        error_queue=error_q,
        feed_config_info={"latitude": 0.0, "longitude": 0.0} # Dummy config info
    )

    # Assert that VideoWriter was NOT initialized
    mock_video_writer.assert_not_called()
    mock_fourcc.assert_not_called()
