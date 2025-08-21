import base64
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import StreamingResponse
from pathlib import Path
from app.utils.auth_utils import verify_firebase_token
import logging
from app.config import get_current_config
from app.dependencies import get_current_active_user, get_token_from_query
from app.exceptions import ResourceNotFound, OperationFailed
from app.models.common import APIResponse
from app.services.video_ws_manager import video_ws_manager
from app.services.video_manager import VideoManager

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/stream/sample-feed")
async def stream_video(current_user: dict = Depends(get_current_active_user)):
    """Stream sample traffic video with real-time processing"""

    logger.info(
        f"GET /video/sample-video/stream endpoint called by user: {current_user.get('email')}"
    )
    config = get_current_config()
    sample_videos = config.get("video_input", {}).get("sample_videos")
    video_path_str = sample_videos[0] if sample_videos else None
    if not video_path_str:
        logger.error("Sample video path not configured.")
        raise ResourceNotFound(detail="Sample video path not configured.")
    video_path = Path(video_path_str)
    logger.info(f"Attempting to stream video from: {video_path.resolve()}")
    if not video_path.exists():
        logger.error(
            f"Sample video file not found at expected path: {video_path.resolve()}"
        )
        raise ResourceNotFound(
            detail=f"Sample video file not found at {video_path.resolve()}"
        )
    else:
        logger.info(f"Video file found at: {video_path.resolve()}")

    try:
        config = get_current_config()
        processed_video_dir = config.get("video_output", {}).get("output_directory")
        if not processed_video_dir:
            logger.error("Processed video output directory not configured.")
            raise OperationFailed(detail="Processed video output directory not configured.")

        try:
            video_manager = VideoManager.get_instance(processed_video_dir)
        except Exception as e:
            logger.error(f"VideoManager error: {e}")
            raise OperationFailed(detail=f"VideoManager error: {e}")
        try:
            processor = video_manager.get_processor(str(video_path))
        except Exception as e:
            logger.error(f"Processor error: {e}")
            raise OperationFailed(detail=f"Processor error: {e}")

        async def generate_frames():
            try:
                for data in processor.get_frame_generator():
                    frame_bytes = data["frame"]
                    # Yield only image frames for performance and compatibility
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
                    )
            except Exception as e:
                logger.error(f"Streaming interrupted: {e}")
                # Optionally yield an error frame or just break
                return

        return StreamingResponse(
            generate_frames(), media_type="multipart/x-mixed-replace; boundary=frame"
        )
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

    logger.info(
        f"GET /video/sample-video/kpis endpoint called by user: {current_user.get('email')}"
    )
    config = get_current_config()
    sample_videos = config.get("video_input", {}).get("sample_videos")
    video_path_str = sample_videos[0] if sample_videos else None
    if not video_path_str:
        logger.error("Sample video path not configured.")
        raise ResourceNotFound(detail="Sample video path not configured.")
    video_path = Path(video_path_str)
    try:
        config = get_current_config()
        processed_video_dir = config.get("video_output", {}).get("output_directory")
        if not processed_video_dir:
            logger.error("Processed video output directory not configured.")
            raise OperationFailed(detail="Processed video output directory not configured.")

        try:
            video_manager = VideoManager.get_instance(processed_video_dir)
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
        return APIResponse.success(
            data=data["kpis"], message="Successfully retrieved video KPIs."
        )
    except FileNotFoundError:
        raise ResourceNotFound(detail="Sample video file not found")
    except Exception as e:
        logger.error(f"Error getting video KPIs: {e}", exc_info=True)
        raise OperationFailed(detail=f"Error getting video KPIs: {e}")


@router.websocket("/video/ws/{stream_id:path}")
async def video_ws_endpoint(
    websocket: WebSocket, stream_id: str, token: str = Depends(get_token_from_query)
):
    """WebSocket endpoint for real-time video KPIs."""
    try:
        # Validate the token
        if not token:
            await websocket.close(code=4401, reason="Not authenticated")
            return
        
        # Verify the Firebase token
        await verify_firebase_token(token) # Assuming verify_firebase_token takes the token string

    except HTTPException as e:
        logger.warning(f"WebSocket authentication failed: {e.detail}")
        await websocket.close(code=e.status_code, reason=e.detail)
        return

    await websocket.accept()
    await video_ws_manager.connect(websocket, stream_id)
    
    # Keep the connection open for sending messages. Receiving is optional unless needed for control messages.
    try:
        # Get the VideoManager instance
        config = get_current_config()
        processed_video_dir = config.get("video_output", {}).get("output_directory")
        if not processed_video_dir:
            logger.error("Processed video output directory not configured for WebSocket.")
            raise OperationFailed(detail="Processed video output directory not configured.")

        video_manager = VideoManager.get_instance(processed_video_dir)
        processor = video_manager.get_processor(stream_id) # Use stream_id as video_path

        # Loop to send frames and metrics
        for data in processor.get_frame_generator():
            frame_bytes = data["frame"]
            kpis = data["kpis"]

            # Send video frame
            await video_ws_manager.send_message(
                stream_id,
                {"type": "video_frame", "frame": base64.b64encode(frame_bytes).decode('utf-8')},
            )

            # Send metrics update
            await video_ws_manager.send_message(
                stream_id,
                {"type": "metrics_update", "metrics": kpis},
            )
            await asyncio.sleep(0.03) # Simulate ~30 FPS, adjust as needed

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for stream_id: {stream_id}")
        video_ws_manager.disconnect(websocket, stream_id)
    except ResourceNotFound:
        logger.warning(f"Video stream {stream_id} not found for WebSocket connection.")
        await websocket.close(code=404, reason=f"Stream {stream_id} not found")
    except Exception as e:
        logger.error(f"Unexpected error in WebSocket connection for stream_id {stream_id}: {e}", exc_info=True)
        video_ws_manager.disconnect(websocket, stream_id)

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for stream_id: {stream_id}")
        video_ws_manager.disconnect(websocket, stream_id)
    except Exception as e:
        logger.error(f"Unexpected error in WebSocket connection for stream_id {stream_id}: {e}", exc_info=True)
        video_ws_manager.disconnect(websocket, stream_id)
