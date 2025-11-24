
import logging
import json
import asyncio
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query
from app.dependency_injection import get_feed_manager, get_connection_manager
from app.services.feed_manager import FeedManager
from app.websocket.connection_manager import ConnectionManager
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum
from app.dependencies.auth import get_current_user_ws
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()

async def keepalive_sender(websocket: WebSocket):
    """Sends a keepalive message every 15 seconds."""
    pong_message = WebSocketMessage(type=WebSocketMessageTypeEnum.INTERNAL_PONG, data={}).model_dump_json()
    while True:
        try:
            await asyncio.sleep(15)
            await websocket.send_text(pong_message)
            logger.debug("Sent keepalive pong to client.")
        except WebSocketDisconnect:
            logger.debug("Client disconnected, stopping keepalive sender.")
            break
        except RuntimeError as e:
            if "after sending 'websocket.close'" in str(e) or "Unexpected ASGI message" in str(e):
                logger.debug("Keepalive sender: connection already closed.")
            else:
                logger.error(f"Error in keepalive sender: {e}", exc_info=True)
            break
        except Exception as e:
            logger.error(f"Error in keepalive sender: {e}", exc_info=True)
            break

async def message_receiver(
    websocket: WebSocket,
    client_id: str,
    connection_manager: ConnectionManager,
    feed_manager: FeedManager,
):
    """Receives and processes messages from the client."""
    while True:
        try:
            raw_data = await websocket.receive_text()
            try:
                # Handle non-JSON keepalive messages (like browser-sent pongs)
                if raw_data.strip().startswith("pong"):
                    logger.debug(f"Received browser pong from {client_id}.")
                    continue
                
                data = json.loads(raw_data)

                # Also handle our own internal ping from client
                if data.get("type") == WebSocketMessageTypeEnum.INTERNAL_PING.value:
                    logger.debug(f"Received internal ping from {client_id}.")
                    continue

            except json.JSONDecodeError:
                logger.warning(
                    f"Could not decode JSON from client {client_id}. "
                    f"Received data: {raw_data[:200]}... (truncated). Ignoring."
                )
                continue  # Ignore malformed JSON

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
                feed_id = message.data.feed_id
                if feed_id:
                    logger.info(f"Client {client_id} subscribing to feed {feed_id}")
                    await connection_manager.subscribe_to_topic(client_id, f"video:{feed_id}")
                    await connection_manager.subscribe_to_topic(client_id, f"feed:{feed_id}")
                    await connection_manager.subscribe_to_topic(client_id, f"feed_alerts:{feed_id}")

            elif message.type == WebSocketMessageTypeEnum.UNSUBSCRIBE_FROM_FEED:
                feed_id = message.data.feed_id
                if feed_id:
                    logger.info(f"Client {client_id} unsubscribing from feed {feed_id}")
                    await connection_manager.unsubscribe_from_topic(client_id, f"video:{feed_id}")
                    await connection_manager.unsubscribe_from_topic(client_id, f"feed:{feed_id}")
                    await connection_manager.unsubscribe_from_topic(client_id, f"feed_alerts:{feed_id}")

            elif message.type == WebSocketMessageTypeEnum.START_FEED:
                feed_id = message.data.feed_id
                if feed_id:
                    logger.info(f"Client {client_id} requested to start feed {feed_id}")
                    await feed_manager.handle_start_feed(feed_id)

            elif message.type == WebSocketMessageTypeEnum.STOP_FEED:
                feed_id = message.data.feed_id
                if feed_id:
                    logger.info(f"Client {client_id} requested to stop feed {feed_id}")
                    await feed_manager.handle_stop_feed(feed_id)
            
            # Other message types can be handled here

        except WebSocketDisconnect:
            logger.info(f"Client {client_id} disconnected during message reception.")
            break # Exit loop to end the receiver task
        except RuntimeError as e:
            logger.warning(f"RuntimeError during message reception for client {client_id}: {e}")
            break


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
        # Run sender and receiver concurrently
        await asyncio.gather(
            keepalive_sender(websocket),
            message_receiver(websocket, client_id, connection_manager, feed_manager)
        )
    except Exception as e:
        # This will catch errors from either gather task
        logger.error(f"Error in WebSocket endpoint for client {client_id}: {e}", exc_info=True)
    finally:
        logger.info(f"Closing connection for client {client_id}.")
        await connection_manager.disconnect(client_id)
