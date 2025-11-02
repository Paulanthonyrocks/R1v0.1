import base64
import asyncio
import datetime
import collections.abc
import uuid
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import StreamingResponse
from pathlib import Path
from app.utils.auth_utils import verify_firebase_token
import logging
from app.config import get_current_config
from app.dependency_injection import get_current_active_user, get_token_from_query, get_feed_manager
from app.exceptions import ResourceNotFound, OperationFailed
from app.models.common import APIResponse
from app.services.video_processor import VideoManager
from app.services.feed_manager import FeedManager
from app.database import get_database_manager
from app.utils.video import FrameReader
from app.websocket.connection_manager import ConnectionManager
from app.utils.service_getters import get_connection_manager
import cv2

router = APIRouter()
logger = logging.getLogger(__name__)


def convert_datetime_to_iso(obj):
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    elif isinstance(obj, str) or isinstance(obj, bytes):
        return obj
    elif isinstance(obj, collections.abc.Mapping):
        return {k: convert_datetime_to_iso(v) for k, v in obj.items()}
    elif isinstance(obj, collections.abc.Iterable):
        return [convert_datetime_to_iso(i) for i in obj]
    else:
        return obj


@router.get("/stream/{stream_id:path}")
async def stream_video(stream_id: str, current_user: dict = Depends(get_current_active_user)):
    """Stream sample traffic video with real-time processing"""

    logger.info(
        f"GET /video/stream/{stream_id} endpoint called by user: {current_user.get('email')}"
    )
    
    config = get_current_config()
    output_directory = config.get("video_output", {}).get("output_directory")
    video_manager = VideoManager.get_instance(output_directory=output_directory)
    video_manager.cancel_background_task(stream_id)

    feed_manager = get_feed_manager()
    processor = video_manager.get_processor(stream_id, feed_manager)

    async def generate_frames():
        async for data in processor.get_frame_generator():
            frame_bytes = data["frame"]
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
            )

    return StreamingResponse(
        generate_frames(), media_type="multipart/x-mixed-replace; boundary=frame"
    )


@router.websocket("/video/ws/{stream_id:path}")
async def video_ws_endpoint(
    websocket: WebSocket, 
    stream_id: str, 
    token: str = Depends(get_token_from_query),
    feed_manager: FeedManager = Depends(get_feed_manager),
    connection_manager: ConnectionManager = Depends(get_connection_manager)
):
    """WebSocket endpoint for real-time video KPIs."""
    logger.info(f"New WebSocket connection to video_ws_endpoint with stream_id: {stream_id}")
    client_id = str(uuid.uuid4())
    try:
        if not token:
            await websocket.close(code=4401, reason="Not authenticated")
            return
        user_data = await verify_firebase_token(token)
        user_id = user_data.get("uid")
        if not user_id:
            await websocket.close(code=4401, reason="Invalid token: UID missing")
            return
    except HTTPException as e:
        logger.warning(f"WebSocket authentication failed: {e.detail}")
        await websocket.close(code=e.status_code, reason=e.detail)
        return

    await connection_manager.connect(websocket, client_id, user_id)
    await connection_manager.subscribe_to_topic(client_id, stream_id)
    current_stream_id = stream_id

    config = get_current_config()
    output_directory = config.get("video_output", {}).get("output_directory")
    video_manager = VideoManager.get_instance(output_directory=output_directory)

    async def send_video_data(feed_id):
        processor = video_manager.get_processor(feed_id, feed_manager)
        try:
            async for data in processor.get_frame_generator():
                try:
                    frame_bytes = data["frame"]
                    kpis = data["kpis"]

                    kpis_serializable = convert_datetime_to_iso(kpis)

                    # Encode the frame in base64
                    frame_base64 = base64.b64encode(frame_bytes).decode('utf-8')

                    # Send frame and KPIs in a single message
                    await connection_manager.broadcast_to_topic(
                        {
                            "type": "video_update",
                            "data": {
                                "feed_id": feed_id,
                                "frame": frame_base64,
                                "kpis": kpis_serializable,
                            }
                        },
                        feed_id
                    )
                    
                    await asyncio.sleep(1 / 30)

                except WebSocketDisconnect:
                    logger.info(f"WebSocket send_video_data disconnected for stream_id: {feed_id}")
                    break
                
                except Exception as e:
                    logger.error(f"Error in send_video_data for {feed_id}: {e}")
        finally:
            logger.info(f"Closing processor for feed_id: {feed_id}")
            processor.cancel()

    # Start the video data sending task in the background
    task = asyncio.create_task(send_video_data(current_stream_id))

    try:
        # Keep the connection alive to receive messages
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info(f"WebSocket client {client_id} disconnected.")
    finally:
        task.cancel()
        await connection_manager.unsubscribe_from_topic(client_id, current_stream_id)
        await connection_manager.disconnect(client_id)