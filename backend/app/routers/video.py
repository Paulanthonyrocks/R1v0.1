import base64
import asyncio
import datetime
import collections.abc
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
from app.services.video_processor import VideoManager
from app.services.feed_manager import FeedManager
from app.database import get_database_manager
from app.utils.video import FrameReader
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
    feed_manager: FeedManager = Depends(get_feed_manager) # Inject FeedManager
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

    config = get_current_config()
    output_directory = config.get("video_output", {}).get("output_directory")
    video_manager = VideoManager.get_instance(output_directory=output_directory)
    video_manager.cancel_background_task(stream_id)

    processor = video_manager.get_processor(stream_id, feed_manager)

    async def send_video_data():
        try:
            async for data in processor.get_frame_generator():
                try:
                    frame_bytes = data["frame"]
                    kpis = data["kpis"]

                    kpis_serializable = convert_datetime_to_iso(kpis)

                    # Encode the frame in base64
                    frame_base64 = base64.b64encode(frame_bytes).decode('utf-8')

                    # Send frame and KPIs in a single message
                    await video_ws_manager.broadcast(
                        stream_id,
                        {
                            "type": "video_update",
                            "data": {
                                "feed_id": stream_id,
                                "frame": frame_base64,
                                "kpis": kpis_serializable,
                            }
                        },
                    )
                    
                    await asyncio.sleep(1 / 30)

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
            message_type = data.get("type")
            
            if message_type in ("ping", "PING"):
                await websocket.send_json({
                    "type": "pong",
                    "data": {"timestamp": datetime.datetime.utcnow().isoformat()}
                })
            elif message_type == "get_initial_feed_statuses":
                logger.info("Received get_initial_feed_statuses request.")
                all_statuses = await feed_manager.get_all_statuses()
                # Pydantic models need to be converted to dicts for JSON serialization
                statuses_dict = [status.model_dump() for status in all_statuses]
                await websocket.send_json({
                    "type": "initial_feed_statuses",
                    "data": convert_datetime_to_iso(statuses_dict)
                })
            elif message_type == "start_feed":
                feed_id = data.get("data", {}).get("feed_id")
                if feed_id:
                    logger.info(f"Received start_feed request for feed_id: {feed_id}")
                    await feed_manager.handle_start_feed(feed_id)
                else:
                    logger.warning("Received start_feed request without feed_id.")
            elif message_type == "stop_feed":
                feed_id = data.get("data", {}).get("feed_id")
                if feed_id:
                    logger.info(f"Received stop_feed request for feed_id: {feed_id}")
                    await feed_manager.handle_stop_feed(feed_id)
                else:
                    logger.warning("Received stop_feed request without feed_id.")
            elif message_type == "refresh_feed":
                feed_id = data.get("data", {}).get("feed_id")
                if feed_id:
                    logger.info(f"Received refresh_feed request for feed_id: {feed_id}")
                    await feed_manager.refresh_feed(feed_id)
                else:
                    logger.warning("Received refresh_feed request without feed_id.")
            else:
                logger.warning(f"Received unhandled message type: {message_type}")
            
    except WebSocketDisconnect:
        logger.info(f"WebSocket receive loop disconnected for stream_id: {stream_id}")
        if sender_task:
            sender_task.cancel()
        video_ws_manager.disconnect(websocket, stream_id)
    except Exception as e:
        logger.error(f"Unexpected error in WebSocket connection for stream_id {stream_id}: {e}", exc_info=True)
        if sender_task:
            sender_task.cancel()
        video_ws_manager.disconnect(websocket, stream_id)