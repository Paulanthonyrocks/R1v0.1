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
    logger.info(f"Attempting to stream video from: {video_path.resolve()}")
    if not video_path.exists():
        logger.error(f"Sample video file not found at expected path: {video_path.resolve()}")
        raise ResourceNotFound(detail=f"Sample video file not found at {video_path.resolve()}")
    else:
        logger.info(f"Video file found at: {video_path.resolve()}")

    try:
        video_manager = VideoManager.get_instance()
        processor = video_manager.get_processor(str(video_path))

        async def generate_frames():
            for data in processor.get_frame_generator():
                frame_bytes = data["frame"]
                yield (
                    b'--frame\r\n'                     b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n'
                )
        return StreamingResponse(generate_frames(), media_type="multipart/x-mixed-replace; boundary=frame")
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