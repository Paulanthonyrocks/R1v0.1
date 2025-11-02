
import logging
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from app.dependency_injection import get_feed_manager, get_connection_manager
from app.services.feed_manager import FeedManager
from app.websocket.connection_manager import ConnectionManager
from app.models.websocket import WebSocketMessage, WebSocketMessageType

logger = logging.getLogger(__name__)
router = APIRouter()

@router.websocket("/ws/{client_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    client_id: str,
    connection_manager: ConnectionManager = Depends(get_connection_manager),
    feed_manager: FeedManager = Depends(get_feed_manager),
):
    await connection_manager.connect(websocket, client_id, "anonymous")  # Or get user_id from token

    try:
        while True:
            data = await websocket.receive_json()
            message = WebSocketMessage(**data)

            if message.type == WebSocketMessageType.GET_INITIAL_FEED_STATUSES:
                logger.info(f"Client {client_id} requested initial feed statuses.")
                statuses = await feed_manager.get_all_statuses()
                response = WebSocketMessage(
                    type=WebSocketMessageType.INITIAL_FEED_STATUSES,
                    data=statuses
                )
                await websocket.send_json(response.dict())

            elif message.type == WebSocketMessageType.SUBSCRIBE_TO_FEED:
                feed_id = message.data.get("feed_id")
                if feed_id:
                    logger.info(f"Client {client_id} subscribing to feed {feed_id}")
                    await connection_manager.subscribe_to_topic(client_id, feed_id)
                else:
                    logger.warning(f"Client {client_id} sent subscribe message without feed_id")

            # Add other message type handlers here

    except WebSocketDisconnect:
        logger.info(f"Client {client_id} disconnected.")
        await connection_manager.disconnect(client_id)
    except Exception as e:
        logger.error(f"Error in WebSocket endpoint for client {client_id}: {e}", exc_info=True)
        await connection_manager.disconnect(client_id)
