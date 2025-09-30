import base64
import asyncio
import datetime
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import StreamingResponse
from pathlib import Path
from app.utils.auth_utils import verify_firebase_token
import logging
from app.config import get_current_config
from app.dependency_injection import get_current_active_user, get_token_from_query, get_feed_manager
from app.exceptions import ResourceNotFound, OperationFailed
from app.models.common import APIResponse
from app.services.video_ws_manager import video_ws_manager
from app.services.video_manager import VideoManager, process_video_feed
from app.services.feed_manager import FeedManager
from app.database import get_database_manager
from app.utils.video import FrameReader
import cv2

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
        async def generate_frames():
            async for data in process_video_feed(str(video_path)):
                frame_bytes = data["frame"]
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
                )

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
        async for data in process_video_feed(str(video_path)):
            return APIResponse.success(
                data=data["kpis"], message="Successfully retrieved video KPIs."
            )
        
        raise OperationFailed(detail="Failed to process video frame")

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
        if not token:
            await websocket.close(code=4401, reason="Not authenticated")
            return
        await verify_firebase_token(token)
    except HTTPException as e:
        logger.warning(f"WebSocket authentication failed: {e.detail}")
        await websocket.close(code=e.status_code, reason=e.detail)
        return

    await websocket.accept()
    await video_ws_manager.connect(websocket, stream_id)

    async def send_video_data():
        try:
            config = get_current_config()
            
            sample_videos_list = config.get("video_input", {}).get("sample_videos", [])
            
            video_path_str = None
            if sample_videos_list:
                video_path_str = sample_videos_list[0]
            else:
                video_path_str = config.get("video_input", {}).get("sample_video")

            if not video_path_str:
                logger.error("Sample video path not configured for WebSocket stream.")
                await websocket.close(code=4000, reason="Video source not configured")
                return

            video_manager = VideoManager.get_instance()
            processor, _ = video_manager.get_video_pipeline(video_path_str)

            async for data in process_video_feed(video_path_str):
                try:
                    frame_bytes = data["frame"]
                    kpis = data["kpis"]

                    await video_ws_manager.broadcast_bytes(stream_id, frame_bytes)
                    await video_ws_manager.broadcast(
                        stream_id,
                        {"type": "metrics_update", "data": kpis},
                    )
                    
                    await asyncio.sleep(1 / processor.fps if processor.fps > 0 else 0.03)

                except WebSocketDisconnect:
                    logger.info(f"WebSocket send_video_data disconnected for stream_id: {stream_id}")
                    break
                
                except Exception as e:
                    logger.error(f"Error in send_video_data for {stream_id}: {e}", exc_info=True)
                    break
        except WebSocketDisconnect:
            logger.info(f"WebSocket send_video_data disconnected for stream_id: {stream_id}")
        except Exception as e:
            logger.error(f"Error in send_video_data for {stream_id}: {e}", exc_info=True)

    sender_task = asyncio.create_task(send_video_data())

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") in ("ping", "PING"):
                await websocket.send_json({
                    "type": "pong",
                    "data": {"timestamp": datetime.datetime.utcnow().isoformat()}
                })
                continue

            if data.get("type") == "get_initial_feed_statuses":
                config = get_current_config()
                sample_videos = config.get("video_input", {}).get("sample_videos", [])
                feeds = [
                    {
                        "id": f"/stream/sample-feed",
                        "name": f"Feed {i+1}",
                        "status": "online",
                        "last_updated": datetime.datetime.now().isoformat()
                    }
                    for i, video_path in enumerate(sample_videos)
                ]
                await video_ws_manager.broadcast(
                    stream_id,
                    {"type": "initial_feed_statuses", "data": {"feeds": feeds}},
                )
    except WebSocketDisconnect:
        logger.info(f"WebSocket receive loop disconnected for stream_id: {stream_id}")
        sender_task.cancel()
        video_ws_manager.disconnect(websocket, stream_id)
    except Exception as e:
        logger.error(f"Unexpected error in WebSocket connection for stream_id {stream_id}: {e}", exc_info=True)
        sender_task.cancel()
        video_ws_manager.disconnect(websocket, stream_id)