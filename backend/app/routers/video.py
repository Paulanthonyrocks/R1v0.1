from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pathlib import Path
import os
import logging
import asyncio
from ..services.video_processor import VideoManager
from app.dependencies import get_current_active_user, verify_firebase_token
import json
from app.exceptions import ResourceNotFound, OperationFailed
from app.models.common import APIResponse

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/sample-video/stream")
async def stream_video(
    current_user: dict = Depends(get_current_active_user),
    token: str = Depends(lambda x: x.headers.get("Authorization", "").replace("Bearer ", ""))
):
    # Security: Ensure user is validated with retry
    retry_count = 0
    max_retries = 2
    
    while retry_count < max_retries:
        if current_user and current_user.get("email"):
            break
        retry_count += 1
        if retry_count < max_retries:
            # Small delay to allow token refresh
            await asyncio.sleep(0.5)
            try:
                current_user = await verify_firebase_token(token)
            except Exception as e:
                logger.warning(f"Token validation retry {retry_count} failed: {e}")
                continue
    
    if not current_user or not current_user.get("email"):
        logger.warning("Unauthorized access attempt to /sample-video/stream")
        raise OperationFailed(detail="Unauthorized")
    
    # Store the token for periodic validation
    current_user["token"] = token

    logger.info(f"GET /video/sample-video/stream endpoint called by user: {current_user.get('email')}")
    VIDEO_PATH = os.getenv("SAMPLE_VIDEO_PATH", str(Path(__file__).parent.parent.parent.parent / "frontend" / "public" / "sample_traffic.mp4"))
    video_path = Path(VIDEO_PATH)
    logger.info(f"Attempting to stream video from: {video_path.resolve()}")
    if not video_path.exists():
        logger.error(f"Sample video file not found at expected path: {video_path.resolve()}")
        raise ResourceNotFound(detail=f"Sample video file not found at {video_path.resolve()}")
    else:
        logger.info(f"Video file found at: {video_path.resolve()}")

    try:
        try:
            video_manager = VideoManager.get_instance()
        except Exception as e:
            logger.error(f"VideoManager error: {e}")
            raise OperationFailed(detail=f"VideoManager error: {e}")
        try:
            processor = video_manager.get_processor(str(video_path))
        except Exception as e:
            logger.error(f"Processor error: {e}")
            raise OperationFailed(detail=f"Processor error: {e}")

        async def generate_frames():
            frame_count = 0
            auth_check_interval = 30  # Check auth token every 30 frames
            try:
                for data in processor.get_frame_generator():
                    # Periodically verify token is still valid
                    frame_count += 1
                    if frame_count % auth_check_interval == 0:
                        try:
                            # Re-verify the token
                            await verify_firebase_token(current_user.get("token"))
                        except Exception as auth_error:
                            logger.error(f"Token validation failed during streaming: {auth_error}")
                            return
                    
                    frame_bytes = data["frame"]
                    # Yield only image frames for performance and compatibility
                    yield (
                        b'--frame\r\n'
                        b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n'
                    )
            except Exception as e:
                logger.error(f"Streaming interrupted: {e}")
                # Optionally yield an error frame or just break
                return
        return StreamingResponse(generate_frames(), media_type="multipart/x-mixed-replace; boundary=frame")
    except ResourceNotFound:
        raise
    except Exception as e:
        logger.error(f"Error streaming video: {e}", exc_info=True)
        raise OperationFailed(detail=f"Error streaming video: {e}")

@router.get("/sample-video/kpis", response_model=APIResponse[dict])
async def get_video_kpis(current_user: dict = Depends(get_current_active_user)):
    # Security: Ensure user is validated
    if not current_user or not current_user.get("email"):
        logger.warning("Unauthorized access attempt to /sample-video/kpis")
        raise OperationFailed(detail="Unauthorized")

    logger.info(f"GET /video/sample-video/kpis endpoint called by user: {current_user.get('email')}")
    VIDEO_PATH = os.getenv("SAMPLE_VIDEO_PATH", str(Path(__file__).parent.parent.parent.parent / "frontend" / "public" / "sample_traffic.mp4"))
    video_path = Path(VIDEO_PATH)
    try:
        try:
            video_manager = VideoManager.get_instance()
        except Exception as e:
            logger.error(f"VideoManager error: {e}")
            raise OperationFailed(detail=f"VideoManager error: {e}")
        try:
            processor = video_manager.get_processor(str(video_path))
        except Exception as e:
            logger.error(f"Processor error: {e}")
            raise OperationFailed(detail=f"Processor error: {e}")
        # Get one frame of KPIs
        try:
            data = next(processor.get_frame_generator())
        except Exception as e:
            logger.error(f"Frame generator error: {e}")
            raise OperationFailed(detail=f"Frame generator error: {e}")
        return APIResponse.success(data=data["kpis"], message="Successfully retrieved video KPIs.")
    except FileNotFoundError:
        raise ResourceNotFound(detail="Sample video file not found")
    except Exception as e:
        logger.error(f"Error getting video KPIs: {e}", exc_info=True)
        raise OperationFailed(detail=f"Error getting video KPIs: {e}")