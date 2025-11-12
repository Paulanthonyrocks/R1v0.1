
import logging
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query
from app.dependency_injection import get_feed_manager, get_connection_manager
from app.services.feed_manager import FeedManager
from app.websocket.connection_manager import ConnectionManager
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum
from app.dependencies.auth import get_current_user_ws
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()

@router.websocket("/ws/{client_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    client_id: str,
    connection_manager: ConnectionManager = Depends(get_connection_manager),
    feed_manager: FeedManager = Depends(get_feed_manager),
    user: User = Depends(get_current_user_ws),
):
    if not user:
        await websocket.close(code=1008)
        return
    await connection_manager.connect(websocket, client_id, user.username)

    try:
        while True:
            data = await websocket.receive_json()
            message = WebSocketMessage(**data)

            if message.type == WebSocketMessageTypeEnum.GET_INITIAL_FEED_STATUSES:
                logger.info(f"Client {client_id} requested initial feed statuses.")
                statuses = await feed_manager.get_all_statuses()
                response = WebSocketMessage(
                    type=WebSocketMessageTypeEnum.INITIAL_FEED_STATUSES,
                    data={"feeds": statuses}
                )
                await websocket.send_text(response.model_dump_json())

            elif message.type == WebSocketMessageTypeEnum.SUBSCRIBE_TO_FEED:
                feed_id = getattr(message.data, 'feed_id', None)
                if feed_id:
                    logger.info(f"Client {client_id} subscribing to feed {feed_id}")
                    await connection_manager.subscribe_to_topic(client_id, f"video:{feed_id}")
                    await connection_manager.subscribe_to_topic(client_id, f"feed:{feed_id}")
                    await connection_manager.subscribe_to_topic(client_id, f"feed_alerts:{feed_id}")
                else:
                    logger.warning(f"Client {client_id} sent subscribe message without feed_id")

            elif message.type == WebSocketMessageTypeEnum.UNSUBSCRIBE_FROM_FEED:
                feed_id = getattr(message.data, 'feed_id', None)
                if feed_id:
                    logger.info(f"Client {client_id} unsubscribing from feed {feed_id}")
                    await connection_manager.unsubscribe_from_topic(client_id, f"video:{feed_id}")
                    await connection_manager.unsubscribe_from_topic(client_id, f"feed:{feed_id}")
                    await connection_manager.unsubscribe_from_topic(client_id, f"feed_alerts:{feed_id}")
                else:
                    logger.warning(f"Client {client_id} sent unsubscribe message without feed_id")

            elif message.type == WebSocketMessageTypeEnum.AUTHENTICATE:
                logger.info(f"Client {client_id} sent an authentication message.")


            # Add other message type handlers here

    except WebSocketDisconnect:
        logger.info(f"Client {client_id} disconnected.")
        await connection_manager.disconnect(client_id)
    except Exception as e:
        logger.error(f"Error in WebSocket endpoint for client {client_id}: {e}", exc_info=True)
        await connection_manager.disconnect(client_id)
