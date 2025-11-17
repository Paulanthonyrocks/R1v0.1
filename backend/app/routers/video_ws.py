import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Request
from app.websocket.connection_manager import ConnectionManager # Import the main ConnectionManager
from app.dependency_injection import get_token_from_query, verify_firebase_token

router = APIRouter()
logger = logging.getLogger(__name__)

@router.websocket("/video-ws/{stream_id}")
async def video_websocket_endpoint(
    websocket: WebSocket,
    stream_id: str,
    request: Request, # Add Request to access app.state
    token: str = Depends(get_token_from_query),
):
    connection_manager: ConnectionManager = request.app.state.connection_manager
    try:
        # Verify Firebase token
        user = await verify_firebase_token(token)
        if not user:
            await websocket.close(code=1008)  # Invalid authentication
            return

        logger.info(f"Client {user['email']} connected to video-ws for stream_id: {stream_id}")
        # Use the main ConnectionManager's connect method
        await connection_manager.connect(websocket, client_id=stream_id, user_id=user['email'])
        try:
            while True:
                # Keep the connection alive, or handle specific messages if needed
                # For now, just receiving to keep the connection open
                await websocket.receive_text()
        except WebSocketDisconnect:
            logger.info(f"Client {user['email']} disconnected from video-ws for stream_id: {stream_id}")
            # Use the main ConnectionManager's disconnect method
            await connection_manager.disconnect(stream_id)
        except Exception as e:
            logger.error(f"Error in video-ws for stream_id {stream_id}, client {user['email']}: {e}", exc_info=True)
            # Use the main ConnectionManager's disconnect method
            await connection_manager.disconnect(stream_id)
    except Exception as e:
        logger.error(f"Authentication or connection error for video-ws on stream_id {stream_id}: {e}", exc_info=True)
        await websocket.close(code=1011) # Internal error
