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
            logger.error(f"cv2.VideoCapture failed to open video file: {video_path.resolve()}.")
            logger.error(f"Failed to open video file: {video_path.resolve()}. Check codecs or file integrity.")
            raise OperationFailed(detail=f"Failed to open video file for streaming at {video_path.resolve()}")
        output_video_path = Path(__file__).parent.parent.parent / "data" / "processed_videos" / "output.mp4"
        fourcc = cv2.VideoWriter_fourcc(*'mp4v') # Codec for .mp4
        out = None # Initialize out to None

        frame_index = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                logger.info("End of video stream or error. Releasing resources.")
                break # Exit loop if no more frames

            if out is None: # Initialize VideoWriter on first frame
                height, width, _ = frame.shape
                fps = cap.get(cv2.CAP_PROP_FPS)
                out = cv2.VideoWriter(str(output_video_path), fourcc, fps, (width, height))
                if not out.isOpened():
                    logger.error(f"Failed to open video writer for {output_video_path}. Check codec or path.")
                    raise OperationFailed(detail=f"Failed to open video writer for {output_video_path}")

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
            out.write(vis_frame) # Write the processed frame to the output video
            frame_index += 1
        
        if out:
            out.release() # Release the video writer
            logger.info(f"Processed video saved to {output_video_path}")
        
        return JSONResponse(content={"message": f"Processed video saved to {str(output_video_path)}"})
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