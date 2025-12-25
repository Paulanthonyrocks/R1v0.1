import logging
import json
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from app.dependency_injection import get_feed_manager, get_connection_manager
from app.services.feed_manager import FeedManager
from app.websocket.connection_manager import ConnectionManager
from app.models.websocket import (
    WebSocketMessage,
    WebSocketMessageTypeEnum,
    FeedIdData,
    PongData,
    InitialFeedStatusesData,
    SubscribeData,
    UnsubscribeData
)
from app.dependencies.auth import get_current_user_ws
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()

async def message_receiver(
    websocket: WebSocket,
    client_id: str,
    connection_manager: ConnectionManager,
    feed_manager: FeedManager
):
    """
    Main loop for receiving and processing messages from a connected client.
    """
    try:
        async for message_text in websocket.iter_text():
            try:
                # 1. Parse raw JSON
                message_dict = json.loads(message_text)
                
                # 2. Validate basic structure using Pydantic
                # Using construct/dict access for speed if needed, but model_validate is safer
                try:
                    message = WebSocketMessage.model_validate(message_dict)
                except Exception as e:
                    logger.warning(f"Invalid message format from {client_id}: {e}")
                    continue

                msg_type = message.type
                data = message.data or {}

                # 3. Handle Message Types
                if msg_type == WebSocketMessageTypeEnum.PING:
                    await connection_manager.send_personal_message(
                        WebSocketMessage(
                            type=WebSocketMessageTypeEnum.PONG,
                            data=PongData().model_dump()
                        ).model_dump_json(),
                        client_id
                    )
                elif msg_type == WebSocketMessageTypeEnum.PONG:
                    # Client responded to a server PING
                    logger.debug(f"Received PONG from {client_id}")
                    connection_manager.record_pong(client_id) # Record the pong message
                    pass

                elif msg_type == WebSocketMessageTypeEnum.GET_INITIAL_FEED_STATUSES:
                    statuses = await feed_manager.get_all_statuses()
                    response = WebSocketMessage(
                        type=WebSocketMessageTypeEnum.INITIAL_FEED_STATUSES,
                        data=InitialFeedStatusesData(feeds=statuses).model_dump()
                    )
                    await connection_manager.send_personal_message(response.model_dump_json(), client_id)

                elif msg_type == WebSocketMessageTypeEnum.START_FEED:
                    try:
                        feed_id_data = FeedIdData(**data)
                        await feed_manager.start_feed(feed_id_data.feed_id)
                    except Exception as e:
                        logger.error(f"Error starting feed: {e}")
                        # Optionally send error back to client

                elif msg_type == WebSocketMessageTypeEnum.STOP_FEED:
                    try:
                        feed_id_data = FeedIdData(**data)
                        await feed_manager.stop_feed(feed_id_data.feed_id)
                    except Exception as e:
                        logger.error(f"Error stopping feed: {e}")

                elif msg_type == WebSocketMessageTypeEnum.RESTART_FEED:
                    try:
                        feed_id_data = FeedIdData(**data)
                        await feed_manager.restart_feed(feed_id_data.feed_id)
                    except Exception as e:
                        logger.error(f"Error restarting feed: {e}")

                elif msg_type == WebSocketMessageTypeEnum.SUBSCRIBE:
                    try:
                        sub_data = SubscribeData(**data)
                        await connection_manager.subscribe(client_id, sub_data.topic)
                    except Exception as e:
                        logger.warning(f"Subscribe error: {e}")

                elif msg_type == WebSocketMessageTypeEnum.UNSUBSCRIBE:
                    try:
                        unsub_data = UnsubscribeData(**data)
                        await connection_manager.unsubscribe(client_id, unsub_data.topic)
                    except Exception as e:
                        logger.warning(f"Unsubscribe error: {e}")
                
                elif msg_type == WebSocketMessageTypeEnum.SUBSCRIBE_TO_FEED:
                    try:
                        feed_id_data = FeedIdData(**data)
                        await connection_manager.subscribe_to_feed(client_id, feed_id_data.feed_id)
                    except Exception as e:
                        logger.warning(f"Subscribe to feed error: {e}")

            except json.JSONDecodeError:
                logger.warning(f"Received invalid JSON from {client_id}: {message_text}")
            except Exception as e:
                logger.error(f"Error processing message from {client_id}: {e}", exc_info=True)

    except WebSocketDisconnect:
        logger.info(f"Client {client_id} disconnected normally.")
    except Exception as e:
        logger.error(f"Unexpected error in message_receiver for {client_id}: {e}", exc_info=True)

@router.websocket("/ws/{client_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    client_id: str,
    connection_manager: ConnectionManager = Depends(get_connection_manager),
    feed_manager: FeedManager = Depends(get_feed_manager),
    user: User = Depends(get_current_user_ws),
):
    if not user:
        # Auth failed (usually caught by dependency, but safe double check)
        await websocket.close(code=1008)
        return

    await connection_manager.connect(websocket, client_id, user.username)

    try:
        # Run the receiver loop directly.
        # The ConnectionManager handles keepalives (ping/pong) independently.
        await message_receiver(websocket, client_id, connection_manager, feed_manager)

    except Exception as e:
        logger.error(f"Critical WebSocket error for {client_id}: {e}", exc_info=True)
    finally:
        await connection_manager.disconnect(client_id, websocket)