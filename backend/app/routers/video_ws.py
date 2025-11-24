
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from app.services.video_ws_manager import video_ws_manager
from app.dependencies.auth import get_current_user_ws
from app.models.user import User
from app.dependency_injection import is_admin
import json

router = APIRouter()
logger = logging.getLogger(__name__)

@router.websocket("/video-ws/{stream_id}")
async def video_websocket_endpoint(
    websocket: WebSocket,
    stream_id: str,
    user: User = Depends(get_current_user_ws),
):
    await websocket.accept()
    if not user:
        logger.warning("video-ws: WebSocket connection without authenticated user.")
        await websocket.close(code=1008)
        return

    logger.info(f"video-ws: User {user.email} attempting to connect to stream {stream_id}. About to check admin status.")

    if not is_admin(user):
        logger.warning(f"Forbidden: User {user.email} does not have admin privileges for stream {stream_id}.")
        await websocket.close(code=403)
        return

    logger.info(f"video-ws: User {user.email} has admin privileges. Proceeding.")

    logger.info(f"Client {user.email} connected to video-ws for stream_id: {stream_id}")
    await video_ws_manager.connect(websocket, stream_id)
    
    try:
        while True:
            message = await websocket.receive_text()
            if message.strip().lower() == 'ping':
                await websocket.send_text('pong')
            else:
                logger.info(f"video-ws: Received message from {user.email}: {message}")

    except WebSocketDisconnect:
        logger.info(f"Client {user.email} disconnected from video-ws for stream_id: {stream_id}")
        video_ws_manager.disconnect(websocket, stream_id)
    except Exception as e:
        logger.error(f"Error in video-ws for stream_id {stream_id}, client {user.email}: {e}", exc_info=True)
        video_ws_manager.disconnect(websocket, stream_id)
