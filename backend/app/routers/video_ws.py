import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Request
from app.services.video_ws_manager import video_ws_manager
from app.dependency_injection import get_token_from_query, verify_firebase_token

router = APIRouter()
logger = logging.getLogger(__name__)

@router.websocket("/video-ws/{stream_id}")
async def video_websocket_endpoint(
    websocket: WebSocket,
    stream_id: str,
    token: str = Depends(get_token_from_query),
):
    try:
        # Verify Firebase token
        user = await verify_firebase_token(token)
        if not user:
            await websocket.close(code=1008)
            return

        logger.info(f"Client {user.get('email', 'Unknown')} connected to video-ws for stream_id: {stream_id}")
        await video_ws_manager.connect(websocket, stream_id)
        
        try:
            while True:
                # Keep the connection alive by waiting for messages.
                # The primary purpose is to maintain the connection for broadcasting frames.
                await websocket.receive_text()
        except WebSocketDisconnect:
            logger.info(f"Client {user.get('email', 'Unknown')} disconnected from video-ws for stream_id: {stream_id}")
            video_ws_manager.disconnect(websocket, stream_id)
        except Exception as e:
            logger.error(f"Error in video-ws for stream_id {stream_id}, client {user.get('email', 'Unknown')}: {e}", exc_info=True)
            video_ws_manager.disconnect(websocket, stream_id)
            
    except Exception as e:
        logger.error(f"Authentication or connection error for video-ws on stream_id {stream_id}: {e}", exc_info=True)
        # Ensure the connection is closed if it was accepted before the error.
        # The `connect` method in the manager now handles acceptance.
        # If an error occurs before `connect`, the socket might not be managed yet.
        # The websocket context manager should handle closing.
        pass
