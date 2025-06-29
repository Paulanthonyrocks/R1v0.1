from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from pathlib import Path
import logging
from ..services.video_processor import VideoManager
from app.dependencies import get_current_active_user
import io
from ..core.core_module import CoreModule
from ..utils.visualization import visualize_data
from ..utils.monitoring import TrafficMonitor
import cv2
import yaml
from app.exceptions import ResourceNotFound, OperationFailed
from app.models.common import APIResponse

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/sample-video/stream")
async def stream_video(current_user: dict = Depends(get_current_active_user)):
    logger.info(f"GET /video/sample-video/stream endpoint called by user: {current_user.get('username')}")
    video_path = Path(__file__).parent.parent.parent.parent / "frontend" / "public" / "sample_traffic.mp4"
    if not video_path.exists():
        logger.error(f"Sample video file not found at expected path: {video_path.resolve()}")
        raise ResourceNotFound(detail=f"Sample video file not found at {video_path.resolve()}")
    config_path = Path(__file__).parent.parent.parent / "configs" / "config.yaml"
    try:
        # Load config
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        # Initialize CoreModule
        core_module = CoreModule(
            feed_id="sample_feed",
            gemini_api_key=config.get('ocr_engine', {}).get('gemini_api_key', ''),
            model_path=config['vehicle_detection']['model_path'],
            config=config,
            fps=config.get('fps', 30),
            db_queue=None
        )

        traffic_monitor = TrafficMonitor(config)
        vis_options = {"Tracked Vehicles", "Vehicle Data"} # Commented out "Lane Density Overlay", "Grid Overlay"
        # vis_options = {"Tracked Vehicles", "Vehicle Data", "Lane Density Overlay", "Grid Overlay"}

        logger.info(f"Attempting to open video file: {video_path.resolve()}")
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            logger.error(f"Failed to open video file: {video_path.resolve()}. Check codecs or file integrity.")
            raise OperationFailed(detail=f"Failed to open video file for streaming at {video_path.resolve()}")
        def generate_frames():
            frame_index = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                # CoreModule detection/tracking
                tracked_vehicles = core_module.detect_and_track(
                    frame, frame_index=frame_index,
                    confidence_threshold=config['vehicle_detection']['confidence_threshold'],
                    proximity_threshold=config['vehicle_detection']['proximity_threshold'],
                    track_timeout=config['vehicle_detection']['track_timeout']
                )
                # Ensure tracked_vehicles is a dict of int keys (track IDs) to dicts with bbox, speed, lane, class_id, license_plate
                if not isinstance(tracked_vehicles, dict):
                    tracked_vehicles = {}
                else:
                    # Convert keys to int if needed
                    tracked_vehicles = {int(k): v for k, v in tracked_vehicles.items() if isinstance(v, dict)}
                    # Ensure all required fields are present
                    for v in tracked_vehicles.values():
                        v.setdefault('bbox', None)
                        v.setdefault('speed', 0.0)
                        v.setdefault('lane', -1)
                        v.setdefault('class_id', -1)
                        v.setdefault('license_plate', '')
                traffic_monitor.update_vehicles(tracked_vehicles)
                metrics = traffic_monitor.get_metrics()
                # Visualization
                vis_frame = visualize_data(
                    frame=frame,
                    tracked_vehicles=tracked_vehicles,
                    traffic_metrics=metrics,
                    visualization_options=vis_options,
                    config=config,
                    feed_id="sample_feed"
                )
                _, buffer = cv2.imencode('.jpg', vis_frame)
                frame_bytes = buffer.tobytes()
                # Format as multipart response
                boundary = b"frame"
                frame_data = b"".join([
                    b"--", boundary, b"\r\n",
                    b"Content-Type: image/jpeg\r\n",
                    b"Content-Length: ", str(len(frame_bytes)).encode(), b"\r\n",
                    b"\r\n",
                    frame_bytes, b"\r\n",
                    b"--", boundary, b"\r\n",
                    b"Content-Type: application/json\r\n",
                    b"\r\n",
                    str(metrics).encode(), b"\r\n"
                ])
                yield frame_data
                frame_index += 1
        return StreamingResponse(
            generate_frames(),
            media_type="multipart/x-mixed-replace;boundary=frame"
        )
    except ResourceNotFound:
        raise
    except Exception as e:
        logger.error(f"Error streaming video: {e}", exc_info=True)
        raise OperationFailed(detail=f"Error streaming video: {e}")

@router.get("/sample-video/kpis", response_model=APIResponse[dict])
async def get_video_kpis(current_user: dict = Depends(get_current_active_user)):
    logger.info(f"GET /video/sample-video/kpis endpoint called by user: {current_user.get('username')}")
    video_path = Path(__file__).parent.parent.parent.parent / "frontend" / "public" / "sample_traffic.mp4"
    
    try:
        video_manager = VideoManager.get_instance()
        processor = video_manager.get_processor(str(video_path))
        
        # Get one frame of KPIs
        data = next(processor.get_frame_generator())
        return APIResponse.success(data=data["kpis"], message="Successfully retrieved video KPIs.")
        
    except FileNotFoundError:
        raise ResourceNotFound(detail="Sample video file not found")
    except Exception as e:
        logger.error(f"Error getting video KPIs: {e}", exc_info=True)
        raise OperationFailed(detail=f"Error getting video KPIs: {e}")